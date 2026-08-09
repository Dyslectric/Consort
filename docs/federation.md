# Federation

One key, many servers.

A user holds a keypair. A **shell** — an app you or anyone else can host — keeps a
list of servers and proves that key to each of them. Every server decides
independently what the key is entitled to. Nothing else crosses the boundary.

This document is the design, before the code. It fixes the scope, names the
threat that shapes every other decision, specifies the wire protocol, and lists
what is still open. Work lives on branch `federation`.

---

## Scope — settled, do not re-open

**Identity federates. Nothing else does.**

| | federated? | why |
|---|---|---|
| identity | **yes** | one key authenticates you to every server on your list |
| channels, DMs, messages | no | realm-scoped integer IDs, no federation protocol, multi-year project |
| calls | no | a call stays inside the server that minted it |
| presence, occupancy | no | follows the call |

The decision that makes this tractable: **a conversation never spans servers.**
You are a member of server A and a member of server B; you are not a member of a
channel that lives on both. Everything expensive about federation — state
resolution, identity mapping across realms, cross-server membership — is
downstream of content federation, and content federation is out.

What you get is the thing that was actually asked for: a server list, one
identity, and no password per server.

### Why this is worth doing on its own

- **Revocation stays local.** Each server holds its own binding from your key to
  an account and can drop it. There is no global revocation registry to build,
  host, or trust — a direct consequence of the scope above.
- **The call layer does not move.** Room derivation, JWT minting, tenancy, the
  conferencing service and Prosody are untouched. See "What does not change".
- **It composes with what exists.** Authentik/SAML stays exactly as it is. Key
  auth is an additional backend, not a replacement.

---

## The threat that shapes everything

> **If the shell is a web page, whoever hosts it can take the key.**

This is not a detail to mitigate later; it decides the product. A hosted
JavaScript app can ship different JavaScript tomorrow. Subresource integrity does
not help when the host controls the document that names the hashes. The user's
private key is in that origin's storage, and the code that unlocks it is code the
host wrote.

So say the trust model out loud rather than implying one that does not exist:

| shell form | who can steal the key | honest pitch |
|---|---|---|
| installed app (desktop/PWA), signed releases | whoever signs releases, at update time | **the real one** |
| hosted web page | the host, silently, at any page load | convenience; trust the host as much as a password manager you don't control |

Two consequences:

1. **The installed app is the reference implementation.** A hosted build may
   exist and should work, but its README must say what it is. "I or others may
   host a frontend" is fine — provided the people typing their recovery phrase in
   know whose JavaScript is asking.
2. **Keep the key unlocked for as short a window as possible.** Decrypt to
   memory for a signature, then drop it. This does not stop a malicious host, but
   it bounds an opportunistic XSS to the moments you are actually logging in.

A future upgrade closes most of this: non-extractable per-device WebCrypto keys
signed by the root identity, so day-to-day auth uses a key that *cannot* be
exfiltrated even by hostile page script, and the extractable root key comes out
only to enroll a new device. That is the cross-signing model, and the protocol
below is written so it can be added without a wire break — the presented key is a
field, never an assumption.

---

## The identity key

**Ed25519.** Small, fast, no curve/parameter choices to get wrong, and available
in WebCrypto (Chrome 137+, Firefox, Safari 17+) as well as every server-side
runtime that matters.

```
root identity key   Ed25519, generated client-side, never transmitted
  ├── public half   the user's portable identity; shareable, printable
  └── private half  encrypted at rest, decrypted only to sign
```

**At rest:** encrypted with a key derived from a passkey via the WebAuthn `prf`
extension, falling back to a passphrase through Argon2id where `prf` is
unavailable. The passkey is *not* the identity — it is the lock on the box. This
matters: WebAuthn credentials are scoped to one relying-party origin, so a
passkey can never itself be the cross-server identity without making the shell's
origin a trusted third party to every server. Wrapping sidesteps that entirely.

**Recovery:** a BIP39-style phrase encoding the root private key. It is the only
backup. Printing it is the supported path; a hosted shell offering to "store it
safely for you" is the thing this design exists to avoid.

**Fingerprint:** `base32(sha256(pubkey))[:20]`, grouped for reading aloud. This is
what a user pastes into an invitation, and what a server shows next to a bound
key so a human can compare.

---

## The wire protocol

Challenge–response, once per server, producing a normal Zulip session.

### One rule above all others

**The client constructs the signed payload. The server supplies only a nonce.**

A client that signs opaque server-chosen bytes is a signing oracle. A hostile
server hands you "random bytes" that are in fact a meaningful statement somewhere
else in the system — another server's challenge, a future vouching statement, a
token body — and you sign it. So: the nonce arrives as base64url of exactly 32
bytes, it is validated as such, and it is placed in *a field* of a structure the
client builds. It is never the message.

### Step 1 — challenge

```http
GET /api/v1/federation/auth/challenge?realm=engineering
```
```json
{
  "nonce": "9f2c…",            // 32 bytes, base64url, single-use, server-stored
  "server": "chat.example.org",
  "realm": "engineering",
  "expires_at": 1786500000
}
```

The server stores the nonce in redis with a short TTL and **deletes it on use.**
Without single-use, a signature captured inside the validity window replays.

### Step 2 — the client builds and signs

```json
{
  "v": 1,
  "typ": "zulip-meet.auth",
  "aud": "chat.example.org",
  "realm": "engineering",
  "key": "<base64url ed25519 public key>",
  "nonce": "<echoed from step 1>",
  "iat": 1786499940,
  "exp": 1786499970
}
```

Signed over:

```
"zulip-meet.auth.v1" || 0x00 || canonical_json(payload)
```

Both halves of that construction are load-bearing:

- **The `aud` field binds the signature to one server.** Without it, server A
  takes the signature you just gave it, replays it to server B, and is you on
  server B. Every server rejects a payload whose `aud` is not its own canonical
  host — this is the single most important line of verification code in the
  design.
- **The constant prefix separates domains.** A signature produced here can never
  be reinterpreted as a signature over anything else this project ever defines,
  because nothing else will use that prefix. Add a new signed statement type
  later (device enrollment, vouching), give it a new prefix.

`exp` is tight — 30 seconds. This is a login handshake, not a credential.

### Step 3 — verify, and hand back a login token

```http
POST /api/v1/federation/auth/verify
{ "payload": { … }, "signature": "<base64url>" }
```

The server checks, in this order and failing closed at each: `typ` and `v` known;
`aud` equals its own canonical host; `realm` exists; `nonce` present in redis
(then delete); `iat`/`exp` sane against clock skew; signature valid over the
prefixed canonical encoding using the `key` in the payload; **and the key is
bound to an active account in that realm.**

Then it returns a one-time login token, and the shell navigates that server's
view to `/accounts/login/subdomain/<token>`.

---

## The Zulip seam

This does not need inventing — Zulip already has the shape, and the federation
backend is a small one. Verified against the fork at
`\\wsl.localhost\Ubuntu-24.04\home\dave\zulip`:

| piece | where | what it gives us |
|---|---|---|
| `ExternalAuthResult` | `zproject/backends.py:1844` | wraps a `UserProfile` + data dict |
| `.store_data()` | `zproject/backends.py:1895` | puts it in redis, returns a one-time token |
| `LOGIN_KEY_EXPIRATION_SECONDS = 15` | `zproject/backends.py:1847` | already a 15-second single-use handoff — exactly right |
| `log_into_subdomain` | `zerver/views/auth.py:831` | consumes the token, establishes the session |
| route | `zproject/urls.py:690` | `accounts/login/subdomain/<token>` |
| `@external_auth_method` + `AUTH_BACKEND_NAME_MAP` | `zproject/backends.py:4508` | registers a backend and its login button |
| `ZulipRemoteUserBackend` | `zproject/backends.py:2016` | the closest existing model to copy |

So `ZulipMeetKeyBackend` mints an `ExternalAuthResult`, calls `store_data()`, and
step 3 returns that token. Session establishment, realm checks, deactivated-user
races and signup routing are all Zulip's existing code path.

`ExternalAuthDataDict` (`backends.py:1825`) also carries `is_signup` and
`multiuse_object_key` — which is Zulip's invitation mechanism, and therefore the
seam for invite-gated key registration below.

### One hazard to not recreate

`architecture.md` §4.4 already flags `JWT_AUTH_KEYS` + `POST /api/v1/jwt/fetch_api_key`:
a holder of one shared secret can obtain **any** user's API key, and the standing
advice is to keep it empty. That is the wrong-shaped version of exactly this
feature. The difference is the whole point — here the proof is a per-user
signature over a server-bound, single-use challenge, so there is no bearer secret
whose theft is total. Do not add a "trusted shell secret" shortcut later; it
would collapse this design back into that one.

---

## Server discovery

Adding a server to the list means typing a hostname. The shell then fetches:

```http
GET https://chat.example.org/.well-known/zulip-meet/server
```
```json
{
  "version": 1,
  "server": "chat.example.org",
  "realms": [
    { "subdomain": "engineering", "name": "Engineering", "url": "https://engineering.chat.example.org" }
  ],
  "auth": {
    "methods": ["ed25519-challenge", "saml"],
    "registration": "invite"
  },
  "features": ["calls", "lounges", "occupancy", "embedded-call"],
  "meet": { "url": "https://meet.example.org" }
}
```

Deliberately **not signed.** TLS already authenticates the origin, and a signature
here would only be verifiable against a key fetched from the same origin.
Introducing a server signing key buys nothing until something needs to be
verified *away* from the server that issued it — which, under identity-only
scope, nothing does. Do not add it speculatively.

`registration` is the interesting field: `open`, `invite`, or `closed`. It tells
the shell whether a key it has never presented here can become an account, which
determines whether "Add server" shows a join button or an "ask for an invite"
message.

---

## Binding a key to an account

Zulip needs a `UserProfile`, which needs a `delivery_email`. A public key is not
an email address, and pretending otherwise breaks notifications, invitations and
every admin screen. Two paths, in build order:

**1. Bind to an existing account** *(first, and the default forever)*
You log in the way you already do — Authentik via SAML — go to settings, and add
a key. Same gesture as adding an SSH key to a forge. The server stores
`(realm, public_key, label, added_at, last_used_at)` and you can revoke a row.
No provisioning problem, no email problem, and federation becomes strictly
additive to a deployment that already works.

**2. Key-provisioned accounts** *(opt-in, per server)*
For "others may host" to mean anything, a stranger's key must be able to become
an account. Gate it through the invitation flow that already exists rather than
inventing one: the invitation carries `multiuse_object_key`, the user presents a
key and an email, the email is verified as it is today (per the existing Mailcow
provisioning path), and the key is bound on acceptance. `registration: "open"`
exists in the descriptor for people who want it; it should not be the default,
and the docs should not pretend it is safe on a server with real channels.

Authentication is not authorization. The current SAML setup gates org access on
`attr_org_membership`; a bound key needs the equivalent — being able to prove a
key says which account you are, not which realms you may enter.

---

## The shell

Owns three things: the key, the server list, and per-server credentials. It
renders each server's own (forked) Zulip web app, which is what preserves the
embedded call panel, the call-aware sidebar, lounges and everything else already
built.

### Trap: do not embed a server in a cross-origin iframe

The obvious build — a web page with each server in an `<iframe>` — is a dead end.
Zulip's session cookie in a cross-origin frame is a third-party cookie, and
modern browsers restrict or drop those (Safari ITP, Chrome's phase-out). Sessions
will be flaky in ways that look like server bugs and are not.

Render each server as a **top-level context**: an Electron `BrowserView` /
`WebContentsView` with its own `persist:` partition, or a real tab. This is what
Zulip's own desktop app does, and it gives credential isolation between servers
for free, from the browser's process model rather than from our discipline.

That constraint is most of the argument for the installed app in the threat
model section above. They are the same conclusion arrived at twice.

### Storage isolation

One server must never read another's session, and no server may read the key
store. Separate partitions per server; the key store in the shell's own context;
`postMessage` across that boundary only with an exact origin check and a
fixed message schema.

---

## What does not change

Worth stating plainly, because it bounds the blast radius of this whole branch:

- **Room derivation.** `HMAC(JITSI_ROOM_KEY, scope‖epoch)` — untouched.
- **Token minting.** `mint_jitsi_token` — untouched. Still short-lived, still
  single-room, still HS256-or-RS256 per deployment.
- **Tenancy.** Still `user.realm.subdomain` via `resolve_jitsi_tenant`.
- **Prosody, `event_sync`, `muc_census`, the conferencing service.** Untouched.
- **Authentik and SAML.** Untouched, and still the primary login for existing
  deployments.

By the time a call is minted, the user is an ordinary authenticated `UserProfile`
in one realm. Everything downstream cannot tell how they logged in — which is the
property that makes identity-only federation cheap, and the property to protect
when tempted to widen scope.

---

## Open decisions

**Correlation across servers.** Presenting the same public key everywhere lets
any two servers confirm you are the same person by comparing one string. That may
be exactly what is wanted; it is also unfixable after the fact. The alternative is
per-server subkeys, `HKDF(root, server_origin)`, unlinkable by default, with the
root reserved for invitations and vouching. Cost: your fingerprint is no longer
one thing you can hand someone. The protocol above already treats the presented
key as a field, so this can be decided later — but decide it before anyone has
keys worth keeping.

**Theft recovery is O(servers).** Local revocation means a stolen root key must be
revoked on every server individually, by a user who may not remember the whole
list. A "revoke everywhere" gesture needs the shell's list to be recoverable,
which means backing it up alongside the key. Cross-signed device keys reduce how
often the root is exposed at all and are the better answer if this becomes real.

**Which realm does a server default to?** A server hosts many realms
(`*.chat.example.org`). One list entry per server, or one per realm? Per realm is
more honest about where accounts live and makes the list long; per server needs a
picker on select. Leaning per realm, because the account is the realm-scoped
thing.

**Does the shell hold the key, or does the OS?** Keychain / Credential Manager /
Secret Service is strictly better than an encrypted blob when the shell is an
installed app, and unavailable when it is a web page. Probably both, chosen at
build time.

**Mobile.** Still the one requirement with no good answer, exactly as
`architecture.md` §7.3 says. Do not let it silently become a shell requirement.

---

## Build ladder

Each rung is independently useful and independently abandonable.

| | what | depends on |
|---|---|---|
| **F0** | this document | — |
| **F1** | `.well-known/zulip-meet/server` descriptor + a shell that lists servers and opens them, logging in the existing way | — |
| **F2** | the key library: generate, wrap via WebAuthn `prf`/Argon2id, recovery phrase, sign. Standalone, testable, no server | — |
| **F3** | `ZulipMeetKeyBackend` + challenge/verify endpoints + "add a key" in settings | F2 |
| **F4** | shell wiring: per-server partitions, credential isolation, select-server-and-you-are-in | F1, F3 |
| **F5** | invite-gated key registration for accounts that did not exist first | F3 |

F1 and F2 do not touch each other and can be built in either order. F3 is the
first rung that requires a Zulip dev environment, and the first that can be got
wrong in a way that matters — the `aud` check and the single-use nonce are where
the review effort belongs.

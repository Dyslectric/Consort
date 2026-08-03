# Zulip server patch — authenticated Jitsi calls

A patch against `zulip/zulip` adding `POST /calls/jitsi/create`: a server-side endpoint that
checks the requesting user is a member of the conversation, derives an unguessable room name,
and mints a short-lived JWT scoped to exactly that room and tenant.

It replaces the entitlement model Zulip ships today, which is a client-side `Math.random()` room
name and no check of any kind.

```
 10 files changed, 747 insertions(+), 1 deletion(-)
```

## Applying it

Two commits, structured per Zulip's commit discipline (backend with tests and API
documentation; frontend separately), against `main` at commit `f6470bb`:

Three forms of the same two commits are included, in order of fidelity:

- `zulip-jitsi-jwt.bundle` — a git bundle carrying the real commit objects. Restores SHAs
  `013f389` and `4d9faf7` exactly, with authorship, dates and trailers intact.
- `patches/*.patch` — `git format-patch` output. `git am` reconstructs the same two commits.
- `zulip-jitsi-jwt.patch` — one squashed diff, if you would rather not take the commit
  structure.

```bash
git clone https://github.com/zulip/zulip.git && cd zulip

# From the bundle (preserves the original commit hashes):
git fetch /path/to/zulip-jitsi-jwt.bundle HEAD:jitsi-jwt && git switch jitsi-jwt

# ...or from the patches:
git am /path/to/patches/*.patch

# Then, before anything else:
./tools/test-backend zerver.tests.test_jitsi_jwt
./tools/lint
```

`zulip-jitsi-jwt.patch` is the same change as a single squashed diff, if you would
rather not take the commit structure.

The repository's own `.claude/CLAUDE.md` sets out the standards this was written
against: the API double-entry changelog (`api_docs/unmerged.d/ZF-bdd936.md` plus the
matching `ZF-bdd936` reference in the OpenAPI **Changes** note), translated user-facing
strings, complete type annotations, and one coherent idea per commit. `PR-description.md`
contains a suggested PR body following their template, including the open questions
worth raising in review rather than deciding silently.

## What it does

**`zerver/lib/jitsi_token.py`** — room derivation and token minting. Deliberately free of
Zulip-specific imports beyond `django.conf.settings`, so it can be reviewed on its own terms.

```
room = "c-" + HMAC-SHA256(JITSI_ROOM_KEY, scope || epoch)[:16]
```

`scope` is `realm:<id>|channel:<id>` or `realm:<id>|dm:<sorted user ids>`. The epoch rotates
a conversation's room, which gives a clean "start a fresh meeting" primitive and a clean
recovery path if a link leaks. Rotating `JITSI_ROOM_KEY` rekeys every room at once.

**`create_jitsi_call` in `zerver/views/video_calls.py`** — the endpoint, following the
established pattern of the four existing server-brokered providers (`rest_path` registration,
`@typed_endpoint` injecting the authenticated `UserProfile`, `json_success` with a `url`).

It differs from those four in one respect, which is the entire point of the patch: **it
performs an authorization check.** `access_stream_by_id` raises unless the user can reach the
channel, and a `None` subscription is rejected — subscription is what we treat as membership.
None of Zulip's existing call endpoints check anything; any realm member can mint a call for
any room name. That is harmless while the resulting room is unauthenticated anyway. It stops
being harmless the moment Prosody starts trusting our signature.

The epoch is signed with Django's `Signer` and bound to its scope, so it round-trips through
the client without becoming a way to force a room in someone else's conversation. This is the
same trick `get_bigbluebutton_url` already uses to stop a user tampering with their own
moderator flag. Keeping it signed rather than stored is what lets the endpoint stay stateless;
the conferencing service in phase three is what will own the epoch and pass it back.

**The client** asks the server for a URL when `server_jitsi_jwt_enabled` is advertised, and
otherwise falls back to the existing behaviour unchanged. A deployment that has not configured
JWT sees no difference at all.

## Token shape

```json
{
  "iss": "zulip", "aud": "jitsi",
  "sub": "engineering",
  "room": "c-6fe01c2e8f27d6f5",
  "iat": 1785524477, "nbf": 1785524472, "exp": 1785524597,
  "context": {
    "group": "engineering",
    "user": {"id": "31", "name": "David Green", "moderator": "false"},
    "features": {"recording": false, "livestreaming": false,
                 "transcription": false, "outbound-call": false}
  }
}
```

Four details that are easy to get wrong and are enforced in code:

- **`exp` is two minutes.** Prosody validates at connection and at MUC join and never again,
  so a short window costs the user nothing and shrinks the blast radius of a leak. There is no
  revocation; short expiry bounds how long a leaked token is useful for *joining* and does
  nothing about an established session.
- **`context.user.id` is a string.** Zulip user IDs are integers, and a numeric value inside
  the user context makes Prosody throw rather than degrade. Minting refuses non-strings.
- **`moderator` is a string nested in `context.user`**, matching what JaaS documents. There is
  no blessed top-level `moderator` claim in Jitsi; `token_affiliation` reads it from here. It
  maps to "may administer this channel", not to whoever clicked first.
- **A wildcard `room` is refused outright.** It is a skeleton key for the whole deployment and
  the failure is silent.

## Configuration

```python
# /etc/zulip/settings.py
JITSI_SERVER_URL = "https://jitsi.example.com"
JITSI_JWT_APP_ID = "zulip"
JITSI_TENANT_BY_GROUP = {"conf-engineering": "engineering", "conf-design": "design"}

# /etc/zulip/zulip-secrets.conf
jitsi_jwt_app_secret = <same value as Prosody's app_secret>
jitsi_room_key = <random string>
```

Tenants are resolved from Zulip user group membership, falling back to
`JITSI_DEFAULT_TENANT` and then the realm subdomain. Group names are matched in sorted order so
that a user in two mapped groups always gets the same tenant — otherwise their room name would
change between calls.

For RS256, set `jitsi_jwt_private_key` and `JITSI_JWT_KEY_ID` instead of the shared secret.
Prosody's `asap_key_server` is a static file server, **not** a JWKS endpoint: it fetches
`sha256(kid).pem` from under that base URL.

## Verification status

- **The token library is verified.** 20 assertions covering room derivation (stability,
  channel/realm/epoch scoping, key rotation), claim shape, and every refusal.
- **End to end against real Jitsi.** Tokens minted by *this patch* were fed to a real Prosody
  running Jitsi's real modules — `mod_auth_token`, `mod_jitsi_session`, `token_verification`,
  `token_no_wildcard`, `muc_domain_mapper` and `luajwtjitsi`. A correctly scoped token was
  admitted; a token for another tenant and a token for another room were both refused by
  Prosody. That is the phase one harness and the phase two patch validating each other.
- **`zerver/tests/test_jitsi_jwt.py` has not been executed.** Zulip's test suite needs its
  full dev environment (postgres, redis, rabbitmq), which was not available here. The file
  is written to Zulip's conventions and is syntax-clean, but treat every assertion in it as
  unproven until `./tools/test-backend zerver.tests.test_jitsi_jwt` passes. **Run it first.**
  The same goes for `./tools/lint`, which includes mypy and the TypeScript checker — the
  type annotations have not been machine-checked.
- **The client change has not been exercised.** No node/webpack build was run, and none of
  the manual UI verification Zulip requires (themes, window sizes, string lengths, keyboard
  navigation) has been done.
- **The patch series applies cleanly** to a pristine checkout of `f6470bb`, verified with
  `git am` on a fresh branch.

## Known gaps

**Rooms must exist before anyone can join them.** Jitsi's `token_util:verify_room` returns
`room-does-not-exist` *before* it reads any claim in the token. In a normal deployment Jicofo
creates the room during conference allocation, so this is invisible. But every epoch rotation
produces a brand-new room, so this design exercises that path constantly rather than rarely.
Test rotation explicitly against your staging Jitsi before relying on it.

**The moderator mapping is minimal.** It checks `can_administer_channel_group` for channels and
realm admin otherwise. Direct-message calls give nobody moderator. Phase five is where that
gets a real policy.

**The API feature level in the OpenAPI change is a placeholder.** Fill it in if you upstream.

## Upstreaming

This is written to be offered upstream against
[zulip#28657](https://github.com/zulip/zulip/issues/28657), "User authentication in Jitsi Meet
using JWTs", which is open with maintainer interest and no PR. The tenancy piece is the part
most likely to attract debate; if it does, `JITSI_TENANT_BY_GROUP` degrades cleanly to a single
default tenant and the patch still stands on its own as "authenticate Jitsi calls".

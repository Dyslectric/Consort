# Channel-Scoped Jitsi Conferencing for Zulip, Backed by Authentik

Architecture document, revision 4. Revised after phases one and two were built and run.
Revision 3 was written from research; this one is written from a working staging deployment
and a patch whose tokens a real Prosody has accepted.

> **AS-BUILT UPDATE (2026-08-03) — the client-side call UI has moved past §5.6.** Channels no longer
> get a roster *message*; they surface calls through a **call-aware left sidebar** fed by a real-time
> `jitsi_occupancy` push (service → core hook → `send_event` to the channel's subscribers), with
> per-user speaking rings driven by an in-iframe relay (`embedded-call/jitsi-speaking-relay.js`).
> DM/group calls keep the roster message. The [top-level README](../README.md) is the current overview.

> **AS-BUILT DEVIATION (2026-08-01) — read before trusting §2.4, §4, §5.2.** Phase 0 was built and
> the **tenancy model changed from what this document describes**. The identity layer is now
> **multi-organization**: each team is its own Zulip organization (realm at a subdomain), and access
> to each is gated by Authentik group via SAML **`attr_org_membership`**. Consequences that override
> the body of this doc:
> - **The Jitsi tenant is now the realm subdomain**, not a Zulip user group. `resolve_jitsi_tenant`'s
>   realm-subdomain fallback covers it; **`JITSI_TENANT_BY_GROUP` is left empty** and the group→tenant
>   discussion in §5.2 no longer applies.
> - **Group sync is not used** — §4.2's group-sync reasoning is moot (it was the reason for SAML, but
>   SAML stayed after group sync was dropped).
> - **Enrollment is open self-service** via an Authentik enrollment flow.
> - Zulip runs on `zulip/docker-zulip`.
>
> The identity layer (SSO and enrollment) is deployment-specific and out of scope for this
> document. Everything here about Jitsi, Prosody, the token schema, the server-patch mechanics, and
> the threat model's *call-isolation* reasoning remains valid; only the **Zulip-org / tenancy /
> group** parts are superseded.

## 1. What changed since revision 3

Revision 3 targeted Zulip after Mattermost was abandoned on licensing grounds. That decision
stands and its reasoning is unchanged. What follows is what building it taught, listed here
because several corrections invalidate specific claims made earlier.

**Phase one is complete and its gate passes.** Seven cases — a correctly scoped token
admitted, and cross-tenant, cross-room, expired, empty, forged and wildcard tokens all
refused — verified against Prosody running Jitsi's real modules. The exit criterion revision 3
set ("do not proceed until you have proven that a token for tenant A cannot open a room in
tenant B") is met and is now a command you can run in CI.

**Phase two is written.** `POST /calls/jitsi/create` exists as a two-commit patch against
Zulip, and tokens minted by that patch have been admitted by real Prosody and refused when
scoped elsewhere. Its unit tests have not been executed — Zulip's suite needs a provisioned
dev environment — so treat the patch as verified in its cryptography and unverified in its
Django plumbing.

**Four factual corrections.** Sections 2.3, 2.4, 2.8 and 2.9 below replace claims revision 3
made from documentation. Two of them would have cost days if discovered later:

- The JWT does not travel as a SASL password. It travels in the BOSH query string, and SASL
  is ANONYMOUS.
- Jitsi refuses joins to rooms that do not yet exist, *before* it reads any claim in the
  token. Since this design rotates rooms deliberately, that path is hot rather than rare.

**One methodological lesson, in 7.1.** The first live run of the phase one gate reported four
of seven cases as passing. All four were false. A security test that fails open and looks
green is the specific failure mode this project has to defend against, and it has now
happened twice for different reasons.

## 2. How Jitsi authorization really works

### 2.1 The token is a capability, not an identity

Jitsi's JWT support is not "log in as this person." It is "bearer of this token may enter
this room." Prosody's `mod_auth_token` verifies the signature, issuer, audience and expiry;
`token_verification` on the MUC component checks the `room` claim and the tenant. Everything
else in the token, including the entire `context` object, is decorative — the official
documentation is explicit that none of the context field is used for validation.

A token with `room` set to `*` is a skeleton key for the entire deployment. A token is
validated when the client connects and again at MUC join, and then never again. There is no
revocation. If someone is unsubscribed from a Zulip channel while sitting in a call, nothing
in Jitsi notices. Short expiry bounds how long a leaked token remains useful for *joining*;
it does nothing about a session already established.

Mint narrow, short-lived, single-room tokens at the moment of joining, and never issue a
wildcard token to a browser.

### 2.2 Claim structure

The claims that carry weight are `iss`, `aud`, `room` (the bare room name, not the MUC JID),
`sub` (the lowercase tenant), and `exp`. Optionally a `context` object containing `group` and
a `user` object with `id`, `name`, `email` and `avatar`.

Every field inside `user` must be a valid string. Passing `null` or a numeric type throws
rather than degrading. Zulip user IDs are integers, so this coercion is load-bearing; the
phase two patch refuses to mint a token with a non-string user field rather than discovering
it at join time.

`presence_identity` on the virtual host is what surfaces the context data to other
participants. Without it the display information is discarded.

### 2.3 Moderator status — corrected

Revision 3 described `token_affiliation` as a `jitsi-contrib` module. **That is no longer
true.** jitsi-contrib renamed theirs to `token_affiliation_legacy`, with the note "Jitsi
officially provides a `token_affiliation` module now." The upstream module ships inside the
Jitsi distribution, so enabling it in the MUC component's module list is enough and nothing
needs fetching.

If you ever want the old behaviour, the legacy module is at
`token_affiliation_legacy/mod_token_affiliation_legacy.lua` — the filename changed too, not
just the directory — and it additionally wants `wait_for_host_disable_auto_owners = true` on
the MUC component.

Either way the substance is unchanged and still catches people: the flag is read from
`context.user.moderator`, a **string** nested inside the user context, not from a top-level
claim. Setting `"moderator": true` at the top level validates fine and grants nobody
anything.

### 2.4 Tenancy — corrected, and this one matters

Revision 3 said tenant-style URLs work by giving each tenant its own virtual host. That is
wrong, and building it that way fails in a way that looks like a token problem.

The real layout is **one virtual host and one MUC component**. Clients address
`conference.<tenant>.<base>`, and `mod_muc_domain_mapper` rewrites that into
`[<tenant>]room@conference.<base>`. `token_util:verify_room` then recovers the tenant by
subtracting `muc_mapper_domain_base` from the mapped name and compares it against `sub`.

Three consequences:

- **`muc_mapper_domain_base` is required.** Without it the tenant cannot be recovered and
  every join is refused, including legitimate ones.
- **Tenant enforcement lives on the MUC component, not at SASL.** A cross-tenant token
  authenticates successfully and is refused at join. Any test that asserts *where* a
  rejection happens rather than *whether* it happens will fail against a correct deployment.
- The isolation property itself is unchanged and confirmed by measurement: a token for
  tenant A cannot open a room in tenant B.

The working configuration is in the phase one harness at
`reference/prosody-standalone.cfg.lua`. Two honest caveats carry forward: multi-tenancy is
thinly documented, and the Docker distribution has a history of friction here. Both are
reasons the gate in phase one exists.

### 2.5 Push events replace your census poller

`event_sync` from `jitsi-contrib` is a Prosody component that POSTs to an external API on
room and occupant lifecycle events, hitting `/events/room/created`, `/events/room/destroyed`,
`/events/occupant/joined` and `/events/occupant/left` under a configured prefix.

Set `include_user_info = true`. With JWT auth in use it then includes the name, email and id
from the user context in the occupant payload — so putting the Zulip user ID in
`context.user.id` (which the phase two patch does) makes occupancy events directly
attributable with no correlation table. It accepts extra headers, so attach a bearer secret,
and its HTTP calls are non-blocking, so a slow consumer cannot wedge Prosody.

Its README flags two breakout-room subtleties: breakout rooms are created only when an
occupant actually moves into one, and the main room is destroyed only when it and all its
breakouts are empty.

Keep `mod_muc_census` as a reconciliation mechanism rather than a primary source. Note that
it contains logic to detect leaked occupants, which tells you the Jitsi authors consider
drift real rather than theoretical.

### 2.6 The rest of the useful module inventory

`token_no_wildcard` rejects wildcard room claims and is cheap insurance against your own
bugs — phase one confirms it works. `token_lobby_bypass` and
`token_lobby_bypass_for_initiator` handle the waiting room. `token_owner_party` prevents
unauthorized room creation and ends the conference when the owner leaves. `frozen_nick` stops
users renaming themselves away from the identity the token asserted.
`per_room_max_occupants` caps occupancy by room or subdomain.

The Jicofo reservation system calls an external HTTP service when a conference is created and
can refuse it. That is a second, independent enforcement point that could ask the conferencing
service "is this room legitimate right now", catching the case where a token was valid at mint
time but membership has since changed. More moving parts than phase three wants, but the right
answer if you ever need harder guarantees than a bearer token provides.

### 2.7 Key validation: shared secret versus public key

Shared secret uses HMAC with an `app_secret` known to both sides. Public key validation uses
`asap_key_server`, where Prosody fetches the public key by taking SHA-256 of the `kid` header
and appending `.pem`.

`asap_key_server` **is not a JWKS endpoint**. It is a base URL under which files named
`sha256(kid).pem` are served. Pointing it at Authentik's JWKS URL does not work. If you want
asymmetric signing you need a trivial static file server. The phase one harness generates the
keypair and names the public half correctly, because this is the detail people get wrong.

Set `asap_accepted_issuers` and `asap_accepted_audiences` explicitly; they no longer default
to accepting anything.

### 2.8 How the token actually reaches Prosody — new

This was not in revision 3 and is not obvious from the documentation.

The JWT is **not** a SASL password. Jitsi's `mod_jitsi_session` hooks the `bosh-session`
event and reads `token`, `room` and `prefix` off the BOSH URL query string — or an
`Authorization: Bearer` header — and stores the token on the session. `mod_auth_token` then
registers a SASL **ANONYMOUS** mechanism whose callback validates that stored token. Its
`provider.test_password` returns "Password based auth not supported" in as many words.

Two things follow. Because `mod_jitsi_session` hooks session *creation*, the token must be
present on the request that opens the BOSH session, not merely on a later one. And a PLAIN
attempt fails with `invalid-mechanism`, which is indistinguishable from a rejected token
unless you are reading carefully — see 7.1, because that is exactly how the phase one gate
first lied.

`mod_auth_token` declares `module:depends("jitsi_session")`. If that module is not loadable,
the auth provider silently provides nothing and Prosody logs "No available SASL mechanisms".

### 2.9 Rooms must exist before anyone can join — new

`token_util:verify_room` returns `room-does-not-exist` **before it reads a single claim in
the token**. Room creation is Jicofo's job; Jicofo sits in `token_verification`'s allowlist
and creates the room during conference allocation, after which participants join an existing
one.

This is invisible in a normal deployment and very visible in this design, because **room
rotation is a feature here**. Every epoch increment produces a room that has never existed.
Whatever creates rooms — Jicofo in production — is therefore on the hot path for the "start a
fresh meeting" primitive rather than being exercised occasionally.

It also has a nasty testing consequence, covered in 7.1: a cross-room test whose target room
does not exist passes without the `room` claim ever being read.

## 3. The Zulip integration surface

Unchanged from revision 3 in substance; summarised here, with the parts phase two exercised
marked as confirmed.

### 3.1 There is no plugin system, and this is deliberate

`INSTALLED_APPS` is static. The event dispatch functions in `zerver/tornado/django_api.py`
are plain in-process Python with a comment saying they should only be called from
`zerver/actions/*.py`. Zulip's own widget documentation states there is no plugin model and
none on the immediate roadmap. Any server behaviour change is a fork or an upstream pull
request. **Confirmed** by phase two, which is a fork.

### 3.2 What a bot can do without touching the server

A generic bot is an ordinary account with API-only login, so it can subscribe to channels and
consume the full event stream through `POST /register` plus `GET /events`. Outgoing webhook
bots fire only on mentions and DMs, by design. No bot can post as another user.

The endpoints phase three needs: `POST /messages`, `PATCH /messages/{id}`,
`GET /streams/{stream_id}/members`, `GET /users/{user_id}/channels`, `POST /json/submessage`,
and `POST /users/me/status`.

### 3.3 The submessage mechanism is the custom event channel

`POST /json/submessage` attaches a free-form `msg_type` and `content` to an existing message.
Validation only inspects the payload for `poll` and `todo` widgets; anything else is stored
unmodified and broadcast as a `submessage` event to everyone who can see the parent message.

The entitlement filtering phase three would otherwise implement by hand is therefore free:
occupancy updates reach exactly the people who can see the call message. `verify_submessage_sender`
restricts submessages to the message's author, so **the call message must be posted by the
bot**. The event type is marked experimental in the API documentation, which is why 5.6
option 1 exists as a fallback that depends on nothing but message edits.

### 3.4 Widgets, and where the fork becomes unavoidable

Through the public API a third party can create `poll`, `todo` and `zform`. A `zform` renders
buttons that auto-send a reply — a usable join button, but it cannot render a live roster.
The client renderer registry in `web/src/generic_widget.ts` is a hardcoded map. A live
occupancy roster inside a message means a new widget type, which means a backend and a
frontend change.

### 3.5 Organizations, channels, and the missing middle

Server, realm, channel — no intermediate grouping. Realms are hard boundaries with separate
`UserProfile` rows and per-subdomain sessions. The recommendation remains **user groups as the
tenancy concept** (5.2), and phase two implements it that way via `JITSI_TENANT_BY_GROUP`.

### 3.6 The pattern phase two followed

`zerver/views/video_calls.py` contains four server-brokered providers. The pattern:
`rest_path("calls/<provider>/create", ...)`, a `@typed_endpoint` view receiving the
authenticated `UserProfile`, secrets from settings, and `json_success(request, {"url": ...})`.
BigBlueButton signs tamper-sensitive data with Django's `Signer` — phase two reuses that trick
for the room epoch.

**None of the four check membership.** Any realm member can mint a call for any room name.
That is harmless while the rooms are unauthenticated and stops being harmless the moment
Prosody trusts your signature. Adding that check is the substance of phase two.

### 3.7 What Zulip's Jitsi integration did before phase two

A client-side `Math.random()` fifteen-digit integer appended to the configured base URL and
inserted as a plain markdown link. No server round trip, no token, no membership check, no
relationship between room and channel. PR #8071 proposed merely prefixing the ID and was
closed with "I don't see the need for it" — the design was intentional, not an oversight.

### 3.8 Prior art and upstream appetite

Issue #28657, "User authentication in Jitsi Meet using JWTs", is open with maintainer
attention and no pull request. Phase two is written against it. Ringing is tracked under
#28505 with #16838, #18979 and #7330 feeding in; nothing has shipped as of Zulip 12.0
(27 April 2026).

## 4. Identity: Authentik and Zulip

### 4.1 There is no code to write

Enable the backend in `/etc/zulip/settings.py`, put the secret in
`/etc/zulip/zulip-secrets.conf`, restart. Revision 2's entire hand-rolled SSO plugin and its
release-blocking security checklist are Zulip's problem rather than yours.

The gating question is settled in the source, not the marketing:

```python
def all_default_backend_names() -> list[str]:
    if not settings.BILLING_ENABLED or settings.DEVELOPMENT:
        # If billing isn't enabled, it's a self-hosted server
        # and has access to all authentication backends.
        return list(AUTH_BACKEND_NAME_MAP.keys())
```

`BILLING_ENABLED` is true on zulipchat.com and nowhere else. There is no license key and no
runtime check on a self-hosted install.

### 4.2 Choose SAML, not OIDC, and the reason is group sync

Group synchronisation via `SOCIAL_AUTH_SYNC_ATTRS_DICT` was added for SAML in Zulip 11.0 and
for OIDC in 13.0. Current stable is 12.x, so OIDC group sync is on `main` and not in a release
you should run. Authentik's documented Zulip integration is SAML-only, which settles it.

The IdP emits group names in a `zulip_groups` attribute; the mapping creates Zulip user
groups, which are what 5.2 resolves tenants from.

### 4.3 The limits of group sync

Sync happens at login only, so removal lags by up to a session lifetime. Bound it by choosing
a session expiry you can defend, run periodic reconciliation against Authentik's API, or
revoke sessions at offboarding. Only direct membership syncs.

SCIM is documented as beta and Zulip's own documentation contradicts itself on whether
self-hosted SCIM syncs groups. Verify before depending on it.

### 4.4 One thing to lock down

`JWT_AUTH_KEYS` plus `POST /api/v1/jwt/fetch_api_key` lets a holder of a shared secret obtain
any user's API key. Keep it empty. The conferencing service authenticates as a bot with its
own key, which is the correct blast radius.

## 5. Target architecture

### 5.1 Components

**Authentik** authenticates humans, enforces MFA, and owns the groups that become Zulip user
groups. It signs nothing Jitsi sees.

**Zulip** owns channel subscription and user group membership — the actual authorization
question — running the standard open-source build plus the phase two patch.

**The calls patch** is `POST /calls/jitsi/create`: subscription check, tenant resolution, room
derivation, token minting. The only component holding the Jitsi signing key. Two commits,
written to be upstreamable.

**The conferencing service** (phase three) runs as a Zulip bot outside the server. It owns
call state, private-call signalling and the occupancy pipeline. It holds a Zulip bot API key
and the `event_sync` bearer secret, and never touches the Jitsi signing key.

**Prosody** verifies tokens, enforces room and tenant, applies affiliations, emits occupancy
events. It trusts the calls patch's signature and nothing else.

**Traefik** terminates TLS and routes: tenant path routing, keeping the Prosody-to-service
event path off the public internet, and serving the ASAP key directory if you use RS256.

The split between patch and service is deliberate and has survived contact with the
implementation. Token minting must be in-process: it needs the authenticated identity Zulip's
REST dispatch provides, and an external minting service would be able to mint for anyone.
Everything else — state machines, timers, occupancy, message updates — belongs outside, where
it deploys without restarting Zulip and cannot break a chat server upgrade. The phase two
patch is 856 lines including tests and documentation; every line of it is a line you rebase.

### 5.2 Naming, tenancy, and derivation — as built

Tenant is resolved from Zulip **user group** membership through `JITSI_TENANT_BY_GROUP`,
falling back to `JITSI_DEFAULT_TENANT` and then the realm subdomain. Groups are matched in
sorted order so a user in two mapped groups always gets the same tenant; otherwise their room
name would change between calls.

```
room = "c-" + HMAC-SHA256(JITSI_ROOM_KEY, scope || epoch)[:16]
```

`scope` is `realm:<id>|channel:<id>` or `realm:<id>|dm:<sorted user ids>`. Sorting is what
makes both participants in a direct message derive the same room.

The epoch is signed with Django's `Signer` and **bound to its scope**, so it round-trips
through an untrusted client without becoming a way to force a room in someone else's
conversation. Revision 3 left this open between a signed parameter and a small table; the
signed parameter won, because it keeps the patch stateless and the conferencing service is
the natural owner of the value.

The honest weakness stated in revision 3 stands: the tenant is a convention asserted by the
patch and backed by a Zulip user group, not a first-class platform object. Prosody's
enforcement is identical either way; the correctness of the *mapping* rests on your code. The
mitigation is that the mapping is small, static configuration rather than logic.

### 5.3 Token schema — as minted

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

`exp` is two minutes. `nbf` is backdated five seconds for clock skew. `context.user.id` is the
Zulip user ID as a string, which is what makes `event_sync` payloads attributable in phase
three. `moderator` is a string nested in `context.user`. `context.features` defaults to all
false so that a bug in issuance cannot silently enable recording.

Avatars are omitted in phase one and two. The obvious implementation is wrong: Zulip's avatar
URLs may require a session, and the fetch originates from a cross-origin Jitsi iframe where
the cookie will not be sent. Either serve them from a dedicated endpoint with a short-lived
HMAC signature, or leave the field out — never `null`.

### 5.4 Prosody configuration — corrected

The layout below is what phase one's gate passes against, and differs from revision 3's in
the ways described in 2.4.

```lua
muc_mapper_domain_base = "meet.jitsi"

VirtualHost "meet.jitsi"
    authentication = "token"
    app_id = "zulip"
    app_secret = "<matches JITSI_JWT_APP_SECRET>"
    allow_empty_token = false
    asap_accepted_issuers = { "zulip" }
    asap_accepted_audiences = { "jitsi" }
    modules_enabled = { "presence_identity" }

Component "conference.meet.jitsi" "muc"
    modules_enabled = {
        "token_verification";
        "token_no_wildcard";
        "token_affiliation";   -- ships upstream now; see 2.3
        "muc_domain_mapper";
        "muc_census";
        "frozen_nick";
    }
```

`jitsi_session` must be loadable on the virtual host; `mod_auth_token` depends on it and fails
silently otherwise (2.8).

The event sync component, for phase three:

```lua
Component "esync.meet.jitsi" "event_sync_component"
    muc_component = "conference.meet.jitsi"
    api_prefix = "http://conferencing:8080/api/v1/jitsi"
    api_headers = { ["Authorization"] = "Bearer <shared secret>"; }
    include_user_info = true
```

The prefix uses an internal service name so the traffic never leaves the bridge network, and
the bearer secret is mandatory because the endpoint is publicly routable.

### 5.5 The conferencing service — phase three

An external process authenticating as a Zulip generic bot.

**Inbound from Zulip:** a long-lived `POST /register` plus `GET /events` loop. Handle
heartbeats (roughly one a minute, bounded by `event_queue_longpoll_timeout_seconds`, which the
register response supplies and which you should use as your HTTP timeout). Handle
`BAD_EVENT_QUEUE_ID` by re-registering and running a full reconciliation — queues are
collected after `idle_queue_timeout`, ten minutes by default.

**Inbound from Prosody:** the four `event_sync` sinks, authenticated on a bearer secret, which
do not trust any identity in the body beyond using it as a lookup key against state the
service itself created.

**Outbound to Zulip:** `POST /messages` to create the call message (posted by the bot, which
is what makes submessages possible), `PATCH /messages/{id}` for state changes, and
`POST /json/submessage` with a custom `msg_type` carrying the roster.

**Call state** keyed by call ID with a secondary index by channel. `ringing` moves to `active`
on answer or to `declined`, `missed` or `cancelled`; `active` moves to `ended`. Ring timeout
is a service-side timer of about forty-five seconds.

**Reconciliation** at startup and on a slow ticker: fetch `/room-census`, diff, correct.

**Room creation** is the new requirement 2.9 imposes. The service must not assume a derived
room exists. Establish during phase three whether Jicofo creates it reliably at the moment the
first participant presents a token, and if not, the service needs an explicit creation step
before it advertises a call.

### 5.6 The client problem

Unchanged. Option 1 — the service edits the call message to read "3 people in this call:
Alice, Bob, Carol" — requires no client patch, works on mobile, and depends on nothing
experimental. Option 2 is a new `videocall` widget type with a live roster, costing a web-app
patch on a surface upstream may itself replace. Option 3 is a companion client.

Start with 1. It lets you finish and validate everything underneath it.

## 6. End-to-end flows

**First login.** Authentik authenticates, enforces MFA, and posts a SAML assertion to
`/complete/saml/`. Zulip provisions or resolves the account, reads `zulip_groups`, and
reconciles user group membership. No code you wrote ran.

**Starting a channel meeting.** The client posts to `/calls/jitsi/create` with the channel ID.
`access_stream_by_id` raises unless the user can reach the channel and a `None` subscription
is refused; the tenant is resolved from group membership; the room is derived from the channel
and epoch; a two-minute token is minted with moderator from `can_administer_channel_group`.
The browser opens `https://jitsi.example/engineering/c-6fe01c2e8f27d6f5?jwt=...`. Prosody's
`muc_domain_mapper` rewrites the room, `token_verification` recovers `engineering` from the
mapped name and matches it against `sub`, and admits. `event_sync` posts room-created and
occupant-joined; the service updates the call message and appends an occupancy submessage,
which Zulip fans out to exactly the entitled viewers.

**A private call.** The service writes state as `ringing`, derives a room from the sorted user
ID pair plus a fresh epoch, and posts a direct message rendering as an incoming call. Jitsi is
not involved until somebody answers, which is correct — Jitsi has no concept of a user not yet
in a room.

**A cross-tenant attempt.** Someone opens a pasted engineering room URL. The endpoint will not
mint them a token, because they are not subscribed. If they present a design-tenant token,
`token_verification` refuses it because the tenant recovered from the mapped room name does not
match `sub`. If they present no token, `allow_empty_token = false` refuses them. Three
independent failures, only one of which depends on your code. **All three measured in phase
one.**

## 7. Threat model and residual risk

The token is a bearer capability with no revocation. Short expiry limits joining and does not
evict an established session. The Jicofo reservation callback is the mechanism if you need
eviction.

A wildcard room claim is catastrophic and silent. `token_no_wildcard` turns it into a loud
failure — verified working.

The `event_sync` endpoint is publicly routable. Bearer secret, network isolation, and never
trusting identity from the body.

Room name entropy matters because room names end up in pasted links and screenshots. HMAC
derivation plus a rotatable epoch is the mitigation, and it is a strict improvement on the
`Math.random()` naming Zulip ships.

Moderator escalation: test both the everyone-is-moderator and nobody-can-join failure modes
against your pinned module version.

Tenancy is asserted by your code, not read off a platform object (5.2). Treat the
group-to-tenant mapping as security configuration and log every token minted with user,
channel, tenant and room.

Membership refresh lags by a session lifetime (4.3).

The submessage channel is marked experimental. Pin your Zulip version and keep 5.6 option 1
as the fallback.

### 7.1 Tests that fail open — the lesson from phase one

The phase one gate reported four of seven cases as PASS on its first live run. All four were
false. The probe was speaking SASL PLAIN; Prosody answered `invalid-mechanism` before it ever
examined a token; four cases happened to expect *a* rejection and got one.

It then happened a second time, differently. With rooms that did not exist,
`token_util:verify_room` returned `room-does-not-exist` before reading the `room` claim — so
the cross-room case passed without the control under test ever executing.

Both failures share a shape: **the test asserted that something was refused, and something
was, for a reason unrelated to the control.** This is the characteristic way a security test
lies, and it lies in the dangerous direction. Three mitigations are now built into the
harness and should survive any modification of it:

1. A transport-level failure is a distinct outcome and never satisfies a negative assertion.
2. The happy path is in the matrix. If it does not pass, no other result means anything.
3. Preconditions the control depends on — here, room existence — are set up explicitly rather
   than assumed, because a missing precondition produces a passing test.

Carry this into phase three. An occupancy pipeline that silently reports nobody in a call
looks identical to a quiet channel.

### 7.2 Operational dependencies

Jitsi is hard-dependent on Zulip: if the calls endpoint is down, nobody can get a token and
nobody can join. Document a break-glass procedure such as temporarily flipping
`allow_empty_token`.

Zulip login depends on Authentik. Keep one local administrator account with password
authentication, stored somewhere reachable when Authentik is down, and test it periodically.

Room creation depends on Jicofo (2.9). This is new in revision 4 and belongs on the
operational list rather than the security one, but it is on the critical path for every
rotated room.

### 7.3 The mobile ringing problem

Unchanged and still the hardest constraint. Self-hosted push relays through Zulip's bouncer,
capped at ten users on the free plan unless the organization qualifies for the free community
plan. The payload schema is welded to real message rows and a fixed trigger enum; the APNs
sound field is hardcoded. A real CallKit experience requires patching `push_notifications.py`,
patching the Flutter app, and still relaying through a bouncer operated under Zulip's terms.

Desktop and web ringing are fine. On mobile, plan for "you get a notification that a call
started", not "your phone rings".

## 8. Migration status

**Phase zero — identity. Not started.** Authentik SAML against a staging Zulip, with
`SOCIAL_AUTH_SYNC_ATTRS_DICT` verified in both directions: groups produce memberships on
login, and removal takes them away on next login. Hours of configuration, not weeks of code.
Keep a local admin account with password login throughout.

**Phase one — prove Jitsi. Complete.** Seven-case gate passing against real Prosody with real
Jitsi modules. Two things remain unverified because they need infrastructure with container
registry access: nginx tenant routing (`ENABLE_SUBDOMAINS`, the `/TENANT/http-bind` path) and
Jicofo's role in room creation. Run `make up && make check` with `JITSI_TENANT_IN_PATH=1` to
close both.

**Phase two — the calls patch. Written, partially verified.** Two commits against Zulip main.
The token library and the room derivation are verified, and tokens minted by the patch have
been admitted and refused correctly by real Prosody. `./tools/test-backend
zerver.tests.test_jitsi_jwt` and `./tools/lint` have not been run and are the gate before this
phase counts as done.

**Phase three — conferencing service and occupancy. Next.** Bot, event-queue loop, four
`event_sync` sinks, occupancy via message edits first. Retire the census website only after a
week of running it in parallel and diffing.

**Phase four — private calls.** State machine, ring timeout, decline and missed. Desktop
first, mobile scoped as notification-only per 7.3.

**Phase five — the client.** Widget type, live roster, incoming-call modal. Last because it is
the largest frontend piece and the most exposed to upstream churn.

**Phase six — hardening.** Lobby, moderator mapping, feature flags, room rotation under load,
and the reservation callback if you want it.

## 9. Open decisions

**Resolved since revision 3.** The epoch is a signed parameter, not a table — it keeps the
patch stateless and the service is the natural owner. Tenancy is by user group, implemented.
HS256 is the staging default with RS256 wired and tested; the choice for production is still
open but no longer blocking, since the key tooling exists either way.

**Upstreaming the calls patch.** It fills an obvious gap in a four-instance pattern and
answers an open issue with maintainer interest. Against: Zulip's review pipeline is six stages,
and their appetite for a tenancy model may be lower than for plain JWT support. The patch is
written so that `JITSI_TENANT_BY_GROUP` can be dropped and it still stands as "authenticate
Jitsi calls". Offer it after you have run it.

**Subscription versus content access.** A user who can read a public channel without being
subscribed is currently refused a call token. Deliberate, but a product decision worth
confirming against how your teams actually work.

**Moderator in a direct message call.** Nobody gets moderator today, so Jitsi's
first-joiner-becomes-owner behaviour applies unless configured otherwise. Decide it rather
than inheriting it.

**How much client work to accept.** Option 1 in 5.6 gets a working system with no frontend
patching and a mediocre UI. If Zulip ships native calling under #28505, option 1 plus patience
may be the better engineering decision.

**Whether mobile matters.** Per 7.3 this is the one requirement with no good answer. Decide
before phase four, not during it.

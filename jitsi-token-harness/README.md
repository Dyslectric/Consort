# Phase one: prove Jitsi

This is the staging harness for phase one of the architecture document, revision 3.

Phase one has exactly one deliverable and one exit criterion.

> The deliverable is a token you mint by hand that gets you into a room, with domain
> verification on and tenant routing working. **Do not proceed until you have proven that a
> token for tenant A cannot open a room in tenant B**, because that is the assumption
> everything else rests on.

Everything here exists to make that criterion something you can run rather than something
you believe. It has no dependency on Zulip and can be done in parallel with phase zero.

## Quick start

```bash
make install          # Python deps
make secrets          # generates .env with random staging secrets
make modules          # fetches the jitsi-contrib Prosody modules
make up               # starts the stack
make check            # runs the isolation matrix — this is the gate
```

`make check` prints a table and exits non-zero if any case misbehaves. This is a real run
against Prosody with Jitsi's token modules:

```
case                 want       outcome          result
------------------------------------------------------------------------
happy path           join       joined           PASS
cross-tenant         refuse     room_rejected    PASS
cross-room           refuse     room_rejected    PASS
expired              refuse     auth_rejected    PASS
empty token          refuse     auth_rejected    PASS
forged signature     refuse     auth_rejected    PASS
wildcard room        refuse     room_rejected    PASS

All 7 cases behaved correctly. Phase one gate passed.
```

The gate asserts **admission** — was this token let in or not — and merely *reports* which
module did the rejecting. The enforcement point varies with deployment shape, and asserting
it produces a gate that fails on correct deployments, which teaches people to ignore it.

For a human sanity check, `make url TENANT=engineering ROOM=c-demo` prints a joinable link.

## What the matrix actually proves

The seven cases are not arbitrary. Each one corresponds to a control that the design leans
on, and each would fail silently if the control were missing.

| Case | Control | What breaks without it |
| --- | --- | --- |
| happy path | — | Nothing works at all |
| cross-tenant | `sub` vs. virtual host, in `mod_auth_token` | Team isolation is decorative |
| cross-room | `room` claim, in `token_verification` | Any token opens any room |
| expired | `exp` | A leaked link is permanent |
| empty token | `allow_empty_token = false` | Anyone joins without a token |
| forged signature | signature verification | Anyone mints their own access |
| wildcard room | `token_no_wildcard` | One bug yields a skeleton key |

The harness reports `auth_rejected` and `room_rejected` distinctly so you can see which
module caught each case, and prints a note when a rejection moves. In Jitsi's standard
layout most of the isolation cases land on `token_verification` at the MUC component rather
than on `mod_auth_token` at SASL — including cross-tenant, because the tenant is recovered
from the mapped room name rather than from the virtual host.

A transport failure is reported as `transport_error` and never as a rejection. A dead
endpoint must not be mistakable for a security control working.

### Rooms must already exist

`token_util:verify_room` returns `room-does-not-exist` **before** it looks at any claim in
the token. In a real deployment Jicofo creates the room — it is in `token_verification`'s
allowlist — and participants join an existing one.

Two consequences for this harness. The happy path fails against an empty deployment for a
reason that has nothing to do with tokens. And, worse, **the cross-room case passes without
the room claim ever being read** if its target room does not exist. Make sure both
`$ROOM` and `$ROOM-other` exist before reading anything into a green run — opening each
once in a browser is enough on a full stack. `reference/mod_phase1_rooms.lua` pre-creates
them when there is no Jicofo.

## How it works

`make check` speaks BOSH to Prosody directly rather than driving a browser. It performs the
real sequence — session creation, SASL, stream restart, resource bind, then MUC join
presence — and reports where Prosody said no. That is faster and far less brittle than
asserting on Jitsi's DOM, and it reaches both enforcement points.

**How the token actually travels** is not obvious, and is worth knowing before you debug
anything here. It is *not* a SASL password. Jitsi's `mod_jitsi_session` reads it from the
BOSH URL query string (`?token=`, alongside `room=` and `prefix=`) or from an
`Authorization: Bearer` header, and stores it on the session. `mod_auth_token` then
registers a SASL **ANONYMOUS** mechanism whose callback validates that stored token — its
`provider.test_password` explicitly returns "Password based auth not supported". Because
`mod_jitsi_session` hooks the `bosh-session` event, the token must be present on the request
that *creates* the session, not merely on a later one.

A PLAIN attempt therefore fails with `invalid-mechanism`, which looks exactly like a
rejected token if you are not reading carefully. The harness classifies `invalid-mechanism`
as `transport_error` rather than `auth_rejected` for precisely this reason — see below.

Host names are read from the deployment's own `config.js` rather than guessed, because
tenant routing changes them in ways that are easy to get wrong, and getting them wrong
produces a failure that looks exactly like the token being rejected. Set
`JITSI_XMPP_DOMAIN` to skip discovery when there is no web container in front, and
`JITSI_TENANT_IN_PATH=0` when BOSH is at `/http-bind` rather than `/TENANT/http-bind`.

### Why false passes are the thing to fear

An early live run of this harness reported four of seven cases as PASS. Every one was
false: the probe was using SASL PLAIN, Prosody was answering `invalid-mechanism` before it
ever looked at a token, and four cases happened to expect a rejection. The controls were
not being tested at all.

That is the characteristic failure of a security test — it fails *open* and looks green.
Two mitigations are built in and should be kept if you modify this:

1. `invalid-mechanism` is `transport_error`, never `auth_rejected`.
2. The happy path is in the matrix. If it does not pass, no other result means anything,
   and the README says so where you will read it.

## Files

```
docker-compose.yml           Staging stack: web, prosody, jicofo, jvb, keyserver
.env.example                 All configuration, commented
scripts/fetch-contrib-modules.sh   Pinned jitsi-contrib module fetch
jitsi_phase1/tokens.py       Token minting; lift this into the calls patch later
jitsi_phase1/keys.py         RS256 keypair generation with correct ASAP filenames
jitsi_phase1/bosh.py         The BOSH probe
jitsi_phase1/cli.py          keygen / mint / probe / check
tests/                       Offline suite plus the live gate as pytest
reference/prosody-standalone.cfg.lua   A Prosody config the gate passes against
reference/mod_phase1_rooms.lua         Pre-creates rooms where there is no Jicofo
```

`tokens.py` is written to be lifted wholesale into the Zulip `calls` patch in phase two. It
has no dependency on anything in this harness, and the refusals it encodes — non-string user
IDs, wildcard rooms, uppercase tenants — are exactly the ones that patch will need.

## Configuration notes

**Pin the image tag.** `.env.example` ships `JITSI_IMAGE_TAG=stable`, which is a moving
target. Pin it to a specific `stable-NNNN` before you trust any result, because
`token_affiliation`'s behaviour has changed between versions.

**Pin the contrib modules.** `make modules` fetches from `main` by default and writes a
content hash per module to `prosody-plugins-custom/.pinned`. Diff those hashes on every
refetch, and read the Lua. (A commit SHA would be the obvious pin, but `api.github.com` is
not reachable from every environment, and a content hash is the stronger claim anyway: it
pins what you are running, not what a ref pointed at when you looked.)

**`token_affiliation` now ships with Jitsi.** The architecture document describes it as a
`jitsi-contrib` module. That changed: jitsi-contrib renamed theirs to
`token_affiliation_legacy`, with the note "Jitsi officially provides a `token_affiliation`
module now." So enable it in `XMPP_MUC_MODULES` and fetch nothing. If you ever need the old
behaviour, the legacy module is `token_affiliation_legacy/mod_token_affiliation_legacy.lua`
— the *filename* changed too, not just the directory — and it additionally wants
`wait_for_host_disable_auto_owners = true` on the MUC component. Either way, read it before
trusting it: it reads `context.user.moderator` from inside the user context rather than a
top-level claim, and that detail has moved more than once.

**HS256 by default, RS256 available.** The harness defaults to a shared secret because it is
one fewer moving part and phase one is about isolation, not key management. The RS256 path
is wired up and tested — `python -m jitsi_phase1 keygen --kid ...` generates the keypair and
names the public half `sha256(kid).pem`, which is what Prosody actually requests.
`asap_key_server` is a static file server, not a JWKS endpoint; pointing it at Authentik's
JWKS URL will not work. The choice between the two is still open (rev 3 section 9) and
nothing here forces it.

**Verify domain verification is genuinely on.** The Docker distribution has a history of
friction here, including a long-lived issue where JWT auth only worked with domain
verification switched off. The `cross-tenant` case is what catches that, which is precisely
why it is in the matrix rather than in a comment.

## Troubleshooting

`make logs` tails Prosody, which is where token rejections are explained. If the whole
matrix returns `transport_error`, the stack is not up or `JITSI_BASE_URL` is wrong. If the
happy path fails but everything else passes, that is not a pass — the isolation cases may be
failing for the wrong reason, so fix the happy path first.

`invalid-mechanism` on every case means Prosody offered no usable SASL mechanism, which
almost always means the token never reached it. Check that `mod_jitsi_session` is loaded on
the virtual host (`mod_auth_token` declares `module:depends("jitsi_session")`), that
`authentication = "token"`, and that `app_id` and `app_secret` are set on the host rather
than only globally. Prosody logs `No available SASL mechanisms, verify that the configured
authentication module 'token' is loaded and configured correctly` in this situation.

If `cross-tenant` reports `joined`, stop. Domain verification is off or tenant routing is
not reaching Prosody, and the entire entitlement model in the design document is
unenforced.

## Verification status

- **48 tests pass.** 41 offline, plus the 7-case live gate.
- **The gate has been run green against real Prosody running Jitsi's real token modules** —
  `mod_auth_token`, `mod_jitsi_session`, `token_verification`, `token_no_wildcard`,
  `muc_domain_mapper`, `token/util.lib.lua` and `luajwtjitsi`, all from the jitsi-meet
  repository. Every control in the table above was observed doing its job: a cross-tenant
  token refused, a cross-room token refused, a wildcard refused by `token_no_wildcard`, an
  expired and a forged token refused at SASL, and a correctly scoped token admitted.
- **Not verified: the full container stack.** The environment this was validated in blocks
  container registries, so `jitsi/web`, `jicofo` and `jvb` were never started. Prosody was
  assembled from the distribution package plus the Jitsi modules — see
  `reference/prosody-standalone.cfg.lua` for the exact configuration that passes. What this
  leaves unproven is nginx tenant *routing* (`ENABLE_SUBDOMAINS`, the `/TENANT/http-bind`
  path) and Jicofo's role in room creation. Run `make up && make check` on the real stack
  with `JITSI_TENANT_IN_PATH=1` to close that gap.

## Where this goes next

Phase two lifts `tokens.py` into a Zulip patch adding `POST /calls/jitsi/create`, which
performs the channel subscription check that none of Zulip's four existing call endpoints
perform, and returns a token minted exactly like the ones this harness mints by hand. This
matrix stays useful after that as a regression test against the real deployment.

# zulip-meet-conferencing

The external service that keeps Zulip's call UI in sync with live Jitsi calls. It is the brain
behind [conversation-scoped video calls in Zulip](https://github.com/Dyslectric/zulip-meet-integration/tree/jitsi-jwt):
it watches Jitsi — through
Prosody's `event_sync` — for who is in which room, owns each call's state, renders the roster
message, and posts and edits that message into Zulip through a secret-authed internal hook.

It holds **no Jitsi signing key**. Minting the room-scoped token — the only place that decides who
is allowed into a call — stays in the Zulip server patch. This service observes and reflects; it
never authorizes.

```
  Prosody ──event_sync (occupancy)──▶  zulip-meet-conferencing  ──post / edit──▶  Zulip
 (muc.…)  ◀──── /room-census ────────  (state · render · timing)   (core hook)
```

## Quick start

```bash
pip install -r requirements.txt
python -m pytest -q               # 112 offline tests, no socket / sleep / race

# run it (needs a Zulip bot for the event loop, the shared event_sync secret,
# and the core-hook URL it posts through):
ZULIP_SITE=https://zulip.example \
ZULIP_EMAIL=conference-bot@zulip.example \
ZULIP_API_KEY=... \
EVENT_SYNC_SECRET=shared-with-prosody \
ZULIP_HOOK_URL=http://zulip/api/internal/jitsi \
ZULIP_HOOK_HOST=zulip.example \
python -m conferencing
```

A container image is provided (`Dockerfile`); it runs `python -m conferencing` and serves the
sinks on `:8080`.

## Configuration

Read from the environment; the process refuses to start if a required value is missing and reports
*all* of them at once, rather than failing on the first blank.

| Variable | Required | Purpose |
|---|---|---|
| `ZULIP_SITE`, `ZULIP_EMAIL`, `ZULIP_API_KEY` | yes | Bot credentials for the event-queue loop (drives reconcile-on-reconnect; the seam for the future private-call flow). |
| `EVENT_SYNC_SECRET` | yes | Bearer secret shared with Prosody's `event_sync`; guards the occupancy sinks. |
| `ZULIP_HOOK_URL` | no* | Base URL of Zulip's internal message hook. Absent → posting disabled (occupancy still tracked, widget still answers, no messages written). |
| `ZULIP_HOOK_HOST` | no | `Host` header for the hook hop when reaching Zulip by an internal name Django's `ALLOWED_HOSTS` would otherwise reject. |
| `ZULIP_HOOK_SECRET` | no | Bearer for the hook; defaults to `EVENT_SYNC_SECRET`. |
| `ZULIP_HOOK_VERIFY_TLS` | no | Verify Zulip's TLS cert on the hook hop (default true; set `false` for an internal self-signed hop). |
| `CENSUS_URL` | no | Prosody `mod_muc_census` URL. Absent → reconciliation disabled (the push stream is trusted alone; a lost delivery then drifts silently). |
| `JITSI_ROOM_KEY` | no | HMAC key for room derivation, shared byte-for-byte with the Zulip patch. Only needed by flows that derive rooms themselves. |
| `BIND_HOST` / `BIND_PORT` | no | Sink bind address (default `0.0.0.0:8080`). |

\* Not required to *start*, but nothing gets posted to Zulip without it.

## What is in here

```
conferencing/rooms.py         Room derivation shared with the patch, and mapped-name parsing
conferencing/state.py         Call state machine and occupancy, with drift tracking
conferencing/sinks.py         The event_sync HTTP endpoints Prosody posts occupancy to
conferencing/hook_client.py   The wire to Zulip's internal message hook (post / edit)
conferencing/zulip_client.py  REST calls and the event-queue loop
conferencing/render.py        Call state → message text
conferencing/census.py        Reconciliation against mod_muc_census
conferencing/config.py        Environment → Config, refusing to start on a missing secret
conferencing/service.py       The object that owns one of each and says what fires when
conferencing/__main__.py      The process: sinks + event loop + ticker, three threads, clean shutdown
deploy/prosody/               event_sync config + a custom Prosody image to load it
```

## The four things worth reading the code for

**`event_sync` reports mapped room names.** A room the patch called `c-7f3a` in tenant
`engineering` arrives from Prosody as `[engineering]c-7f3a`, because `muc_domain_mapper` rewrote it.
`rooms.parse_mapped_room` undoes that. Getting it wrong raises nothing at all — it just attributes
every occupancy event to no conversation, and the roster stays permanently empty while looking
exactly like a quiet channel.

**Identity in a sink payload is a lookup key, never an assertion.** These endpoints are publicly
routable. The body carries a name, an email and an id, and anyone who can reach the endpoint
controls all three. The Zulip user id is read to associate an occupant with an account and is used
for nothing else; no call is created, transitioned, or authorized on the strength of it. (The 2025
Mattermost Jira plugin CVE is this exact bug; the tests assert the negative.)

**A roster we know is wrong renders differently from one we trust.** The census reports counts, not
identities, so reconciliation cannot rebuild a roster — it can only establish that ours is wrong.
When it does, `render.py` prints "3 in the call *(roster resyncing)*" rather than a list of names it
has reason to doubt. Same for a room it has heard nothing about in fifteen minutes.

**Failing to reach the census is not the census saying "empty".** Neither is a census response whose
rooms cannot be parsed. Both leave state untouched, because treating either as "there are no rooms"
would silently empty every roster in the deployment — a failure that looks exactly like everyone
hanging up at once.

## The event loop

Zulip's event system is a long poll: register a queue, then ask repeatedly for events newer than the
last one processed. Three things have to be handled or the service looks fine and is deaf:

- **Heartbeats** arrive about once a minute, carry no information, and must not advance application
  state. They are counted, not dispatched.
- **The poll timeout** comes from `POST /register`. The HTTP timeout is set above it, so a normal
  empty poll returns an empty result rather than a client-side timeout indistinguishable from the
  server going away.
- **`BAD_EVENT_QUEUE_ID`** means the queue was garbage collected. Recovery is to register a new queue
  **and reconcile**, because everything during the gap was missed. Re-registering *without*
  reconciling is the quiet failure — the service reconnects, reports no error, and is simply wrong.
  `on_reconnect` fires on every registration, including the first, for exactly this reason.

## Status

Deployed and running. Channel and direct-message calls post a roster message via the core hook,
occupancy events edit it, and room teardown ends it. Not built yet: the **private-call (ringing)
flow** — the state machine handles `ringing → active | declined | missed | cancelled` and the
ticker sweeps ring timeouts, but nothing yet drives those transitions from Zulip events;
`Service.handle_event` is the seam it attaches to.

The Prosody side (`event_sync` posting to the sinks, `muc_census` for the census, and
`token_affiliation_legacy` for moderator enforcement) and the full deployment story live in the
[top-level README](../README.md) and `../docs/`.

## License

[Apache-2.0](LICENSE).

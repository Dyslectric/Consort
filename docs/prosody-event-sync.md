# Phase 3 — wiring Prosody to the conferencing service

Status: **PROVEN END-TO-END on the live stack (2026-08-02).** event_sync POSTs
reach the service's sinks and return `200`; the whole path browser → token →
Jicofo → Prosody → event_sync → sink → Store works. This document is the runbook
plus a verification ledger, because the single most likely outcome here is a
deploy that looks green and delivers nothing — and this project has a rule about
that.

> ### As-deployed corrections (read these first — they cost a morning)
>
> 1. **The `/defaults/conf.d` delivery below is WRONG for this image.**
>    docker-jitsi-meet's config generator writes a *fixed* set of files into the
>    runtime `conf.d` and does not glob custom ones, so a config dropped in
>    `/defaults/conf.d` never loads. **What works:** a tiny custom Prosody image
>    that appends `Include "/prosody-plugins-custom/*.cfg.lua"` to
>    `/defaults/prosody.cfg.lua` (as `USER root`, then `USER 1000` back or Prosody
>    refuses to run), with the rendered `event_sync.cfg.lua` placed in the
>    persistent `prosody-plugins-custom` dir. See
>    `consort-conferencing/deploy/prosody/Dockerfile`.
> 2. **Prosody's Lua HTTP resolver cannot resolve a bare Docker service name.**
>    A shell `wget` to `http://conferencing:8080` works, but event_sync logs
>    `API Response code 0. Will retry`. Use the container's **IP** in `api_prefix`
>    and pin it (compose `ipam` subnet + `ipv4_address`).
> 3. **A `401` from the sink means transport works but the bearer secret differs.**
>    The `Bearer` in `event_sync.cfg.lua` must byte-equal the service's
>    `EVENT_SYNC_SECRET`. `code 0` = unreachable; `401` = reachable-but-wrong-secret;
>    `200` = done.
> 4. `mod_muc_census` ships in the image already (no vendoring). `muc_census`
>    serves `{"room_census":[…]}` at `/room-census`.

Design of record: `architecture.md` §2.5, §5.4, §5.5.
Where this document and rev4 disagree, **this one is right about the live
deployment** and rev4 is right about standalone Prosody — see "The trap" below.

---

## The data flow

```
  companion Jitsi (docker-jitsi-meet, meet.zulip.davig01.net)
  ┌───────────────────────────────────────────────┐
  │ Prosody                                        │
  │   muc.meet.jitsi ── muc_census ──► GET /room-census
  │        │                                       │        (reconciliation)
  │        └── esync.meet.jitsi (event_sync) ──┐   │
  └────────────────────────────────────────────┼──┘
                                               │ POST {prefix}/events/...
                                               ▼   Authorization: Bearer <secret>
                          conferencing service (python -m conferencing)
                          ┌──────────────────────────────────────────┐
                          │ four sinks ─► Store ─► render ─► Zulip    │
                          │ census reconcile (slow ticker + reconnect)│
                          └──────────────────────────────────────────┘
```

`event_sync` is the fast path (push); `muc_census` is the slow safety net the
service diffs against, because a push-only design drifts whenever a delivery is
lost. The service **holds no Jitsi signing key** — it observes and edits
messages; it never admits anyone.

---

## The trap: `muc.meet.jitsi`, not `conference.meet.jitsi`

docker-jitsi-meet's internal MUC component is **`muc.meet.jitsi`**. Standalone
Prosody (and rev4 §5.4) uses `conference.meet.jitsi`. This was confirmed during
the phase-1 harness work: the mapper is `muc_mapper_domain_prefix="muc"` over
base `meet.jitsi`, giving component `muc.meet.jitsi`, and mapped room names of
the form `[tenant]c-<hash>@muc.meet.jitsi`.

Point `event_sync`'s `muc_component`, or `muc_census`, at the wrong name and
**nothing errors**: the component loads against a MUC that has no rooms, no
event ever fires, and every roster stays empty — indistinguishable from a
deployment where nobody is on a call. This is the exact "empty result and broken
pipeline look identical" failure the project keeps hitting. Confirm the real
name on the running server before trusting anything downstream:

Read it straight out of the generated config — do not use `prosodyctl --config`
here: the jitsi prosody wrapper rewrites the path (strips the leading slash and
prepends `/etc/prosody/`), so it fails with "unable to find the configuration
file /etc/prosody//config/prosody.cfg.lua". Grep the file instead:

```bash
docker compose exec prosody sh -c 'find / -name "*.cfg.lua" 2>/dev/null; echo "--- components & mapper ---"; grep -rniE "Component \"|muc_mapper_domain_(base|prefix)" /config /etc/prosody 2>/dev/null'
```

**Confirmed on the live companion (2026-08-02).** The config is at
`/run/prosody/config/`; the relevant lines in `conf.d/jitsi-meet.cfg.lua`:

```
muc_mapper_domain_base   = "meet.jitsi"
muc_mapper_domain_prefix = "muc"
Component "internal-muc.meet.jitsi" "muc"   -- bridge/JVB signalling; NOT user rooms
Component "muc.meet.jitsi" "muc"            -- the public MUC; user calls live here
```

So `muc_component = "muc.meet.jitsi"` (the public one — **not** `internal-muc`,
and **not** rev4's `conference.meet.jitsi`), and rooms arrive mapped as
`[tenant]c-…@muc.meet.jitsi`. `event_sync.cfg.lua` already carries this value.

After deploy, confirm `esync.meet.jitsi` actually loaded (`docker compose logs
prosody | grep -iE "esync|event_sync"`) — a component that failed to load says
so there without stopping Prosody.

---

## What to install

Two Prosody modules, from **different** sources (verified 2026-08-02):

| Module | File | Source | Vendor it? |
| --- | --- | --- | --- |
| `event_sync` (component) | `mod_event_sync_component.lua` | [`jitsi-contrib/prosody-plugins`](https://github.com/jitsi-contrib/prosody-plugins/blob/main/event_sync/mod_event_sync_component.lua), `event_sync/` | **Yes** — not in the image |
| `muc_census` | `mod_muc_census.lua` | [`jitsi/jitsi-meet`](https://github.com/jitsi/jitsi-meet/blob/master/resources/prosody-plugins/mod_muc_census.lua), `resources/prosody-plugins/` | **Probably not** — ships with jitsi-meet |

`muc_census` is part of jitsi-meet itself, so docker-jitsi-meet's prosody image
very likely already carries it. **Check before downloading:**

```bash
docker compose exec prosody find / -name 'mod_muc_census.lua' 2>/dev/null
```

If that prints a path, you only need `XMPP_MUC_MODULES=muc_census` (Phase B) —
no vendoring. If it prints nothing, drop the jitsi-meet copy into
`/prosody-plugins-custom` like `event_sync`.

Fetch `event_sync` (pin a commit rather than `main`, and record it):

```bash
curl -fsSL https://raw.githubusercontent.com/jitsi-contrib/prosody-plugins/<commit>/event_sync/mod_event_sync_component.lua \
  -o ./prosody-plugins-custom/mod_event_sync_component.lua
```

> **Why pin:** a schema change in either module is exactly the silent break
> `census.fetch` and the sink parser are hardened against — and the census key
> already bit once (see below). A pinned commit is how you avoid the surprise.
> Commits pinned: event_sync `__HASH__`, muc_census (image default / `__HASH__`).

### The payload shapes, confirmed against the module source

Reading the real modules corrected two assumptions — worth stating so the next
person does not re-guess:

- **Census key is `room_census`, not `rooms`.** `mod_muc_census` returns
  `{"room_census": [{"room_name", "participants", "created_time", "leaked"}]}` at
  `/room-census`. `census.fetch` now reads `room_census` first; reading only
  `rooms` (as it originally did) returned `{}` and made reconciliation destroy
  every roster. Fixed and covered by a test using the real shape.
- **event_sync's occupant JID field is `occupant_jif`** (a typo baked into the
  module), and room events carry `room_name`. `sinks.py` already tolerates both
  `occupant_jif`/`occupant_jid` and reads `room_name`, so no change was needed —
  but that is why the tolerance exists, not defensive paranoia.
- **event_sync retry option is `api_retry_delay`** (one number), not a list.

---

## Delivery into docker-jitsi-meet

Three separate mechanisms, because the two plugins attach differently.

### 1. The plugin code (both modules)

Mount a host directory into the `prosody` (a.k.a. `xmpp`) service at
`/prosody-plugins-custom`; docker-jitsi-meet adds it to Prosody's plugin paths
automatically. Put `mod_event_sync_component.lua` in it (and `mod_muc_census.lua`
too, only if the `find` check above showed it is not already in the image).

```yaml
# docker-compose override for the prosody/xmpp service
services:
  prosody:
    volumes:
      - ./prosody-plugins-custom:/prosody-plugins-custom:ro
```

### 2. `muc_census` — a module on the MUC component (env var)

`muc_census` is a *module*, so it is enabled by adding it to the MUC component's
module list. docker-jitsi-meet exposes exactly this as an environment variable —
no config file needed:

```env
# .env
XMPP_MUC_MODULES=muc_census
```

(Comma-separate if other custom MUC modules are already listed.)

### 3. `event_sync` — a whole Component (config file, via `/defaults`)

`event_sync_component` is its own Prosody `Component`, which no docker-jitsi-meet
env var can express, so it needs the config block in
[`consort-conferencing/deploy/prosody/event_sync.cfg.lua`](../consort-conferencing/deploy/prosody/event_sync.cfg.lua)
loaded by Prosody's main config.

**Confirmed delivery model (2026-08-02).** On this image the runtime config dir
`/run/prosody/config/` is a **tmpfs, regenerated from `/defaults/` on every
container start**, and `prosody.cfg.lua` ends with `Include "conf.d/*.cfg.lua"`
(line 221). So a file dropped directly into the runtime `conf.d` **vanishes on
the next restart** — the same non-durability trap phase 2 hit editing inside the
Zulip container. The durable path is to mount the config into **`/defaults/conf.d/`**;
the startup `tpl` step copies it into the runtime `conf.d`, where the `Include`
picks it up. (The file has no `{{ }}` template variables, so `tpl` passes it
through unchanged.)

Substitute the secret on the host first (it ships with a `__EVENT_SYNC_SECRET__`
placeholder), then mount the rendered file:

```bash
mkdir -p ./prosody-config
sed "s|__EVENT_SYNC_SECRET__|$EVENT_SYNC_SECRET|" \
    event_sync.cfg.lua > ./prosody-config/event_sync.cfg.lua
```

```yaml
# docker-compose.override.yml, prosody service
    volumes:
      - ./prosody-config/event_sync.cfg.lua:/defaults/conf.d/event_sync.cfg.lua:ro
```

Then `docker compose up -d --force-recreate prosody`.

**Do not trust the restart.** Confirm the component actually loaded:
`docker compose logs prosody | grep -iE "esync|event_sync"` must show
`esync.meet.jitsi` starting. A typo, or a plugin the loader could not find,
fails there without stopping Prosody — green container, dead pipeline.

A ready-to-copy override with all three mounts is in
[`consort-conferencing/deploy/prosody/docker-compose.override.example.yml`](../consort-conferencing/deploy/prosody/docker-compose.override.example.yml).

### Stage it: event_sync first, census second

The service treats the census as optional — reconciliation is cleanly disabled
when `CENSUS_URL` is unset — and `event_sync` is the part that actually delivers
occupancy. `muc_census`'s exact upstream path is also the one thing not yet
confirmed. So bring up `event_sync` alone first (service running with no
`CENSUS_URL`), prove occupancy end to end, and only then add `muc_census` and
set `CENSUS_URL`. Two smaller changes, each independently verifiable, instead of
one change with two ways to look falsely green.

---

## Everything that must line up

One mismatch in this table is one silently-empty roster. The service defaults
were chosen to match rev4, so the out-of-the-box values already agree.

| Prosody (`event_sync.cfg.lua`) | Service (env) | Value |
| --- | --- | --- |
| `api_prefix` host:port | `BIND_PORT` | `8080` |
| `api_prefix` path | `SINK_PREFIX` | `/api/v1/jitsi` |
| `api_headers` Bearer | `EVENT_SYNC_SECRET` | the shared secret |
| `muc_component` | — | `muc.meet.jitsi` (the live name) |
| census route | `CENSUS_URL` | `http://<prosody>:5280/room-census` |

The service reaches Prosody's census over Prosody's HTTP port (`5280` by
default inside the network). The route and JSON are confirmed from the module
source: `GET /room-census` → `{"room_census": [{"room_name", "participants",
"created_time", "leaked"}]}`, which `census.fetch` now parses. One thing still to
verify on the running box: that the HTTP port really is `5280` and reachable
from wherever the service runs. `census.fetch` **raises** (leaving state
untouched) rather than returning empty if it ever parses zero rooms out of a
non-empty response, so a future schema drift degrades to "reconciliation
skipped," never "every roster emptied."

---

## Verification — prove it, do not assume it

The happy path belongs in the check. If it does not fire, no negative result
means anything.

1. **Start the service** where Prosody can reach it, with the matching secret.
   `GET /healthz` returns `{"ok": true, "rooms": 0}`.
2. **Join a real call** from Zulip (the phase-2 call button) into a channel.
3. **Watch the service, not Jitsi.** Within a second or two:
   - the service log shows a `POST .../events/occupant/joined` at 200 (not 401 —
     a 401 means the bearer secrets disagree);
   - `GET /healthz` now reports `{"rooms": 1}` (or more).
   If the join works in the browser but `rooms` stays `0`, event_sync is not
   reaching the service or is attached to the wrong MUC component — go back to
   "The trap."
4. **Leave the call.** An `occupant/left` arrives; when the room empties,
   `room/destroyed` follows and `rooms` returns toward `0`.
5. **Census.** `curl http://<prosody>:5280/room-census` by hand and check the
   shape against the table above. Then let the service's reconcile ticker run
   (default 60 s) with a known call up, and confirm the log reports a clean
   reconciliation rather than corrections — corrections at steady state mean the
   push path is lossy, not that reconciliation is working.

---

## What this buys, and what it does not

**Buys:** real, pushed per-room occupancy in the service's `Store`, reconciled
against Prosody. The service will re-render the *call message* (render.py,
option 1) as people join and leave — the architecture's chosen occupancy surface.

**Does not buy, yet:** the phase-2 frontend occupancy *shell* (the channel-narrow
indicator fed by a mock) does not read any of this. Closing that is the client
push decision recorded in `HANDOFF.md` §4: either the shell reads the
call-message roster, or the service pushes a per-channel occupancy submessage
the client subscribes to. That decision is independent of this Prosody wiring
and is the remaining frontend work for phase 3.

**Failure posture:** if the service is down or unreachable, event_sync retries
under a circuit breaker and then drops events; **calls themselves are
unaffected** because admission is Prosody's job and the token's, not the
service's. The only casualty of an outage is occupancy freshness, which
reconciliation repairs on the next successful census. That is the correct blast
radius for an observability component and is why the service holds no key.

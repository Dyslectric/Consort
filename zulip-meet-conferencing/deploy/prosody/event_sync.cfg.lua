-- Prosody config for the phase-three occupancy pipeline, for the token-auth
-- companion Jitsi (docker-jitsi-meet at meet.zulip.davig01.net).
--
-- Two things live here:
--   * the `event_sync` component, which POSTs room/occupant lifecycle events to
--     the conferencing service's four sinks;
--   * a note on where `muc_census` is enabled (it is a *module* on the MUC
--     component, not a component of its own, so it is added via the MUC modules
--     list rather than here — see deploy/prosody/README.md).
--
-- ────────────────────────────────────────────────────────────────────────────
-- THE ONE THING TO GET RIGHT: the MUC component name.
--
-- docker-jitsi-meet's internal MUC component is `muc.meet.jitsi`. (The
-- architecture doc §5.4 writes `conference.meet.jitsi`; that is standalone
-- Prosody's default and is WRONG for this deployment. The live companion uses
-- `muc.meet.jitsi` — confirmed against the running stack: mapper
-- `muc_mapper_domain_prefix="muc"`, base `meet.jitsi`, component
-- `muc.meet.jitsi`.) Point `muc_component` at the wrong name and event_sync
-- silently attaches to nothing: no error, no events, every roster stays empty
-- while looking exactly like a set of quiet channels. Confirm the real name on
-- the running server before trusting a green-looking deploy:
--     docker compose exec prosody prosodyctl --config /config/prosody.cfg.lua \
--         shell 'for k in prosody.hosts do print(k) end'
-- ────────────────────────────────────────────────────────────────────────────

Component "esync.meet.jitsi" "event_sync_component"
    -- The MUC whose events we want. MUST match the live component name above.
    muc_component = "muc.meet.jitsi"

    -- Where to POST. The four sinks live under this prefix:
    --   {api_prefix}/events/room/created
    --   {api_prefix}/events/room/destroyed
    --   {api_prefix}/events/occupant/joined
    --   {api_prefix}/events/occupant/left
    -- An internal service name keeps this traffic on the bridge network. The
    -- port (8080) and prefix (/api/v1/jitsi) must match the service's BIND_PORT
    -- and SINK_PREFIX — the service defaults to exactly these.
    api_prefix = "http://conferencing:8080/api/v1/jitsi"

    -- MANDATORY. The sinks are publicly routable and reject any request whose
    -- bearer does not match, in constant time. It must equal the service's
    -- EVENT_SYNC_SECRET byte for byte.
    --
    -- Read from the environment so a single .env value feeds BOTH containers
    -- (this Prosody and the conferencing service) — no substitution step, no
    -- secret in source control. Requires `EVENT_SYNC_SECRET: ${EVENT_SYNC_SECRET}`
    -- on the prosody service in docker-compose.yml. `or ""` guards against a nil
    -- concat if the var is unset (which then just fails auth, loudly, as a 401).
    api_headers = {
        ["Authorization"] = "Bearer " .. (os.getenv("EVENT_SYNC_SECRET") or "");
    }

    -- Include the occupant's identity in the payload. The service reads
    -- context.user.id (the Zulip user ID the calls patch put in the token) as a
    -- LOOKUP KEY only — never as an assertion — so including it is safe and is
    -- what makes an occupancy event attributable to a Zulip account at all.
    include_user_info = true

    -- Do not let a slow or missing service wedge Prosody. Option names are the
    -- module's exact ones: api_retry_delay is a single number (seconds), NOT a
    -- list. (Defaults if omitted: api_timeout=20, api_retry_count=3,
    -- api_retry_delay=1.)
    api_timeout = 5
    api_retry_count = 3
    api_retry_delay = 2

-- event_sync for a LOCAL docker-jitsi-meet, posting to the stub service running
-- on the developer's machine.
--
-- Identical to deploy/prosody/event_sync.cfg.lua except for api_prefix. Read
-- that file first: the warning about the MUC component name applies here in full,
-- and getting it wrong is silent — no error, no events, every roster stays empty
-- while looking exactly like a quiet lounge.

Component "esync.meet.jitsi" "event_sync_component"
    -- MUST match the live component name. docker-jitsi-meet's internal MUC is
    -- `muc.meet.jitsi`, NOT `conference.meet.jitsi`. Confirm on the running
    -- container before trusting a green-looking start:
    --     docker compose exec prosody prosodyctl --config /config/prosody.cfg.lua \
    --         shell 'for k in prosody.hosts do print(k) end'
    muc_component = "muc.meet.jitsi"

    -- The one line that differs from production. In production the service is a
    -- container on the same bridge, reachable as `conferencing`. Here it is a
    -- plain process on the host, so Prosody has to leave the container network to
    -- reach it. Docker Desktop resolves host.docker.internal natively; the
    -- extra_hosts entry in the compose override covers plain Linux Docker, where
    -- it does not.
    --
    -- The stub must be started with --bind 0.0.0.0 for this to connect at all:
    -- bound to 127.0.0.1 it is unreachable from inside a container, and the only
    -- symptom is Prosody logging a failed POST every time somebody joins.
    api_prefix = "http://host.docker.internal:8080/api/v1/jitsi"

    -- MUST equal the stub's secret byte for byte, which it reads from Zulip's
    -- dev-secrets.conf (jitsi_conferencing_secret). A mismatch is a 401 on every
    -- event and an empty roster forever.
    api_headers = {
        ["Authorization"] = "Bearer " .. (os.getenv("EVENT_SYNC_SECRET") or "");
    }

    -- Puts context.user.id (the Zulip user ID the calls patch puts in the token)
    -- in the payload. Without it an occupancy event cannot be attributed to a
    -- Zulip account and the sidebar shows nameless avatars.
    include_user_info = true

    api_timeout = 5
    api_retry_count = 3
    api_retry_delay = 2

"""Configuration, read from the environment.

The one opinion this file has is that missing configuration is a startup
failure, never a runtime surprise. A conferencing service that boots without its
``EVENT_SYNC_SECRET`` is an open write endpoint to the occupancy state of every
call (see ``sinks.py``); one that boots with the event loop on but no Zulip
credentials is a process that polls nothing while looking perfectly healthy.
Both are the "looks fine, is wrong" failure this project keeps producing, so the
service refuses to start and says *everything* that is missing at once, rather
than failing on the first blank and making the operator rediscover the next one
on the next run.

What is required therefore depends on what is turned on. The bot credentials are
read by exactly one thing, the event-queue loop, so they are required exactly
when it runs. That is not a relaxation for its own sake: on a fresh deployment
the bot is a Zulip account that cannot exist until Zulip has booted, so a service
that demanded one unconditionally could never be brought up alongside the server
it belongs to. ``EVENT_LOOP=0`` is the honest way to say "not yet".

Nothing here is secret-bearing at rest: values come from the environment so the
same image runs in every deployment and the secrets live wherever the operator
already keeps them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


class ConfigError(RuntimeError):
    """Raised when the environment cannot produce a runnable configuration."""


@dataclass(frozen=True)
class Config:
    # -- required always: nothing the service does is safe without it --------
    event_sync_secret: str

    # -- required only while the event loop runs -----------------------------
    #: The bot's account, used by nothing but ``EventLoop``. Blank is legal when
    #: ``event_loop_enabled`` is false, and is what a deployment looks like
    #: between "Zulip is up" and "the bot has been created".
    zulip_site: str = ""
    zulip_email: str = ""
    zulip_api_key: str = ""

    # -- optional, each with a documented consequence when absent -----------
    #: Prosody's mod_muc_census URL. Absent → reconciliation is disabled and the
    #: service trusts the push stream alone. Left as a deliberate choice rather
    #: than a hard requirement, but the operator is warned, because a push-only
    #: design drifts whenever a delivery is lost.
    census_url: str | None = None
    #: Zulip's internal core-hook base URL (jitsi_hook.py), e.g.
    #: ``http://zulip/api/internal/jitsi``. Absent → posting is disabled: the
    #: service still tracks occupancy and answers the widget, but writes no call
    #: messages. Warned, not fatal, so the occupancy-only mode still boots.
    zulip_hook_url: str | None = None
    #: Bearer secret for the hook. Defaults to ``event_sync_secret`` because the
    #: current mesh shares one secret across the internal edges; set
    #: ``ZULIP_HOOK_SECRET`` to split that trust edge onto its own key.
    hook_secret: str = ""
    #: Verify Zulip's TLS certificate on the hook hop. Default True. Set
    #: ``ZULIP_HOOK_VERIFY_TLS=false`` when reaching Zulip directly over the
    #: internal network by a name the public cert does not cover (the container's
    #: self-signed cert) — there the bearer secret is the real authentication and
    #: the transport never leaves the docker host.
    hook_verify_tls: bool = True
    #: Host header to send on the hook hop. Reaching Zulip by an internal name
    #: (e.g. the container name over the shared network) means Django sees that
    #: name as the Host and rejects it with a 400 (DisallowedHost) before the
    #: bearer check. Set ``ZULIP_HOOK_HOST`` to the real external host so the
    #: request is accepted while still travelling directly over the internal net.
    #: Unset → the URL's own host is used.
    zulip_hook_host: str | None = None
    #: The HMAC key that derives room names, shared byte-for-byte with the calls
    #: patch. The wired flows (occupancy re-render) take room names from Prosody
    #: already mapped, so this is not needed yet; it is here for the flows that
    #: derive rooms themselves (private-call ring, channel reverse-lookup).
    room_key: str = ""

    #: Whether to run the Zulip event-queue loop. On by default; its reconnect
    #: drives reconciliation and it is the seam for the private-call flow. Turn it
    #: OFF in dev (``EVENT_LOOP=0``) when you have no real bot key — the sinks and
    #: the occupancy push do not use it, so it is only log noise there.
    event_loop_enabled: bool = True

    bind_host: str = "0.0.0.0"
    #: Matches the architecture doc's `api_prefix = http://conferencing:8080/...`,
    #: so the Prosody event_sync component and the service agree out of the box.
    bind_port: int = 8080
    sink_prefix: str = "/api/v1/jitsi"

    #: How often the ticker sweeps for ring timeouts. Cheap and local, so short.
    tick_seconds: float = 5.0
    #: How often reconciliation runs against the census. The push stream is the
    #: fast path; this is the slow safety net, so it is measured in minutes.
    reconcile_seconds: float = 60.0
    #: A ringing call with no answer becomes a missed call after this long.
    ring_timeout_seconds: float = 45.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Config":
        env = os.environ if env is None else env

        event_loop_enabled = (env.get("EVENT_LOOP", "true").strip().lower()) not in (
            "0", "false", "no", "off",
        )

        required = {"event_sync_secret": "EVENT_SYNC_SECRET"}
        if event_loop_enabled:
            required |= {
                "zulip_site": "ZULIP_SITE",
                "zulip_email": "ZULIP_EMAIL",
                "zulip_api_key": "ZULIP_API_KEY",
            }
        values: dict[str, str] = {}
        missing: list[str] = []
        for field_name, env_name in required.items():
            raw = env.get(env_name, "").strip()
            if raw:
                values[field_name] = raw
            else:
                missing.append(env_name)
        if missing:
            message = "missing required configuration: " + ", ".join(sorted(missing))
            if event_loop_enabled and missing != ["EVENT_SYNC_SECRET"]:
                # Said here because the alternative is an operator concluding the
                # service cannot start without a bot account, when in fact it
                # runs the occupancy half perfectly well without one — which is
                # the only way to bring it up beside a Zulip that does not have a
                # bot yet.
                message += (
                    "; these are only needed for the Zulip event loop, "
                    "which EVENT_LOOP=0 turns off"
                )
            raise ConfigError(message)

        census_url = (env.get("CENSUS_URL") or "").strip() or None
        zulip_hook_url = (env.get("ZULIP_HOOK_URL") or "").strip() or None
        # One shared secret across the internal mesh today: the hook bearer
        # defaults to the event_sync secret unless split out explicitly.
        hook_secret = (env.get("ZULIP_HOOK_SECRET") or "").strip() or values["event_sync_secret"]
        hook_verify_tls = (env.get("ZULIP_HOOK_VERIFY_TLS", "true").strip().lower()) not in (
            "0", "false", "no", "off",
        )
        zulip_hook_host = (env.get("ZULIP_HOOK_HOST") or "").strip() or None

        try:
            bind_port = int(env.get("BIND_PORT", "8080"))
            tick_seconds = float(env.get("TICK_SECONDS", "5"))
            reconcile_seconds = float(env.get("RECONCILE_SECONDS", "60"))
            ring_timeout_seconds = float(env.get("RING_TIMEOUT_SECONDS", "45"))
        except ValueError as exc:
            raise ConfigError(f"a numeric setting was not a number: {exc}") from exc

        return cls(
            event_sync_secret=values["event_sync_secret"],
            zulip_site=values.get("zulip_site", "").rstrip("/"),
            zulip_email=values.get("zulip_email", ""),
            zulip_api_key=values.get("zulip_api_key", ""),
            census_url=census_url,
            zulip_hook_url=zulip_hook_url,
            hook_secret=hook_secret,
            hook_verify_tls=hook_verify_tls,
            zulip_hook_host=zulip_hook_host,
            room_key=(env.get("JITSI_ROOM_KEY") or "").strip(),
            event_loop_enabled=event_loop_enabled,
            bind_host=(env.get("BIND_HOST") or "0.0.0.0").strip(),
            bind_port=bind_port,
            sink_prefix=(env.get("SINK_PREFIX") or "/api/v1/jitsi").strip(),
            tick_seconds=tick_seconds,
            reconcile_seconds=reconcile_seconds,
            ring_timeout_seconds=ring_timeout_seconds,
        )

    @property
    def reconciliation_enabled(self) -> bool:
        return self.census_url is not None

    @property
    def posting_enabled(self) -> bool:
        return self.zulip_hook_url is not None

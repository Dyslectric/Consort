"""The process. ``python -m conferencing`` starts the service.

Three things run at once, and each is a thread so that a stall in one cannot
silence the others:

* **The sinks**, a WSGI server, in the main thread. Prosody posts occupancy here.
* **The event loop**, polling Zulip's event queue, in a daemon thread. Its first
  act on every (re)registration is to reconcile, via ``Service.on_reconnect``.
* **The ticker**, in a daemon thread, sweeping ring timeouts on every tick and
  running census reconciliation on the slower interval.

The logic all lives in ``service.py`` and is tested there. This file owns only
the wiring and the shutdown, which are the parts a unit test should not try to
own: threads, signals and a real socket. Keeping them apart is what lets the
behaviour be tested without any of the three.

``werkzeug``'s server is used directly (it ships with Flask) so shutdown is
clean and explicit. For a hardened deployment, put a real WSGI server in front;
the sink app is a plain Flask object and works under gunicorn or waitress
unchanged.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from types import FrameType

from werkzeug.serving import make_server

from .census import Census
from .config import Config, ConfigError
from .hook_client import HookClient
from .service import Service
from .state import Store
from .zulip_client import EventLoop, ZulipClient

logger = logging.getLogger(__name__)


def _ticker(service: Service, stop: threading.Event, config: Config) -> None:
    """Sweep ring timeouts every tick; reconcile on the slower interval.

    ``stop.wait(interval)`` is the loop's clock and its shutdown signal at once:
    it returns early the moment ``stop`` is set, so the process never waits a
    full interval to exit.
    """
    last_reconcile = time.monotonic()
    # Reconciliation also runs on the event loop's first registration; here it is
    # the periodic safety net, so it does not need to fire immediately.
    while not stop.wait(config.tick_seconds):
        try:
            service.sweep_ring_timeouts()
        except Exception:
            logger.exception("ring-timeout sweep failed")
        if config.reconciliation_enabled:
            now = time.monotonic()
            if now - last_reconcile >= config.reconcile_seconds:
                try:
                    service.reconcile()
                except Exception:
                    logger.exception("scheduled reconciliation failed")
                last_reconcile = now


def build(config: Config) -> tuple[Service, EventLoop]:
    """Assemble the service and its event loop from configuration.

    Split out from ``main`` so the assembly can be exercised without binding a
    socket or starting a thread.
    """
    store = Store()
    # The event-loop client still uses the bot credentials — it registers a
    # Zulip event queue whose reconnect drives reconciliation (and is the seam
    # the private-call flow will attach to). Posting no longer goes through it.
    client = ZulipClient(config.zulip_site, config.zulip_email, config.zulip_api_key)
    census = (
        Census(config.census_url, store) if config.reconciliation_enabled else None
    )
    poster = (
        HookClient(
            config.zulip_hook_url,
            config.hook_secret,
            verify=config.hook_verify_tls,
            host_header=config.zulip_hook_host,
        )
        if config.posting_enabled
        else None
    )
    service = Service(
        store,
        poster,
        census=census,
        room_key=config.room_key,
        ring_timeout_seconds=config.ring_timeout_seconds,
    )
    loop = EventLoop(client, service.handle_event, on_reconnect=service.on_reconnect)
    return service, loop


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = Config.from_env()
    except ConfigError as exc:
        # A configuration failure is the operator's to fix, so it goes to the log
        # as a plain message and the process exits non-zero rather than crashing
        # with a traceback that buries the one line that matters.
        logger.error("%s", exc)
        return 2

    service, loop = build(config)
    app = service.make_app(config.event_sync_secret, prefix=config.sink_prefix)

    stop = threading.Event()
    server = make_server(config.bind_host, config.bind_port, app, threaded=True)

    loop_thread = threading.Thread(target=loop.run_forever, name="event-loop", daemon=True)
    ticker_thread = threading.Thread(
        target=_ticker, args=(service, stop, config), name="ticker", daemon=True
    )

    def shutdown(signum: int, _frame: FrameType | None) -> None:
        logger.info("signal %s received; shutting down", signum)
        stop.set()
        loop.stop()
        # Unblocks server.serve_forever() from within the handler.
        threading.Thread(target=server.shutdown, name="shutdown", daemon=True).start()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    loop_thread.start()
    ticker_thread.start()
    logger.info(
        "serving event_sync on %s:%s%s (reconciliation %s, posting %s)",
        config.bind_host,
        config.bind_port,
        config.sink_prefix,
        "on" if config.reconciliation_enabled else "off",
        "on" if config.posting_enabled else "off",
    )
    try:
        server.serve_forever()
    finally:
        stop.set()
        loop.stop()
    logger.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

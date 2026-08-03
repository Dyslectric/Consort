"""The four HTTP endpoints Prosody's `event_sync` component posts to.

Three rules govern everything in this file, and all three come from the threat
model rather than from convenience:

1. **The endpoint is publicly routable.** Network isolation is a mitigation, not
   a guarantee, so every request must carry the bearer secret and it is compared
   in constant time.
2. **Identity in the body is a lookup key, never an assertion.** The payload
   contains a name, an email and an id, and a caller who reached this endpoint
   controls all three. They are used to find state we already created and for
   nothing else. The 2025 Mattermost Jira plugin CVE is this exact bug.
3. **An unknown room is not an error.** Prosody will report rooms this service
   never created — someone joining a stale link, another integration, a manually
   created room. Those are recorded and ignored, not treated as failures, because
   a sink that 500s on unexpected input gives Prosody a retry storm.
"""

from __future__ import annotations

import hmac
import logging
import time
from typing import Any, Callable

from flask import Blueprint, Flask, jsonify, request

from .rooms import parse_mapped_room
from .state import Occupant, Store

logger = logging.getLogger(__name__)


def _authorized(expected_secret: str) -> bool:
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    return hmac.compare_digest(header[len(prefix) :], expected_secret)


def _occupant_from(payload: dict[str, Any], now: float) -> Occupant:
    """Build an Occupant from an event_sync payload.

    `context.user.id` is the Zulip user ID the calls patch put in the token, so
    it is the one field here worth reading — but it is still only a key. If it is
    not an integer we record the occupant anonymously rather than rejecting the
    event, because a participant we cannot name is still a participant and
    dropping them would understate the roster.
    """
    occupant = payload.get("occupant") or {}
    raw_id = occupant.get("id")
    zulip_user_id: int | None = None
    if isinstance(raw_id, str) and raw_id.isdigit():
        zulip_user_id = int(raw_id)
    elif isinstance(raw_id, int):
        zulip_user_id = raw_id

    return Occupant(
        occupant_jid=str(occupant.get("occupant_jid") or occupant.get("occupant_jif") or ""),
        zulip_user_id=zulip_user_id,
        display_name=str(occupant.get("name") or ""),
        joined_at=now,
    )


def build_blueprint(
    store: Store,
    secret: str,
    *,
    on_change: Callable[[str], None] | None = None,
    on_call_created: Callable[[dict[str, Any]], None] | None = None,
    on_occupancy_query: Callable[[int], dict[str, Any]] | None = None,
    on_occupancy_all: Callable[[], dict[str, Any]] | None = None,
    clock: Callable[[], float] = time.time,
) -> Blueprint:
    """Sinks for Prosody's occupancy events plus the calls patch's call notices.

    ``on_change`` is called with a room name after any occupancy change.
    ``on_call_created`` is called with the notice body the phase-two patch POSTs
    when a call is minted — the one place that knows which conversation a room
    belongs to, since the room name is a one-way HMAC the service cannot reverse.
    """
    bp = Blueprint("event_sync", __name__)

    def notify(room: str) -> None:
        if on_change is None:
            return
        try:
            on_change(room)
        except Exception:
            # Never let a rendering failure turn into a 500 for Prosody; it
            # would retry, and the retry would fail the same way.
            logger.exception("on_change failed for room %s", room)

    @bp.before_request
    def check_secret():  # type: ignore[no-untyped-def]
        if not _authorized(secret):
            logger.warning("rejected unauthenticated event_sync request to %s", request.path)
            return jsonify({"error": "unauthorized"}), 401
        return None

    def room_of(payload: dict[str, Any]) -> tuple[str, str | None]:
        # event_sync reports the name Prosody sees, which muc_domain_mapper has
        # already rewritten to [tenant]room. Parsing it back is what makes the
        # event attributable at all.
        mapped = parse_mapped_room(str(payload.get("room_name") or payload.get("room_jid") or ""))
        return mapped.room, mapped.tenant

    @bp.post("/events/room/created")
    def room_created():  # type: ignore[no-untyped-def]
        payload = request.get_json(silent=True) or {}
        room, tenant = room_of(payload)
        if not room:
            return jsonify({"ok": True, "ignored": "no room name"})
        store.room_created(room, tenant, now=clock())
        notify(room)
        return jsonify({"ok": True})

    @bp.post("/events/room/destroyed")
    def room_destroyed():  # type: ignore[no-untyped-def]
        payload = request.get_json(silent=True) or {}
        room, _ = room_of(payload)
        store.room_destroyed(room, now=clock())
        notify(room)
        return jsonify({"ok": True})

    @bp.post("/events/occupant/joined")
    def occupant_joined():  # type: ignore[no-untyped-def]
        payload = request.get_json(silent=True) or {}
        room, tenant = room_of(payload)
        if not room:
            return jsonify({"ok": True, "ignored": "no room name"})
        now = clock()
        store.room_created(room, tenant, now=now)
        store.occupant_joined(room, _occupant_from(payload, now), now=now)
        notify(room)
        return jsonify({"ok": True})

    @bp.post("/events/occupant/left")
    def occupant_left():  # type: ignore[no-untyped-def]
        payload = request.get_json(silent=True) or {}
        room, _ = room_of(payload)
        occupant = payload.get("occupant") or {}
        jid = str(occupant.get("occupant_jid") or occupant.get("occupant_jif") or "")
        store.occupant_left(room, jid, now=clock())
        notify(room)
        return jsonify({"ok": True})

    @bp.post("/calls/created")
    def call_created():  # type: ignore[no-untyped-def]
        # Posted by the calls patch at mint time. Unlike the occupancy sinks,
        # this body is trusted as far as its bearer secret goes — it comes from
        # Zulip, not from a participant — but it still describes a call the
        # service then owns; we do not act on any identity beyond what is here.
        payload = request.get_json(silent=True) or {}
        if on_call_created is not None:
            try:
                on_call_created(payload)
            except Exception:
                logger.exception("on_call_created failed for room %s", payload.get("room"))
        return jsonify({"ok": True})

    @bp.get("/occupancy")
    def occupancy_query():  # type: ignore[no-untyped-def]
        # The occupancy widget's data source, called by the Zulip patch (which
        # has already checked the user may see the channel), not by a browser.
        # Bearer-protected like everything here. Returns {} shape for an unknown
        # or callless channel — an absent call is not an error.
        if on_occupancy_query is None:
            return jsonify({"error": "occupancy queries not supported"}), 404
        raw = request.args.get("stream_id", "")
        if not raw.isdigit():
            return jsonify({"error": "a numeric stream_id is required"}), 400
        return jsonify(on_occupancy_query(int(raw)))

    @bp.get("/occupancy_all")
    def occupancy_all_query():  # type: ignore[no-untyped-def]
        # Every channel with a live call, for the sidebar. Called by the Zulip
        # patch (which then filters to the channels the requesting user may see),
        # not by a browser. Bearer-protected like everything here.
        if on_occupancy_all is None:
            return jsonify({"error": "occupancy queries not supported"}), 404
        return jsonify(on_occupancy_all())

    return bp


def create_app(
    store: Store,
    secret: str,
    *,
    prefix: str = "/api/v1/jitsi",
    on_change: Callable[[str], None] | None = None,
    on_call_created: Callable[[dict[str, Any]], None] | None = None,
    on_occupancy_query: Callable[[int], dict[str, Any]] | None = None,
    on_occupancy_all: Callable[[], dict[str, Any]] | None = None,
    clock: Callable[[], float] = time.time,
) -> Flask:
    if not secret:
        # Refusing to start is the correct response: a sink with no secret is
        # an open write endpoint to the occupancy state of every call.
        raise ValueError("event_sync bearer secret must not be empty")
    app = Flask(__name__)
    app.register_blueprint(
        build_blueprint(
            store,
            secret,
            on_change=on_change,
            on_call_created=on_call_created,
            on_occupancy_query=on_occupancy_query,
            on_occupancy_all=on_occupancy_all,
            clock=clock,
        ),
        url_prefix=prefix,
    )

    @app.get("/healthz")
    def healthz():  # type: ignore[no-untyped-def]
        return jsonify({"ok": True, "rooms": len(store.all_occupancy())})

    return app

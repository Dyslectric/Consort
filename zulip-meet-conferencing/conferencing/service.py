"""The thing that was missing: the piece that connects the other pieces.

Everything under ``conferencing/`` was built and tested in isolation — the
sinks, the store, the census, the renderer, the event loop. This is the object
that owns one of each and defines what happens when one of them fires:

* Prosody posts an occupancy event to a sink → ``on_occupancy_change`` re-renders
  the call message for that room, if there is one.
* The event queue registers (at startup, and after every loss) → ``on_reconnect``
  reconciles, because a fresh queue means we missed whatever happened in the gap.
* The ticker fires → ``sweep_ring_timeouts`` turns unanswered calls into missed
  ones, and ``reconcile`` runs the slow census safety net.
* A Zulip event arrives → ``handle_event`` dispatches it. The private-call flow
  (a direct message that drives ringing → active | declined) plugs in here; it
  is the one flow not yet built, and this is the seam it attaches to.

The threads that call these methods live in ``__main__``. Keeping the logic here
and the threading there is deliberate: every method below is synchronous and
takes its clock as data, so the behaviour is testable without a socket, a
sleep, or a race — which is the only way this project trusts a test.

The service holds **no Jitsi signing key**. It never mints a token and never
authorises a join; it observes call state and occupancy and edits messages. The
only place that can check who is asking is the phase-two patch, and that is
where the key stays.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable
from uuid import uuid4

from flask import Flask

from .census import Census, ReconcileResult
from .hook_client import HookClient
from .render import call_message
from .sinks import create_app
from .state import Call, CallState, InvalidTransition, Occupancy, Store

logger = logging.getLogger(__name__)


class Service:
    def __init__(
        self,
        store: Store,
        poster: HookClient | None,
        *,
        census: Census | None = None,
        room_key: str = "",
        ring_timeout_seconds: float = 45.0,
        call_topic: str = "Calls",
        clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] = lambda: uuid4().hex,
    ) -> None:
        self.store = store
        #: The wire to Zulip's core message hook (send/edit). ``None`` disables
        #: posting entirely — occupancy is still tracked and the widget still
        #: answers, there is just no message to write. This is the graceful
        #: degradation the rest of the service already assumes of a down Zulip.
        self.poster = poster
        self.census = census
        self.room_key = room_key
        self.ring_timeout_seconds = ring_timeout_seconds
        #: The topic a channel call message is posted under. A call is
        #: channel-scoped, not topic-scoped, so it needs a home topic of its own.
        self.call_topic = call_topic
        self.clock = clock
        #: Call IDs must be unique across time — a channel with epoch rotation
        #: off derives the same room name every call, so the room cannot be the
        #: id. Injectable so tests are deterministic.
        self._id_factory = id_factory
        #: Join URLs the calls patch produced, kept per call so a re-render can
        #: reproduce the link. The service does not mint these; it only remembers
        #: the ones handed to it when a call is registered. Absent → the message
        #: renders without a join link, which is correct for a call we are only
        #: observing rather than hosting.
        self._join_urls: dict[str, str] = {}

    # -- wiring the sinks -------------------------------------------------

    def make_app(self, secret: str, *, prefix: str = "/api/v1/jitsi") -> Flask:
        """The Flask app serving Prosody's four event_sync endpoints.

        ``on_change`` is the whole point of the connection: an occupancy event
        that changed the store must be reflected in what people read.
        """
        return create_app(
            self.store,
            secret,
            prefix=prefix,
            on_change=self.on_occupancy_change,
            on_call_created=self.handle_call_created,
            on_occupancy_query=self.occupancy_for_stream,
            on_occupancy_all=self.occupancy_all,
            clock=self.clock,
        )

    # -- occupancy → the widget -------------------------------------------

    def occupancy_for_stream(self, stream_id: int) -> dict[str, Any]:
        """The roster the occupancy widget shows for a channel.

        The client knows a channel, not a room, so this is the reverse of the
        sinks' lookup. Returns a JSON-able dict: whether a call is live, the
        count, and the names. A drifted roster reports its count but no names —
        the same honesty the call message uses, so a roster we know is wrong is
        never shown as if it were right.
        """
        empty = {"stream_id": stream_id, "active": False, "count": 0, "occupants": [], "drifted": False}
        call = self.store.active_call_for_stream(stream_id)
        if call is None:
            return empty
        occupancy = self.store.occupancy(call.room)
        if occupancy is None:
            return {**empty, "active": True}
        if occupancy.drifted:
            return {
                "stream_id": stream_id,
                "active": True,
                "count": occupancy.count,
                "occupants": [],
                "drifted": True,
            }
        return {
            "stream_id": stream_id,
            "active": True,
            "count": occupancy.count,
            "occupants": self._widget_roster(occupancy),
            "drifted": False,
        }

    @staticmethod
    def _widget_roster(occupancy: Occupancy) -> list[dict[str, Any]]:
        """Occupants as ``{name, user_id}`` so the client can show avatars.

        ``user_id`` is the Zulip user ID the calls patch put in the token; it may
        be ``None`` for a participant we could not name (see ``sinks``), and the
        client falls back to a nameless avatar in that case. Sorted by name so the
        widget order is stable rather than join-order jitter.
        """
        people = [
            {"name": occ.display_name or "someone", "user_id": occ.zulip_user_id}
            for occ in occupancy.occupants.values()
        ]
        people.sort(key=lambda person: person["name"].lower())
        return people

    def occupancy_all(self) -> dict[str, Any]:
        """Occupancy for every channel with a live call, for the sidebar.

        The sidebar shows call state across all of a user's channels at once, so
        it wants every active channel call in one request rather than polling each.
        DM calls (no ``stream_id``) are omitted — the sidebar is channels. Like
        ``occupancy_for_stream`` this does not itself check access: its only caller
        is the phase-two patch, which filters the result to the channels the
        requesting user may actually see before it reaches a browser.
        """
        rooms = [
            self.occupancy_for_stream(call.stream_id)
            for call in self.store.active_calls()
            if call.stream_id is not None
        ]
        return {"rooms": rooms}

    # -- call bookkeeping -------------------------------------------------

    def register_call(self, call: Call, *, join_url: str | None = None) -> Call:
        """Adopt a call the service should render and drive.

        This is the seam the call-creation flow calls once it exists. It is here,
        rather than folded into ``Store``, because remembering the join URL is a
        rendering concern the store deliberately does not carry.
        """
        created = self.store.create_call(call)
        if join_url:
            self._join_urls[created.call_id] = join_url
        return created

    # -- occupancy → the message people read ------------------------------

    def on_occupancy_change(self, room: str) -> None:
        """A sink changed occupancy for ``room``. Reflect it, if it has a call.

        Occupancy can change for a room we have no call record for — a stale
        link, another integration, a census-discovered room. That is not an
        error and not something to render; we simply have no message to edit.

        A destroyed room (occupancy gone, not merely empty) ends the call. Using
        "occupancy is None" rather than "count == 0" is deliberate: ``room/created``
        and the last ``occupant/left`` both legitimately leave a count of zero for
        a moment, and ending on those would kill a call before anyone joined, or
        the instant someone stepped out. Only Prosody destroying the room — which
        pops the occupancy — means the call is actually over.
        """
        call = self.store.call_for_room(room)
        if call is None:
            return
        occupancy = self.store.occupancy(room)
        if occupancy is None and call.state is CallState.ACTIVE:
            # Room destroyed → end the call. Do this even when the call never got
            # a message_id (its initial post failed): otherwise a call whose post
            # failed stays ACTIVE forever and wedges the room's dedup slot, so
            # every later call for this channel silently no-ops until a restart.
            try:
                call = self.store.transition(call.call_id, CallState.ENDED, now=self.clock())
            except InvalidTransition:
                pass
        # Only a call that actually posted has something to re-render.
        if call.message_id is not None:
            self._rerender(call)
        # Channels post no message; their real-time signal is a push to the
        # sidebar instead (best-effort — the client's poll is the safety net).
        if call.stream_id is not None:
            self._push_occupancy(call)

    def handle_call_created(self, notice: dict[str, Any]) -> Call | None:
        """A call was minted. Create its record and post the roster message.

        This is the notice the calls patch POSTs at mint time — the only place
        that knows which conversation a room belongs to, since the room name is a
        one-way HMAC the service cannot reverse. Idempotent: a second notice for
        a room that already has a live call (a double-click, a retry) posts
        nothing and returns the existing call.
        """
        room = str(notice.get("room") or "")
        if not room:
            return None

        existing = self.store.call_for_room(room)
        if existing is not None and not existing.state.is_terminal:
            return existing

        now = self.clock()
        stream_id = notice.get("stream_id")
        initiator_id = notice.get("initiator_id")
        realm_id = notice.get("realm_id")
        call = Call(
            call_id=self._id_factory(),
            scope=str(notice.get("scope") or ""),
            room=room,
            tenant=notice.get("tenant"),
            # A channel call is live the moment it is created — nobody has to
            # "answer" it. Direct-message ringing is a later flow.
            state=CallState.ACTIVE,
            created_at=now,
            stream_id=stream_id if isinstance(stream_id, int) else None,
            user_ids=[int(u) for u in (notice.get("user_ids") or [])],
            initiator_id=initiator_id if isinstance(initiator_id, int) else None,
            realm_id=realm_id if isinstance(realm_id, int) else None,
        )
        try:
            call = self.store.create_call(call)
        except InvalidTransition:
            return self.store.call_for_room(room)

        if self.poster is None:
            return call  # posting disabled; the call is tracked, just not written

        try:
            if call.stream_id is not None:
                # Channel call: tracked for occupancy so the sidebar can show it,
                # but deliberately no roster message. Channel calls are found
                # through the sidebar (visibility), not a posted "Calls" message.
                pass
            elif call.user_ids:
                # DM / group DM: there is no sidebar there, so it still gets a
                # roster message, authored as the initiator so it lands in the real
                # conversation.
                call.message_id = self.poster.send(
                    realm_id=call.realm_id,
                    stream_id=None,
                    user_ids=call.user_ids,
                    initiator_id=call.initiator_id,
                    topic=str(notice.get("topic") or self.call_topic),
                    content=call_message(call, self.store.occupancy(room), None, now=now),
                )
            else:
                logger.warning("call notice for %s had neither stream_id nor user_ids", room)
        except Exception:
            # A failed post must not 500 the sink. The call record still exists
            # and occupancy is still tracked; there is just no message to edit
            # until the next call.
            logger.exception("failed to post call message for room %s", room)
        return call

    def _rerender(self, call: Call) -> None:
        if self.poster is None or call.message_id is None:
            return
        occupancy = self.store.occupancy(call.room)
        body = call_message(
            call,
            occupancy,
            self._join_urls.get(call.call_id),
            now=self.clock(),
        )
        try:
            self.poster.update(call.message_id, body)
        except Exception:
            # ZulipError is the expected failure, but any error here (a bad
            # render, a dropped connection) must not take down the ticker or the
            # sink that called us. The next occupancy event or reconcile pass
            # tries again with fresher state, which beats retrying this body.
            logger.exception("failed to update call message for room %s", call.room)

    def _push_occupancy(self, call: Call) -> None:
        """Push a channel call's occupancy to Zulip's hook, which fans it out to
        the channel's subscribers as a client event so the sidebar updates the
        instant someone joins or leaves. Best-effort: a failed push (or no hook
        configured) is swallowed, because the client's slow poll heals it and a
        sink must never fail for want of a real-time nicety."""
        if self.poster is None or call.stream_id is None:
            return
        roster = self.occupancy_for_stream(call.stream_id)
        try:
            self.poster.occupancy(
                realm_id=call.realm_id,
                stream_id=call.stream_id,
                active=bool(roster["active"]),
                count=int(roster["count"]),
                occupants=list(roster["occupants"]),
            )
        except Exception:
            logger.warning("occupancy push failed for room %s", call.room, exc_info=True)

    # -- ring timeouts ----------------------------------------------------

    def sweep_ring_timeouts(self, now: float | None = None) -> list[Call]:
        """Turn calls that have been ringing too long into missed calls.

        Selecting and transitioning are two steps, and a call can be answered in
        between — a race the browser wins legitimately. An ``InvalidTransition``
        there is expected, not an error: it means someone picked up, so we leave
        that call alone.
        """
        now = self.clock() if now is None else now
        cutoff = now - self.ring_timeout_seconds
        swept: list[Call] = []
        for call in self.store.calls_ringing_since(cutoff):
            try:
                updated = self.store.transition(call.call_id, CallState.MISSED, now=now)
            except InvalidTransition:
                continue
            self._rerender(updated)
            swept.append(updated)
        return swept

    # -- reconciliation ---------------------------------------------------

    def reconcile(self) -> ReconcileResult | None:
        """Run the census safety net, then re-render anything it may have moved.

        Returns ``None`` when reconciliation is disabled (no census configured),
        so a caller can tell "nothing to do" from "checked and clean".
        """
        if self.census is None:
            return None
        result = self.census.reconcile()
        # A correction changes what a roster should say — a drifted flag, a count,
        # a dropped room — so re-render every active call we render. This is a
        # blunt sweep rather than a targeted one because the census reports by
        # room and our calls are few; precision here would buy nothing.
        for call in self.store.active_calls():
            if call.message_id is not None:
                self._rerender(call)
        return result

    def on_reconnect(self) -> None:
        """Fires on every event-queue registration, including the first.

        A fresh queue means the world moved while we were not listening.
        Reconnecting without reconciling is the quiet failure the whole census
        exists to prevent, so the two are bound together here.
        """
        self.reconcile()

    # -- Zulip events -----------------------------------------------------

    def handle_event(self, event: dict) -> str | None:
        """Dispatch one Zulip event. Returns the type it recognised, or ``None``.

        Today the service drives nothing off Zulip events: occupancy comes from
        Prosody's sinks and timeouts from the ticker, so there is nothing here to
        act on yet. The dispatcher exists — and the loop runs — because the
        private-call flow attaches here, and because a live queue is what makes
        ``on_reconnect`` reconciliation happen at all. Recognising and ignoring
        is honest; pretending to handle would not be.
        """
        etype = event.get("type")
        if etype in ("message", "submessage"):
            logger.debug("event %s received; no handler wired yet", etype)
            return etype
        return None

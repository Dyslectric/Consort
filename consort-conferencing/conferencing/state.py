"""Call state and room occupancy.

Two things live here, and they are deliberately separate:

* **Call state** is what the service believes, and it is authoritative for
  ringing, timeouts and message rendering. Nothing outside this process may set
  it directly.
* **Occupancy** is what Prosody has told us, and it is authoritative for nothing
  except who is in a room right now. It arrives over a publicly routable
  endpoint, so identity in those payloads is treated as a *lookup key* against
  state we created, never as an assertion. This is the same class of bug as the
  2025 Mattermost Jira plugin CVE, where a plugin read a user ID out of a
  request body.

The occupancy half also carries the lesson from phase one: an empty roster and a
broken pipeline look identical. `Occupancy.stale_for` exists so that "we have
heard nothing about this room for a suspiciously long time" is a state the
service can notice and report, rather than something that silently renders as
"nobody is here".
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum


class CallState(Enum):
    RINGING = "ringing"
    ACTIVE = "active"
    ENDED = "ended"
    DECLINED = "declined"
    MISSED = "missed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL


_TERMINAL = frozenset(
    {CallState.ENDED, CallState.DECLINED, CallState.MISSED, CallState.CANCELLED}
)

#: The only transitions the service will make. Anything else is a bug, and
#: raising here is preferable to a call that can never be cleaned up.
_ALLOWED: dict[CallState, frozenset[CallState]] = {
    CallState.RINGING: frozenset(
        {CallState.ACTIVE, CallState.DECLINED, CallState.MISSED, CallState.CANCELLED}
    ),
    CallState.ACTIVE: frozenset({CallState.ENDED}),
    CallState.ENDED: frozenset(),
    CallState.DECLINED: frozenset(),
    CallState.MISSED: frozenset(),
    CallState.CANCELLED: frozenset(),
}


class InvalidTransition(Exception):
    pass


@dataclass
class Occupant:
    """A person Prosody says is in a room.

    `zulip_user_id` is resolved from `context.user.id`, which the calls patch
    puts there. It is only ever used to look up a user we already know about.
    """

    occupant_jid: str
    zulip_user_id: int | None = None
    display_name: str = ""
    joined_at: float = 0.0


@dataclass
class Occupancy:
    room: str
    tenant: str | None = None
    occupants: dict[str, Occupant] = field(default_factory=dict)
    created_at: float | None = None
    #: None means "we have never heard anything about this room", which is not
    #: the same as "we heard about it at time zero". Using a falsy check on a
    #: timestamp conflates the two and makes staleness undetectable.
    last_event_at: float | None = None
    #: Set when reconciliation finds Prosody disagrees with us.
    drifted: bool = False

    @property
    def count(self) -> int:
        return len(self.occupants)

    def stale_for(self, now: float) -> float:
        if self.last_event_at is None:
            return 0.0
        return now - self.last_event_at


@dataclass
class Call:
    call_id: str
    scope: str
    room: str
    tenant: str | None
    state: CallState
    created_at: float
    stream_id: int | None = None
    user_ids: list[int] = field(default_factory=list)
    #: Set when this call is a room inside a lounge rather than a channel's own
    #: call. ``stream_id`` is still the lounge, because that is whose subscribers
    #: hear about it, so the two are not alternatives: this is what distinguishes
    #: one of a lounge's many rooms from the single call an ordinary channel has.
    lounge_room_id: int | None = None
    initiator_id: int | None = None
    #: The realm the conversation lives in. The core-hook send path needs it to
    #: post into the right org; absent for records created before it was carried.
    realm_id: int | None = None
    message_id: int | None = None
    epoch_token: str | None = None
    updated_at: float = 0.0


class Store:
    """In-memory state, guarded by a lock.

    Deliberately not a database. A restart should recover from Zulip and Prosody
    rather than from local durable state, because those two are the systems of
    record and any local store immediately starts disagreeing with them. The
    reconciliation pass in census.py is what makes that safe.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._calls: dict[str, Call] = {}
        self._by_room: dict[str, str] = {}
        self._occupancy: dict[str, Occupancy] = {}

    # -- calls ------------------------------------------------------------

    def create_call(self, call: Call) -> Call:
        with self._lock:
            if call.call_id in self._calls:
                raise InvalidTransition(f"call {call.call_id} already exists")
            existing = self._by_room.get(call.room)
            if existing is not None and not self._calls[existing].state.is_terminal:
                # Two people hitting the call button at the same moment derive
                # the same room. The first one wins and the second joins it;
                # creating a second call record would give the channel two
                # competing messages for one conversation.
                raise InvalidTransition(f"room {call.room} already has an active call")
            self._calls[call.call_id] = call
            self._by_room[call.room] = call.call_id
            return call

    def get_call(self, call_id: str) -> Call | None:
        with self._lock:
            return self._calls.get(call_id)

    def call_for_room(self, room: str) -> Call | None:
        with self._lock:
            call_id = self._by_room.get(room)
            return self._calls.get(call_id) if call_id else None

    def transition(self, call_id: str, to: CallState, *, now: float) -> Call:
        with self._lock:
            call = self._calls.get(call_id)
            if call is None:
                raise InvalidTransition(f"no such call {call_id}")
            if to not in _ALLOWED[call.state]:
                raise InvalidTransition(f"{call.state.value} -> {to.value} is not allowed")
            call.state = to
            call.updated_at = now
            if to.is_terminal and self._by_room.get(call.room) == call_id:
                del self._by_room[call.room]
            return call

    def active_calls(self) -> list[Call]:
        with self._lock:
            return [c for c in self._calls.values() if not c.state.is_terminal]

    def active_call_for_stream(self, stream_id: int) -> Call | None:
        """The live call in a channel, if any — the widget's lookup key.

        The occupancy widget knows a channel (stream_id), not a room, so this is
        the reverse of what the sinks use. There is at most one live call per
        channel (``create_call`` enforces one active call per room, and a channel
        maps to one room per epoch), so returning the first non-terminal match is
        unambiguous.

        Lounge rooms are skipped, and the "at most one" above is why they have to
        be: a lounge carries many live rooms at once, all of them stamped with the
        lounge's stream_id, so without this the first one started would answer for
        the whole lounge and the rest would be invisible. A lounge has no
        channel-level call of its own to find.
        """
        with self._lock:
            for call in self._calls.values():
                if (
                    call.stream_id == stream_id
                    and call.lounge_room_id is None
                    and not call.state.is_terminal
                ):
                    return call
            return None

    def calls_ringing_since(self, cutoff: float) -> list[Call]:
        with self._lock:
            return [
                c
                for c in self._calls.values()
                if c.state is CallState.RINGING and c.created_at <= cutoff
            ]

    # -- occupancy --------------------------------------------------------

    def room_created(self, room: str, tenant: str | None, *, now: float) -> Occupancy:
        with self._lock:
            occupancy = self._occupancy.setdefault(room, Occupancy(room=room, tenant=tenant))
            occupancy.tenant = tenant or occupancy.tenant
            if occupancy.created_at is None:
                occupancy.created_at = now
            occupancy.last_event_at = now
            return occupancy

    def room_destroyed(self, room: str, *, now: float) -> Occupancy | None:
        with self._lock:
            occupancy = self._occupancy.pop(room, None)
            if occupancy is not None:
                occupancy.occupants.clear()
                occupancy.last_event_at = now
            return occupancy

    def occupant_joined(self, room: str, occupant: Occupant, *, now: float) -> Occupancy:
        with self._lock:
            occupancy = self._occupancy.setdefault(room, Occupancy(room=room))
            occupancy.occupants[occupant.occupant_jid] = occupant
            occupancy.last_event_at = now
            occupancy.drifted = False
            return occupancy

    def occupant_left(self, room: str, occupant_jid: str, *, now: float) -> Occupancy:
        with self._lock:
            occupancy = self._occupancy.setdefault(room, Occupancy(room=room))
            occupancy.occupants.pop(occupant_jid, None)
            occupancy.last_event_at = now
            return occupancy

    def occupancy(self, room: str) -> Occupancy | None:
        with self._lock:
            return self._occupancy.get(room)

    def all_occupancy(self) -> list[Occupancy]:
        with self._lock:
            return list(self._occupancy.values())

    def replace_occupancy(self, room: str, counts: int, *, now: float) -> Occupancy:
        """Correct our view from the census. Marks the room as drifted.

        The census reports counts, not identities, so this cannot rebuild the
        roster — it can only tell us ours is wrong. Saying so out loud is the
        point: a roster we know to be wrong must not render as though it were
        right.
        """
        with self._lock:
            occupancy = self._occupancy.setdefault(room, Occupancy(room=room))
            if len(occupancy.occupants) != counts:
                occupancy.drifted = True
            occupancy.last_event_at = now
            return occupancy

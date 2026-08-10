"""Reconciliation against Prosody's `mod_muc_census`.

A push-only design drifts whenever a delivery is lost, and `mod_muc_census`
itself contains logic to detect leaked occupants — which tells you the Jitsi
authors consider drift real rather than theoretical.

This runs at startup, after any event-queue reconnect, and on a slow ticker. It
cannot rebuild a roster, because the census reports counts rather than
identities. What it can do is establish that our roster is wrong, which is worth
more than it sounds: a roster we know to be wrong renders differently from one we
trust (see render.py), so a lost event degrades into an honest "resyncing"
instead of a confident lie.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

import requests

from .rooms import parse_mapped_room
from .state import Store

logger = logging.getLogger(__name__)


@dataclass
class ReconcileResult:
    checked: int = 0
    corrected: int = 0
    unknown_to_us: int = 0
    missing_from_census: int = 0

    @property
    def clean(self) -> bool:
        return self.corrected == 0 and self.missing_from_census == 0


class Census:
    def __init__(
        self,
        url: str,
        store: Store,
        *,
        session: requests.Session | None = None,
        timeout: float = 10.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.url = url
        self.store = store
        self.timeout = timeout
        self.clock = clock
        self._session = session or requests.Session()

    def fetch(self) -> dict[str, int]:
        """Return {room name: occupant count}, keyed by unmapped room name.

        Raises ValueError if the response listed rooms but none of them could be
        understood. That is a schema change or a wrong URL, and treating it as
        "there are no rooms" would silently empty every roster in the
        deployment — a failure that looks exactly like everyone hanging up.
        """
        response = self._session.get(self.url, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        # jitsi-meet's mod_muc_census keys the list `room_census`. Earlier code
        # looked only for `rooms`, which the real module never sends — so fetch
        # returned {} and reconcile then destroyed every roster as "missing from
        # census". Accept the real key first, then the older/bare shapes.
        if isinstance(payload, dict):
            rooms = payload.get("room_census")
            if rooms is None:
                rooms = payload.get("rooms", [])
        else:
            rooms = payload if isinstance(payload, list) else []
        counts: dict[str, int] = {}
        skipped = 0
        for entry in rooms:
            name = entry.get("room_name") or entry.get("room_jid") or entry.get("jid") or ""
            if not name:
                skipped += 1
                continue
            counts[parse_mapped_room(str(name)).room] = int(
                entry.get("participants", entry.get("occupants", 0)) or 0
            )
        if rooms and not counts:
            raise ValueError(
                f"census listed {len(rooms)} rooms but none were parseable "
                f"({skipped} skipped); refusing to treat that as an empty server"
            )
        return counts

    def reconcile(self) -> ReconcileResult:
        result = ReconcileResult()
        try:
            counts = self.fetch()
        except (requests.RequestException, ValueError):
            # Failing to reach the census is not the same as the census saying
            # everything is empty, and must never be treated as such.
            logger.exception("census fetch failed; leaving state untouched")
            return result

        ours = {occupancy.room: occupancy for occupancy in self.store.all_occupancy()}
        now = self.clock()

        for room, count in counts.items():
            result.checked += 1
            occupancy = ours.get(room)
            if occupancy is None:
                # A room Prosody knows and we do not. Someone joined via a stale
                # link, or we missed a room-created event. Record it so the count
                # is at least right.
                result.unknown_to_us += 1
                self.store.room_created(room, None, now=now)
                self.store.replace_occupancy(room, count, now=now)
                continue
            if occupancy.count != count:
                result.corrected += 1
                self.store.replace_occupancy(room, count, now=now)

        for room, occupancy in ours.items():
            if room not in counts and occupancy.count:
                # We think people are in a room Prosody has never heard of. Our
                # state is stale; the room is gone.
                result.missing_from_census += 1
                self.store.room_destroyed(room, now=now)

        if not result.clean:
            logger.warning(
                "census reconciliation corrected %d rooms and dropped %d stale rooms",
                result.corrected,
                result.missing_from_census,
            )
        return result

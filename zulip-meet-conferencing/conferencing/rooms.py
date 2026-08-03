"""Room naming, shared with the Zulip calls patch.

The derivation here must stay byte-identical to `zerver/lib/jitsi_token.py` in
the phase two patch. The service does not mint tokens — it holds no signing key —
but it does need to answer "which conversation is this room?" when Prosody tells
it somebody joined, and the only way to do that without a lookup table is to
derive the same names from the same inputs.

There is a second, subtler job here. `event_sync` reports the room name Prosody
sees *after* `mod_muc_domain_mapper` has rewritten it, which is not the name that
went into the token. A room the patch called ``c-7f3a`` in tenant ``engineering``
arrives as ``[engineering]c-7f3a``. Parsing that back is not optional: without it
every occupancy event is attributed to nothing and the roster silently stays
empty, which looks exactly like a quiet channel.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass

ROOM_NAME_PREFIX = "c-"
ROOM_NAME_HASH_LENGTH = 16

#: Prosody's mapped form, e.g. "[engineering]c-7f3a91b2e4c8d5a6".
MAPPED_ROOM_RE = re.compile(r"^\[(?P<tenant>[^\]]+)\](?P<room>.+)$")


@dataclass(frozen=True)
class MappedRoom:
    tenant: str | None
    room: str

    @property
    def is_tenanted(self) -> bool:
        return self.tenant is not None


def parse_mapped_room(name: str) -> MappedRoom:
    """Split Prosody's mapped room name into tenant and room.

    Accepts either form, because a deployment without tenants reports the bare
    room name and there is no reason for the service to care which it is.

    >>> parse_mapped_room("[engineering]c-7f3a")
    MappedRoom(tenant='engineering', room='c-7f3a')
    >>> parse_mapped_room("c-7f3a")
    MappedRoom(tenant=None, room='c-7f3a')
    """
    # event_sync sometimes reports the full JID rather than the node.
    name = name.split("@", 1)[0]
    match = MAPPED_ROOM_RE.match(name)
    if match:
        return MappedRoom(tenant=match["tenant"], room=match["room"])
    return MappedRoom(tenant=None, room=name)


def channel_scope(realm_id: int, stream_id: int) -> str:
    return f"realm:{realm_id}|channel:{stream_id}"


def direct_message_scope(realm_id: int, user_ids: list[int]) -> str:
    return f"realm:{realm_id}|dm:" + ",".join(str(user_id) for user_id in sorted(set(user_ids)))


def derive_room_name(room_key: str, scope: str, epoch: int) -> str:
    """Identical to the calls patch. Changing one without the other breaks both."""
    digest = hmac.new(room_key.encode(), f"{scope}|{epoch}".encode(), hashlib.sha256).hexdigest()
    return ROOM_NAME_PREFIX + digest[:ROOM_NAME_HASH_LENGTH]

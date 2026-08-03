"""Turning call state into the message people actually read.

This is option 1 from the architecture document, section 5.6: the call message is
plain markdown that the service edits as things change. It requires no Zulip
patch, works identically on the Flutter mobile app, and depends on nothing marked
experimental. The live-roster widget is a later phase; this is the version that
lets everything underneath it be finished and verified first.

One rule shapes all of it: **never render a state we know to be wrong as though
it were right.** A roster that has drifted, or a room we have heard nothing about
for a long time, says so. An occupancy pipeline that silently reports nobody in a
call is indistinguishable from a quiet channel, and that is precisely the failure
this project has already been bitten by twice.
"""

from __future__ import annotations

from .state import Call, CallState, Occupancy

#: How long without an event before we stop trusting a roster we are showing.
STALE_AFTER_SECONDS = 15 * 60


def _names(occupancy: Occupancy | None, name_for: dict[int, str] | None = None) -> list[str]:
    if occupancy is None:
        return []
    names = []
    for occupant in occupancy.occupants.values():
        if name_for and occupant.zulip_user_id in name_for:
            names.append(name_for[occupant.zulip_user_id])
        elif occupant.display_name:
            names.append(occupant.display_name)
        else:
            names.append("someone")
    return sorted(names)


def roster_line(
    occupancy: Occupancy | None,
    *,
    now: float,
    name_for: dict[int, str] | None = None,
) -> str:
    if occupancy is None or occupancy.count == 0:
        return "_Nobody has joined yet._"

    if occupancy.drifted:
        # We know our roster disagrees with Prosody. Give the count we can still
        # justify and say the names are unreliable, rather than printing a list
        # we have reason to doubt.
        return f"**{occupancy.count} in the call.** _(roster resyncing)_"

    if occupancy.stale_for(now) > STALE_AFTER_SECONDS:
        return f"**{occupancy.count} in the call**, as of a while ago. _(no recent updates)_"

    names = _names(occupancy, name_for)
    if len(names) == 1:
        return f"**{names[0]}** is in the call."
    return f"**{len(names)} in the call:** " + ", ".join(names)


def call_message(
    call: Call,
    occupancy: Occupancy | None,
    join_url: str | None,
    *,
    now: float,
    name_for: dict[int, str] | None = None,
) -> str:
    """The full body of the call message, for create and for every edit."""
    if call.state is CallState.RINGING:
        head = "📞 **Calling…**"
    elif call.state is CallState.ACTIVE:
        head = "📹 **Call in progress**"
    elif call.state is CallState.ENDED:
        head = "**Call ended.**"
    elif call.state is CallState.DECLINED:
        head = "**Call declined.**"
    elif call.state is CallState.MISSED:
        head = "**Missed call.**"
    else:
        head = "**Call cancelled.**"

    lines = [head]

    if call.state in (CallState.RINGING, CallState.ACTIVE):
        lines.append("")
        lines.append(roster_line(occupancy, now=now, name_for=name_for))
        if join_url:
            lines.append("")
            lines.append(f"[Join the call]({join_url})")
    elif occupancy is not None and occupancy.count:
        # A terminal state with people still in the room means our state and
        # Prosody's disagree. Surface it rather than hiding it.
        lines.append("")
        lines.append(f"_Prosody still reports {occupancy.count} in the room._")

    return "\n".join(lines)

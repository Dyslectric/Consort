#!/usr/bin/env python3
"""A fake conferencing service, for developing against without Prosody or Jitsi.

The real service learns who is in a room from Prosody, which means testing
anything about occupancy normally needs a whole Jitsi deployment. This stands in
for it: it accepts the same notices Zulip sends, serves the same occupancy
endpoints Zulip polls, and pushes the same occupancy events back -- but it takes
its idea of who is in a room from you, over a small control API and a web page,
rather than from a real conference.

That is the point for lounges in particular. A room lives exactly as long as
somebody is in it, and Zulip deletes the row when this service says the room
emptied. Without something to say that, a room can be started but never seen to
end, which is the half of the lifecycle that is easiest to get wrong and hardest
to notice.

    Run it:

        ./scripts/stub_conferencing_service.py

    Then open http://localhost:8080/ for the control panel. Start a room in a
    lounge in Zulip and it appears there; press Leave on the last occupant and
    watch the row disappear from Zulip's sidebar.

What it is NOT: a Jitsi server. Zulip still mints a real token and the browser
still tries to open a real conference at whatever JITSI_SERVER_URL points to.
This only stands in for the bookkeeping half.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

DEFAULT_SECRETS_PATH = "/home/dave/zulip/zproject/dev-secrets.conf"
SECRET_RE = re.compile(r"^\s*jitsi_conferencing_secret\s*=\s*(\S+)\s*$", re.MULTILINE)

#: Prosody's mapped form, e.g. "[root]c-7f3a91b2e4c8d5a6". muc_domain_mapper
#: rewrites the room name before event_sync sees it, so the name in an event is
#: not the name that went into the token. Parsing it back is not optional:
#: without it every event is attributed to nothing and every roster stays empty,
#: which looks exactly like a quiet lounge.
MAPPED_ROOM_RE = re.compile(r"^\[(?P<tenant>[^\]]+)\](?P<room>.+)$")


def parse_mapped_room(name: str) -> str:
    """The bare room name, from whichever form Prosody reported.

    Mirrors conferencing/rooms.py. Accepts the untenanted form too, because a
    deployment without tenants reports the bare name and there is no reason to
    care which it is.
    """
    name = name.split("@", 1)[0]
    match = MAPPED_ROOM_RE.match(name)
    return match["room"] if match else name


def read_secret_from_zulip(path: str) -> str:
    """Pick the shared secret out of Zulip's dev secrets, so that starting this
    needs no arguments in the usual case. A mismatched secret is the single most
    likely reason for a silent do-nothing, so it is worth removing the chance."""
    try:
        with open(path) as f:
            match = SECRET_RE.search(f.read())
    except OSError:
        return ""
    return match.group(1) if match else ""


@dataclass
class Room:
    """One live call, as this service imagines it."""

    room: str
    tenant: str | None = None
    realm_id: int | None = None
    stream_id: int | None = None
    lounge_room_id: int | None = None
    user_ids: list[int] = field(default_factory=list)
    #: occupant key -> zulip user id (or None for somebody we cannot name). The
    #: key is Prosody's occupant JID where there is one, because that is what a
    #: "left" event names; where there is not — somebody added by hand from the
    #: panel — it is just their display name.
    occupants: dict[str, int | None] = field(default_factory=dict)
    #: occupant key -> display name, for the keys that are opaque JIDs.
    names: dict[str, str] = field(default_factory=dict)
    #: Monotonic time the room was first heard of, for --empty-after.
    started_at: float | None = None

    def display(self, key: str) -> str:
        return self.names.get(key, key)

    @property
    def kind(self) -> str:
        if self.lounge_room_id is not None:
            return "lounge room"
        if self.stream_id is not None:
            return "channel"
        return "direct message"

    def roster(self) -> list[dict[str, Any]]:
        people = [
            {"name": self.display(key), "user_id": user_id}
            for key, user_id in self.occupants.items()
        ]
        # Sorted by name so the sidebar's order is stable rather than join-order
        # jitter, matching what the real service does.
        people.sort(key=lambda person: str(person["name"]).lower())
        return people

    def occupancy_payload(self) -> dict[str, Any]:
        base: dict[str, Any] = {
            "active": True,
            "count": len(self.occupants),
            "occupants": self.roster(),
            "drifted": False,
            "realm_id": self.realm_id,
        }
        if self.lounge_room_id is not None:
            base["stream_id"] = self.stream_id
            base["lounge_room_id"] = self.lounge_room_id
        elif self.stream_id is not None:
            base["stream_id"] = self.stream_id
        else:
            base["user_ids"] = sorted(self.user_ids)
        return base


class Store:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.rooms: dict[str, Room] = {}

    def upsert(self, room: Room) -> Room:
        with self._lock:
            existing = self.rooms.get(room.room)
            if existing is not None:
                return existing
            self.rooms[room.room] = room
            return room

    def get(self, name: str) -> Room | None:
        with self._lock:
            return self.rooms.get(name)

    def drop(self, name: str) -> None:
        with self._lock:
            self.rooms.pop(name, None)

    def all(self) -> list[Room]:
        with self._lock:
            return list(self.rooms.values())


class Pusher:
    """Sends occupancy to Zulip's internal hook, exactly as the real service does."""

    def __init__(self, zulip_url: str, secret: str, host_header: str) -> None:
        self.url = zulip_url.rstrip("/") + "/api/internal/jitsi/occupancy"
        self.secret = secret
        self.host_header = host_header

    def push(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        request = urllib.request.Request(
            self.url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.secret}",
                "Host": self.host_header,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                log(f"  -> zulip {response.status}")
        except urllib.error.HTTPError as err:
            # A 404 here almost always means the bearer secret does not match:
            # the hook answers 404 rather than 401 so as not to confirm it exists.
            log(f"  -> zulip HTTP {err.code} ({'bad secret?' if err.code == 404 else err.reason})")
        except Exception as err:  # noqa: BLE001
            log(f"  -> zulip unreachable: {err}")


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


NOT_FOUND_PAGE = """<!doctype html>
<title>No such room</title>
<style> body {{ font: 14px/1.6 system-ui, sans-serif; margin: 3rem auto; max-width: 34rem; }} </style>
<h1>Nothing happened</h1>
<p>This service has never heard of the room <code>{room}</code>, so there was
nothing to do.</p>
<p>That usually means the page you pressed the button on was out of date: the
room had already ended, or this service was restarted since the page was drawn.
Its memory of rooms does not survive a restart.</p>
<p><a href="/">Back to the current rooms</a></p>
"""

PANEL = """<!doctype html>
<title>Stub conferencing service</title>
<meta http-equiv="refresh" content="5">
<style>
 body {{ font: 14px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 46rem; }}
 h1 {{ font-size: 1.2rem; }}
 .room {{ border: 1px solid #ccc; border-radius: 6px; padding: .75rem 1rem; margin: .75rem 0; }}
 .kind {{ color: #666; font-size: .85em; }}
 .occ {{ display: flex; align-items: center; gap: .5rem; margin: .25rem 0; }}
 button {{ font: inherit; padding: .15rem .6rem; cursor: pointer; }}
 .empty {{ color: #666; }}
 form {{ display: inline; }}
 input {{ font: inherit; padding: .15rem .4rem; }}
</style>
<h1>Stub conferencing service</h1>
<p class="empty">Zulip pushes rooms here when somebody starts a call. Leaving the
last occupant ends the room, which is what makes Zulip delete a lounge room.</p>
{rooms}
<p><a href="/">Refresh</a></p>
"""

ROOM_BLOCK = """<div class="room">
  <div><strong>{title}</strong> <span class="kind">({kind}, room {room})</span></div>
  {occupants}
  <form method="post" action="/stub/join">
    <input type="hidden" name="room" value="{room}">
    <input name="name" placeholder="another person" required>
    <input name="user_id" placeholder="Zulip user id (optional)" size="20">
    <button type="submit">Join</button>
  </form>
  <form method="post" action="/stub/end">
    <input type="hidden" name="room" value="{room}">
    <button type="submit">End room</button>
  </form>
</div>"""

OCCUPANT_BLOCK = """<div class="occ"><span>{label}</span>
  <form method="post" action="/stub/leave">
    <input type="hidden" name="room" value="{room}">
    <input type="hidden" name="name" value="{key}">
    <button type="submit">Leave</button>
  </form></div>"""


class Handler(BaseHTTPRequestHandler):
    store: Store
    pusher: Pusher
    secret: str
    auto_join: bool = True

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # The default writes a line per request, which drowns out the events
        # that actually matter here.
        pass

    # -- helpers ----------------------------------------------------------

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {self.secret}"

    def _json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, body: str, status: int = 200) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _redirect(self) -> None:
        self.send_response(303)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("application/x-www-form-urlencoded"):
            return {k: v[0] for k, v in parse_qs(raw.decode()).items()}
        try:
            parsed = json.loads(raw or b"{}")
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    # -- the room's life --------------------------------------------------

    def _push(self, room: Room) -> None:
        self.pusher.push(room.occupancy_payload())

    def _end(self, room: Room) -> None:
        """Everybody has gone. Tell Zulip the room is over and forget it.

        The payload is the same shape as any other occupancy push, with active
        false and a count of zero -- that combination is what Zulip reads as
        "this room has ended", and for a lounge room it deletes the row.
        """
        payload = room.occupancy_payload()
        payload.update({"active": False, "count": 0, "occupants": []})
        log(f"[end] {room.kind} {room.room}")
        self.pusher.push(payload)
        self.store.drop(room.room)

    # -- what Zulip calls -------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path

        if path == "/api/v1/jitsi/calls/created":
            if not self._authorized():
                self._json({"error": "unauthorized"}, status=404)
                return
            notice = self._body()
            name = str(notice.get("room") or "")
            if not name:
                self._json({"error": "no room"}, status=400)
                return
            room = self.store.upsert(
                Room(
                    room=name,
                    tenant=notice.get("tenant"),
                    realm_id=notice.get("realm_id"),
                    stream_id=notice.get("stream_id"),
                    lounge_room_id=notice.get("lounge_room_id"),
                    user_ids=[int(u) for u in (notice.get("user_ids") or [])],
                    started_at=time.monotonic(),
                )
            )
            # Whoever minted the token is *probably* about to walk through the
            # door, so with no Prosody to say otherwise we assume they did.
            # It is a fiction, and it is the one that makes a room appear to hold
            # somebody who is not in it: minting a token is not joining a
            # conference, and a person who never got through the door -- or who
            # hung up -- is still shown as present. Switch it off with
            # --no-auto-join once Prosody is reporting real occupancy.
            if self.auto_join:
                initiator = str(notice.get("initiator_name") or "Someone")
                initiator_id = notice.get("initiator_id")
                room.occupants[initiator] = (
                    int(initiator_id) if isinstance(initiator_id, int) else None
                )
                log(f"[join] {room.kind} {room.room}: {initiator} (assumed)")
            self._push(room)
            self._json({"ok": True})
            return

        # The four sinks Prosody's event_sync component posts to, with the same
        # paths, the same bearer and the same payload shapes the real service
        # accepts. With these wired up the occupancy is real: joining and leaving
        # a conference in the browser drives the sidebar, and nothing here has to
        # pretend on your behalf.
        if path in (
            "/api/v1/jitsi/events/room/created",
            "/api/v1/jitsi/events/room/destroyed",
            "/api/v1/jitsi/events/occupant/joined",
            "/api/v1/jitsi/events/occupant/left",
        ):
            if not self._authorized():
                # 401 rather than 404: this one is documented, and Prosody's
                # retry loop should be able to tell "wrong secret" from "wrong
                # path" in its log.
                self._json({"error": "unauthorized"}, status=401)
                return
            self._prosody_event(path.rsplit("/api/v1/jitsi", 1)[1], self._body())
            return

        if path.startswith("/stub/"):
            self._control(path)
            return

        self._json({"error": "not found"}, status=404)

    def _prosody_event(self, kind: str, payload: dict[str, Any]) -> None:
        """One room/occupant lifecycle event from Prosody.

        An unknown room is not an error. Prosody reports rooms this service never
        minted -- a stale link, another integration, someone opening a room by
        hand -- and a sink that fails on unexpected input just earns a retry
        storm. Those are acknowledged and dropped.
        """
        name = parse_mapped_room(str(payload.get("room_name") or payload.get("room_jid") or ""))
        room = self.store.get(name)
        if room is None:
            log(f"[prosody] {kind} for unknown room {name!r}; ignored")
            self._json({"ok": True, "ignored": "unknown room"})
            return

        occupant = payload.get("occupant") or {}
        jid = str(occupant.get("occupant_jid") or occupant.get("occupant_jif") or "")
        # Identity in the body is a lookup key, never an assertion: this endpoint
        # is reachable by anyone who has the bearer, so the name and id are used
        # to label a roster entry and for nothing else.
        display = str(occupant.get("name") or "someone")
        raw_id = occupant.get("id")
        user_id = int(raw_id) if isinstance(raw_id, int) else (
            int(raw_id) if isinstance(raw_id, str) and raw_id.isdigit() else None
        )

        if kind == "/events/occupant/joined":
            room.occupants[jid or display] = user_id
            room.names[jid or display] = display
            log(f"[prosody] join {name}: {display} (now {len(room.occupants)})")
            self._push(room)
        elif kind == "/events/occupant/left":
            key = jid or display
            # A "left" event names only the JID, so who it was has to come from
            # what we recorded when they arrived.
            gone = room.display(key)
            room.occupants.pop(key, None)
            room.names.pop(key, None)
            log(f"[prosody] leave {name}: {gone} (now {len(room.occupants)})")
            self._push(room)
        elif kind == "/events/room/destroyed":
            log(f"[prosody] destroyed {name}")
            self._end(room)
        else:
            # room/created: nothing to say yet, and saying "active with nobody in
            # it" would read as a call that failed.
            log(f"[prosody] created {name}")
        self._json({"ok": True})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/v1/jitsi/occupancy_all":
            if not self._authorized():
                self._json({"error": "unauthorized"}, status=404)
                return
            self._json({"rooms": [room.occupancy_payload() for room in self.store.all()]})
            return

        if path == "/api/v1/jitsi/occupancy":
            if not self._authorized():
                self._json({"error": "unauthorized"}, status=404)
                return
            wanted = parse_qs(parsed.query).get("stream_id", [""])[0]
            for room in self.store.all():
                # A lounge room is not the channel's own call, so it must not
                # answer for one: Zulip asks this per channel.
                if room.lounge_room_id is None and str(room.stream_id) == wanted:
                    self._json(room.occupancy_payload())
                    return
            self._json(
                {
                    "stream_id": int(wanted) if wanted.isdigit() else None,
                    "active": False,
                    "count": 0,
                    "occupants": [],
                    "drifted": False,
                }
            )
            return

        if path in ("/", "/index.html"):
            self._html(self._panel())
            return

        self._json({"error": "not found"}, status=404)

    # -- what you call ----------------------------------------------------

    def _control(self, path: str) -> None:
        data = self._body()
        wants_html = "text/html" in self.headers.get("Accept", "")

        if path == "/stub/reset":
            for room in self.store.all():
                self._end(room)
            self._redirect() if wants_html else self._json({"ok": True})
            return

        # Addressable by the Jitsi room name or by the lounge room id. The id is
        # the one you have when scripting against Zulip, and the one that stays
        # meaningful if the panel in front of you is out of date.
        room = self.store.get(str(data.get("room") or ""))
        if room is None and str(data.get("lounge_room_id") or "").isdigit():
            wanted = int(data["lounge_room_id"])
            room = next(
                (r for r in self.store.all() if r.lounge_room_id == wanted), None
            )
        if room is None:
            # Said out loud rather than redirected away silently. A stale panel
            # -- one open from before this service restarted, or from before the
            # room it names ended -- posts a room this process has never heard
            # of, and quietly doing nothing looks exactly like a button that does
            # not work.
            if wants_html:
                self._html(
                    NOT_FOUND_PAGE.format(room=str(data.get("room") or "?")), status=404
                )
            else:
                self._json({"error": "no such room"}, status=404)
            return

        if path == "/stub/join":
            name = str(data.get("name") or "Someone")
            # Coerced from a string as well as an int: a form post carries
            # everything as text, and the panel's join field is how you give a
            # fake occupant a real Zulip identity. That matters for anything
            # that asks *who* is in a call rather than how many -- Zulip's
            # "require a moderator to join" reads exactly this id, and an
            # occupant with none is nobody in particular.
            raw_user_id = data.get("user_id")
            user_id: int | None = None
            if isinstance(raw_user_id, int):
                user_id = raw_user_id
            elif isinstance(raw_user_id, str) and raw_user_id.strip().isdigit():
                user_id = int(raw_user_id.strip())
            room.occupants[name] = user_id
            log(f"[join] {room.kind} {room.room}: {name} (now {len(room.occupants)})")
            self._push(room)
        elif path == "/stub/leave":
            name = str(data.get("name") or "")
            room.occupants.pop(name, None)
            log(f"[leave] {room.kind} {room.room}: {name} (now {len(room.occupants)})")
            if room.occupants:
                self._push(room)
            else:
                self._end(room)
        elif path == "/stub/end":
            self._end(room)
        else:
            self._json({"error": "not found"}, status=404)
            return

        self._redirect() if wants_html else self._json({"ok": True})

    def _panel(self) -> str:
        rooms = self.store.all()
        if not rooms:
            return PANEL.format(rooms='<p class="empty">No live rooms.</p>')
        blocks = []
        for room in rooms:
            title = (
                f"lounge room #{room.lounge_room_id}"
                if room.lounge_room_id is not None
                else (f"channel #{room.stream_id}" if room.stream_id else "direct message")
            )
            occupants = (
                "".join(
                    OCCUPANT_BLOCK.format(
                        label=room.display(key), key=key, room=room.room
                    )
                    for key in sorted(room.occupants, key=lambda k: room.display(k).lower())
                )
                or '<div class="empty">nobody</div>'
            )
            blocks.append(
                ROOM_BLOCK.format(
                    title=title, kind=room.kind, room=room.room, occupants=occupants
                )
            )
        return PANEL.format(rooms="".join(blocks))


def start_auto_empty(store: Store, pusher: Pusher, after: float) -> None:
    """Let rooms end on their own, on a timer.

    Hanging up in the browser cannot reach this service: there is no Prosody here
    to notice you have gone, and the real one is the only thing that ever knows.
    Without something standing in for that, the natural way to test -- join a
    room, hang up, watch it disappear -- has no second half. This is that second
    half, bluntly: after a while, everybody is deemed to have left.
    """

    def loop() -> None:
        while True:
            time.sleep(1)
            now = time.monotonic()
            for room in store.all():
                if room.started_at is not None and now - room.started_at >= after:
                    payload = room.occupancy_payload()
                    payload.update({"active": False, "count": 0, "occupants": []})
                    log(f"[auto-empty] {room.kind} {room.room} after {after:g}s")
                    pusher.push(payload)
                    store.drop(room.room)

    threading.Thread(target=loop, daemon=True).start()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument(
        "--zulip",
        default="http://localhost:9991",
        help="where to push occupancy (default: the dev server)",
    )
    parser.add_argument(
        "--host-header",
        default="localhost",
        help="Host header for pushes; must be a host Zulip answers for",
    )
    parser.add_argument(
        "--secret",
        default=None,
        help=f"shared secret; read from {DEFAULT_SECRETS_PATH} when omitted",
    )
    parser.add_argument("--secrets-path", default=DEFAULT_SECRETS_PATH)
    parser.add_argument(
        "--bind",
        default="127.0.0.1",
        help="address to listen on; use 0.0.0.0 to let a Prosody container reach it",
    )
    parser.add_argument(
        "--no-auto-join",
        dest="auto_join",
        action="store_false",
        help=(
            "stop pretending whoever minted a token has joined the conference. "
            "Use this once Prosody's event_sync is posting to the four sinks: "
            "the assumption is what makes a room appear to hold someone who is "
            "not in it"
        ),
    )
    parser.add_argument(
        "--empty-after",
        type=float,
        default=0,
        help=(
            "seconds after which a room empties by itself (0 = never). Hanging up "
            "in the browser cannot reach this service -- there is no Prosody to "
            "notice -- so this is how to watch a room end without pressing Leave"
        ),
    )
    args = parser.parse_args()

    secret = args.secret or read_secret_from_zulip(args.secrets_path)
    if not secret:
        parser.error(
            "no shared secret: pass --secret, or point --secrets-path at Zulip's "
            "dev-secrets.conf. Without a matching one, Zulip ignores everything "
            "this sends and this ignores everything Zulip sends."
        )

    Handler.store = Store()
    Handler.pusher = Pusher(args.zulip, secret, args.host_header)
    Handler.secret = secret
    Handler.auto_join = args.auto_join

    if args.empty_after > 0:
        start_auto_empty(Handler.store, Handler.pusher, args.empty_after)

    server = ThreadingHTTPServer((args.bind, args.port), Handler)
    log(f"stub conferencing service on http://{args.bind}:{args.port}/")
    log(f"pushing occupancy to {args.zulip} (Host: {args.host_header})")
    log(
        "occupancy: Prosody's event_sync sinks are live at "
        f"http://{args.bind}:{args.port}/api/v1/jitsi/events/..."
    )
    if args.auto_join:
        log("assuming whoever mints a token joins (--no-auto-join once Prosody is wired)")
    if args.empty_after > 0:
        log(f"rooms empty themselves after {args.empty_after:g}s")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("bye")


if __name__ == "__main__":
    main()

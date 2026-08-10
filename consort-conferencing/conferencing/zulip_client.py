"""A minimal Zulip API client and event-queue loop.

Only what the service needs, so that the failure modes are visible rather than
buried in a general-purpose library.

The loop is the interesting part. Zulip's event system is a long poll: register
once for a queue, then repeatedly ask for events newer than the last one you
processed. Three things must be handled or the service will look fine and be
deaf:

* **Heartbeats.** The server sends one roughly every minute. They carry no
  information and must not advance any application state, but they do prove the
  connection is alive.
* **The poll timeout.** `POST /register` returns
  `event_queue_longpoll_timeout_seconds`, guaranteed to be at least the heartbeat
  interval. Using anything shorter turns normal operation into a stream of
  spurious timeouts.
* **`BAD_EVENT_QUEUE_ID`.** Queues are garbage collected after
  `idle_queue_timeout` (ten minutes by default). Recovery is to register a new
  queue *and reconcile*, because everything that happened during the gap was
  missed. Re-registering without reconciling is the quiet failure: the service
  reconnects, reports no error, and is simply wrong about the world.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Callable, Iterable

import requests

logger = logging.getLogger(__name__)

DEFAULT_POLL_TIMEOUT_SECONDS = 90.0


class ZulipError(RuntimeError):
    pass


class BadEventQueueId(ZulipError):
    """The queue we were polling no longer exists."""


class ZulipClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        api_key: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = (email, api_key)
        self.timeout = timeout
        self._session = session or requests.Session()

    # -- REST -------------------------------------------------------------

    def _call(self, method: str, path: str, *, timeout: float | None = None, **kwargs) -> dict:
        response = self._session.request(
            method,
            f"{self.base_url}/api/v1{path}",
            auth=self.auth,
            timeout=timeout or self.timeout,
            **kwargs,
        )
        try:
            body = response.json()
        except ValueError:
            raise ZulipError(f"{method} {path} returned non-JSON ({response.status_code})")
        if body.get("result") != "success":
            if body.get("code") == "BAD_EVENT_QUEUE_ID":
                raise BadEventQueueId(body.get("msg", "queue expired"))
            raise ZulipError(f"{method} {path}: {body.get('msg', body)}")
        return body

    def send_message(self, *, to: Any, content: str, topic: str | None = None, type_: str) -> int:
        # Zulip wants `to` as a JSON-encoded value: a list of user IDs for a
        # direct message, a channel ID for a stream message. A raw Python list
        # handed to requests is form-encoded as repeated fields and rejected.
        recipients = json.dumps(to) if isinstance(to, (list, tuple)) else to
        data: dict[str, Any] = {"type": type_, "to": recipients, "content": content}
        if topic is not None:
            data["topic"] = topic
        return int(self._call("POST", "/messages", data=data)["id"])

    def update_message(self, message_id: int, content: str) -> None:
        self._call("PATCH", f"/messages/{message_id}", data={"content": content})

    def add_submessage(self, message_id: int, msg_type: str, content: str) -> None:
        """Attach a free-form payload to a message.

        Zulip broadcasts this to exactly the users who can see the parent
        message, which is where the entitlement filtering for occupancy comes
        from — we never compute a recipient list ourselves. Only the message's
        author may do this, which is why the service posts the call message.
        """
        self._call(
            "POST",
            "/submessage",
            data={"message_id": message_id, "msg_type": msg_type, "content": content},
        )

    def get_subscribers(self, stream_id: int) -> list[int]:
        return list(self._call("GET", f"/streams/{stream_id}/members")["subscribers"])

    # -- events -----------------------------------------------------------

    def register(self, event_types: Iterable[str]) -> dict:
        # event_types must be a JSON-encoded array string, not a Python list;
        # requests would otherwise send repeated form fields and Zulip rejects it
        # with "event_types is not valid JSON".
        return self._call(
            "POST",
            "/register",
            data={"event_types": json.dumps(list(event_types)), "apply_markdown": "false"},
        )

    def get_events(self, queue_id: str, last_event_id: int, *, timeout: float) -> list[dict]:
        return self._call(
            "GET",
            "/events",
            params={"queue_id": queue_id, "last_event_id": last_event_id},
            # Slightly longer than the server's own long-poll timeout, so that a
            # normal empty poll comes back as an empty result rather than as a
            # client-side timeout we would have to distinguish from a real one.
            timeout=timeout + 10,
        )["events"]


class EventLoop:
    """Polls a Zulip event queue and dispatches events, recovering from loss."""

    def __init__(
        self,
        client: ZulipClient,
        handler: Callable[[dict], None],
        *,
        event_types: Iterable[str] = ("message", "submessage"),
        on_reconnect: Callable[[], None] | None = None,
        backoff_seconds: float = 2.0,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.handler = handler
        self.event_types = list(event_types)
        self.on_reconnect = on_reconnect
        self.backoff_seconds = backoff_seconds
        self.clock = clock
        self.sleep = sleep

        self._stop = threading.Event()
        self.queue_id: str | None = None
        self.last_event_id = -1
        self.poll_timeout = DEFAULT_POLL_TIMEOUT_SECONDS
        self.heartbeats = 0
        self.reconnects = 0

    def stop(self) -> None:
        self._stop.set()

    def _register(self) -> None:
        response = self.client.register(self.event_types)
        self.queue_id = response["queue_id"]
        self.last_event_id = response.get("last_event_id", -1)
        self.poll_timeout = float(
            response.get("event_queue_longpoll_timeout_seconds", DEFAULT_POLL_TIMEOUT_SECONDS)
        )
        logger.info("registered event queue %s", self.queue_id)

    def run_once(self) -> int:
        """One poll. Returns the number of non-heartbeat events dispatched."""
        if self.queue_id is None:
            self._register()
            # A fresh queue means we know nothing about what happened before it
            # existed. Reconcile before processing anything.
            self.reconnects += 1
            if self.on_reconnect is not None:
                self.on_reconnect()
            return 0

        try:
            events = self.client.get_events(
                self.queue_id, self.last_event_id, timeout=self.poll_timeout
            )
        except BadEventQueueId:
            logger.warning("event queue %s expired; re-registering", self.queue_id)
            self.queue_id = None
            return 0

        dispatched = 0
        for event in events:
            self.last_event_id = max(self.last_event_id, int(event.get("id", self.last_event_id)))
            if event.get("type") == "heartbeat":
                self.heartbeats += 1
                continue
            try:
                self.handler(event)
            except Exception:
                # One malformed event must not stop the loop; the alternative is
                # a service that goes deaf on the first surprise.
                logger.exception("handler failed for event %s", event.get("type"))
            dispatched += 1
        return dispatched

    def run_forever(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except (ZulipError, requests.RequestException):
                logger.exception("event loop error; backing off")
                self.queue_id = None
                self.sleep(self.backoff_seconds)

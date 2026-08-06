"""Posting and editing call messages through Zulip's internal core hook.

The service originally posted call messages through Zulip's **public API** as a bot
(see ``zulip_client.ZulipClient.send_message``). That has three walls this client
exists to get past — all of them documented in the fork's ``jitsi_hook.py``:

* a bot always sends **as itself**, so a DM/group message lands in a
  bot-inclusive conversation, never the participants' real one;
* a bot belongs to **one realm**, so multi-realm posting is impossible; and
* a bot cannot **edit** a message past the realm's content-edit time limit, so a
  call open longer than ten minutes freezes its roster.

Instead of the public API, this client calls the fork's internal, secret-authed
endpoints, which run on Zulip's server-privileged internal send/edit functions:

    POST {base}/message         -> {"message_id": N}   channel: Notification Bot
                                                        DM/group: the initiator
    POST {base}/message/update  -> {}                  edit by id, any realm

The service stays the brain: it renders the body (``render.py``) and decides when
to post or edit. This client is only the wire to Zulip, and it holds **no bot API
key** — just the shared bearer secret and the internal URL.
"""

from __future__ import annotations

import logging
from typing import Iterable

import requests

logger = logging.getLogger(__name__)


class HookError(RuntimeError):
    """The core hook could not be reached, or refused the request."""


class HookClient:
    def __init__(
        self,
        base_url: str,
        secret: str,
        *,
        session: requests.Session | None = None,
        timeout: float = 5.0,
        verify: bool = True,
        host_header: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._secret = secret
        self.timeout = timeout
        self._verify = verify
        #: Overrides the Host header when reaching Zulip by an internal name that
        #: is not in Django's ALLOWED_HOSTS (which would otherwise 400).
        self._host_header = host_header
        self._session = session or requests.Session()
        if not verify:
            # The hook is reached over the internal network by a name the public
            # cert does not cover; the bearer secret is the authentication. Quiet
            # the per-request warning rather than spam the log on every edit.
            try:
                import urllib3

                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except Exception:
                pass

    def _post(self, path: str, payload: dict) -> dict:
        headers = {"Authorization": f"Bearer {self._secret}"}
        if self._host_header:
            headers["Host"] = self._host_header
        response = self._session.post(
            self.base_url + path,
            json=payload,
            headers=headers,
            # The target is a trusted internal Zulip endpoint. If the service is
            # ever run behind an outbound proxy, bypass it for this hop the same
            # way Zulip's own side bypasses smokescreen for the reverse call.
            proxies={"http": None, "https": None},
            verify=self._verify,
            timeout=self.timeout,
        )
        try:
            body = response.json()
        except ValueError:
            raise HookError(f"POST {path} returned non-JSON ({response.status_code})")
        if response.status_code >= 400 or body.get("result") == "error":
            raise HookError(f"POST {path}: {body.get('msg', response.status_code)}")
        return body

    def send(
        self,
        *,
        realm_id: int | None,
        stream_id: int | None,
        user_ids: Iterable[int] | None,
        initiator_id: int | None,
        topic: str,
        content: str,
    ) -> int:
        """Post a call message and return its id.

        A channel call (``stream_id``) is authored by the realm's Notification
        Bot under ``topic``. A DM/group call (``user_ids``) is authored by
        ``initiator_id`` into the real conversation. Exactly one of the two must
        be set, which is the caller's (``Call``) invariant, not this client's to
        second-guess.
        """
        payload: dict = {"realm_id": realm_id, "content": content}
        if stream_id is not None:
            payload["stream_id"] = stream_id
            payload["topic"] = topic
        else:
            payload["user_ids"] = list(user_ids or [])
            payload["initiator_id"] = initiator_id
        body = self._post("/message", payload)
        message_id = body.get("message_id")
        if not isinstance(message_id, int):
            raise HookError("hook did not return a message_id")
        return message_id

    def update(self, message_id: int, content: str) -> None:
        """Edit a previously posted call message. Realm-agnostic: the message id
        is globally unique, so the hook needs nothing but the id and new body."""
        self._post("/message/update", {"message_id": message_id, "content": content})

    def occupancy(
        self,
        *,
        realm_id: int | None,
        stream_id: int | None = None,
        user_ids: list[int] | None = None,
        active: bool,
        count: int,
        occupants: list[dict],
    ) -> None:
        """Push a conversation's live occupancy to Zulip, which fans it out as a
        ``jitsi_occupancy`` client event so the sidebar updates instantly rather
        than on its slow poll. Identified by ``stream_id`` for a channel or
        ``user_ids`` for a DM/group; Zulip sends the event to that channel's
        subscribers or to those participants. The caller treats it as
        best-effort."""
        payload: dict[str, Any] = {
            "realm_id": realm_id,
            "active": active,
            "count": count,
            "occupants": occupants,
        }
        if stream_id is not None:
            payload["stream_id"] = stream_id
        else:
            payload["user_ids"] = user_ids or []
        self._post("/occupancy", payload)

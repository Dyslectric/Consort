"""The wire to Zulip's core message hook.

HookClient makes no decisions — the service does. These tests pin the one thing
it is responsible for: putting the right JSON on the right path with the bearer
secret, and turning the hook's replies (and failures) into a message id or a
clear error rather than a silent wrong answer.
"""

import pytest

from conferencing.hook_client import HookClient, HookError

_NON_JSON = object()


class FakeResponse:
    def __init__(self, payload, status: int = 200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if self._payload is _NON_JSON:
            raise ValueError("not json")
        return self._payload


class FakeSession:
    """Captures the single POST HookClient makes and returns a canned response."""

    def __init__(self, response: FakeResponse):
        self.response = response
        self.calls: list[dict] = []

    def post(self, url, *, json=None, headers=None, proxies=None, timeout=None, verify=None):
        self.calls.append(
            {
                "url": url, "json": json, "headers": headers,
                "proxies": proxies, "timeout": timeout, "verify": verify,
            }
        )
        return self.response


def _client(
    response: FakeResponse, *, verify: bool = True, host_header: str | None = None
) -> tuple[HookClient, FakeSession]:
    session = FakeSession(response)
    client = HookClient(
        "http://zulip/api/internal/jitsi/",
        "s3cret",
        session=session,
        verify=verify,
        host_header=host_header,
    )
    return client, session


class TestSend:
    def test_a_channel_send_posts_stream_id_topic_and_returns_the_id(self):
        client, session = _client(FakeResponse({"result": "success", "message_id": 555}))
        message_id = client.send(
            realm_id=1, stream_id=7, user_ids=None, initiator_id=None,
            topic="Calls", content="hi",
        )
        assert message_id == 555
        call = session.calls[0]
        # Trailing slash on the base URL is trimmed; the path is /message.
        assert call["url"] == "http://zulip/api/internal/jitsi/message"
        assert call["json"] == {"realm_id": 1, "content": "hi", "stream_id": 7, "topic": "Calls"}
        assert call["headers"]["Authorization"] == "Bearer s3cret"
        # Trusted internal target: outbound proxy is bypassed.
        assert call["proxies"] == {"http": None, "https": None}
        assert call["verify"] is True  # default: verify the cert

    def test_verify_false_is_forwarded_for_the_internal_self_signed_hop(self):
        client, session = _client(
            FakeResponse({"result": "success", "message_id": 1}), verify=False
        )
        client.send(
            realm_id=1, stream_id=7, user_ids=None, initiator_id=None,
            topic="Calls", content="hi",
        )
        assert session.calls[0]["verify"] is False

    def test_host_header_is_sent_when_set(self):
        # Reaching Zulip by the container name needs the real Host or Django 400s.
        client, session = _client(
            FakeResponse({"result": "success", "message_id": 1}),
            host_header="zulip.davig01.net",
        )
        client.send(
            realm_id=1, stream_id=7, user_ids=None, initiator_id=None,
            topic="Calls", content="hi",
        )
        assert session.calls[0]["headers"]["Host"] == "zulip.davig01.net"

    def test_no_host_header_by_default(self):
        client, session = _client(FakeResponse({"result": "success", "message_id": 1}))
        client.update(5, "x")
        assert "Host" not in session.calls[0]["headers"]

    def test_a_dm_send_posts_user_ids_and_initiator_not_stream(self):
        client, session = _client(FakeResponse({"result": "success", "message_id": 9}))
        message_id = client.send(
            realm_id=2, stream_id=None, user_ids=[3, 9], initiator_id=3,
            topic="ignored", content="hi",
        )
        assert message_id == 9
        body = session.calls[0]["json"]
        assert body == {"realm_id": 2, "content": "hi", "user_ids": [3, 9], "initiator_id": 3}
        assert "stream_id" not in body and "topic" not in body

    def test_a_non_integer_message_id_is_an_error_not_a_silent_zero(self):
        client, _ = _client(FakeResponse({"result": "success"}))  # no message_id
        with pytest.raises(HookError):
            client.send(
                realm_id=1, stream_id=7, user_ids=None, initiator_id=None,
                topic="Calls", content="hi",
            )

    def test_an_error_status_raises(self):
        client, _ = _client(FakeResponse({"result": "error", "msg": "unknown realm"}, status=400))
        with pytest.raises(HookError):
            client.send(
                realm_id=1, stream_id=7, user_ids=None, initiator_id=None,
                topic="Calls", content="hi",
            )

    def test_a_non_json_reply_raises(self):
        client, _ = _client(FakeResponse(_NON_JSON, status=200))
        with pytest.raises(HookError):
            client.send(
                realm_id=1, stream_id=7, user_ids=None, initiator_id=None,
                topic="Calls", content="hi",
            )


class TestUpdate:
    def test_update_posts_id_and_content_to_the_update_path(self):
        client, session = _client(FakeResponse({"result": "success"}))
        client.update(555, "new body")
        call = session.calls[0]
        assert call["url"] == "http://zulip/api/internal/jitsi/message/update"
        assert call["json"] == {"message_id": 555, "content": "new body"}
        assert call["headers"]["Authorization"] == "Bearer s3cret"

    def test_update_raises_on_an_unknown_message(self):
        client, _ = _client(FakeResponse({"result": "error", "msg": "unknown message"}, status=400))
        with pytest.raises(HookError):
            client.update(999, "body")

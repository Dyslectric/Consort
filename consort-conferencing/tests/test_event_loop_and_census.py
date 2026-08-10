"""The Zulip event loop and census reconciliation.

Both are about the same thing: noticing when we have stopped knowing what is
happening, rather than carrying on confidently.
"""

import pytest
import requests

from conferencing.census import Census
from conferencing.render import call_message, roster_line
from conferencing.state import Call, CallState, Occupant, Store
from conferencing.zulip_client import BadEventQueueId, EventLoop, ZulipClient, ZulipError


class FakeClient:
    """Stands in for ZulipClient, scripted per test."""

    def __init__(self, batches, *, register_response=None):
        self.batches = list(batches)
        self.registered = 0
        self.register_response = register_response or {
            "queue_id": "q1",
            "last_event_id": -1,
            "event_queue_longpoll_timeout_seconds": 90,
        }
        self.poll_timeouts = []

    def register(self, event_types):
        self.registered += 1
        return dict(self.register_response, queue_id=f"q{self.registered}")

    def get_events(self, queue_id, last_event_id, *, timeout):
        self.poll_timeouts.append(timeout)
        if not self.batches:
            return []
        batch = self.batches.pop(0)
        if isinstance(batch, Exception):
            raise batch
        return batch


class TestEventLoop:
    def test_registers_before_polling_and_reconciles(self):
        reconciled = []
        loop = EventLoop(FakeClient([]), lambda e: None, on_reconnect=lambda: reconciled.append(1))
        loop.run_once()
        assert loop.queue_id == "q1"
        # A fresh queue means we know nothing about the gap before it existed.
        assert reconciled == [1]

    def test_dispatches_events_and_tracks_the_last_id(self):
        seen = []
        client = FakeClient([[{"id": 5, "type": "message"}, {"id": 6, "type": "submessage"}]])
        loop = EventLoop(client, seen.append)
        loop.run_once()
        assert loop.run_once() == 2
        assert loop.last_event_id == 6
        assert [e["type"] for e in seen] == ["message", "submessage"]

    def test_heartbeats_advance_nothing_but_are_counted(self):
        seen = []
        client = FakeClient([[{"id": 1, "type": "heartbeat"}]])
        loop = EventLoop(client, seen.append)
        loop.run_once()
        assert loop.run_once() == 0
        assert seen == []
        assert loop.heartbeats == 1
        assert loop.last_event_id == 1

    def test_uses_the_servers_poll_timeout(self):
        client = FakeClient(
            [[]],
            register_response={
                "queue_id": "q1",
                "last_event_id": -1,
                "event_queue_longpoll_timeout_seconds": 120,
            },
        )
        loop = EventLoop(client, lambda e: None)
        loop.run_once()
        loop.run_once()
        assert client.poll_timeouts == [120.0]

    def test_a_lost_queue_is_re_registered_and_reconciled(self):
        reconciled = []
        client = FakeClient([BadEventQueueId("gone"), [{"id": 1, "type": "message"}]])
        loop = EventLoop(client, lambda e: None, on_reconnect=lambda: reconciled.append(1))
        loop.run_once()                      # register
        loop.run_once()                      # queue expired
        assert loop.queue_id is None
        loop.run_once()                      # re-register
        assert client.registered == 2
        # Reconnecting without reconciling is the quiet failure: no error, and
        # simply wrong about everything that happened during the gap.
        assert reconciled == [1, 1]

    def test_a_failing_handler_does_not_stop_the_loop(self):
        def explode(event):
            raise RuntimeError("bad event")

        client = FakeClient([[{"id": 1, "type": "message"}, {"id": 2, "type": "message"}]])
        loop = EventLoop(client, explode)
        loop.run_once()
        assert loop.run_once() == 2
        assert loop.last_event_id == 2


class TestZulipClientPolling:
    def test_the_http_timeout_exceeds_the_long_poll_timeout(self):
        """Otherwise a normal empty poll arrives as a client-side timeout, which
        is indistinguishable from the server having gone away."""
        seen = {}

        class Session:
            def request(self, method, url, **kwargs):
                seen.update(kwargs)
                seen["url"] = url

                class R:
                    status_code = 200

                    @staticmethod
                    def json():
                        return {"result": "success", "events": []}

                return R()

        client = ZulipClient("https://zulip.example", "bot@x", "key", session=Session())
        client.get_events("q1", 3, timeout=120.0)
        assert seen["timeout"] == 130.0
        assert seen["params"] == {"queue_id": "q1", "last_event_id": 3}

    def _capturing_client(self, captured):
        import json

        class Session:
            def request(self, method, url, **kwargs):
                captured.update(kwargs)

                class R:
                    status_code = 200

                    @staticmethod
                    def json():
                        return {"result": "success", "queue_id": "q", "last_event_id": -1, "id": 7}

                return R()

        return ZulipClient("https://zulip.example", "bot@x", "key", session=Session())

    def test_register_sends_event_types_as_a_json_string(self):
        """Zulip rejects a raw list with 'event_types is not valid JSON'."""
        captured: dict = {}
        self._capturing_client(captured).register(["message", "submessage"])
        assert captured["data"]["event_types"] == '["message", "submessage"]'

    def test_send_message_json_encodes_a_direct_message_recipient_list(self):
        captured: dict = {}
        self._capturing_client(captured).send_message(to=[3, 9], content="hi", type_="direct")
        assert captured["data"]["to"] == "[3, 9]"

    def test_send_message_leaves_a_scalar_recipient_alone(self):
        captured: dict = {}
        self._capturing_client(captured).send_message(to=7, content="hi", type_="stream")
        assert captured["data"]["to"] == 7


class FakeCensusResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self._response = response

    def get(self, url, timeout=None):
        if isinstance(self._response, Exception):
            raise self._response
        return self._response


def census_for(store, payload):
    return Census(
        "http://prosody:5280/room-census",
        store,
        session=FakeSession(FakeCensusResponse(payload)),
        clock=lambda: 1000.0,
    )


class TestCensusReconciliation:
    def test_the_real_room_census_key_is_parsed(self):
        """jitsi-meet's mod_muc_census keys the list `room_census`, not `rooms`.

        Reading the wrong key made fetch return {}, and reconcile then destroys
        every roster as "missing from census" — a silent wipe. This is the exact
        payload shape the module sends: room_census, participants, created_time,
        leaked.
        """
        store = Store()
        store.occupant_joined("c-abc", Occupant("j1"), now=1)
        result = census_for(
            store,
            {
                "room_census": [
                    {
                        "room_name": "[engineering]c-abc",
                        "participants": 1,
                        "created_time": 1000,
                        "leaked": False,
                    }
                ]
            },
        ).reconcile()
        assert result.clean
        assert store.occupancy("c-abc").count == 1  # NOT destroyed
        assert not store.occupancy("c-abc").drifted

    def test_the_real_census_shape_can_still_correct(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("j1"), now=1)
        result = census_for(
            store, {"room_census": [{"room_name": "[engineering]c-abc", "participants": 3}]}
        ).reconcile()
        assert result.corrected == 1
        assert store.occupancy("c-abc").drifted

    def test_agreement_changes_nothing(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("j1"), now=1)
        result = census_for(
            store, {"rooms": [{"room_name": "[engineering]c-abc", "participants": 1}]}
        ).reconcile()
        assert result.clean
        assert not store.occupancy("c-abc").drifted

    def test_a_disagreement_is_corrected_and_flagged(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("j1"), now=1)
        result = census_for(
            store, {"rooms": [{"room_name": "[engineering]c-abc", "participants": 3}]}
        ).reconcile()
        assert result.corrected == 1
        assert store.occupancy("c-abc").drifted

    def test_a_room_prosody_knows_and_we_do_not(self):
        store = Store()
        result = census_for(
            store, {"rooms": [{"room_name": "[engineering]c-new", "participants": 2}]}
        ).reconcile()
        assert result.unknown_to_us == 1
        assert store.occupancy("c-new") is not None

    def test_a_room_we_think_is_occupied_but_prosody_has_dropped(self):
        store = Store()
        store.occupant_joined("c-gone", Occupant("j1"), now=1)
        result = census_for(store, {"rooms": []}).reconcile()
        assert result.missing_from_census == 1
        assert store.occupancy("c-gone") is None

    def test_an_unreachable_census_leaves_state_alone(self):
        """Failing to reach the census is not the census saying 'empty'."""
        store = Store()
        store.occupant_joined("c-abc", Occupant("j1"), now=1)
        census = Census(
            "http://prosody:5280/room-census",
            store,
            session=FakeSession(requests.ConnectionError("down")),
        )
        result = census.reconcile()
        assert result.checked == 0
        assert store.occupancy("c-abc").count == 1

    def test_malformed_census_json_leaves_state_alone(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("j1"), now=1)
        census = Census(
            "http://prosody:5280/room-census",
            store,
            session=FakeSession(FakeCensusResponse({"rooms": [{"participants": 2}]})),
        )
        census.reconcile()
        assert store.occupancy("c-abc").count == 1


class TestRendering:
    def a_call(self, state=CallState.ACTIVE):
        return Call(
            call_id="c1",
            scope="realm:1|channel:7",
            room="c-abc",
            tenant="engineering",
            state=state,
            created_at=0.0,
        )

    def test_an_empty_room_says_so_plainly(self):
        assert "Nobody has joined" in roster_line(None, now=1000)

    def test_names_are_listed(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("j1", display_name="Alice"), now=1000)
        store.occupant_joined("c-abc", Occupant("j2", display_name="Bob"), now=1000)
        line = roster_line(store.occupancy("c-abc"), now=1000)
        assert "Alice" in line and "Bob" in line and "2 in the call" in line

    def test_a_drifted_roster_does_not_pretend_to_be_accurate(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("j1", display_name="Alice"), now=1000)
        store.replace_occupancy("c-abc", 4, now=1000)
        line = roster_line(store.occupancy("c-abc"), now=1000)
        assert "resyncing" in line
        assert "Alice" not in line

    def test_a_stale_roster_is_marked_as_stale(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("j1", display_name="Alice"), now=0)
        line = roster_line(store.occupancy("c-abc"), now=10_000)
        assert "no recent updates" in line

    def test_zulip_names_win_over_the_jitsi_display_name(self):
        store = Store()
        store.occupant_joined(
            "c-abc", Occupant("j1", zulip_user_id=31, display_name="whatever they typed"), now=1000
        )
        line = roster_line(store.occupancy("c-abc"), now=1000, name_for={31: "David Green"})
        assert "David Green" in line
        assert "whatever they typed" not in line

    def test_the_join_link_only_appears_while_the_call_is_live(self):
        live = call_message(self.a_call(), None, "https://jitsi/x", now=1000)
        assert "Join the call" in live
        ended = call_message(self.a_call(CallState.ENDED), None, "https://jitsi/x", now=1000)
        assert "Join the call" not in ended
        assert "Call ended" in ended

    def test_an_ended_call_with_people_still_in_it_is_surfaced(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("j1"), now=1000)
        body = call_message(
            self.a_call(CallState.ENDED), store.occupancy("c-abc"), None, now=1000
        )
        assert "still reports 1" in body

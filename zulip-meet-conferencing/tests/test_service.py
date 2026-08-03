"""The entry point: configuration and the wiring in ``Service``.

Every test here is synchronous and passes its own clock, because the whole point
of keeping the logic out of the threads is that none of it needs a socket, a
sleep, or a race to be checked.
"""

import pytest

from conferencing.config import Config, ConfigError
from conferencing.service import Service
from conferencing.state import Call, CallState, Occupant, Store
from conferencing.census import ReconcileResult

ENV = {
    "ZULIP_SITE": "https://zulip.example/",
    "ZULIP_EMAIL": "bot@zulip.example",
    "ZULIP_API_KEY": "key",
    "EVENT_SYNC_SECRET": "shared-with-prosody",
}


class FakePoster:
    """Captures hook send/edit calls instead of making HTTP calls.

    Mirrors HookClient's interface: ``send(**call fields) -> message_id`` and
    ``update(message_id, content)``. The service routes channel vs DM by which of
    stream_id/user_ids is set, so the fake just records the whole call.
    """

    def __init__(self, *, fail: bool = False, message_id: int = 101):
        self.updates: list[tuple[int, str]] = []
        self.sent: list[dict] = []
        self.fail = fail
        self._message_id = message_id

    def send(self, *, realm_id, stream_id, user_ids, initiator_id, topic, content):
        if self.fail:
            raise RuntimeError("hook is down")
        self.sent.append(
            {
                "realm_id": realm_id,
                "stream_id": stream_id,
                "user_ids": list(user_ids or []),
                "initiator_id": initiator_id,
                "topic": topic,
                "content": content,
            }
        )
        return self._message_id

    def update(self, message_id, content):
        if self.fail:
            raise RuntimeError("hook is down")
        self.updates.append((message_id, content))


class FakeCensus:
    def __init__(self, result=None):
        self.result = result or ReconcileResult()
        self.calls = 0

    def reconcile(self):
        self.calls += 1
        return self.result


def a_call(store, *, room="c-abc", state=CallState.ACTIVE, created_at=0.0, message_id=42):
    return store.create_call(
        Call(
            call_id="call-1",
            scope="realm:1|channel:7",
            room=room,
            tenant="engineering",
            state=state,
            created_at=created_at,
            message_id=message_id,
        )
    )


class TestConfig:
    def test_a_complete_environment_loads(self):
        config = Config.from_env(ENV)
        assert config.zulip_site == "https://zulip.example"  # trailing slash trimmed
        assert config.event_sync_secret == "shared-with-prosody"
        assert config.reconciliation_enabled is False  # no CENSUS_URL

    def test_every_missing_required_key_is_reported_at_once(self):
        with pytest.raises(ConfigError) as exc:
            Config.from_env({"ZULIP_SITE": "https://z"})
        message = str(exc.value)
        # The operator should not have to rediscover the next blank on the next
        # run, so all three still-missing keys appear together.
        assert "ZULIP_EMAIL" in message
        assert "ZULIP_API_KEY" in message
        assert "EVENT_SYNC_SECRET" in message

    def test_a_blank_secret_is_missing_not_present(self):
        with pytest.raises(ConfigError):
            Config.from_env({**ENV, "EVENT_SYNC_SECRET": "   "})

    def test_a_census_url_enables_reconciliation(self):
        config = Config.from_env({**ENV, "CENSUS_URL": "http://prosody:5280/census"})
        assert config.reconciliation_enabled is True
        assert config.census_url == "http://prosody:5280/census"

    def test_a_non_numeric_setting_is_a_config_error_not_a_crash(self):
        with pytest.raises(ConfigError):
            Config.from_env({**ENV, "BIND_PORT": "not-a-port"})


class TestOccupancyRerender:
    def test_an_occupancy_change_rerenders_the_call_message(self):
        store = Store()
        a_call(store, state=CallState.ACTIVE, message_id=42)
        store.occupant_joined("c-abc", Occupant("j1", display_name="Ada"), now=1000)
        client = FakePoster()
        service = Service(store, client, clock=lambda: 1000.0)

        service.on_occupancy_change("c-abc")

        assert len(client.updates) == 1
        message_id, body = client.updates[0]
        assert message_id == 42
        assert "Ada" in body

    def test_occupancy_for_a_room_with_no_call_is_ignored(self):
        # A stale link or another integration; not an error, and nothing to edit.
        store = Store()
        store.occupant_joined("c-orphan", Occupant("j1"), now=1000)
        client = FakePoster()
        Service(store, client, clock=lambda: 1000.0).on_occupancy_change("c-orphan")
        assert client.updates == []

    def test_a_call_without_a_posted_message_is_not_edited(self):
        store = Store()
        a_call(store, message_id=None)
        store.occupant_joined("c-abc", Occupant("j1"), now=1000)
        client = FakePoster()
        Service(store, client, clock=lambda: 1000.0).on_occupancy_change("c-abc")
        assert client.updates == []

    def test_a_failed_edit_does_not_propagate(self):
        # The sink and the ticker both call this; a Zulip outage must not take
        # either down.
        store = Store()
        a_call(store, message_id=42)
        client = FakePoster(fail=True)
        service = Service(store, client, clock=lambda: 1000.0)
        service.on_occupancy_change("c-abc")  # must not raise
        assert client.updates == []


class TestRingTimeouts:
    def test_an_unanswered_call_becomes_missed_and_is_rerendered(self):
        store = Store()
        a_call(store, state=CallState.RINGING, created_at=0.0, message_id=42)
        client = FakePoster()
        service = Service(store, client, ring_timeout_seconds=45.0, clock=lambda: 1000.0)

        swept = service.sweep_ring_timeouts()

        assert [c.call_id for c in swept] == ["call-1"]
        assert store.get_call("call-1").state is CallState.MISSED
        assert client.updates and "Missed call" in client.updates[0][1]

    def test_a_call_still_within_its_window_is_left_ringing(self):
        store = Store()
        a_call(store, state=CallState.RINGING, created_at=980.0, message_id=42)
        client = FakePoster()
        service = Service(store, client, ring_timeout_seconds=45.0, clock=lambda: 1000.0)

        assert service.sweep_ring_timeouts() == []
        assert store.get_call("call-1").state is CallState.RINGING
        assert client.updates == []

    def test_an_answered_call_is_never_swept(self):
        store = Store()
        a_call(store, state=CallState.ACTIVE, created_at=0.0, message_id=42)
        service = Service(store, FakePoster(), ring_timeout_seconds=45.0, clock=lambda: 1000.0)
        assert service.sweep_ring_timeouts() == []
        assert store.get_call("call-1").state is CallState.ACTIVE


class TestReconciliation:
    def test_reconcile_is_a_noop_when_disabled(self):
        service = Service(Store(), FakePoster())
        # None, not an empty result: "not configured" is distinct from "clean".
        assert service.reconcile() is None

    def test_reconcile_runs_the_census_and_rerenders_active_calls(self):
        store = Store()
        a_call(store, state=CallState.ACTIVE, message_id=42)
        store.occupant_joined("c-abc", Occupant("j1", display_name="Ada"), now=1000)
        client = FakePoster()
        census = FakeCensus(ReconcileResult(checked=1, corrected=1))
        service = Service(store, client, census=census, clock=lambda: 1000.0)

        result = service.reconcile()

        assert census.calls == 1
        assert result.corrected == 1
        assert len(client.updates) == 1  # the active call was refreshed

    def test_on_reconnect_reconciles(self):
        store = Store()
        census = FakeCensus()
        service = Service(store, FakePoster(), census=census)
        service.on_reconnect()
        assert census.calls == 1


class TestCallCreated:
    def _service(self, store, client, mid=555):
        return Service(store, client, clock=lambda: 1000.0, id_factory=lambda: "call-1")

    def test_a_channel_notice_creates_the_call_and_posts_a_roster_message(self):
        store = Store()
        client = FakePoster(message_id=555)
        call = self._service(store, client).handle_call_created(
            {"room": "c-abc", "tenant": "root", "scope": "realm:1|channel:7",
             "stream_id": 7, "realm_id": 1, "initiator_id": 6}
        )
        assert call.call_id == "call-1"
        assert call.message_id == 555
        assert store.call_for_room("c-abc").call_id == "call-1"
        assert len(client.sent) == 1
        sent = client.sent[0]
        # Channel post: routed by stream_id, into the call topic, in the right realm.
        assert sent["stream_id"] == 7 and sent["topic"] == "Calls" and sent["realm_id"] == 1
        assert sent["user_ids"] == []  # not a DM
        assert "Call in progress" in sent["content"]
        assert "Nobody has joined" in sent["content"]  # posted before anyone joins

    def test_occupancy_then_edits_that_message(self):
        store = Store()
        client = FakePoster(message_id=555)
        service = self._service(store, client)
        service.handle_call_created({"room": "c-abc", "stream_id": 7, "scope": "s"})
        store.occupant_joined("c-abc", Occupant("j1", display_name="Ada"), now=1000)
        service.on_occupancy_change("c-abc")
        assert client.updates[-1][0] == 555
        assert "Ada" in client.updates[-1][1]

    def test_a_duplicate_notice_does_not_post_twice(self):
        store = Store()
        client = FakePoster()
        service = self._service(store, client)
        notice = {"room": "c-abc", "stream_id": 7, "scope": "s"}
        service.handle_call_created(notice)
        service.handle_call_created(notice)  # double-click / retry
        assert len(client.sent) == 1

    def test_a_destroyed_room_ends_the_call(self):
        store = Store()
        client = FakePoster(message_id=555)
        service = self._service(store, client)
        service.handle_call_created({"room": "c-abc", "stream_id": 7, "scope": "s"})
        store.occupant_joined("c-abc", Occupant("j1"), now=1000)
        service.on_occupancy_change("c-abc")
        store.room_destroyed("c-abc", now=1001)  # everyone left → MUC torn down
        service.on_occupancy_change("c-abc")
        assert store.get_call("call-1").state is CallState.ENDED
        assert "Call ended" in client.updates[-1][1]

    def test_a_destroyed_room_ends_a_call_whose_post_failed_and_frees_the_slot(self):
        # If the initial post failed (message_id stays None), destroying the room
        # must still end the call and free the room's dedup slot — otherwise the
        # channel is wedged and every later call silently no-ops until a restart.
        store = Store()
        client = FakePoster(fail=True)  # posting fails, so message_id stays None
        service = self._service(store, client)
        service.handle_call_created({"room": "c-abc", "stream_id": 7, "scope": "s"})
        assert store.call_for_room("c-abc").message_id is None
        store.occupant_joined("c-abc", Occupant("j1"), now=1000)
        service.on_occupancy_change("c-abc")
        store.room_destroyed("c-abc", now=1001)  # MUC torn down
        service.on_occupancy_change("c-abc")
        assert store.get_call("call-1").state is CallState.ENDED
        assert store.call_for_room("c-abc") is None  # slot freed → next call can post

    def test_an_empty_room_that_is_merely_quiet_does_not_end_the_call(self):
        # room/created and the last occupant/left both leave count 0 briefly;
        # only a destroyed room (occupancy gone) ends things.
        store = Store()
        client = FakePoster(message_id=555)
        service = self._service(store, client)
        service.handle_call_created({"room": "c-abc", "stream_id": 7, "scope": "s"})
        store.room_created("c-abc", "root", now=1000)  # count 0, occupancy present
        service.on_occupancy_change("c-abc")
        assert store.get_call("call-1").state is CallState.ACTIVE

    def test_a_direct_message_notice_posts_a_dm_authored_by_the_initiator(self):
        store = Store()
        client = FakePoster(message_id=9)
        call = self._service(store, client).handle_call_created(
            {"room": "c-dm", "user_ids": [3, 9], "initiator_id": 3, "realm_id": 1,
             "scope": "realm:1|dm:3,9"}
        )
        assert call.message_id == 9
        sent = client.sent[0]
        # DM post: routed by user_ids (no stream_id), authored by the initiator,
        # so the hook lands it in the real conversation — the thing the bot could
        # not do.
        assert sent["stream_id"] is None
        assert sent["user_ids"] == [3, 9]
        assert sent["initiator_id"] == 3
        assert sent["realm_id"] == 1

    def test_a_notice_with_no_conversation_creates_the_call_but_posts_nothing(self):
        store = Store()
        client = FakePoster()
        call = self._service(store, client).handle_call_created({"room": "c-x", "scope": "s"})
        assert call is not None and call.message_id is None
        assert client.sent == []

    def test_posting_disabled_tracks_the_call_but_writes_no_message(self):
        # No hook configured (poster=None): the call and its occupancy are still
        # tracked so the widget answers, but nothing is posted or edited, and an
        # occupancy change must not raise for want of a poster.
        store = Store()
        service = Service(store, None, clock=lambda: 1000.0, id_factory=lambda: "call-1")
        call = service.handle_call_created(
            {"room": "c-abc", "stream_id": 7, "scope": "s", "realm_id": 1}
        )
        assert call is not None and call.message_id is None
        assert store.call_for_room("c-abc").call_id == "call-1"
        store.occupant_joined("c-abc", Occupant("j1", display_name="Ada"), now=1000)
        service.on_occupancy_change("c-abc")  # must not raise
        assert service.occupancy_for_stream(7)["count"] == 1

    def test_an_empty_room_string_is_ignored(self):
        assert Service(Store(), FakePoster()).handle_call_created({"room": ""}) is None

    def test_the_sink_dispatches_the_notice_only_with_auth(self):
        store = Store()
        service = Service(
            store, FakePoster(message_id=1), clock=lambda: 1000.0, id_factory=lambda: "call-1"
        )
        app = service.make_app("s3cret", prefix="/api/v1/jitsi")
        app.config.update(TESTING=True)
        http = app.test_client()

        unauth = http.post(
            "/api/v1/jitsi/calls/created", json={"room": "c-abc", "stream_id": 7, "scope": "s"}
        )
        assert unauth.status_code == 401
        assert store.call_for_room("c-abc") is None  # nothing created without the secret

        ok = http.post(
            "/api/v1/jitsi/calls/created",
            json={"room": "c-abc", "stream_id": 7, "scope": "s"},
            headers={"Authorization": "Bearer s3cret"},
        )
        assert ok.status_code == 200
        assert store.call_for_room("c-abc").call_id == "call-1"


class TestOccupancyWidget:
    def _service(self, store, client):
        return Service(store, client, clock=lambda: 1000.0, id_factory=lambda: "call-1")

    def test_no_call_for_the_channel_is_inactive_and_empty(self):
        result = self._service(Store(), FakePoster()).occupancy_for_stream(7)
        assert result == {
            "stream_id": 7, "active": False, "count": 0, "occupants": [], "drifted": False
        }

    def test_a_live_call_reports_its_roster_with_user_ids_for_avatars(self):
        store = Store()
        service = self._service(store, FakePoster(message_id=1))
        service.handle_call_created({"room": "c-abc", "stream_id": 7, "scope": "s"})
        store.occupant_joined("c-abc", Occupant("j1", zulip_user_id=31, display_name="Ada"), now=1000)
        store.occupant_joined("c-abc", Occupant("j2", zulip_user_id=32, display_name="Bob"), now=1000)
        result = service.occupancy_for_stream(7)
        assert result["active"] is True
        assert result["count"] == 2
        # {name, user_id} objects, sorted by name, so the client can show avatars.
        assert result["occupants"] == [
            {"name": "Ada", "user_id": 31},
            {"name": "Bob", "user_id": 32},
        ]

    def test_an_unnamed_occupant_still_appears_with_a_null_user_id(self):
        store = Store()
        service = self._service(store, FakePoster(message_id=1))
        service.handle_call_created({"room": "c-abc", "stream_id": 7, "scope": "s"})
        store.occupant_joined("c-abc", Occupant("j1"), now=1000)  # no id, no name
        result = service.occupancy_for_stream(7)
        assert result["occupants"] == [{"name": "someone", "user_id": None}]

    def test_a_drifted_roster_is_flagged_and_shows_no_names(self):
        store = Store()
        service = self._service(store, FakePoster(message_id=1))
        service.handle_call_created({"room": "c-abc", "stream_id": 7, "scope": "s"})
        store.occupant_joined("c-abc", Occupant("j1", display_name="Ada"), now=1000)
        store.replace_occupancy("c-abc", 4, now=1000)  # census disagrees → drifted
        result = service.occupancy_for_stream(7)
        assert result["drifted"] is True
        # The census can flag drift but can't rebuild the roster, so we report the
        # count we still hold (like render.py) and, crucially, no names — a roster
        # we know is wrong must never render as if it were right.
        assert result["occupants"] == []

    def test_the_query_sink_needs_auth_and_returns_the_roster(self):
        store = Store()
        service = self._service(store, FakePoster(message_id=1))
        service.handle_call_created({"room": "c-abc", "stream_id": 7, "scope": "s"})
        store.occupant_joined("c-abc", Occupant("j1", display_name="Ada"), now=1000)
        app = service.make_app("s3cret", prefix="/api/v1/jitsi")
        app.config.update(TESTING=True)
        http = app.test_client()

        assert http.get("/api/v1/jitsi/occupancy?stream_id=7").status_code == 401  # no secret
        ok = http.get(
            "/api/v1/jitsi/occupancy?stream_id=7", headers={"Authorization": "Bearer s3cret"}
        )
        assert ok.status_code == 200
        assert ok.get_json()["occupants"] == [{"name": "Ada", "user_id": None}]
        bad = http.get(
            "/api/v1/jitsi/occupancy?stream_id=abc", headers={"Authorization": "Bearer s3cret"}
        )
        assert bad.status_code == 400


class TestEventDispatch:
    def test_message_and_submessage_are_recognised(self):
        service = Service(Store(), FakePoster())
        assert service.handle_event({"type": "message", "message": {}}) == "message"
        assert service.handle_event({"type": "submessage"}) == "submessage"

    def test_an_unrelated_event_is_ignored(self):
        service = Service(Store(), FakePoster())
        assert service.handle_event({"type": "heartbeat"}) is None
        assert service.handle_event({"type": "presence"}) is None


class TestBuild:
    def test_build_wires_census_only_when_configured(self):
        from conferencing.__main__ import build

        without = build(Config.from_env(ENV))[0]
        assert without.census is None

        with_census = build(
            Config.from_env({**ENV, "CENSUS_URL": "http://prosody:5280/census"})
        )[0]
        assert with_census.census is not None

    def test_build_binds_the_loop_to_the_services_reconcile(self):
        from conferencing.__main__ import build

        service, loop = build(Config.from_env(ENV))
        assert loop.on_reconnect == service.on_reconnect
        assert loop.handler == service.handle_event

    def test_build_wires_a_poster_only_when_the_hook_url_is_set(self):
        from conferencing.__main__ import build
        from conferencing.hook_client import HookClient

        # No ZULIP_HOOK_URL → posting disabled, service still builds.
        assert build(Config.from_env(ENV))[0].poster is None

        with_hook = build(
            Config.from_env({**ENV, "ZULIP_HOOK_URL": "http://zulip/api/internal/jitsi"})
        )[0]
        assert isinstance(with_hook.poster, HookClient)
        # Bearer defaults to the event_sync secret unless split out explicitly.
        assert with_hook.poster._secret == ENV["EVENT_SYNC_SECRET"]

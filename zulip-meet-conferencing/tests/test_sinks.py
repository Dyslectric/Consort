"""The event_sync sinks.

These endpoints are publicly routable, so most of what is worth testing here is
what they refuse.
"""

import pytest

from conferencing.sinks import create_app
from conferencing.state import Store

SECRET = "shared-with-prosody"
PREFIX = "/api/v1/jitsi"


@pytest.fixture
def store() -> Store:
    return Store()


@pytest.fixture
def client(store):
    changed: list[str] = []
    app = create_app(store, SECRET, on_change=changed.append, clock=lambda: 1000.0)
    app.config.update(TESTING=True)
    test_client = app.test_client()
    test_client.changed = changed  # type: ignore[attr-defined]
    return test_client


def auth(extra=None):
    headers = {"Authorization": f"Bearer {SECRET}"}
    if extra:
        headers.update(extra)
    return headers


def joined_payload(room="[engineering]c-abc", user_id="31", name="David Green"):
    return {
        "event_name": "muc-occupant-joined",
        "room_name": room,
        "room_jid": f"{room}@conference.meet.jitsi",
        "occupant": {"occupant_jid": "room@conf/nick", "id": user_id, "name": name},
    }


class TestAuthentication:
    def test_refuses_a_request_with_no_secret(self, client, store):
        response = client.post(f"{PREFIX}/events/occupant/joined", json=joined_payload())
        assert response.status_code == 401
        assert store.occupancy("c-abc") is None

    def test_refuses_the_wrong_secret(self, client, store):
        response = client.post(
            f"{PREFIX}/events/occupant/joined",
            json=joined_payload(),
            headers={"Authorization": "Bearer nope"},
        )
        assert response.status_code == 401
        assert store.occupancy("c-abc") is None

    def test_refuses_a_non_bearer_scheme(self, client):
        response = client.post(
            f"{PREFIX}/events/room/created",
            json={"room_name": "[engineering]c-abc"},
            headers={"Authorization": f"Basic {SECRET}"},
        )
        assert response.status_code == 401

    def test_every_sink_is_protected(self, client):
        for path in (
            "events/room/created",
            "events/room/destroyed",
            "events/occupant/joined",
            "events/occupant/left",
        ):
            assert client.post(f"{PREFIX}/{path}", json={}).status_code == 401

    def test_refuses_to_start_without_a_secret(self, store):
        # An unauthenticated sink is an open write endpoint to the occupancy
        # state of every call in the deployment.
        with pytest.raises(ValueError):
            create_app(store, "")


class TestIdentityIsNeverTrusted:
    """The body is attacker-controlled. Identity in it is a lookup key only."""

    def test_the_user_id_is_recorded_but_grants_nothing(self, client, store):
        client.post(
            f"{PREFIX}/events/occupant/joined",
            json=joined_payload(user_id="31"),
            headers=auth(),
        )
        occupant = next(iter(store.occupancy("c-abc").occupants.values()))
        assert occupant.zulip_user_id == 31
        # No call exists, so nothing was created or transitioned on the strength
        # of the body's claims.
        assert store.call_for_room("c-abc") is None
        assert store.active_calls() == []

    def test_a_nonsense_user_id_does_not_drop_the_occupant(self, client, store):
        # A participant we cannot name is still a participant; dropping them
        # would understate the roster, which is the failure mode that matters.
        client.post(
            f"{PREFIX}/events/occupant/joined",
            json=joined_payload(user_id="not-an-id"),
            headers=auth(),
        )
        occupant = next(iter(store.occupancy("c-abc").occupants.values()))
        assert occupant.zulip_user_id is None
        assert store.occupancy("c-abc").count == 1

    def test_an_integer_user_id_is_accepted_too(self, client, store):
        client.post(
            f"{PREFIX}/events/occupant/joined", json=joined_payload(user_id=31), headers=auth()
        )
        occupant = next(iter(store.occupancy("c-abc").occupants.values()))
        assert occupant.zulip_user_id == 31


class TestEventHandling:
    def test_room_created_then_joined_then_left(self, client, store):
        client.post(
            f"{PREFIX}/events/room/created",
            json={"room_name": "[engineering]c-abc"},
            headers=auth(),
        )
        assert store.occupancy("c-abc").tenant == "engineering"

        client.post(f"{PREFIX}/events/occupant/joined", json=joined_payload(), headers=auth())
        assert store.occupancy("c-abc").count == 1

        client.post(
            f"{PREFIX}/events/occupant/left",
            json={
                "room_name": "[engineering]c-abc",
                "occupant": {"occupant_jid": "room@conf/nick"},
            },
            headers=auth(),
        )
        assert store.occupancy("c-abc").count == 0

    def test_a_join_for_an_unannounced_room_still_counts(self, client, store):
        """Prosody does not guarantee we saw room-created first."""
        client.post(f"{PREFIX}/events/occupant/joined", json=joined_payload(), headers=auth())
        assert store.occupancy("c-abc").count == 1

    def test_room_destroyed_clears_the_room(self, client, store):
        client.post(f"{PREFIX}/events/occupant/joined", json=joined_payload(), headers=auth())
        client.post(
            f"{PREFIX}/events/room/destroyed",
            json={"room_name": "[engineering]c-abc"},
            headers=auth(),
        )
        assert store.occupancy("c-abc") is None

    def test_the_occupant_jif_typo_in_the_payload_is_tolerated(self, client, store):
        # event_sync's README documents the field as `occupant_jif`.
        client.post(
            f"{PREFIX}/events/occupant/joined",
            json={
                "room_name": "[engineering]c-abc",
                "occupant": {"occupant_jif": "room@conf/nick", "id": "31"},
            },
            headers=auth(),
        )
        assert store.occupancy("c-abc").count == 1

    def test_a_room_we_never_created_is_recorded_not_rejected(self, client, store):
        """A 500 here would give Prosody a retry storm for no benefit."""
        response = client.post(
            f"{PREFIX}/events/occupant/joined",
            json=joined_payload(room="[other]c-someone-elses-room"),
            headers=auth(),
        )
        assert response.status_code == 200
        assert store.occupancy("c-someone-elses-room").count == 1

    def test_a_garbage_payload_does_not_500(self, client):
        for path in ("events/room/created", "events/occupant/joined", "events/occupant/left"):
            assert client.post(f"{PREFIX}/{path}", json={}, headers=auth()).status_code == 200

    def test_changes_are_announced(self, client):
        client.post(f"{PREFIX}/events/occupant/joined", json=joined_payload(), headers=auth())
        assert client.changed == ["c-abc"]

    def test_a_failing_change_handler_does_not_break_the_sink(self, store):
        def explode(room):
            raise RuntimeError("rendering is broken")

        app = create_app(store, SECRET, on_change=explode)
        app.config.update(TESTING=False)
        response = app.test_client().post(
            f"{PREFIX}/events/occupant/joined", json=joined_payload(), headers=auth()
        )
        # Prosody would retry a 500, and the retry would fail identically.
        assert response.status_code == 200
        assert store.occupancy("c-abc").count == 1

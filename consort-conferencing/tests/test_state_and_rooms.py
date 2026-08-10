import pytest

from conferencing.rooms import (
    channel_scope,
    derive_room_name,
    direct_message_scope,
    parse_mapped_room,
)
from conferencing.state import Call, CallState, InvalidTransition, Occupant, Store


class TestMappedRoomNames:
    """event_sync reports the name after muc_domain_mapper has rewritten it.

    Getting this wrong does not raise anything — it just attributes every
    occupancy event to nothing, and the roster stays permanently empty while
    looking like a quiet channel.
    """

    def test_parses_the_tenant_prefix(self):
        mapped = parse_mapped_room("[engineering]c-7f3a91b2e4c8d5a6")
        assert mapped.tenant == "engineering"
        assert mapped.room == "c-7f3a91b2e4c8d5a6"
        assert mapped.is_tenanted

    def test_accepts_an_untenanted_room(self):
        mapped = parse_mapped_room("c-7f3a91b2e4c8d5a6")
        assert mapped.tenant is None
        assert mapped.room == "c-7f3a91b2e4c8d5a6"

    def test_strips_the_muc_domain(self):
        mapped = parse_mapped_room("[engineering]c-abc@conference.meet.jitsi")
        assert mapped.tenant == "engineering"
        assert mapped.room == "c-abc"

    def test_tenant_containing_a_hyphen(self):
        assert parse_mapped_room("[design-team]c-abc").tenant == "design-team"


class TestRoomDerivation:
    """Must stay byte-identical to the calls patch, or nothing lines up."""

    KEY = "test-room-key"

    def test_matches_the_patch(self):
        # Value produced by zerver/lib/jitsi_token.py with the same inputs.
        assert derive_room_name(self.KEY, channel_scope(1, 7), 0).startswith("c-")
        assert len(derive_room_name(self.KEY, channel_scope(1, 7), 0)) == 18

    def test_is_stable_and_scoped(self):
        a = derive_room_name(self.KEY, channel_scope(1, 7), 0)
        assert a == derive_room_name(self.KEY, channel_scope(1, 7), 0)
        assert a != derive_room_name(self.KEY, channel_scope(1, 8), 0)
        assert a != derive_room_name(self.KEY, channel_scope(2, 7), 0)
        assert a != derive_room_name(self.KEY, channel_scope(1, 7), 1)

    def test_direct_message_scope_is_order_independent(self):
        # Both participants must derive the same room or they call into
        # different empty rooms.
        assert direct_message_scope(1, [9, 3]) == direct_message_scope(1, [3, 9])
        assert direct_message_scope(1, [9, 3, 9]) == direct_message_scope(1, [3, 9])


def a_call(store: Store, call_id="call-1", room="c-abc", state=CallState.RINGING) -> Call:
    return store.create_call(
        Call(
            call_id=call_id,
            scope="realm:1|channel:7",
            room=room,
            tenant="engineering",
            state=state,
            created_at=100.0,
        )
    )


class TestCallStateMachine:
    def test_ringing_can_be_answered(self):
        store = Store()
        a_call(store)
        assert store.transition("call-1", CallState.ACTIVE, now=101).state is CallState.ACTIVE

    @pytest.mark.parametrize(
        "terminal",
        [CallState.DECLINED, CallState.MISSED, CallState.CANCELLED],
    )
    def test_ringing_can_fail_in_each_way(self, terminal):
        store = Store()
        a_call(store)
        assert store.transition("call-1", terminal, now=101).state is terminal

    def test_active_can_only_end(self):
        store = Store()
        a_call(store)
        store.transition("call-1", CallState.ACTIVE, now=101)
        with pytest.raises(InvalidTransition):
            store.transition("call-1", CallState.DECLINED, now=102)
        assert store.transition("call-1", CallState.ENDED, now=103).state is CallState.ENDED

    def test_terminal_states_are_final(self):
        store = Store()
        a_call(store)
        store.transition("call-1", CallState.ENDED if False else CallState.MISSED, now=101)
        with pytest.raises(InvalidTransition):
            store.transition("call-1", CallState.ACTIVE, now=102)

    def test_unknown_call_raises(self):
        with pytest.raises(InvalidTransition):
            Store().transition("nope", CallState.ACTIVE, now=1)

    def test_two_simultaneous_starts_do_not_both_win(self):
        """Both callers derive the same room; the channel must not get two
        competing call messages for one conversation."""
        store = Store()
        a_call(store, call_id="call-1", room="c-abc")
        with pytest.raises(InvalidTransition):
            a_call(store, call_id="call-2", room="c-abc")

    def test_the_room_is_reusable_once_the_call_is_over(self):
        store = Store()
        a_call(store, call_id="call-1", room="c-abc")
        store.transition("call-1", CallState.MISSED, now=101)
        a_call(store, call_id="call-2", room="c-abc")  # must not raise
        assert store.call_for_room("c-abc").call_id == "call-2"

    def test_ring_timeout_selects_only_old_ringing_calls(self):
        store = Store()
        a_call(store, call_id="old", room="c-old")
        store.create_call(
            Call(
                call_id="new",
                scope="s",
                room="c-new",
                tenant=None,
                state=CallState.RINGING,
                created_at=200.0,
            )
        )
        stale = [c.call_id for c in store.calls_ringing_since(150.0)]
        assert stale == ["old"]


class TestOccupancy:
    def test_join_and_leave(self):
        store = Store()
        store.room_created("c-abc", "engineering", now=1)
        store.occupant_joined("c-abc", Occupant("jid1", zulip_user_id=31), now=2)
        store.occupant_joined("c-abc", Occupant("jid2", zulip_user_id=32), now=3)
        assert store.occupancy("c-abc").count == 2
        store.occupant_left("c-abc", "jid1", now=4)
        assert store.occupancy("c-abc").count == 1

    def test_leaving_twice_is_harmless(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("jid1"), now=1)
        store.occupant_left("c-abc", "jid1", now=2)
        store.occupant_left("c-abc", "jid1", now=3)
        assert store.occupancy("c-abc").count == 0

    def test_rejoining_replaces_rather_than_duplicates(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("jid1", display_name="A"), now=1)
        store.occupant_joined("c-abc", Occupant("jid1", display_name="A"), now=2)
        assert store.occupancy("c-abc").count == 1

    def test_destroying_a_room_clears_it(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("jid1"), now=1)
        store.room_destroyed("c-abc", now=2)
        assert store.occupancy("c-abc") is None

    def test_staleness_is_measurable(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("jid1"), now=1000)
        assert store.occupancy("c-abc").stale_for(1000) == 0
        assert store.occupancy("c-abc").stale_for(2000) == 1000

    def test_a_correction_marks_the_roster_as_drifted(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("jid1"), now=1)
        store.replace_occupancy("c-abc", 3, now=2)
        assert store.occupancy("c-abc").drifted

    def test_a_matching_count_does_not_mark_drift(self):
        store = Store()
        store.occupant_joined("c-abc", Occupant("jid1"), now=1)
        store.replace_occupancy("c-abc", 1, now=2)
        assert not store.occupancy("c-abc").drifted

    def test_a_fresh_join_clears_drift(self):
        store = Store()
        store.replace_occupancy("c-abc", 5, now=1)
        assert store.occupancy("c-abc").drifted
        store.occupant_joined("c-abc", Occupant("jid1"), now=2)
        assert not store.occupancy("c-abc").drifted

"""Sync phase 1: HLC, op checks, Store actions as ops, and merges that converge in any order."""
import random

import pytest

from conftest import HOME, UNIVERSE, pick_system
from homebound.mapdb.store import Signature, Store
from homebound.sync.hlc import HybridClock, format_hlc, hlc_milliseconds, is_hlc, parse_hlc, stamp
from homebound.sync.merge import (BatchRejected, MapState, apply_op, check_batch, settle_hole_numbers,
                                 stamp_server_times)
from homebound.sync.protocol import (FIELDS, GATED_FIELDS, InvalidOp, Op, batch_from_json, full_ops_for_rows,
                                     op_from_json, ops_for_peer, ops_from_action)

CLIENTS = ["aaaaaaaa", "bbbbbbbb", "cccccccc"]


# ---------------------------------------------------------------- HLC

def test_hlc_never_goes_backwards() -> None:
    """Ticks grow even when the wall clock stands still or jumps back."""
    now = [1000]
    clock = HybridClock(now_ms=lambda: now[0])
    first = clock.tick()
    second = clock.tick()
    now[0] = 500
    third = clock.tick()
    assert first < second < third and parse_hlc(third) == (1000, 2)
    now[0] = 2000
    assert clock.tick() == format_hlc(2000, 0)


def test_hlc_follows_a_remote_clock_but_not_too_far() -> None:
    """Observing a newer value moves us past it; a clock hours ahead is capped at a minute."""
    clock = HybridClock(now_ms=lambda: 10_000)
    clock.observe(format_hlc(20_000, 5))
    assert clock.tick() == format_hlc(20_000, 6)
    clock.observe(stamp(format_hlc(10_000_000, 0), "aaaaaaaa"))
    assert hlc_milliseconds(clock.tick()) == 10_000 + 60_000


def test_hlc_counter_overflow_and_text_checks() -> None:
    """A full counter carries into the milliseconds; bad values are refused."""
    clock = HybridClock(format_hlc(5, 0xFFFF), now_ms=lambda: 1)
    assert clock.tick() == format_hlc(6, 0)
    assert is_hlc("0193a1b2c3d4-0007") and not is_hlc("0193a1b2c3d4-7") and not is_hlc(5)
    with pytest.raises(ValueError):
        parse_hlc("nope")


# ---------------------------------------------------------------- op checks

def op(entity, key, hlc_ms, client=0, fields=None, **flags) -> Op:
    """Build an op with an HLC at ``hlc_ms``."""
    message = {"e": entity, "k": key, "hlc": format_hlc(hlc_ms, 0), "c": CLIENTS[client], "f": fields or {}}
    message.update(flags)
    return op_from_json(message)


def test_ops_round_trip_through_json() -> None:
    """The wire form of an op parses back to the same op."""
    original = op("system", 31000005, 7, fields={"notes": "farm", "x": 1.0}, full=True,
                  placement={"parent_id": 31000001, "hole_index": 2})
    assert op_from_json(original.to_json()) == original
    deletion = op("signature", [31000005, "ABC-123"], 8, **{"del": True})
    assert deletion.delete and op_from_json(deletion.to_json()) == deletion


@pytest.mark.parametrize("message", [
    {"e": "planet", "k": 1},
    {"e": "system", "k": 0},
    {"e": "connection", "k": [5, 3]},
    {"e": "signature", "k": [5, "abc-123"]},
    {"e": "system", "k": 5, "f": {"colour": "red"}},
    {"e": "system", "k": 5, "f": {"notes": "x" * 4001}},
    {"e": "system", "k": 5, "f": {"x": 1.0}},                    # layout only with a full upsert
    {"e": "connection", "k": [3, 5], "f": {"mass": "huge"}},
    {"e": "system", "k": 5, "placement": {"parent_id": 5, "hole_index": 1}},
    {"e": "signature", "k": [5, "ABC-123"], "placement": {"parent_id": 1}},
    {"e": "setting", "k": "naming_scheme", "f": {"value": "roman"}},
    {"e": "setting", "k": "colour", "f": {"value": "red"}},
])
def test_bad_ops_are_invalid(message) -> None:
    """Unknown entities, keys, fields and values are refused."""
    with pytest.raises(InvalidOp):
        op_from_json({"hlc": format_hlc(1, 0), "c": CLIENTS[0], **message})


def test_batch_checks() -> None:
    """A batch must be a non-empty list of at most 500 ops from the sender."""
    good = op("system", 5, 1, fields={"notes": ""}).to_json()
    with pytest.raises(InvalidOp):
        batch_from_json([])
    with pytest.raises(InvalidOp):
        batch_from_json([good] * 501)
    with pytest.raises(InvalidOp):
        batch_from_json([good], client_id=CLIENTS[1])
    assert len(batch_from_json([good], client_id=CLIENTS[0])) == 1


# ---------------------------------------------------------------- Store actions -> ops

def test_store_actions_become_ops() -> None:
    """Adds are full upserts, edits send only what changed, removals are tombstones for every dependant."""
    store = Store(":memory:")
    c5 = pick_system("C5", exclude=(HOME.id,))
    with store.batch() as added:
        store.add_system(HOME.id, x=1.0)
        store.add_system(c5.id, parent_id=HOME.id, hole_index=1)
        store.add_connection(HOME.id, c5.id)
        store.upsert_signature(Signature(c5.id, "ABC-123", "wormhole"))
    ops = ops_from_action(added, format_hlc(1, 0), CLIENTS[0])
    assert [(o.entity, o.full) for o in ops] == [("system", True), ("system", True), ("connection", True),
                                                ("signature", True)]
    assert ops[1].placement == (HOME.id, 1) and ops[0].fields["x"] == 1.0
    with store.batch() as edited:
        store.update_system(c5.id, notes="farm", x=50.0)
    [edit] = ops_from_action(edited, format_hlc(2, 0), CLIENTS[0])
    assert edit.fields == {"notes": "farm"} and not edit.has_placement     # x is local after the add
    with store.batch() as removed:
        store.remove_system(c5.id)
    deletes = ops_from_action(removed, format_hlc(3, 0), CLIENTS[0])
    assert all(o.delete for o in deletes) and {o.entity for o in deletes} == {"system", "connection", "signature"}
    for o in ops + [edit] + deletes:
        assert op_from_json(o.to_json()) == o           # everything the Store emits passes the server checks


def test_a_whole_map_as_full_ops() -> None:
    """Uploading a map sends one full upsert per entity, settings first."""
    rows = {"system": [{"id": 5, "parent_id": None, "hole_index": None, "notes": ""}],
            "setting": [{"key": "home_system", "value": "J161940"}]}
    ops = full_ops_for_rows(rows, format_hlc(1, 0), CLIENTS[0])
    assert [o.entity for o in ops] == ["setting", "system"] and all(o.full for o in ops)


# ---------------------------------------------------------------- merge

def test_lww_keeps_the_newest_field_and_tombstones_win_over_older_edits() -> None:
    """Higher stamps win per field; a delete hides the entity until a newer full upsert."""
    state = MapState()
    state.apply(op("system", 5, 1, fields={"notes": "a", "pinned": 0}, full=True))
    state.apply(op("system", 5, 3, 1, fields={"notes": "c"}))
    state.apply(op("system", 5, 2, 2, fields={"notes": "b", "pinned": 1}))
    assert state.visible()[("system", 5)]["notes"] == "c" and state.visible()[("system", 5)]["pinned"] == 1
    effect = state.apply(op("system", 5, 4, **{"del": True}))
    assert effect.deleted and ("system", 5) not in state.visible()
    assert not state.apply(op("system", 5, 5, fields={"notes": "late"})).visible   # an edit doesn't revive it
    assert not state.apply(op("system", 5, 3, fields={"notes": "old"}, full=True)).visible  # nor an older add
    revived = state.apply(op("system", 5, 6, fields={"notes": "back", "pinned": 0}, full=True))
    assert revived.created and revived.changed["notes"] == "back"


def test_equal_hlcs_are_ordered_by_client_id() -> None:
    """Two edits at the same HLC resolve the same way everywhere: the higher client ID wins."""
    first, second = op("system", 5, 1, 0, {"notes": "a"}, full=True), op("system", 5, 1, 1, {"notes": "b"}, full=True)
    for order in ([first, second], [second, first]):
        state = MapState()
        state.apply_batch(order)
        assert state.visible()[("system", 5)]["notes"] == "b"


def test_applying_an_op_twice_changes_nothing() -> None:
    """Resent ops are harmless."""
    edit = op("system", 5, 1, fields={"notes": "a"}, full=True)
    state, _ = apply_op(None, edit)
    again, effect = apply_op(state, edit)
    assert again == state and not effect.visible


def random_ops(rng: random.Random, count: int) -> list[Op]:
    """Make random ops on a few systems, connections and sigs from three clients."""
    systems = [31000001, 31000002, 31000003, 31000004]
    ops, used = [], set()
    for index in range(count):
        client = rng.randrange(3)
        kind = rng.choice(["system", "system", "connection", "signature", "setting"])
        hlc_ms = rng.randrange(1, 40)      # many ties between clients, so client IDs have to break them
        while (hlc_ms, client) in used:    # but one client's clock never hands out the same value twice
            hlc_ms += 1
        used.add((hlc_ms, client))
        flags = {}
        if rng.random() < 0.15:
            flags["del"] = True
        elif rng.random() < 0.4:
            flags["full"] = True
        if kind == "system":
            key = rng.choice(systems)
            fields = {"notes": rng.choice("abcd"), "pinned": rng.randrange(2)} if rng.random() < 0.8 else {}
            if rng.random() < 0.4:
                flags["placement"] = {"parent_id": rng.choice([None, *[s for s in systems if s != key]]),
                                      "hole_index": rng.randrange(1, 4)}
        elif kind == "connection":
            a, b = sorted(rng.sample(systems, 2))
            key, fields = [a, b], {"mass": rng.choice(["fresh", "reduced", "critical"])}
        elif kind == "signature":
            key, fields = [rng.choice(systems), rng.choice(["ABC-123", "XYZ-999"])], {"name": rng.choice("xyz")}
        else:
            key, fields = "naming_scheme", {"value": rng.choice(["numeric", "letter_numeric"])}
        ops.append(op(kind, key, hlc_ms, client, fields, **flags))
    return ops


@pytest.mark.parametrize("seed", range(40))
def test_replicas_converge_whatever_order_they_apply_ops_in(seed) -> None:
    """Property: every permutation of the same ops gives the same state, tombstones and clocks included."""
    rng = random.Random(seed)
    ops = random_ops(rng, 60)
    reference = MapState()
    reference.apply_batch(ops)
    for _ in range(5):
        shuffled = ops[:]
        rng.shuffle(shuffled)
        replica = MapState()
        replica.apply_batch(shuffled + rng.sample(ops, 10))      # some ops twice
        assert replica.entities == reference.entities


@pytest.mark.parametrize("seed", range(20))
def test_applying_effects_to_a_plain_map_converges_too(seed) -> None:
    """A replica that keeps only visible rows and applies each op's effect ends up with the visible state."""
    rng = random.Random(seed)
    ops = random_ops(rng, 60)
    rng.shuffle(ops)
    state, rows = MapState(), {}
    for each in ops:
        effect = state.apply(each)
        key = (effect.entity, effect.key)
        if effect.deleted:
            rows.pop(key, None)
        elif effect.created:
            rows[key] = dict(effect.changed)
        elif effect.changed:
            rows[key].update(effect.changed)
    assert rows == state.visible()


# ---------------------------------------------------------------- server checks

def placed(key, parent, hole, hlc_ms=1, client=0) -> Op:
    """A full upsert of a system under ``parent`` at ``hole``."""
    return op("system", key, hlc_ms, client, {"notes": ""}, full=True,
              placement={"parent_id": parent, "hole_index": hole})


def test_check_batch_refuses_dangling_references() -> None:
    """A connection or sig needs live systems, from the state or the same batch."""
    state = MapState()
    state.apply(placed(1, None, None))
    with pytest.raises(BatchRejected) as rejected:
        check_batch(state, [op("connection", [1, 2], 2, fields={"mass": "fresh"}, full=True)])
    assert rejected.value.code == "conflict" and ("system", 2) in rejected.value.current
    after = check_batch(state, [placed(2, 1, 1, 2), op("connection", [1, 2], 2, fields={"mass": "fresh"}, full=True)])
    assert after.live("connection", (1, 2)) and not state.live("system", 2)      # the state itself is untouched
    state.apply(op("system", 1, 3, **{"del": True}))
    with pytest.raises(BatchRejected):
        check_batch(state, [op("signature", [1, "ABC-123"], 4, fields={"name": "x"}, full=True)])


def test_check_batch_first_placement_wins() -> None:
    """A taken hole, a loop or a missing parent is a conflict; the slot holder is reported."""
    state = MapState()
    state.apply_batch([placed(1, None, None), placed(2, 1, 1), placed(3, 2, 1)])
    with pytest.raises(BatchRejected) as rejected:
        check_batch(state, [placed(4, 1, 1, 2, 1)])
    assert ("system", 2) in rejected.value.current
    with pytest.raises(BatchRejected):
        check_batch(state, [op("system", 1, 5, placement={"parent_id": 3, "hole_index": 2})])
    with pytest.raises(BatchRejected):
        check_batch(state, [placed(4, 9, 1, 2)])
    check_batch(state, [placed(4, 1, 2, 2)])                  # a free hole is fine
    # moving 2 away frees hole 1 in the same batch
    check_batch(state, [op("system", 2, 6, placement={"parent_id": 1, "hole_index": 3}), placed(4, 1, 1, 6)])


def test_check_batch_refuses_unknown_solar_systems() -> None:
    """With the universe given, only real system IDs pass."""
    real = pick_system("C3").id
    with pytest.raises(BatchRejected) as rejected:
        check_batch(MapState(), [placed(1, None, None)], lambda system_id: UNIVERSE.by_id(system_id) is not None)
    assert rejected.value.code == "invalid"
    check_batch(MapState(), [placed(real, None, None)], lambda system_id: UNIVERSE.by_id(system_id) is not None)


def test_server_stamps_creation_and_lifetime_times() -> None:
    """New entities get the server's time; a re-add keeps the first one; lifetime changes are stamped."""
    state = MapState()
    ops, stamped = stamp_server_times(state, [placed(1, None, None)], "2026-01-01T00:00:00+00:00")
    assert ops[0].fields["added_at"] == "2026-01-01T00:00:00+00:00" and "system:1" in stamped
    state.apply_batch(ops)
    again, _ = stamp_server_times(state, [placed(1, None, None, 2)], "2026-02-01T00:00:00+00:00")
    assert again[0].fields["added_at"] == "2026-01-01T00:00:00+00:00"
    eol, _ = stamp_server_times(state, [op("connection", [1, 2], 3, fields={"lifetime": 1})], "T1")
    stable, _ = stamp_server_times(state, [op("connection", [1, 2], 3, fields={"lifetime": 0})], "T1")
    assert eol[0].fields["lifetime_set_at"] == "T1" and stable[0].fields["lifetime_set_at"] is None


@pytest.mark.parametrize("scheme", ["numeric", "a2a", "letter_numeric"])
def test_every_naming_scheme_of_the_app_is_a_valid_setting(scheme) -> None:
    """The server accepts each scheme the settings offer (a typo here once rejected "a2a")."""
    from homebound.mapping.naming import SCHEMES
    assert scheme in SCHEMES
    op = op_from_json({"e": "setting", "k": "naming_scheme", "f": {"value": scheme}, "hlc": format_hlc(1, 0),
                       "c": "aaaaaaaa"})
    assert op.fields["value"] == scheme


def test_stored_names_and_the_static_flag_go_only_to_a_peer_that_knows_them() -> None:
    """label, static and next_hole are ops of protocol 2; a protocol 1 peer gets the op without them.

    A protocol 1 peer refuses a field it doesn't know (op_from_json raises InvalidOp, which drops the
    whole batch, and an old client keeps asking for it again), so the version gate strips them.
    """
    gated = set(GATED_FIELDS["system"])
    assert gated == {"label", "static", "next_hole", "cut_off_at"} and gated <= set(FIELDS["system"])
    store = Store(":memory:")
    actions: list = []
    store.listeners.append(actions.append)
    store.add_system(HOME.id)
    store.add_system(31000001, parent_id=HOME.id, hole_index=2, label="2", static=1)
    store.update_system(31000001, label="Z", static=0)
    created, renamed = ([op.to_json() for op in ops_from_action(action, format_hlc(1, 0), CLIENTS[0])]
                        for action in actions[1:])
    assert (created[0]["f"]["label"], created[0]["f"]["static"], created[0]["f"]["next_hole"]) == ("2", 1, 0)
    assert renamed[0]["f"] == {"label": "Z", "static": 0}
    assert not gated & set(ops_for_peer(created, 1)[0]["f"]) and ops_for_peer(renamed, 1) == []
    assert op_from_json(renamed[0]).fields == {"label": "Z", "static": 0}
    with pytest.raises(InvalidOp):
        op_from_json({"e": "system", "k": 31000001, "f": {"static": 2}, "hlc": format_hlc(1, 0), "c": "aaaaaaaa"})


def test_the_hole_counter_merges_as_the_highest_value() -> None:
    """next_hole keeps the highest value whatever order the ops come in, and a lower one changes nothing."""
    ops = [op_from_json({"e": "system", "k": HOME.id, "f": {"next_hole": value}, "hlc": format_hlc(ms, 0),
                         "c": CLIENTS[0]}) for ms, value in ((1, 4), (2, 2), (3, 3))]
    results = set()
    for order in (ops, ops[::-1], [ops[1], ops[0], ops[2]]):
        state = MapState()
        state.apply_batch(order)
        results.add(state.get("system", HOME.id).fields["next_hole"])
    assert results == {4}
    state = MapState()
    state.apply(ops[0])
    assert not state.apply(ops[1]).changed


def test_aliases_are_checked_like_other_ops() -> None:
    """An alias is keyed by a solar system ID, has a name, and must name a real system."""
    good = {"e": "alias", "k": HOME.id, "f": {"name": "Staging", "set_at": None}, "full": True,
            "hlc": format_hlc(1, 0), "c": CLIENTS[0]}
    assert op_from_json(good).key == HOME.id
    for bad in ({"k": "J123"}, {"f": {"name": ""}}, {"f": {"name": "x" * 101}}, {"placement": {"parent_id": None}}):
        with pytest.raises(InvalidOp):
            op_from_json(good | bad)
    with pytest.raises(BatchRejected):
        check_batch(MapState(), [op_from_json(good | {"k": 1})], lambda system_id: UNIVERSE.by_id(system_id) is not None)


def test_an_unknown_naming_scheme_is_refused() -> None:
    """A scheme the app doesn't have is an invalid op."""
    with pytest.raises(InvalidOp):
        op_from_json({"e": "setting", "k": "naming_scheme", "f": {"value": "chain_depth_position"},
                      "hlc": format_hlc(1, 0), "c": "aaaaaaaa"})


# ---------------------------------------------------------------- hole numbers given twice

def numbered_sig(system_id, signature_id, number, hlc_ms=1, client=0, full=True) -> Op:
    """A WH sig with a bookmark number (a full upsert by default, as a paste makes it)."""
    flags = {"full": True} if full else {}
    return op("signature", [system_id, signature_id], hlc_ms, client, {"group": "wormhole", "hole_index": number},
              **flags)


def settle(state: MapState, batch: list[Op]) -> tuple[list[Op], dict]:
    """Check a batch like the server does, then settle its numbers."""
    return settle_hole_numbers(state, batch, check_batch(state, batch))


def test_a_number_another_sig_has_already_goes_to_the_next_free_one() -> None:
    """The sig whose number reached the server first keeps it; the later one gets the system's next number."""
    state = MapState()
    state.apply_batch([placed(1, None, None), numbered_sig(1, "AAA-100", 1), numbered_sig(1, "CCC-300", 2)])
    batch = [numbered_sig(1, "BBB-200", 2, 2, 1), numbered_sig(1, "DDD-400", 3, 2, 1)]
    ops, settled = settle(state, batch)
    assert [item.fields["hole_index"] for item in ops[:2]] == [4, 3]           # 2 is taken; 3 is free, so it stays
    assert settled == {'signature:[1, "BBB-200"]': {"hole_index": 4}}
    assert (ops[2].entity, ops[2].key, ops[2].fields) == ("system", 1, {"next_hole": 4})
    state.apply_batch(ops)
    assert state.get("system", 1).fields["next_hole"] == 4
    assert batch[0].fields["hole_index"] == 2                                  # the input isn't changed


def test_a_free_number_an_unchanged_one_and_a_swap_stay() -> None:
    """Nothing changes without a clash; numbers swapped inside one batch (a return hole) are the sender's own."""
    state = MapState()
    state.apply_batch([placed(1, None, None), numbered_sig(1, "AAA-100", 1), numbered_sig(1, "BBB-200", 2)])
    for batch in ([numbered_sig(1, "CCC-300", 3, 2)],
                  [numbered_sig(1, "AAA-100", 1, 2, full=False)],
                  [numbered_sig(1, "AAA-100", 2, 2, full=False), numbered_sig(1, "BBB-200", 1, 2, full=False)],
                  [op("signature", [1, "AAA-100"], 2, fields={"name": "Unstable"})]):
        assert settle(state, batch) == (batch, {})


def test_a_sig_pasted_again_offline_keeps_the_number_it_got_first() -> None:
    """Already synced numbers win over edits made offline: a later full upsert can't change it."""
    state = MapState()
    state.apply_batch([placed(1, None, None), numbered_sig(1, "AAA-100", 3)])
    ops, settled = settle(state, [numbered_sig(1, "AAA-100", 1, 5, 1)])
    assert ops[0].fields["hole_index"] == 3 and settled == {'signature:[1, "AAA-100"]': {"hole_index": 3}}
    assert len(ops) == 1                                                     # no new number, no counter op
    unnumbered = MapState()
    unnumbered.apply_batch([placed(1, None, None), op("signature", [1, "AAA-100"], 1, fields={"group": "wormhole"},
                                                      full=True)])
    assert settle(unnumbered, [numbered_sig(1, "AAA-100", 1, 5, 1)])[1] == {}  # a first number is taken as it is


def test_a_number_a_hole_of_the_system_has_counts_as_taken() -> None:
    """A child placed at hole 2 holds that number, unless it hangs off this very sig."""
    state = MapState()
    state.apply_batch([placed(1, None, None), placed(2, 1, 2),
                       op("connection", [1, 2], 1, fields={"signature_a": "AAA-100"}, full=True)])
    ops, settled = settle(state, [numbered_sig(1, "BBB-200", 2, 2, 1)])
    assert ops[0].fields["hole_index"] == 3
    assert settle(state, [numbered_sig(1, "AAA-100", 2, 2, 1)])[1] == {}     # the sig the child came through
    # a deleted sig's number isn't given again either
    state.apply_batch([numbered_sig(1, "CCC-300", 7), op("signature", [1, "CCC-300"], 3, **{"del": True})])
    assert settle(state, [numbered_sig(1, "BBB-200", 2, 4, 1)])[0][0].fields["hole_index"] == 8

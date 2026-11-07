"""Sync phase 1: HLC, op checks, Store actions as ops, and merges that converge in any order."""
import random

import pytest

from conftest import HOME, UNIVERSE, pick_system
from homebound.mapdb.store import Signature, Store
from homebound.sync.hlc import HybridClock, format_hlc, hlc_milliseconds, is_hlc, parse_hlc, stamp
from homebound.sync.merge import BatchRejected, MapState, apply_op, check_batch, stamp_server_times
from homebound.sync.protocol import (InvalidOp, Op, batch_from_json, full_ops_for_rows, op_from_json,
                                     ops_from_action)

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

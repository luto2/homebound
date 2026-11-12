"""Sync of the lost-connection mark (``system.cut_off_at``) and the map's watch list (``watched_systems``).

Both came with protocol 2 (task 18, plan-watched-systems.md): a protocol 1 peer must never get
them, since it refuses an unknown field or setting key (and keeps asking for the batch again).
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import HOME, UNIVERSE, pick_system
from homebound.mapdb.store import SETTINGS_MIRRORED
from homebound.mapping.naming import Namer
from homebound.mapping.tracker import CUT_OFF_GRACE, Tracker
from homebound.mapping.watch import WATCH_SETTING, map_watch_list, set_map_watch_list
from homebound.sync import protocol
from homebound.sync.hlc import format_hlc
from homebound.sync.protocol import (GATED_FIELDS, GATED_SETTINGS, InvalidOp, entity_for_peer, message_for_peer,
                                     op_for_peer, op_from_json)
from server_helpers import Harness, Socket
from test_sync_gate import A, B, Install, engine_on, setup_map
from test_ui_cloud import cloud, spin  # noqa: F401 - cloud is a fixture

C5 = pick_system("C5", exclude=(HOME.id,)).id
C3 = pick_system("C3", exclude=(HOME.id, C5)).id
RANCER = UNIVERSE.by_name("Rancer").id


def setting_op(key: str, value, ms: int = 1, client: str = A) -> dict:
    """A setting op in wire form."""
    return {"e": "setting", "k": key, "f": {"value": value}, "hlc": format_hlc(ms, 0), "c": client}


# ---------------------------------------------------------------- the rules

def test_the_new_parts_are_gated_at_protocol_2() -> None:
    """cut_off_at is a gated system field, watched_systems a gated setting key."""
    assert GATED_FIELDS["system"]["cut_off_at"] == protocol.GatedField(2, None)
    assert GATED_SETTINGS == {"watched_systems": 2} and WATCH_SETTING in protocol.MAP_SETTING_KEYS
    assert SETTINGS_MIRRORED == ("home_system", "naming_scheme")


def test_the_values_are_checked() -> None:
    """The watch list may be long but must be a list of IDs; other settings stay short texts."""
    long_list = "[" + ",".join(str(30000000 + number) for number in range(150)) + "]"
    assert op_from_json(setting_op(WATCH_SETTING, long_list)).fields["value"] == long_list
    assert op_from_json(setting_op(WATCH_SETTING, "")).fields["value"] == ""
    for bad in ("Jita", "[1.5]", "[0]", 5):
        with pytest.raises(InvalidOp):
            op_from_json(setting_op(WATCH_SETTING, bad))
    with pytest.raises(InvalidOp):
        op_from_json(setting_op("home_system", "J" * 101))
    op_from_json(setting_op("home_system", "J161940"))
    marked = {"e": "system", "k": C5, "f": {"cut_off_at": "2026-11-12T10:00:00+00:00"}, "hlc": format_hlc(1, 0), "c": A}
    assert op_from_json(marked).fields == {"cut_off_at": "2026-11-12T10:00:00+00:00"}
    with pytest.raises(InvalidOp):
        op_from_json(marked | {"f": {"cut_off_at": 5}})


def test_an_old_peer_never_gets_the_mark_or_the_watch_list() -> None:
    """The mark is stripped from an op; a watch-list op and its merged state are left out whole."""
    marked = {"e": "system", "k": C5, "f": {"cut_off_at": "2026-11-12T10:00:00+00:00", "notes": "x"},
              "hlc": format_hlc(1, 0), "c": A}
    assert op_for_peer(marked, 1)["f"] == {"notes": "x"} and op_for_peer(marked, 2) == marked
    watched = setting_op(WATCH_SETTING, f"[{RANCER}]")
    assert op_for_peer(watched, 1) is None and op_for_peer(watched, 2) == watched
    assert op_for_peer(setting_op("home_system", "J161940"), 1) is not None
    state = {"e": "setting", "k": WATCH_SETTING, "f": {"value": "[]"}, "clocks": {"value": "1"}, "alive": "1", "dead": ""}
    assert entity_for_peer(state, 1) is None and entity_for_peer(state, 2) == state
    snapshot = message_for_peer({"t": "snapshot", "entities": [state]}, 1)
    assert snapshot["entities"] == []
    batch = message_for_peer({"t": "ops", "seq": 4, "ops": [watched, marked]}, 1)
    assert batch["seq"] == 4 and batch["ops"] == [op_for_peer(marked, 1)]


# ---------------------------------------------------------------- through the server

def chain_on(install: Install) -> Tracker:
    """Home -> C5 -> C3 on an install's map (one action each, pushed), and a tracker over it."""
    store = install.store
    with store.batch():
        store.add_system(C5, parent_id=HOME.id, hole_index=1)
        store.add_connection(HOME.id, C5)
    with store.batch():
        store.add_system(C3, parent_id=C5, hole_index=1)
        store.add_connection(C5, C3)
    return Tracker(store, UNIVERSE, Namer(store, UNIVERSE, "numeric", HOME.id))


def test_the_mark_and_the_watch_list_reach_every_new_member_and_no_old_one(caplog) -> None:
    """Kira loses a hole and watches a system: Bob (protocol 2) sees both and his app removes the piece in
    an hour; an old client follows the rest of the map without errors."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    writer, reader = Install(harness, kira, A, map_id, protocol=2), Install(harness, bob, B, map_id, protocol=2)
    old = Install(harness, bob, "cccccccc", map_id, protocol=1)
    for install in (writer, reader, old):
        install.connect()
    tracker = chain_on(writer)
    when = datetime(2026, 11, 12, 10, tzinfo=timezone.utc)
    assert tracker.disconnect(writer.store.connection(HOME.id, C5).id, when) == sorted([C5, C3])
    set_map_watch_list(writer.store, [RANCER])
    writer.pump()
    reader.pump()
    old.pump()
    assert writer.replica.outbox() == []
    reading = Tracker(reader.store, UNIVERSE, Namer(reader.store, UNIVERSE, "numeric", HOME.id))
    assert reading.cut_off_since(C5) == when and map_watch_list(reader.store) == [RANCER]
    assert reading.not_mapped_from(RANCER) == "watched"
    assert old.store.has_system(C5) and old.store.connection(HOME.id, C5) is None
    assert old.store.system(C5).cut_off_at is None and old.store.map_setting(WATCH_SETTING) is None
    assert reading.expire(when + CUT_OFF_GRACE + timedelta(minutes=1)) >= 1    # Bob's app runs the countdown too
    assert not reader.store.has_system(C5)
    reader.pump()
    writer.pump()
    assert not writer.store.has_system(C5)                     # and the removal reaches Kira
    assert "could not handle" not in caplog.text


def test_the_server_refuses_a_watch_list_with_an_unknown_system() -> None:
    """Every ID must be a real solar system (the same check as for keys and ends)."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    socket = Socket(harness, kira, A, protocol=2)
    socket.join(map_id)
    assert socket.push(map_id, [setting_op(WATCH_SETTING, "[123]", 10)])["code"] == "invalid"
    assert socket.push(map_id, [setting_op(WATCH_SETTING, f"[{RANCER}]", 11)])["t"] == "ack"
    editor = Socket(harness, bob, B, protocol=2)
    editor.join(map_id)
    assert editor.push(map_id, [setting_op(WATCH_SETTING, "", 12, B)])["t"] == "ack"   # any editor may change it


def test_a_new_client_sends_an_old_server_no_watch_list() -> None:
    """Against a protocol 1 server the list stays local: the batch is dropped from the outbox, not sent."""
    sent: list[dict] = []
    engine, replica = engine_on({"protocol": 1}, sent)
    with replica.store.batch() as action:
        set_map_watch_list(replica.store, [RANCER])
    before = len(sent)
    engine.local_action("m_1", replica, action)
    assert len(sent) == before and all(message_for_peer(message, 1) == message for message in sent)
    assert not any(op["k"] == WATCH_SETTING for op in replica.snapshot_ops(engine.clock, A, protocol=1))
    assert any(op["k"] == WATCH_SETTING for op in replica.snapshot_ops(engine.clock, A))


def test_the_apps_share_the_watch_list_and_the_mark(cloud) -> None:
    """Two apps on one cloud map: the map's list and the CUT OFF mark reach Bob; a private watch doesn't."""
    harness, kira, bob, code, statuses = cloud
    name = bob.join_with_code(code)
    bob.state.switch_map(name)
    assert spin(lambda: bob.state.store.has_system(C5))
    kira.state.add_watch(RANCER)
    kira.state.add_watch(C3, private=True)
    kira.state.tracker.disconnect(kira.state.store.connection(HOME.id, C5).id)
    assert spin(lambda: bob.state.watch_entries() == [(RANCER, False)] and bob.state.cut_off_age(C5) != "")
    assert bob.state.settings.maps[name].get(WATCH_SETTING) is None and not hasattr(bob.state.settings, WATCH_SETTING)
    assert bob.state.tracker.watched() == {RANCER}

"""Ships and structures on a cloud map: they sync like sigs between protocol 2 clients, never reach protocol 1."""
import json

from conftest import HOME, UNIVERSE
from homebound.mapping.signature_parser import merge_scan, parse_paste
from homebound.sync.protocol import op_for_peer
from homebound_server.prune import prune_ops
from homebound.sync.merge import MapState
from homebound.sync.protocol import op_from_json
from test_scan_paste import REAL_DSCAN, SHIPS_DSCAN
from test_sync_gate import A, B, C5, Install, setup_map
from server_helpers import Harness


def placed(install: Install) -> None:
    """Put C5 under home, linked, on the install's map."""
    with install.store.batch():
        install.store.add_system(C5, parent_id=HOME.id, hole_index=1)
        install.store.add_connection(HOME.id, C5)


def test_a_paste_reaches_the_other_new_client() -> None:
    """Kira pastes a d-scan in C5: Bob gets the structures and the ships; the next scan replaces the ships there too."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    writer, reader = Install(harness, kira, A, map_id, protocol=2), Install(harness, bob, B, map_id, protocol=2)
    writer.connect()
    reader.connect()
    placed(writer)
    merge_scan(writer.store, C5, parse_paste(REAL_DSCAN + SHIPS_DSCAN, UNIVERSE))
    writer.pump()
    reader.pump()
    assert writer.replica.outbox() == []
    assert [(s.type_name, s.name) for s in reader.store.structures(C5)] == [
        (s.type_name, s.name) for s in writer.store.structures(C5)]
    assert len(reader.store.structures(C5)) == 5
    assert [ship.ship_key for ship in reader.store.ships(C5)] == ["587-1", "587-2", "11978-1", "33474-1"]
    assert reader.store.ships(C5)[0].name == "Bob's Rifter"
    merge_scan(writer.store, C5, parse_paste(SHIPS_DSCAN.splitlines()[2] + "\n", UNIVERSE))
    writer.store.update_structure(C5, "Fortizar", "J161940 - Perry", owner_ticker="PERRY")
    writer.pump()
    reader.pump()
    assert [ship.ship_key for ship in reader.store.ships(C5)] == ["11978-1"]
    assert reader.store.structure(C5, "Fortizar", "J161940 - Perry").owner_ticker == "PERRY"
    # the "not seen" mark is the paster's own, like a sig's missing mark
    assert {s.state for s in writer.store.structures(C5)} == {"missing"}
    assert {s.state for s in reader.store.structures(C5)} == {""}


def test_a_removed_system_keeps_its_structures_for_everyone() -> None:
    """Removing the system deletes its ships on the other client; its structures stay and come back with it."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    writer, reader = Install(harness, kira, A, map_id, protocol=2), Install(harness, bob, B, map_id, protocol=2)
    writer.connect()
    reader.connect()
    placed(writer)
    merge_scan(writer.store, C5, parse_paste(REAL_DSCAN + SHIPS_DSCAN, UNIVERSE))
    writer.pump()
    writer.store.remove_system(C5)
    writer.pump()
    reader.pump()
    assert not reader.store.has_system(C5) and reader.store.ships(C5) == []
    assert len(reader.store.structures(C5)) == 5
    later = Install(harness, bob, B, map_id, protocol=2)
    later.connect()
    assert len(later.store.structures(C5)) == 5 and later.store.ships(C5) == []


def test_an_old_client_gets_no_ships_or_structures(caplog) -> None:
    """Protocol 1 client next to a new one: none of it reaches it, live or in a snapshot, and nothing fails."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    writer, old = Install(harness, kira, A, map_id, protocol=2), Install(harness, bob, B, map_id, protocol=1)
    writer.connect()
    old.connect()
    placed(writer)
    merge_scan(writer.store, C5, parse_paste(REAL_DSCAN + SHIPS_DSCAN, UNIVERSE))
    writer.pump()
    old.pump()                                   # Install checks each message is one a protocol 1 client takes
    assert old.store.has_system(C5) and old.store.structures() == [] and old.store.ships(C5) == []
    later = Install(harness, bob, B, map_id, protocol=1)
    later.connect()
    assert later.store.has_system(C5) and later.store.structures() == [] and later.store.ships(C5) == []
    assert "could not handle" not in caplog.text


def test_the_upload_of_a_map_carries_its_ships_and_structures() -> None:
    """Making a cloud map from a local one sends both to a new server and neither to an old one."""
    harness = Harness()
    map_id, kira, bob = setup_map(harness)
    install = Install(harness, kira, A, map_id, protocol=2)
    install.store.add_system(HOME.id)
    merge_scan(install.store, HOME.id, parse_paste(REAL_DSCAN + SHIPS_DSCAN, UNIVERSE))
    from homebound.sync.hlc import HybridClock
    ops = install.replica.snapshot_ops(HybridClock(), A)
    assert {op["e"] for op in ops} >= {"ship", "structure"}
    assert {op["e"] for op in install.replica.snapshot_ops(HybridClock(), A, protocol=1)}.isdisjoint({"ship", "structure"})
    assert all(op_for_peer(op, 2) == op for op in ops)


def test_prune_takes_the_ships_of_a_dead_system_and_keeps_its_structures() -> None:
    """Server auto-prune deletes ships with their system; structures are never pruned."""
    from datetime import datetime, timezone
    state = MapState()
    ops = [{"e": "system", "k": C5, "f": {"added_at": "2020-01-01T00:00:00+00:00"}, "full": True,
            "placement": {"parent_id": None, "hole_index": None}, "hlc": "000000000001-0000", "c": A},
           {"e": "ship", "k": [C5, "587-1"], "f": {"type_name": "Rifter", "category": "ship"}, "full": True,
            "hlc": "000000000001-0000", "c": A},
           {"e": "structure", "k": [C5, "Fortizar", "x"], "f": {"source": "paste"}, "full": True,
            "hlc": "000000000001-0000", "c": A}]
    state.apply_batch([op_from_json(message) for message in ops])
    pruned = prune_ops(state, HOME.id, datetime(2026, 11, 12, tzinfo=timezone.utc), "000000000002-0000", A)
    deleted = {(op.entity, json.dumps(list(op.key) if isinstance(op.key, tuple) else op.key)) for op in pruned if op.delete}
    assert ("ship", json.dumps([C5, "587-1"])) in deleted and ("system", json.dumps(C5)) in deleted
    assert not any(entity == "structure" for entity, _ in deleted)

"""Combat-probe and directional scan pastes: parsing, the merge rules, ships and structures in the Store.

The real paste from the owner (``tests/data/dscan_j161940_structures.txt``) is a directional scan of
a Fortizar and four Customs Offices (plan-combat-scan.md, plan-structure-list.md).
"""
import importlib.util
import json
import sqlite3
import zipfile
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import HOME, SCAN, UNIVERSE, pick_system
from homebound.mapdb.store import Ship, Signature, Store, Structure
from homebound.mapping import structures as structure_rules
from homebound.mapping.scan_type_table import SCAN_TYPES
from homebound.mapping.signature_parser import (
    ScanPaste, ScannedObject, merge_scan, merge_signatures, object_type, parse_paste, parse_scanner_text, ship_keys,
)
from homebound.sync import protocol
from homebound.sync.hlc import format_hlc
from homebound.sync.merge import BatchRejected, MapState, check_batch, referenced_systems

DATA = Path(__file__).parent / "data"
REAL_DSCAN = (DATA / "dscan_j161940_structures.txt").read_text(encoding="utf-8")
OTHER = pick_system("C3").id
SHIPS_DSCAN = ("587\tBob's Rifter\tRifter\t1.2 AU\n"
               "587\tRifter\tRifter\t3 AU\n"
               "11978\tKira's Scimitar\tScimitar\t12 km\n"
               "33474\tDepot\tMobile Depot\t1 AU\n"
               "2454\tHobgoblin I\tHobgoblin I\t2 km\n"           # a drone: left out
               "30000\tJ161940 IV\tPlanet (Barren)\t6.8 AU\n")    # not a known type: left out
COMBAT_PROBES = ("QWE-123\tShip\tFrigate\tRifter\t100,0%\t2,1 AU\n"
                 "RTY-456\tStructure\tCitadel\tAstrahus\t100,0%\t9 AU\n"
                 "UIO-789\tDrone\tCombat Drone\tHobgoblin I\t100,0%\t1 AU\n"
                 "PAS-012\tDeployable\tMobile Depot\tMobile Depot\t80,0%\t4 AU\n")


@pytest.fixture
def store():
    """An empty map with home and one other system on it."""
    with Store(":memory:") as map_store:
        map_store.add_system(HOME.id)
        map_store.add_system(OTHER)
        yield map_store


# ---------------------------------------------------------------- parsing

def test_the_real_dscan_paste_is_one_fortizar_and_four_customs_offices() -> None:
    """Type ID, name, type and distance: the type ID says each row is a structure."""
    paste = parse_paste(REAL_DSCAN, UNIVERSE)
    assert paste.signatures == [] and paste.combat
    assert [(item.kind, item.type_name, item.group_name, item.name, item.type_id, item.distance)
            for item in paste.objects] == [
        ("structure", "Fortizar", "Citadel", "J161940 - Perry", 35833, "0 m"),
        ("structure", "Customs Office", "Orbital Infrastructure", "Customs Office (J161940 IV)", 2233, "6.8 AU"),
        ("structure", "Customs Office", "Orbital Infrastructure", "Customs Office (J161940 III)", 2233, "3.8 AU"),
        ("structure", "Customs Office", "Orbital Infrastructure", "Customs Office (J161940 II)", 2233, "6.5 AU"),
        ("structure", "Customs Office", "Orbital Infrastructure", "Customs Office (J161940 I)", 2233, "5.0 AU")]
    assert parse_scanner_text(REAL_DSCAN) == []          # never a signature, so never "Ice"


def test_dscan_ships_deployables_and_what_is_left_out() -> None:
    """Ships come from the universe file, deployables from the scan table; drones and planets are left out."""
    paste = parse_paste(SHIPS_DSCAN, UNIVERSE)
    assert [(item.kind, item.type_name, item.group_name, item.name) for item in paste.objects] == [
        ("ship", "Rifter", "Frigate", "Bob's Rifter"), ("ship", "Rifter", "Frigate", "Rifter"),
        ("ship", "Scimitar", "Logistics", "Kira's Scimitar"), ("deployable", "Mobile Depot", "Mobile Depot", "Depot")]
    assert paste.noise == 2
    assert [item.kind for item in parse_paste(SHIPS_DSCAN).objects] == ["deployable"]   # no universe: no ships
    assert parse_paste("30000\tJ161940 IV\tPlanet\t1 AU\n", UNIVERSE).combat      # only celestials: still a scan


def test_the_type_lookup() -> None:
    """Ships, structures, POCOs, Skyhooks, towers and deployables have a kind; POS modules and the rest don't."""
    assert object_type(587, UNIVERSE) == ("ship", "Rifter", "Frigate")
    assert object_type(587, None) is None
    assert object_type(2233, None) == ("structure", "Customs Office", "Orbital Infrastructure")
    assert object_type(81080, None)[0] == object_type(12235, None)[0] == object_type(35832, None)[0] == "structure"
    assert object_type(33474, None)[0] == "deployable"
    assert all(kind in ("structure", "deployable") for _, _, kind in SCAN_TYPES.values())
    assert not any(group in ("Mobile Laser Sentry", "Combat Drone") for _, group, _ in SCAN_TYPES.values())
    assert object_type(1, UNIVERSE) is None


def test_combat_probe_rows_are_objects_not_signatures() -> None:
    """The scan group says what a probe row is: a ship, a structure, a deployable, or a drone (left out)."""
    paste = parse_paste(SCAN + COMBAT_PROBES, UNIVERSE)
    assert [row.signature_id for row in paste.signatures] == ["ABC-123", "DEF-456", "GHI-789", "XYZ-000"]
    assert [(item.kind, item.type_name, item.group_name, item.signature_id) for item in paste.objects] == [
        ("ship", "Rifter", "Frigate", "QWE-123"), ("structure", "Astrahus", "Citadel", "RTY-456"),
        ("deployable", "Mobile Depot", "Mobile Depot", "PAS-012")]
    assert paste.noise == 1 and paste.combat
    assert [row.signature_id for row in parse_scanner_text(COMBAT_PROBES)] == []
    other_language = parse_paste("ABC-123\tSignature cosmique\tTrou de ver\t\t100%\t1 AU\n")
    assert [row.kind for row in other_language.signatures] == ["sig"] and not other_language.combat
    assert not parse_paste("hello\nworld\n12\n")


def test_ship_keys_are_stable_for_the_same_scan() -> None:
    """A probe row keeps its probe ID; a d-scan row is its type ID and its count of that type."""
    objects = parse_paste(SHIPS_DSCAN + COMBAT_PROBES, UNIVERSE).objects
    movable = [item for item in objects if item.kind != "structure"]
    assert ship_keys(movable) == ["587-1", "587-2", "11978-1", "33474-1", "QWE-123", "PAS-012"]
    assert ship_keys([ScannedObject("ship", "Rifter")]) == ["x-1"]


# ---------------------------------------------------------------- the merge rules

def test_the_real_paste_fills_the_structure_list_and_leaves_the_sigs_alone(store) -> None:
    """The owner's paste: five structures, no ship, every signature and the rescan time untouched."""
    merge_signatures(store, HOME.id, parse_scanner_text(SCAN))
    signatures, scanned = store.signatures(HOME.id), store.system(HOME.id).scanned_at
    revision = store.revision
    result = merge_scan(store, HOME.id, parse_paste(REAL_DSCAN, UNIVERSE))
    assert store.revision == revision + 1                              # one action: one Undo step
    assert result.signatures is None and result.combat and result.ships == 0
    assert sorted(result.structures_added) == sorted((item.type_name, item.name)
                                                     for item in parse_paste(REAL_DSCAN, UNIVERSE).objects)
    assert [(structure.type_name, structure.name, structure.state, structure.source)
            for structure in store.structures(HOME.id)] == [
        ("Fortizar", "J161940 - Perry", "new", "paste"),
        ("Customs Office", "Customs Office (J161940 I)", "new", "paste"),
        ("Customs Office", "Customs Office (J161940 II)", "new", "paste"),
        ("Customs Office", "Customs Office (J161940 III)", "new", "paste"),
        ("Customs Office", "Customs Office (J161940 IV)", "new", "paste")]
    assert store.signatures(HOME.id) == signatures and store.system(HOME.id).scanned_at == scanned
    assert store.structures(OTHER) == [] and store.ships(HOME.id) == []


def test_a_combat_only_paste_marks_no_signature_missing(store) -> None:
    """The bug: combat rows were sigs, so a combat paste flagged every real sig missing. Now nothing changes."""
    merge_signatures(store, HOME.id, parse_scanner_text(SCAN))
    before = store.signatures(HOME.id)
    store.update_system(HOME.id, scanned_at="2026-01-01T00:00:00+00:00")
    result = merge_scan(store, HOME.id, parse_paste(COMBAT_PROBES, UNIVERSE))
    assert result.signatures is None
    assert store.signatures(HOME.id) == before
    assert all(signature.state != "missing" for signature in store.signatures(HOME.id))
    assert store.system(HOME.id).scanned_at == "2026-01-01T00:00:00+00:00"
    assert [ship.ship_key for ship in store.ships(HOME.id)] == ["QWE-123", "PAS-012"]
    assert [structure.type_name for structure in store.structures(HOME.id)] == ["Astrahus"]


def test_a_mixed_paste_treats_each_part_by_its_own_rule(store) -> None:
    """Sigs merge as before (and count as a rescan); ships replace; structures are matched."""
    result = merge_scan(store, HOME.id, parse_paste(SCAN + COMBAT_PROBES, UNIVERSE))
    assert len(result.signatures.added) == 4 and result.ships == 1 and result.deployables == 1
    assert store.system(HOME.id).scanned_at
    assert {signature.signature_id for signature in store.signatures(HOME.id)} == {
        "ABC-123", "DEF-456", "GHI-789", "XYZ-000"}
    # a cosmic paste alone leaves the ships and structures alone
    merge_scan(store, HOME.id, parse_paste(SCAN.splitlines()[0] + "\n", UNIVERSE))
    assert len(store.ships(HOME.id)) == 2 and store.structures(HOME.id)[0].state == "new"


def test_the_next_combat_paste_replaces_the_ships(store) -> None:
    """Ships not in the next scan go (deleted, not marked); the same scan again keeps their first-seen time."""
    merge_scan(store, HOME.id, parse_paste(SHIPS_DSCAN, UNIVERSE))
    first = {ship.ship_key: ship for ship in store.ships(HOME.id)}
    assert [ship.ship_key for ship in store.ships(HOME.id)] == ["587-1", "587-2", "11978-1", "33474-1"]
    assert first["587-1"].name == "Bob's Rifter" and first["33474-1"].category == "deployable"
    merge_scan(store, HOME.id, parse_paste(SHIPS_DSCAN.splitlines()[0] + "\n", UNIVERSE))
    assert [ship.ship_key for ship in store.ships(HOME.id)] == ["587-1"]
    assert store.ships(HOME.id)[0].created_at == first["587-1"].created_at
    merge_scan(store, HOME.id, parse_paste(REAL_DSCAN, UNIVERSE))       # a scan with no ship: none left
    assert store.ships(HOME.id) == []


def test_ships_stay_until_the_next_combat_paste(store) -> None:
    """No time limit: an hour-old ship is still listed until a scan of that system replaces it."""
    store.replace_ships(HOME.id, [Ship(HOME.id, "587-1", "Rifter", 587, "Frigate", seen_at="2020-01-01T00:00:00+00:00")])
    merge_scan(store, OTHER, parse_paste(SHIPS_DSCAN, UNIVERSE))
    assert [ship.ship_key for ship in store.ships(HOME.id)] == ["587-1"]


def test_structures_are_matched_by_type_and_name(store) -> None:
    """The same paste again only sees them again; one missing from a later scan is marked, not deleted."""
    merge_scan(store, HOME.id, parse_paste(REAL_DSCAN, UNIVERSE))
    store.update_structure(HOME.id, "Fortizar", "J161940 - Perry", owner_ticker="PERRY", note="staging")
    store.update_structure(HOME.id, "Fortizar", "J161940 - Perry", seen_at="2026-01-01T00:00:00+00:00")
    result = merge_scan(store, HOME.id, parse_paste(REAL_DSCAN, UNIVERSE))
    assert result.structures_added == [] and len(result.structures_seen) == 5
    fortizar = store.structure(HOME.id, "Fortizar", "J161940 - Perry")
    assert fortizar.state == "" and fortizar.owner_ticker == "PERRY" and fortizar.note == "staging"
    assert fortizar.seen_at > "2026-01-01T00:00:00+00:00"
    result = merge_scan(store, HOME.id, parse_paste(REAL_DSCAN.splitlines()[0] + "\n", UNIVERSE))
    assert len(result.structures_missing) == 4
    states = {structure.name: structure.state for structure in store.structures(HOME.id)}
    assert states.pop("J161940 - Perry") == "" and set(states.values()) == {"missing"} and len(states) == 4
    merge_scan(store, HOME.id, parse_paste(REAL_DSCAN, UNIVERSE))
    assert {structure.state for structure in store.structures(HOME.id)} == {""}


def test_a_paste_is_one_undo_step(store) -> None:
    """Undo of a combat paste takes its ships and structures away again."""
    actions = []
    store.listeners.append(actions.append)
    merge_scan(store, HOME.id, parse_paste(REAL_DSCAN + SHIPS_DSCAN, UNIVERSE))
    assert len(actions) == 1
    store.undo(actions[0])
    assert store.structures(HOME.id) == [] and store.ships(HOME.id) == []


def test_merge_scan_checks_its_arguments(store) -> None:
    """Wrong types fail loudly."""
    with pytest.raises(TypeError):
        merge_scan(store, HOME.id, [])
    with pytest.raises(ValueError):
        merge_scan(store, 0, ScanPaste())


# ---------------------------------------------------------------- the Store

def test_structures_survive_the_system_leaving_the_map(store) -> None:
    """remove_system takes the ships with it but keeps the structures; they are there when it is mapped again."""
    merge_scan(store, OTHER, parse_paste(REAL_DSCAN + SHIPS_DSCAN, UNIVERSE))
    actions = []
    store.listeners.append(actions.append)
    store.remove_system(OTHER)
    deleted = {change.entity for change in actions[0].changes if change.deleted}
    assert deleted == {"ship", "system"}                 # every ship gets its own tombstone
    assert store.ships(OTHER) == [] and len(store.structures(OTHER)) == 5
    store.add_system(OTHER)
    assert len(store.structures(OTHER)) == 5 and store.ships(OTHER) == []
    assert len(store.structures()) == 5


def test_structure_edits_and_their_checks(store) -> None:
    """Hand-added structures, updates, deletes and the values the Store refuses."""
    added = store.upsert_structure(Structure(HOME.id, "Astrahus", "Home", source="hand", timer_kind="armor",
                                             timer_at="2026-11-13T18:00:00+00:00"))
    assert added.created_at and added.seen_at == added.created_at and added.source == "hand"
    again = store.upsert_structure(replace(added, created_at="", note="x"))
    assert again.created_at == added.created_at and again.note == "x"
    assert store.update_structure(HOME.id, "Astrahus", "Home", owner_ticker="ABC")
    assert not store.update_structure(HOME.id, "Astrahus", "Gone", owner_ticker="ABC")
    assert store.update_structure(HOME.id, "Astrahus", "Home")
    with pytest.raises(ValueError):
        store.update_structure(HOME.id, "Astrahus", "Home", colour="red")
    with pytest.raises(ValueError):
        store.update_structure(HOME.id, "Astrahus", "Home", timer_kind="soon")
    with pytest.raises(ValueError):
        store.upsert_structure(Structure(HOME.id, "Astrahus", "X", source="guess"))
    with pytest.raises(ValueError):
        store.upsert_structure(Structure(HOME.id, "", "X"))
    with pytest.raises(TypeError):
        store.upsert_structure("Astrahus")
    actions = []
    store.listeners.append(actions.append)
    assert store.delete_structure(HOME.id, "Astrahus", "Home")
    assert not store.delete_structure(HOME.id, "Astrahus", "Home")
    store.undo(actions[0])
    assert store.structure(HOME.id, "Astrahus", "Home").owner_ticker == "ABC"


def test_ship_rows_and_their_checks(store) -> None:
    """replace_ships checks each row; a system not on the map gets none; undo brings a deleted ship back."""
    with pytest.raises(ValueError):
        store.replace_ships(HOME.id, [Ship(OTHER, "587-1", "Rifter")])
    with pytest.raises(ValueError):
        store.replace_ships(HOME.id, [Ship(HOME.id, "587-1", "Rifter", category="wreck")])
    with pytest.raises(TypeError):
        store.replace_ships(HOME.id, ["Rifter"])
    gone = pick_system("C2").id
    assert store.replace_ships(gone, [Ship(gone, "587-1", "Rifter")]) == 0 and store.ships(gone) == []
    store.replace_ships(HOME.id, [Ship(HOME.id, "587-1", "Rifter"), Ship(HOME.id, "33474-1", "Mobile Depot",
                                                                         category="deployable")])
    assert [ship.category for ship in store.ships(HOME.id)] == ["ship", "deployable"]   # ships first
    actions = []
    store.listeners.append(actions.append)
    assert store.delete_ships(HOME.id, ["587-1", "nope"]) == 1
    store.undo(actions[0])
    assert [ship.ship_key for ship in store.ships(HOME.id)] == ["587-1", "33474-1"]


def test_an_old_map_file_gets_the_empty_tables(tmp_path) -> None:
    """A map file from before ships and structures opens with both tables, empty."""
    path = tmp_path / "old.db"
    with Store(path) as old:
        old.add_system(HOME.id)
    database = sqlite3.connect(path)
    database.execute("DROP TABLE ships")
    database.execute("DROP TABLE structures")
    database.commit()
    database.close()
    with Store(path) as reopened:
        assert reopened.ships(HOME.id) == [] and reopened.structures(HOME.id) == []


# ---------------------------------------------------------------- what the map shows

NOW = datetime(2026, 11, 12, 12, tzinfo=timezone.utc)


def at(**delta) -> str:
    """An ISO time relative to NOW."""
    return (NOW + timedelta(**delta)).isoformat(timespec="seconds")


def test_structure_badges_and_timer_texts() -> None:
    """POCO, POS and Skyhook get short badges; timers read "Armor in 4h", "Hull 12m ago"."""
    assert structure_rules.structure_badge(Structure(1, "Customs Office", "x", group_name="Orbital Infrastructure")) == "POCO"
    assert structure_rules.structure_badge(Structure(1, "Amarr Control Tower", "x", group_name="Control Tower")) == "POS"
    assert structure_rules.structure_badge(Structure(1, "Fortizar", "x", group_name="Citadel")) == "Fortizar"
    armor = Structure(1, "Fortizar", "x", timer_at=at(hours=4), timer_kind="armor")
    assert structure_rules.timer_text(armor, NOW) == "Armor in 4h"
    assert structure_rules.timer_text(replace(armor, timer_at=at(minutes=-12), timer_kind="hull"), NOW) == "Hull 12m ago"
    assert structure_rules.timer_text(replace(armor, timer_kind=None), NOW) == "Timer in 4h"
    assert structure_rules.timer_text(replace(armor, timer_at=None), NOW) == ""
    assert structure_rules.timer_left(replace(armor, timer_at="not a time"), NOW) is None


def test_the_timer_tab_and_the_warning() -> None:
    """The tab shows the next timer under 24 h; the warning picks timers in the next 15 minutes."""
    soon = Structure(1, "Astrahus", "a", timer_at=at(minutes=10))
    later = Structure(1, "Fortizar", "b", timer_at=at(hours=30))
    past = Structure(1, "Raitaru", "c", timer_at=at(minutes=-5))
    assert structure_rules.timer_tab_label([later, soon, past], NOW) == "⏱ 10m"
    assert structure_rules.timer_tab_label([later], NOW) == ""
    assert structure_rules.timer_tab_label([past], NOW) == ""
    assert structure_rules.timers_to_warn([soon, later, past], NOW) == [soon]
    assert structure_rules.next_timer([], NOW) is None


def test_fresh_ships_and_ship_rows() -> None:
    """The ⚔ chip counts ships (not deployables) seen in the last 5 minutes; rows skip what repeats."""
    ships = [Ship(1, "a", "Rifter", group_name="Frigate", seen_at=at(minutes=-2)),
             Ship(1, "b", "Rifter", group_name="Frigate", seen_at=at(minutes=-6)),
             Ship(1, "c", "Mobile Depot", group_name="Mobile Depot", category="deployable", seen_at=at(minutes=-1))]
    assert structure_rules.fresh_ships(ships, NOW) == 1
    assert structure_rules.ship_text(Ship(1, "a", "Rifter", group_name="Frigate", name="Bob's Rifter",
                                          distance="1 AU")) == "Frigate · Rifter · Bob's Rifter · 1 AU"
    assert structure_rules.ship_text(Ship(1, "c", "Mobile Depot", group_name="Mobile Depot", name="Mobile Depot")) == "Mobile Depot"


# ---------------------------------------------------------------- the sync protocol

def op(entity: str, key, **values) -> dict:
    """A full upsert in wire form."""
    return {"e": entity, "k": key, "f": values, "full": True, "hlc": format_hlc(1, 0), "c": "aaaaaaaa"}


def test_ship_and_structure_ops_pass_the_checks() -> None:
    """The new entities and their keys are known; bad keys and values aren't."""
    ship = protocol.op_from_json(op("ship", [HOME.id, "587-1"], type_name="Rifter", type_id=587, category="ship"))
    assert ship.key == (HOME.id, "587-1")
    structure = protocol.op_from_json(op("structure", [HOME.id, "Fortizar", "J161940 - Perry"], type_id=35833,
                                         source="paste", timer_kind="hull"))
    assert structure.key == (HOME.id, "Fortizar", "J161940 - Perry")
    for bad in (op("ship", [HOME.id, "has space"], type_name="Rifter"), op("ship", [HOME.id], type_name="Rifter"),
                op("ship", [HOME.id, "587-1"], category="wreck"), op("ship", [HOME.id, "587-1"], type_name=""),
                op("structure", [HOME.id, "Fortizar", ""]), op("structure", [HOME.id, "Fortizar"]),
                op("structure", [HOME.id, "Fortizar", "x"], timer_kind="soon"),
                op("structure", [HOME.id, "Fortizar", "x"], source="guess"),
                op("signature", [HOME.id, "ABC-123"], kind="ship")):
        with pytest.raises(protocol.InvalidOp):
            protocol.op_from_json(bad)


def test_old_peers_never_get_ships_or_structures() -> None:
    """Both entities are gated at protocol 2: stripped from ops, snapshots and conflicts for a protocol 1 peer."""
    ships = op("ship", [HOME.id, "587-1"], type_name="Rifter")
    structures = op("structure", [HOME.id, "Fortizar", "x"], source="hand")
    assert protocol.GATED_ENTITIES["ship"] == protocol.GATED_ENTITIES["structure"] == 2
    assert protocol.ops_for_peer([ships, structures], 1) == []
    assert protocol.ops_for_peer([ships, structures], 2) == [ships, structures]
    snapshot = {"t": "snapshot", "entities": [{"e": "ship", "k": [HOME.id, "587-1"], "f": {}, "clocks": {}},
                                              {"e": "structure", "k": [HOME.id, "a", "b"], "f": {}, "clocks": {}}]}
    assert protocol.message_for_peer(snapshot, 1)["entities"] == []


def test_the_server_checks_the_references_of_ships_and_structures() -> None:
    """A ship needs its system on the map; a structure only a real solar system (it outlives the map)."""
    ships = protocol.op_from_json(op("ship", [HOME.id, "587-1"], type_name="Rifter"))
    structure = protocol.op_from_json(op("structure", [OTHER, "Fortizar", "x"], source="hand"))
    assert referenced_systems(ships) == [HOME.id] and referenced_systems(structure) == [OTHER]
    with pytest.raises(BatchRejected) as rejected:
        check_batch(MapState(), [ships])
    assert rejected.value.code == "conflict"
    check_batch(MapState(), [structure])                  # no system on the map: fine
    with pytest.raises(BatchRejected):
        check_batch(MapState(), [structure], system_exists=lambda system_id: False)


def test_rows_become_full_ops_and_back() -> None:
    """A map upload carries ships and structures with their natural keys."""
    rows = {"ship": [{"system_id": HOME.id, "ship_key": "587-1", "type_name": "Rifter", "category": "ship"}],
            "structure": [{"system_id": HOME.id, "type_name": "Fortizar", "name": "x", "source": "paste"}]}
    ops = protocol.full_ops_for_rows(rows, format_hlc(1, 0), "aaaaaaaa")
    assert [(item.entity, item.key) for item in ops] == [("ship", (HOME.id, "587-1")),
                                                         ("structure", (HOME.id, "Fortizar", "x"))]
    assert protocol.key_from_text(protocol.key_text(ops[1].key)) == (HOME.id, "Fortizar", "x")


# ---------------------------------------------------------------- the generator of the scan table

def test_the_scan_table_generator_reads_the_sde(tmp_path, monkeypatch) -> None:
    """Structures, orbitals (unpublished too), control towers and deployables; POS modules and ships aren't kept."""
    spec = importlib.util.spec_from_file_location("make_scan_types", Path(__file__).parents[1] / "tools/make_scan_types.py")
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    name = lambda text: {"en": text}  # noqa: E731
    lines = lambda *records: "\n".join(json.dumps(record) for record in records) + "\n"  # noqa: E731
    sde = tmp_path / "sde.zip"
    with zipfile.ZipFile(sde, "w") as archive:
        archive.writestr("categories.jsonl", lines({"_key": 65, "name": name("Structure")}, {"_key": 46, "name": name("Orbitals")},
                                                   {"_key": 23, "name": name("Starbase")}, {"_key": 22, "name": name("Deployable")},
                                                   {"_key": 6, "name": name("Ship")}))
        archive.writestr("groups.jsonl", lines({"_key": 1657, "categoryID": 65, "name": name("Citadel")},
                                               {"_key": 1025, "categoryID": 46, "name": name("Orbital Infrastructure")},
                                               {"_key": 365, "categoryID": 23, "name": name("Control Tower")},
                                               {"_key": 430, "categoryID": 23, "name": name("Mobile Laser Sentry")},
                                               {"_key": 1246, "categoryID": 22, "name": name("Mobile Depot")},
                                               {"_key": 4810, "categoryID": 22, "name": name("Mercenary Den")},
                                               {"_key": 25, "categoryID": 6, "name": name("Frigate")}))
        archive.writestr("types.jsonl", lines({"_key": 35833, "groupID": 1657, "published": True, "name": name("Fortizar")},
                                              {"_key": 2233, "groupID": 1025, "published": False, "name": name("Customs Office")},
                                              {"_key": 12235, "groupID": 365, "name": name("Amarr Control Tower")},
                                              {"_key": 16, "groupID": 430, "name": name("Small Laser Battery")},
                                              {"_key": 33474, "groupID": 1246, "name": name("Mobile Depot")},
                                              {"_key": 85230, "groupID": 4810, "name": name("Mercenary Den")},
                                              {"_key": 587, "groupID": 25, "name": name("Rifter")}))
    monkeypatch.setattr(tool, "OUTPUT", tmp_path / "table.py")
    monkeypatch.setattr("sys.argv", ["make_scan_types.py", str(sde)])
    assert tool.main() == 0
    namespace: dict = {}
    exec((tmp_path / "table.py").read_text(encoding="utf-8"), namespace)
    assert namespace["SCAN_TYPES"] == {
        2233: ("Customs Office", "Orbital Infrastructure", "structure"), 12235: ("Amarr Control Tower", "Control Tower", "structure"),
        33474: ("Mobile Depot", "Mobile Depot", "deployable"), 35833: ("Fortizar", "Citadel", "structure"),
        85230: ("Mercenary Den", "Mercenary Den", "structure")}


def test_a_structure_twice_in_one_paste_is_one_row(store) -> None:
    """Two rows with the same type and name (a scan copied twice) make one structure."""
    line = REAL_DSCAN.splitlines()[0] + "\n"
    result = merge_scan(store, HOME.id, parse_paste(line + line, UNIVERSE))
    assert result.structures_added == [("Fortizar", "J161940 - Perry")] and len(store.structures(HOME.id)) == 1

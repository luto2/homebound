"""Universe lookups and the pure helpers of the static data build."""
import io
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest

from conftest import HOME, UNIVERSE
from homebound.universe import build_static
from homebound.universe.db import Universe, UniverseDataError, size_for_jump_mass


def test_lookups_by_name_and_id() -> None:
    """Names ignore case and spaces; unknown names and IDs give None."""
    assert UNIVERSE.by_name("  j161940 ") == HOME and UNIVERSE.by_id(HOME.id) == HOME
    assert UNIVERSE.by_name("Nowhere") is None and UNIVERSE.by_id(1) is None
    assert HOME.system_class == "C5" and HOME.wormhole_space and not HOME.known_space
    assert UNIVERSE.by_name("Jita").known_space
    with pytest.raises(TypeError):
        UNIVERSE.by_name(None)
    with pytest.raises(TypeError):
        UNIVERSE.by_id("J161940")


def test_search_and_names() -> None:
    """Prefix search is sorted and limited; a blank prefix finds nothing."""
    hits = UNIVERSE.search("jit", limit=5)
    assert hits[0].name == "Jita" and len(hits) <= 5
    assert UNIVERSE.search("  ") == []
    assert "Jita" in UNIVERSE.all_names() and len(UNIVERSE.all_systems()) == len(UNIVERSE.all_names())
    with pytest.raises(ValueError):
        UNIVERSE.search("j", limit=-1)


def test_gates() -> None:
    """Jita and Perimeter share a gate; distances grow by one per jump."""
    jita, perimeter = UNIVERSE.by_name("Jita").id, UNIVERSE.by_name("Perimeter").id
    assert UNIVERSE.gate_adjacent(jita, perimeter) and perimeter in UNIVERSE.gate_neighbours(jita)
    distances = UNIVERSE.gate_distances(jita, 2)
    assert distances[jita] == 0 and distances[perimeter] == 1 and max(distances.values()) == 2
    assert UNIVERSE.gate_distances(HOME.id, 3) == {HOME.id: 0}
    with pytest.raises(ValueError):
        UNIVERSE.gate_distances(jita, -1)


def test_wormhole_types() -> None:
    """Types are looked up by code; a system offers its statics first and never K162."""
    h296 = UNIVERSE.wormhole_type(" h296 ")
    assert h296.destinations == ("C5",) and h296.size == "XL"
    assert UNIVERSE.wormhole_type("XXXX") is None and "K162" in UNIVERSE.wormhole_type_names()
    types = [wormhole_type.name for wormhole_type in UNIVERSE.wormhole_types_in(HOME.id)]
    assert types[0] == "H296" and "K162" not in types
    assert "C008" in types and "B274" not in types
    jita = [wormhole_type.name for wormhole_type in UNIVERSE.wormhole_types_in(UNIVERSE.by_name("Jita").id)]
    assert jita and all("HS" in UNIVERSE.wormhole_type(name).spawns_in for name in jita)
    assert UNIVERSE.wormhole_types_in(1) == [] and UNIVERSE.static_types(1) == []
    assert UNIVERSE.static_destination_classes(HOME.id) == ["C5"]
    with pytest.raises(ValueError):
        UNIVERSE.wormhole_type("")


def test_size_for_jump_mass() -> None:
    """Max jump mass maps to S/M/L/XL at CCP's limits."""
    assert [size_for_jump_mass(mass) for mass in (None, 0, 5e6, 62e6, 375e6, 2e9)] == [None, None, "S", "M", "L", "XL"]
    with pytest.raises(TypeError):
        size_for_jump_mass("big")
    with pytest.raises(ValueError):
        size_for_jump_mass(-1)


def test_missing_and_outdated_files(tmp_path) -> None:
    """A missing file or one without the current schema version asks for a rebuild."""
    with pytest.raises(UniverseDataError):
        Universe(tmp_path / "missing.sqlite")
    old = tmp_path / "old.sqlite"
    with closing(sqlite3.connect(old)) as database:
        database.executescript("CREATE TABLE meta (key TEXT, value TEXT);")
    with pytest.raises(UniverseDataError, match="schema 1"):
        Universe(old)
    broken = tmp_path / "broken.sqlite"
    with closing(sqlite3.connect(broken)) as database:
        database.executescript("CREATE TABLE systems (id INTEGER);")
    with pytest.raises(UniverseDataError):
        Universe(broken)
    with pytest.raises(TypeError):
        Universe(5)


def test_whtype_wander_reads_wandering_types_only() -> None:
    """Only "Wandering" respawns count; static-only types get no classes."""
    script = ("{wormhole:'C008', respawn:['Wandering'], spawn_in:['Class 5','Class 6','Nowhere']},"
              "{wormhole:'H296', respawn:['Static'], spawn_in:['Class 5']}")
    assert build_static.whtype_wander(script) == {"C008": ["C5", "C6"], "H296": []}
    with pytest.raises(TypeError):
        build_static.whtype_wander(None)


def test_known_space_label_rounds_like_the_client() -> None:
    """0.45 shows as 0.5 (high-sec); 0.04 shows as 0.0 (null-sec)."""
    assert [build_static.known_space_label(security) for security in (1.0, 0.45, 0.44, 0.05, 0.04, -0.5)] == [
        "HS", "HS", "LS", "LS", "NS", "NS"]
    with pytest.raises(ValueError):
        build_static.known_space_label(2.0)


def test_whole_number() -> None:
    """Numbers and numeric strings become ints; anything else None."""
    assert build_static.whole_number("12.7") == 12 and build_static.whole_number(5) == 5
    assert build_static.whole_number(None) is None and build_static.whole_number("x") is None


def test_json_lines_skips_blank_lines(tmp_path) -> None:
    """Each non-blank line of a zipped JSONL file is one record."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("x.jsonl", '{"a": 1}\n\n{"a": 2}\n')
    with zipfile.ZipFile(buffer) as archive:
        assert [record["a"] for record in build_static.json_lines(archive, "x.jsonl")] == [1, 2]


def test_fetch_uses_the_cache(tmp_path) -> None:
    """A cached file is not downloaded again."""
    cached = tmp_path / "file.json"
    cached.write_text("{}")
    assert build_static.fetch("https://example.invalid/", cached, refresh=False) == cached
    with pytest.raises(TypeError):
        build_static.fetch("https://example.invalid/", str(cached), refresh=False)


def test_resolve_statics_uses_pinned_answers(tmp_path, monkeypatch) -> None:
    """Agreeing sources are taken as they are; disputes use the pinned answer without asking zKill."""
    resolved = tmp_path / "statics_resolved.json"
    resolved.write_text(json.dumps({"J2": {"statics": ["B274"]}}))
    monkeypatch.setattr(build_static, "RESOLVED", resolved)
    monkeypatch.setattr(build_static, "zkill_statics", lambda system_id: pytest.fail("zKill was asked"))
    statics = build_static.resolve_statics({1: "J1", 2: "J2"}, {1: ["H296"], 2: ["A239"], 3: ["X"]},
                                           {1: ["H296"], 2: ["B274", "A239"]})
    assert statics == {1: ["H296"], 2: ["B274"]}


def test_resolve_statics_asks_zkill_for_new_disputes(tmp_path, monkeypatch) -> None:
    """A new dispute is settled by zKill (or the union) and pinned for the next build."""
    resolved = tmp_path / "statics_resolved.json"
    monkeypatch.setattr(build_static, "RESOLVED", resolved)
    monkeypatch.setattr(build_static.time, "sleep", lambda seconds: None)
    answers = {1: ["H296"], 2: None}
    monkeypatch.setattr(build_static, "zkill_statics", lambda system_id: answers[system_id])
    statics = build_static.resolve_statics({1: "J1", 2: "J2"}, {1: ["H296"], 2: ["A239"]},
                                           {1: ["D382"], 2: ["B274"]})
    assert statics == {1: ["H296"], 2: ["A239", "B274"]}
    pinned = json.loads(resolved.read_text())
    assert pinned["J1"]["source"] == "zkill" and pinned["J2"]["source"] == "union"

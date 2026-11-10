"""Universe lookups and the pure helpers of the static data build."""
import io
import json
import sqlite3
import zipfile
from contextlib import closing

import pytest

from conftest import HOME, UNIVERSE
from homebound.http import ssl_context
from homebound.platform.paths import static_data_path
from homebound.universe import build_static
from homebound.universe import db
from homebound.universe.db import Universe, UniverseDataError, size_for_jump_mass


UNIVERSE_PATH = static_data_path()


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


def test_empire_of_high_and_low_sec() -> None:
    """HS/LS systems take their region's empire (Jita is CONCORD, Derelik is Ammatar); null and w-space have none."""
    assert UNIVERSE.by_name("Jita").empire == "caldari" and UNIVERSE.by_name("Tama").empire == "caldari"
    assert UNIVERSE.by_name("Amarr").empire == "amarr" and UNIVERSE.by_name("Dodixie").empire == "gallente"
    assert UNIVERSE.by_name("Rens").empire == "minmatar" and UNIVERSE.by_name("Rancer").empire == "gallente"
    derelik = next(system for system in UNIVERSE.all_systems()
                   if system.region == "Derelik" and system.system_class == "HS")
    assert derelik.empire == "amarr"
    assert UNIVERSE.by_name("1DQ1-A").empire is None and HOME.empire is None


def test_nearest_hub() -> None:
    """The closest trade hub by gates, for HS/LS only; a hub is 0 jumps from itself."""
    jita, perimeter = UNIVERSE.by_name("Jita"), UNIVERSE.by_name("Perimeter")
    assert UNIVERSE.nearest_hub(jita.id) == ("Jita", 0) and UNIVERSE.nearest_hub(perimeter.id) == ("Jita", 1)
    assert UNIVERSE.nearest_hub(UNIVERSE.by_name("Rancer").id) == ("Hek", 3)
    assert UNIVERSE.nearest_hub(UNIVERSE.by_name("1DQ1-A").id) is None
    assert UNIVERSE.nearest_hub(HOME.id) is None and UNIVERSE.nearest_hub(1) is None
    with pytest.raises(TypeError):
        UNIVERSE.nearest_hub("Jita")
    assert perimeter.id in UNIVERSE.gate_graph()[jita.id]


def test_nearest_hub_tie_goes_to_the_hub_listed_first(monkeypatch) -> None:
    """With two hubs at the same distance, the one first in TRADE_HUBS wins; unknown hub names are skipped."""
    jita, perimeter = UNIVERSE.by_name("Jita").id, UNIVERSE.by_name("Perimeter").id
    distances = {"Jita": {jita: 0, perimeter: 1}, "Amarr": {perimeter: 1}}
    monkeypatch.setattr(db, "TRADE_HUBS", ("Jita", "Amarr", "Nowhere"))   # an unknown hub is skipped
    universe = object.__new__(Universe)
    universe._by_id, universe._by_name = UNIVERSE._by_id, UNIVERSE._by_name
    universe.gate_distances = lambda start, max_jumps: distances[universe.by_id(start).name]
    assert universe._hub_distances()[perimeter] == ("Jita", 1)


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
    """Max jump mass maps to S/M/L/XL at the game's limits."""
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


def test_open_universe_skips_an_outdated_rebuild(tmp_path, monkeypatch) -> None:
    """A rebuilt copy with an old schema gives way to the bundled file; with nothing usable the first error is raised."""
    old = tmp_path / "old.sqlite"
    with closing(sqlite3.connect(old)) as database:
        database.executescript("CREATE TABLE meta (key TEXT, value TEXT);")
    monkeypatch.setattr(db, "static_data_candidates", lambda: [old, UNIVERSE_PATH])
    assert len(db.open_universe().all_names()) == len(UNIVERSE.all_names())
    monkeypatch.setattr(db, "static_data_candidates", lambda: [old])
    with pytest.raises(UniverseDataError, match="schema 1"):
        db.open_universe()
    monkeypatch.setattr(db, "static_data_candidates", lambda: [])
    monkeypatch.setattr(db, "static_data_path", lambda: tmp_path / "missing.sqlite")
    with pytest.raises(UniverseDataError, match="missing"):
        db.open_universe()


def test_build_command_per_install(monkeypatch) -> None:
    """uv in a checkout, the executable (or the AppImage) in a frozen build, the script in a system install."""
    assert db.build_command().startswith("uv run")
    monkeypatch.setattr(db, "source_tree", lambda: None)
    assert db.build_command() == "homebound --build-static"
    monkeypatch.setattr(db.sys, "frozen", True, raising=False)
    monkeypatch.setattr(db.sys, "executable", "C:/Users/A B/Homebound/Homebound.exe")
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert db.build_command() == '"C:/Users/A B/Homebound/Homebound.exe" --build-static'
    monkeypatch.setenv("APPIMAGE", "/home/a/Homebound.AppImage")
    assert db.build_command() == "/home/a/Homebound.AppImage --build-static"


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


def test_known_type_ignores_k162_blanks_and_case() -> None:
    """known_type gives the type for any case, and None for empty, unknown and K162 codes."""
    assert UNIVERSE.known_type("h296").name == "H296" and UNIVERSE.known_type(" H296 ").name == "H296"
    assert UNIVERSE.known_type("k162") is None and UNIVERSE.known_type("K162") is None
    assert UNIVERSE.known_type(None) is None and UNIVERSE.known_type("") is None
    assert UNIVERSE.known_type("XXXX") is None
    with pytest.raises(TypeError):
        UNIVERSE.known_type(5)


class FakeResponse:
    """A urlopen result: a context manager with headers and a body."""

    headers = {"Content-Length": "2"}

    def __init__(self) -> None:
        self.chunks = [b"{}"]

    def __enter__(self):
        return self

    def __exit__(self, *error) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        return self.chunks.pop(0) if self.chunks else b""


def test_downloads_pass_the_shared_ssl_context(tmp_path, monkeypatch) -> None:
    """fetch and the zKill statics page both hand urlopen homebound's context (certifi's CAs)."""
    calls = []

    def fake_urlopen(request, **options):
        calls.append(options)
        return FakeResponse()

    monkeypatch.setattr(build_static.urllib.request, "urlopen", fake_urlopen)
    build_static.fetch("https://example.invalid/", tmp_path / "file.json", refresh=True)
    build_static.zkill_statics(31000005)
    assert len(calls) == 2 and all(options["context"] is ssl_context() for options in calls)


def test_ship_names() -> None:
    """A known ship has a name and group, an unknown ID or a non-ship type has none; bad input raises."""
    assert UNIVERSE.ship_name(587) == "Rifter" and UNIVERSE.ship_group_name(587) == "Frigate"
    assert UNIVERSE.ship_name(670) == "Capsule"
    assert UNIVERSE.ship_name(-5) is None and UNIVERSE.ship_name(2) is None      # unknown, a corporation
    assert UNIVERSE.ship_group_name(34) is None                                   # Tritanium, not a ship
    with pytest.raises(TypeError):
        UNIVERSE.ship_name("Rifter")

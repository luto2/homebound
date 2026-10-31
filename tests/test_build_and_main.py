"""Tests for the universe build script (offline, with fake sources) and the application entry point."""
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

from homebound import __main__ as entry_point
from homebound.universe import build_static
from homebound.universe.db import SCHEMA_VERSION, Universe, UniverseDataError

REAL_OUTPUT = build_static.OUTPUT
REAL_RESOLVED = build_static.RESOLVED


def jsonl(*records: dict) -> str:
    """Return records as JSON lines, with a blank line in the middle like real files may have."""
    return "\n\n".join(json.dumps(record) for record in records) + "\n"


def make_sde(path: Path, with_build: bool = True) -> None:
    """Write a tiny SDE zip: a w-space region, a k-space region, an abyssal system."""
    name = lambda text: {"en": text}  # noqa: E731
    files = {
        "mapRegions.jsonl": jsonl({"_key": 1, "name": name("A-R00001"), "wormholeClassID": 5},
                                  {"_key": 2, "name": name("The Forge")},
                                  {"_key": 3, "name": name("Abyss"), "wormholeClassID": 19}),
        "mapConstellations.jsonl": jsonl({"_key": 10, "name": name("A-C00001")},
                                         {"_key": 20, "name": name("Kimotoro")},
                                         {"_key": 30, "name": name("Abyss C")}),
        "mapSecondarySuns.jsonl": jsonl({"solarSystemID": 31000001, "typeID": 30577},
                                        {"solarSystemID": 31000002, "typeID": 1}),
        "mapSolarSystems.jsonl": jsonl(
            {"_key": 31000001, "name": name("J100001"), "regionID": 1, "constellationID": 10},
            {"_key": 31000002, "name": name("J100002"), "regionID": 1, "constellationID": 10,
             "wormholeClassID": 6},
            {"_key": 30000142, "name": name("Jita"), "regionID": 2, "constellationID": 20,
             "securityStatus": 0.95},
            {"_key": 30000144, "name": name("Perimeter"), "regionID": 2, "constellationID": 20,
             "securityStatus": 0.3},
            {"_key": 30000150, "name": name("Nullish"), "regionID": 2, "constellationID": 20,
             "securityStatus": -0.4},
            {"_key": 32000001, "name": name("Abyssal"), "regionID": 3, "constellationID": 30}),
        "mapStargates.jsonl": jsonl(
            {"solarSystemID": 30000142, "destination": {"solarSystemID": 30000144}},
            {"solarSystemID": 30000144, "destination": {"solarSystemID": 30000142}}),
    }
    if with_build:
        files["_sde.jsonl"] = jsonl({"_key": "sde", "buildNumber": 12345})
    with zipfile.ZipFile(path, "w") as archive:
        for member, content in files.items():
            archive.writestr(member, content)


WANDERER_SYSTEMS = [{"solarSystemID": 31000001, "statics": ["H296"]},
                    {"solarSystemID": 31000002, "statics": ["B274"]},     # disputed with anoik
                    {"solarSystemID": 31999999, "statics": ["X1"]}]       # not in the SDE
ANOIK = {"systems": {"J100001": {"solarSystemID": 31000001, "statics": ["H296"]},
                     "J100002": {"solarSystemID": 31000002, "statics": ["N110"]},
                     "nameless": {"statics": ["Z000"]}}}
WANDERER_TYPES = [
    {"name": "H296", "dest": ["c5"], "src": ["c5", "c6"], "lifetime": "24", "total_mass": "3000000000",
     "max_mass_per_jump": 300000000, "mass_regen": 0, "static": True},
    {"name": "K162", "dest": [], "src": None, "lifetime": None, "total_mass": "n/a",
     "max_mass_per_jump": None, "mass_regen": None, "static": False},
    {"name": "B274", "dest": ["hs", "barbican"], "src": ["c2"], "lifetime": 16, "total_mass": 2000000000,
     "max_mass_per_jump": 300000000, "mass_regen": 0, "static": True},
]
WHTYPE_SCRIPT = ("{wormhole:'H296', respawn:['Wandering'], spawn_in:['Class 5','HighSec','Nowhere']},\n"
                 "{wormhole:'B274', respawn:['Static'], spawn_in:['Class 2']},")


@pytest.fixture
def fake_sources(tmp_path, monkeypatch):
    """Redirect the build into tmp_path and serve fake source files instead of downloading."""
    monkeypatch.setattr(build_static, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(build_static, "OUTPUT", tmp_path / "universe.sqlite")
    monkeypatch.setattr(build_static, "RESOLVED", tmp_path / "resolved.json")
    state = {"sde_build": True, "calls": []}

    def fake_fetch(url: str, destination: Path, refresh: bool) -> Path:
        state["calls"].append((url, refresh))
        if url == build_static.SDE_URL:
            make_sde(destination, state["sde_build"])
        elif url.endswith("wormholeSystems.json"):
            destination.write_text(json.dumps(WANDERER_SYSTEMS))
        elif url.endswith("wormholes.json"):
            destination.write_text(json.dumps(WANDERER_TYPES))
        elif url == build_static.ANOIK_URL:
            destination.write_text(json.dumps(ANOIK))
        else:
            destination.write_text(WHTYPE_SCRIPT)
        return destination

    monkeypatch.setattr(build_static, "fetch", fake_fetch)
    monkeypatch.setattr(build_static, "zkill_statics", lambda system_id: ["N110"])
    monkeypatch.setattr(build_static.time, "sleep", lambda seconds: None)
    state["tmp"] = tmp_path
    return state


def test_build_end_to_end(fake_sources) -> None:
    """Build end to end."""
    output = build_static.build()
    assert output == fake_sources["tmp"] / "universe.sqlite"
    universe = Universe(output)
    assert sorted(universe.all_names()) == ["J100001", "J100002", "Jita", "Nullish", "Perimeter"]
    j1, j2 = universe.by_name("J100001"), universe.by_name("J100002")
    assert (j1.system_class, j1.effect, j1.wormhole_space, j1.region) == ("C5", "pulsar", True, "A-R00001")
    assert j2.system_class == "C6" and j2.effect is None            # the system's own class wins
    assert [universe.by_name(n).system_class for n in ("Jita", "Perimeter", "Nullish")] == ["HS", "LS", "NS"]
    assert not universe.by_name("Jita").wormhole_space
    assert universe.by_name("Abyssal") is None
    assert universe.gate_adjacent(30000142, 30000144) and len(universe.gate_neighbours(30000142)) == 1
    assert j1.statics == ("H296",) and j2.statics == ("N110",)     # dispute settled by zKill
    h296 = universe.wormhole_type("H296")
    assert h296.destinations == ("C5",) and h296.spawns_in == ("C5", "C6") and h296.wanders_into == ("C5", "HS")
    assert h296.total_mass == 3000000000 and h296.is_static
    assert universe.wormhole_type("K162").total_mass is None
    assert universe.wormhole_type("B274").destinations == ("DR", "HS")
    assert universe.wormhole_type("B274").wanders_into == ()
    assert universe.wormhole_type("K162").wanders_into is None
    meta = dict(universe.database.execute("SELECT key, value FROM meta"))
    assert meta["schema_version"] == SCHEMA_VERSION == build_static.SCHEMA_VERSION
    assert meta["sde_build"] == "12345"
    pinned = json.loads((fake_sources["tmp"] / "resolved.json").read_text())
    assert pinned["J100002"]["source"] == "zkill" and pinned["J100002"]["statics"] == ["N110"]


def test_build_without_sde_build_number_and_second_build_replaces(fake_sources) -> None:
    """Build without sde build number and second build replaces."""
    fake_sources["sde_build"] = False
    build_static.build()
    build_static.build(refresh=True)          # an existing output is replaced
    meta = dict(Universe(fake_sources["tmp"] / "universe.sqlite").database.execute("SELECT key, value FROM meta"))
    assert meta["sde_build"] == "None"
    assert all(refresh for _, refresh in fake_sources["calls"][-5:])


def test_build_validation(fake_sources) -> None:
    """Build validation."""
    with pytest.raises(TypeError):
        build_static.build("yes")


def test_build_uses_pinned_resolution(fake_sources, monkeypatch) -> None:
    """Build uses pinned resolution."""
    (fake_sources["tmp"]).mkdir(exist_ok=True)
    (fake_sources["tmp"] / "resolved.json").write_text(json.dumps({"J100002": {"statics": ["B274"]}}))
    monkeypatch.setattr(build_static, "zkill_statics", lambda system_id: pytest.fail("asked zKill again"))
    build_static.build()
    assert Universe(fake_sources["tmp"] / "universe.sqlite").by_name("J100002").statics == ("B274",)


def test_resolve_statics_union_when_zkill_has_nothing(fake_sources, monkeypatch) -> None:
    """Resolve statics union when zkill has nothing."""
    monkeypatch.setattr(build_static, "zkill_statics", lambda system_id: None)
    result = build_static.resolve_statics({1: "J1", 2: "J2"}, {1: ["A"], 2: ["B"], 3: ["C"]}, {1: ["Z"], 2: ["B"]})
    assert result == {1: ["A", "Z"], 2: ["B"]}
    assert json.loads(build_static.RESOLVED.read_text())["J1"]["source"] == "union"
    with pytest.raises(TypeError):
        build_static.resolve_statics([], {}, {})


def test_resolve_statics_single_source(fake_sources) -> None:
    """Resolve statics single source."""
    assert build_static.resolve_statics({1: "J1", 2: "J2"}, {1: ["A"]}, {2: ["B"]}) == {1: ["A"], 2: ["B"]}


def test_main_passes_refresh(monkeypatch) -> None:
    """Main passes refresh."""
    calls = []
    monkeypatch.setattr(build_static, "build", lambda refresh=False: calls.append(refresh))
    assert build_static.main(["--refresh"]) == 0
    assert build_static.main([]) == 0
    assert calls == [True, False]


def test_main_never_touches_real_outputs() -> None:
    """Main never touches real outputs."""
    assert build_static.OUTPUT == REAL_OUTPUT and build_static.RESOLVED == REAL_RESOLVED


class FakeResponse(io.BytesIO):
    """A urlopen result usable as a context manager."""

    def __enter__(self) -> "FakeResponse":
        """Return self."""
        return self

    def __exit__(self, *exception) -> None:
        """Close nothing."""


def test_fetch_downloads_and_caches(tmp_path, monkeypatch) -> None:
    """Fetch downloads and caches."""
    requested = []

    def fake_urlopen(request, timeout):
        requested.append((request.full_url, request.get_header("User-agent"), timeout))
        return FakeResponse(b"payload")

    monkeypatch.setattr(build_static.urllib.request, "urlopen", fake_urlopen)
    target = tmp_path / "file.bin"
    assert build_static.fetch("https://example.invalid/x", target, False) == target
    assert target.read_bytes() == b"payload" and len(requested) == 1
    assert requested[0][1] == build_static.HEADERS["User-Agent"]
    build_static.fetch("https://example.invalid/x", target, False)
    assert len(requested) == 1                       # cached
    build_static.fetch("https://example.invalid/x", target, True)
    assert len(requested) == 2                       # refreshed
    with pytest.raises(ValueError):
        build_static.fetch("", target, False)
    with pytest.raises(TypeError):
        build_static.fetch("https://example.invalid/x", "file", False)


def test_zkill_statics_parses_page(monkeypatch) -> None:
    """Zkill statics parses page."""
    page = (b"<html><body><div>Statics: <b>(N110)</b> &amp; (H296)</div> Alltime kills</body></html>")
    seen = []
    monkeypatch.setattr(build_static.urllib.request, "urlopen",
                        lambda request, timeout: seen.append(request.full_url) or FakeResponse(page))
    assert build_static.zkill_statics(31000001) == ["H296", "N110"]
    assert seen == ["https://zkillboard.com/system/31000001/"]
    monkeypatch.setattr(build_static.urllib.request, "urlopen",
                        lambda request, timeout: FakeResponse(b"<html>no such line</html>"))
    assert build_static.zkill_statics(31000001) is None
    with pytest.raises(ValueError):
        build_static.zkill_statics(0)


def test_small_helpers() -> None:
    """Small helpers."""
    assert build_static.whole_number("12.7") == 12 and build_static.whole_number(5) == 5
    assert build_static.whole_number("n/a") is None and build_static.whole_number(None) is None
    assert [build_static.known_space_label(s) for s in (0.95, 0.45, 0.44, 0.1, 0.0, -0.3)] == \
        ["HS", "HS", "LS", "LS", "NS", "NS"]
    with pytest.raises(ValueError):
        build_static.known_space_label(2)
    assert build_static.whtype_wander("") == {}


def test_json_lines_skips_blank_lines_and_validates(tmp_path) -> None:
    """Blank lines are skipped; bad arguments fail at the call, not at the first iteration."""
    path = tmp_path / "x.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("a.jsonl", '{"a": 1}\n\n   \n{"a": 2}\n')
    with zipfile.ZipFile(path) as archive:
        assert list(build_static.json_lines(archive, "a.jsonl")) == [{"a": 1}, {"a": 2}]
        with pytest.raises(TypeError):
            build_static.json_lines("not a zip", "a.jsonl")      # no iteration needed
        with pytest.raises(ValueError):
            build_static.json_lines(archive, "")


# ---------------------------------------------------------------- application entry point

class FakeWindow:
    """Stands in for MapWindow."""

    instances: list = []

    def __init__(self, state, focus) -> None:
        """Remember the collaborators."""
        self.state, self.focus, self.shown, self.settings_opened = state, focus, False, None
        FakeWindow.instances.append(self)

    def show(self) -> None:
        """Record the call."""
        self.shown = True

    def open_settings(self, page: int) -> None:
        """Record the page."""
        self.settings_opened = page


class FakeState:
    """Stands in for AppState."""

    def __init__(self, universe, settings) -> None:
        """Remember the arguments."""
        self.universe, self.settings, self.started, self.stopped = universe, settings, False, False

    def start(self) -> None:
        """Record the call."""
        self.started = True

    def stop(self) -> None:
        """Record the call."""
        self.stopped = True


class FakeFocus:
    """Stands in for FocusTracker."""

    started = False

    def start(self) -> None:
        """Record the call."""
        self.started = True


class FakeSettings:
    """Stands in for a loaded Settings."""

    def __init__(self, log_directory: str) -> None:
        """Set the log directory."""
        self.log_directory = log_directory


@pytest.fixture
def fake_app(qt_application, monkeypatch):
    """Replace everything main() would start for real; QApplication.exec returns 0."""
    from PySide6.QtWidgets import QApplication

    import homebound.settings
    import homebound.ui.focus
    import homebound.ui.map_window
    import homebound.ui.state
    FakeWindow.instances.clear()
    monkeypatch.setattr(QApplication, "exec", lambda *arguments: 0)
    monkeypatch.setattr(homebound.ui.map_window, "MapWindow", FakeWindow)
    monkeypatch.setattr(homebound.ui.state, "AppState", FakeState)
    monkeypatch.setattr(homebound.ui.focus, "FocusTracker", FakeFocus)
    monkeypatch.setattr("homebound.universe.db.Universe", lambda: "universe")
    return homebound.settings.Settings


@pytest.mark.parametrize(("log_directory", "settings_page"), [("/logs", None), ("", 0)])
def test_main_normal_path(fake_app, monkeypatch, log_directory, settings_page) -> None:
    """Main normal path."""
    monkeypatch.setattr(fake_app, "load", classmethod(lambda cls: FakeSettings(log_directory)))
    assert entry_point.main() == 0
    window = FakeWindow.instances[0]
    assert window.shown and window.focus.started and window.state.started and window.state.stopped
    assert window.state.universe == "universe"
    assert window.settings_opened == settings_page


def test_main_missing_universe_shows_error(fake_app, monkeypatch) -> None:
    """Main missing universe shows error."""
    shown = []

    def broken_universe() -> None:
        raise UniverseDataError("missing")

    monkeypatch.setattr("homebound.universe.db.Universe", broken_universe)
    import homebound.ui.dialogs
    from PySide6.QtWidgets import QDialog
    monkeypatch.setattr(QDialog, "exec", lambda dialog: shown.append(dialog) or 0)
    assert entry_point.main() == 1
    assert len(shown) == 1 and isinstance(shown[0], homebound.ui.dialogs.StartupErrorDialog)
    assert not FakeWindow.instances


def test_module_exposes_main() -> None:
    """Module exposes main."""
    assert callable(entry_point.main) and "homebound.__main__" in sys.modules

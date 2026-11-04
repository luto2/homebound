"""Tests for the universe build script (offline, with fake sources) and the application entry point."""
import ctypes
import io
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from homebound import __main__ as entry_point
from homebound.universe import build_static
from homebound.universe.db import SCHEMA_VERSION, Universe, UniverseDataError


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
def fake_sources(tmp_path, monkeypatch) -> dict:
    """Redirect the build into tmp_path and serve fake source files instead of downloading."""
    monkeypatch.setattr(build_static, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(build_static, "OUTPUT", tmp_path / "universe.sqlite")
    monkeypatch.setattr(build_static, "RESOLVED", tmp_path / "resolved.json")
    monkeypatch.setattr(build_static, "SHIPPED_RESOLVED", tmp_path / "shipped.json")
    state = {"sde_build": True, "calls": []}

    def fake_fetch(url: str, destination: Path, refresh: bool, progress=None) -> Path:
        """Record the call and write the fake file for the source URL."""
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
    """A build from the fake sources writes the systems with classes, effects, gates and statics (the
    Wanderer/anoik dispute settled by zKill), the wormhole types and the meta rows, and pins the
    zKill resolution."""
    output = build_static.build()
    assert output == fake_sources["tmp"] / "universe.sqlite"
    universe = Universe(output)
    assert sorted(universe.all_names()) == ["J100001", "J100002", "Jita", "Nullish", "Perimeter"]
    first_system, second_system = universe.by_name("J100001"), universe.by_name("J100002")
    assert (first_system.system_class, first_system.effect, first_system.wormhole_space,
            first_system.region) == ("C5", "pulsar", True, "A-R00001")
    assert second_system.system_class == "C6" and second_system.effect is None            # the system's own class wins
    assert [universe.by_name(name).system_class for name in ("Jita", "Perimeter", "Nullish")] == ["HS", "LS", "NS"]
    assert not universe.by_name("Jita").wormhole_space
    assert universe.by_name("Abyssal") is None
    assert universe.gate_adjacent(30000142, 30000144) and len(universe.gate_neighbours(30000142)) == 1
    assert first_system.statics == ("H296",) and second_system.statics == ("N110",)     # dispute settled by zKill
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
    """An SDE without a build number stores "None"; a second build with refresh replaces the output
    and downloads every source again."""
    fake_sources["sde_build"] = False
    build_static.build()
    build_static.build(refresh=True)          # an existing output is replaced
    meta = dict(Universe(fake_sources["tmp"] / "universe.sqlite").database.execute("SELECT key, value FROM meta"))
    assert meta["sde_build"] == "None"
    assert all(refresh for _, refresh in fake_sources["calls"][-5:])


def test_build_validation(fake_sources) -> None:
    """A refresh flag that is not a bool raises TypeError."""
    with pytest.raises(TypeError):
        build_static.build("yes")


def test_build_uses_pinned_resolution(fake_sources, monkeypatch) -> None:
    """Statics pinned by an earlier build are used as they are, without asking zKill again."""
    (fake_sources["tmp"]).mkdir(exist_ok=True)
    (fake_sources["tmp"] / "resolved.json").write_text(json.dumps({"J100002": {"statics": ["B274"]}}))
    monkeypatch.setattr(build_static, "zkill_statics", lambda system_id: pytest.fail("asked zKill again"))
    build_static.build()
    assert Universe(fake_sources["tmp"] / "universe.sqlite").by_name("J100002").statics == ("B274",)


def test_build_starts_from_shipped_resolution(fake_sources, monkeypatch) -> None:
    """Without its own resolution file (a fresh package), the build uses the shipped one and writes its own."""
    (fake_sources["tmp"] / "shipped.json").write_text(json.dumps({"J100002": {"statics": ["B274"]}}))
    monkeypatch.setattr(build_static, "zkill_statics", lambda system_id: pytest.fail("asked zKill again"))
    build_static.build()
    assert Universe(fake_sources["tmp"] / "universe.sqlite").by_name("J100002").statics == ("B274",)
    assert json.loads((fake_sources["tmp"] / "resolved.json").read_text())["J100002"]["statics"] == ["B274"]


def test_build_reports_progress_and_drops_the_cache(fake_sources) -> None:
    """Each step is reported; keep_cache=False deletes the downloads; a stale .part file from a crashed build is
    replaced and none is left behind."""
    steps = []
    (fake_sources["tmp"] / "universe.sqlite.part").write_text("stale")
    build_static.build(progress=lambda text, fraction: steps.append(text), keep_cache=False)
    assert steps[0].startswith("Downloading the EVE static data export") and steps[-1] == "Writing universe.sqlite …"
    assert not (fake_sources["tmp"] / "cache").exists()
    assert (fake_sources["tmp"] / "universe.sqlite").is_file()
    assert not list(fake_sources["tmp"].glob("*.part"))
    with pytest.raises(TypeError):
        build_static.build(keep_cache="no")


def test_build_cancel_keeps_the_old_file(fake_sources) -> None:
    """A progress callback that raises stops the build; the existing universe file is untouched."""
    build_static.build()
    before = (fake_sources["tmp"] / "universe.sqlite").read_bytes()

    def cancel(text: str, fraction) -> None:
        """Cancel at the write step."""
        if text.startswith("Writing"):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        build_static.build(progress=cancel)
    assert (fake_sources["tmp"] / "universe.sqlite").read_bytes() == before


def test_resolve_statics_union_when_zkill_has_nothing(fake_sources, monkeypatch) -> None:
    """When the sources disagree and zKill has no answer, the statics are the sorted union, pinned with
    source "union"; systems without a name are dropped and a list of names raises TypeError."""
    monkeypatch.setattr(build_static, "zkill_statics", lambda system_id: None)
    result = build_static.resolve_statics({1: "J1", 2: "J2"}, {1: ["A"], 2: ["B"], 3: ["C"]}, {1: ["Z"], 2: ["B"]})
    assert result == {1: ["A", "Z"], 2: ["B"]}
    assert json.loads(build_static.RESOLVED.read_text())["J1"]["source"] == "union"
    with pytest.raises(TypeError):
        build_static.resolve_statics([], {}, {})


def test_resolve_statics_single_source(fake_sources) -> None:
    """A system that only one source knows takes that source's statics."""
    assert build_static.resolve_statics({1: "J1", 2: "J2"}, {1: ["A"]}, {2: ["B"]}) == {1: ["A"], 2: ["B"]}


def test_main_passes_refresh(monkeypatch) -> None:
    """The build command passes --refresh on to build() and exits with 0 either way."""
    calls = []
    monkeypatch.setattr(build_static, "build", lambda refresh=False: calls.append(refresh))
    assert build_static.main(["--refresh"]) == 0
    assert build_static.main([]) == 0
    assert calls == [True, False]


def test_fetch_reports_fractions(tmp_path, monkeypatch) -> None:
    """With a Content-Length, fetch reports the fraction per chunk; without one it reports None."""
    class SizedResponse(FakeResponse):
        """A response that announces its length."""

        headers = {"Content-Length": "6"}

    monkeypatch.setattr(build_static, "CHUNK", 4)
    monkeypatch.setattr(build_static.urllib.request, "urlopen", lambda request, timeout: SizedResponse(b"abcdef"))
    fractions = []
    build_static.fetch("https://example.invalid/x", tmp_path / "a", True, fractions.append)
    assert fractions == [4 / 6, 1.0]
    monkeypatch.setattr(build_static.urllib.request, "urlopen", lambda request, timeout: FakeResponse(b"abc"))
    fractions.clear()
    build_static.fetch("https://example.invalid/x", tmp_path / "b", True, fractions.append)
    assert fractions == [None]




class FakeResponse(io.BytesIO):
    """A urlopen result usable as a context manager."""

    headers: dict = {}

    def __enter__(self) -> "FakeResponse":
        """Return self."""
        return self

    def __exit__(self, *exception) -> None:
        """Close nothing."""


def test_fetch_downloads_and_caches(tmp_path, monkeypatch) -> None:
    """fetch downloads with the Homebound User-Agent, reuses the cached file, downloads again on refresh
    and rejects an empty URL or a target that is not a Path."""
    requested = []

    def fake_urlopen(request, timeout) -> FakeResponse:
        """Record the URL, User-Agent and timeout and answer with a fixed payload."""
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
    assert not target.with_name("file.bin.part").exists()
    with pytest.raises(ValueError):
        build_static.fetch("", target, False)
    with pytest.raises(TypeError):
        build_static.fetch("https://example.invalid/x", "file", False)


def test_zkill_statics_parses_page(monkeypatch) -> None:
    """zkill_statics reads the sorted static codes from the system page, gives None for a page without
    them and rejects system ID 0."""
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
    """whole_number truncates numbers and numeric strings and gives None otherwise; known_space_label
    rounds security to HS/LS/NS and rejects values out of range; an empty whtype script gives nothing."""
    assert build_static.whole_number("12.7") == 12 and build_static.whole_number(5) == 5
    assert build_static.whole_number("n/a") is None and build_static.whole_number(None) is None
    assert [build_static.known_space_label(security) for security in (0.95, 0.45, 0.44, 0.1, 0.0, -0.3)] == \
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

    def __init__(self, universe, settings, auto_backup: bool = False) -> None:
        """Remember the arguments."""
        self.universe, self.settings, self.started, self.stopped = universe, settings, False, False
        self.auto_backup = auto_backup

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
def fake_app(qt_application, monkeypatch) -> type:
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
    monkeypatch.setattr("homebound.universe.db.open_universe", lambda: "universe")
    monkeypatch.setattr(entry_point.sys, "argv", ["homebound"])
    return homebound.settings.Settings


@pytest.mark.parametrize(("log_directory", "settings_page"), [("/logs", None), ("", 0)])
def test_main_normal_path(fake_app, monkeypatch, log_directory, settings_page) -> None:
    """main() starts the focus tracker and the state, shows the window, stops the state on exit and
    opens the settings at page 0 only when no log folder is set."""
    monkeypatch.setattr(fake_app, "load", classmethod(lambda cls: FakeSettings(log_directory)))
    assert entry_point.main() == 0
    window = FakeWindow.instances[0]
    assert window.shown and window.focus.started and window.state.started and window.state.stopped
    assert window.state.universe == "universe"
    assert window.settings_opened == settings_page


@pytest.mark.parametrize("platform, preset, expected",
                         [("linux", None, "xcb"), ("linux", "wayland;xcb", "xcb"), ("win32", None, None)])
def test_main_forces_xcb_only_on_linux(fake_app, monkeypatch, platform, preset, expected) -> None:
    """QT_QPA_PLATFORM=xcb is a Linux (XWayland) setting, also over a desktop-wide value; Windows keeps Qt's and
    sets its own task bar app ID instead."""
    monkeypatch.setattr(fake_app, "load", classmethod(lambda cls: FakeSettings("/logs")))
    if preset is None:
        monkeypatch.delenv("QT_QPA_PLATFORM")
    else:
        monkeypatch.setenv("QT_QPA_PLATFORM", preset)
    monkeypatch.setattr(entry_point.sys, "platform", platform)
    app_ids: list[str] = []
    shell32 = SimpleNamespace(SetCurrentProcessExplicitAppUserModelID=app_ids.append)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=shell32), raising=False)
    assert entry_point.main() == 0
    assert entry_point.os.environ.get("QT_QPA_PLATFORM") == expected
    assert app_ids == ([entry_point.APP_USER_MODEL_ID] if platform == "win32" else [])


def test_installer_shortcuts_use_the_app_id() -> None:
    """The Start menu and desktop shortcuts carry the app ID main() sets, so a pinned shortcut groups with the window."""
    script = (Path(__file__).parent.parent / "packaging" / "windows" / "homebound.iss").read_text()
    shortcuts = [line for line in script.splitlines() if 'Filename: "{app}\\Homebound.exe"' in line and line.startswith("Name:")]
    assert shortcuts and all(f'AppUserModelID: "{entry_point.APP_USER_MODEL_ID}"' in line for line in shortcuts)


def test_main_missing_universe_quit(fake_app, monkeypatch) -> None:
    """Without universe data, main() offers the build; quitting it opens no window and returns 1."""
    shown = []

    def broken_universe(*arguments) -> None:
        """Fail like a missing universe file."""
        raise UniverseDataError("missing")

    monkeypatch.setattr("homebound.universe.db.open_universe", broken_universe)
    import homebound.ui.dialogs
    from PySide6.QtWidgets import QDialog
    monkeypatch.setattr(QDialog, "exec", lambda dialog: shown.append(dialog) or 0)
    assert entry_point.main() == 1
    assert len(shown) == 1 and isinstance(shown[0], homebound.ui.dialogs.BuildDataDialog)
    assert not FakeWindow.instances


def test_main_builds_then_starts(fake_app, monkeypatch) -> None:
    """When the first-run build succeeds, main() loads the new data and opens the window."""
    attempts = []

    def universe_after_build(*arguments) -> str:
        """Fail the first time (no data yet), then return the universe."""
        attempts.append(1)
        if len(attempts) == 1:
            raise UniverseDataError("missing")
        return "universe"

    monkeypatch.setattr("homebound.universe.db.open_universe", universe_after_build)
    from PySide6.QtWidgets import QDialog
    monkeypatch.setattr(QDialog, "exec", lambda dialog: 1)
    assert entry_point.main() == 0
    assert len(attempts) == 2 and len(FakeWindow.instances) == 1


def test_main_version_and_build_static(monkeypatch, capsys) -> None:
    """--version prints the version; --build-static hands the remaining arguments to the build, without Qt."""
    monkeypatch.setattr(entry_point.sys, "argv", ["homebound", "--version"])
    assert entry_point.main() == 0
    assert capsys.readouterr().out.startswith("Homebound ")
    calls = []
    monkeypatch.setattr(build_static, "build", lambda refresh=False: calls.append(refresh))
    monkeypatch.setattr(entry_point.sys, "argv", ["homebound", "--build-static", "--refresh"])
    assert entry_point.main() == 0
    assert calls == [True]


def test_module_exposes_main() -> None:
    """Importing homebound.__main__ gives a callable main()."""
    assert callable(entry_point.main) and "homebound.__main__" in sys.modules

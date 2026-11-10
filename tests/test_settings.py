"""Settings: maps, loading old and broken files, marker slots and log folder detection."""
import json
from pathlib import Path

import pytest

import homebound.settings as settings_module
from homebound.settings import Settings


@pytest.fixture
def data_dir(tmp_path, monkeypatch) -> Path:
    """Point the settings at an empty temporary data folder."""
    monkeypatch.setattr(settings_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_module, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(settings_module, "detect_log_directories", lambda: [])
    return tmp_path


def test_maps_in_settings(data_dir) -> None:
    """Old settings become the "Main" map; maps can be added, switched, renamed and removed."""
    (data_dir / "settings.json").write_text('{"home_system": "J161940", "naming_scheme": "a2a"}')
    settings = Settings.load()
    assert settings.maps == {"Main": {"file": "map.db", "home_system": "J161940", "naming_scheme": "a2a"}}
    assert settings.add_map("Corp chain", "J100551", "numeric") == data_dir / "maps/corp-chain.db"
    with pytest.raises(ValueError):
        settings.add_map("Corp chain")
    with pytest.raises(ValueError):
        settings.add_map("  ")
    with pytest.raises(ValueError):
        settings.add_map("Other", naming_scheme="roman")
    assert settings.switch_map("Corp chain") == "Corp chain"
    assert (settings.home_system, settings.naming_scheme) == ("J100551", "numeric")
    settings.naming_scheme = "letter_numeric"
    assert settings.save() == data_dir / "settings.json"
    settings = Settings.load()
    assert settings.active_map == "Corp chain"
    assert settings.maps["Corp chain"]["naming_scheme"] == "letter_numeric"
    assert settings.map_path() == data_dir / "maps/corp-chain.db"
    settings.switch_map("Main")
    assert (settings.home_system, settings.naming_scheme) == ("J161940", "a2a")
    assert settings.rename_map("Corp chain", " Corp ") == "Corp"
    with pytest.raises(ValueError):
        settings.rename_map("Corp", "Main")
    with pytest.raises(ValueError):
        settings.rename_map("Corp", "")
    with pytest.raises(ValueError):
        settings.remove_map("Main")  # active
    with pytest.raises(ValueError):
        settings.switch_map("Nope")
    assert settings.remove_map("Corp") == data_dir / "maps/corp-chain.db"


def test_map_files_get_unique_names(data_dir) -> None:
    """Two names with the same slug, or an existing file, get a numbered file."""
    settings = Settings.load()
    (data_dir / "maps").mkdir()
    (data_dir / "maps/corp.db").write_text("")
    assert settings.add_map("Corp").name == "corp-2.db"
    assert settings.add_map("corp!").name == "corp-3.db"
    assert settings.add_map("???").name == "map.db"


def test_renaming_the_active_map_follows(data_dir) -> None:
    """Renaming the active map keeps it active."""
    settings = Settings.load()
    settings.rename_map("Main", "Home")
    assert settings.active_map == "Home" and list(settings.maps) == ["Home"]


def test_old_keys_and_bad_values_on_load(data_dir) -> None:
    """Old key names are read; values of the wrong type and unknown keys are ignored."""
    (data_dir / "settings.json").write_text(json.dumps({
        "log_dir": "/logs", "kspace_radius": 3, "overlay_opacity": "high", "overlay_pinned_char": 42,
        "zkill_enabled": 1, "overlay_text_px": 13, "unknown": True, "char_slots": {"42": 2},
        "active_map": "Gone"}))
    settings = Settings.load()
    assert (settings.log_directory, settings.known_space_radius, settings.overlay_pinned_character) == ("/logs", 3, 42)
    assert settings.overlay_opacity == 0.78 and settings.zkill_enabled is True
    assert settings.overlay_text_size == 13 and settings.character_slots == {"42": 2}
    assert settings.active_map == "Main"


def test_out_of_range_numbers_are_clamped_on_load(data_dir) -> None:
    """Hand-edited text size, k-space radius, opacity and rescan hours outside their ranges load as the nearest allowed value."""
    (data_dir / "settings.json").write_text(json.dumps({
        "overlay_text_size": 40, "known_space_radius": -3, "overlay_opacity": 0.1, "rescan_due_hours": 500}))
    settings = Settings.load()
    assert settings.rescan_due_hours == settings_module.RESCAN_DUE_HOURS_RANGE[1] == 72
    assert settings.overlay_text_size == settings_module.OVERLAY_TEXT_SIZE_RANGE[1]
    assert settings.known_space_radius == settings_module.KNOWN_SPACE_RADIUS_RANGE[0]
    assert settings.overlay_opacity == settings_module.OVERLAY_OPACITY_RANGE[0]


def test_clamp_to_range() -> None:
    """Values inside a range and settings without one pass unchanged; a non-text key is refused."""
    assert settings_module.clamp_to_range("known_space_radius", 7) == 7
    assert settings_module.clamp_to_range("known_space_radius", 99) == 15
    assert settings_module.clamp_to_range("rescan_due_hours", 0) == 1
    assert settings_module.clamp_to_range("rescan_due_hours", 73) == 72
    assert settings_module.clamp_to_range("rescan_due_hours", 12) == 12
    assert settings_module.clamp_to_range("zkill_enabled", 99) == 99
    with pytest.raises(TypeError):
        settings_module.clamp_to_range(3, 1)


def test_rescan_due_hours_default_and_roundtrip(data_dir) -> None:
    """Rescan hours default to 24, survive a save and load, and a non-integer value keeps the default."""
    assert Settings().rescan_due_hours == 24
    settings = Settings()
    settings.rescan_due_hours = 8
    settings.save()
    assert Settings.load().rescan_due_hours == 8
    (data_dir / "settings.json").write_text(json.dumps({"rescan_due_hours": "12"}))
    assert Settings.load().rescan_due_hours == 24


def test_broken_settings_file_gives_defaults(data_dir) -> None:
    """Unreadable JSON or a JSON list means default settings."""
    for content in ("{not json", "[1, 2]"):
        (data_dir / "settings.json").write_text(content)
        assert Settings.load().naming_scheme == "numeric"


def test_non_utf8_settings_file_gives_defaults(data_dir) -> None:
    """A file that is not UTF-8 is unreadable: default settings, no crash."""
    (data_dir / "settings.json").write_bytes(b"\xff\xfe{}")
    assert Settings.load().naming_scheme == "numeric"


def test_utf8_bom_settings_file_loads(data_dir) -> None:
    """A file saved with a byte order mark (Notepad) loads its values."""
    (data_dir / "settings.json").write_bytes(b"\xef\xbb\xbf" + json.dumps({"naming_scheme": "a2a"}).encode())
    assert Settings.load().naming_scheme == "a2a"


def test_bad_file_is_kept_before_the_next_save(data_dir) -> None:
    """An unusable file is copied aside once; the next save writes good settings over it."""
    garbage = b"{not json"
    (data_dir / "settings.json").write_bytes(garbage)
    settings = Settings.load()
    assert settings.load_problem
    settings.save()
    assert json.loads((data_dir / "settings.json").read_text())["active_map"] == "Main"
    copies = list(data_dir.glob("settings.bad-*.json"))
    assert len(copies) == 1 and copies[0].read_bytes() == garbage
    (data_dir / "settings.json").write_bytes(garbage)
    assert Settings.load().load_problem
    assert list(data_dir.glob("settings.bad-*.json")) == copies


@pytest.mark.parametrize("maps", [
    {"x": 1},
    {"x": {"home_system": "J1"}},
    {"x": {"file": 5}},
    {"x": {"file": " "}},
    {"x": {"file": "../../x.db"}},
    {"x": {"file": "/etc/x.db"}},
    {"": {"file": "maps/x.db"}},
    {"x": "maps/x.db"},
])
def test_malformed_maps_are_dropped(data_dir, maps) -> None:
    """A map entry that can't be used is dropped; the settings load and save, with a map left to use."""
    (data_dir / "settings.json").write_text(json.dumps({"maps": maps}))
    settings = Settings.load()
    assert list(settings.maps) == ["Main"]
    assert settings.map_path() == data_dir / "map.db"
    settings.save()
    assert list(Settings.load().maps) == ["Main"]


def test_maps_that_are_not_an_object_are_ignored(data_dir) -> None:
    """A maps value of the wrong type is ignored, as any other wrong-typed setting."""
    (data_dir / "settings.json").write_text(json.dumps({"maps": [1]}))
    assert list(Settings.load().maps) == ["Main"]


def test_good_maps_survive_a_bad_neighbour(data_dir) -> None:
    """Valid maps keep their order and their entries; a bad one goes, and an active map that was dropped moves on."""
    (data_dir / "settings.json").write_text(json.dumps({
        "active_map": "Bad",
        "maps": {"Main": {"file": "map.db", "home_system": "J161940", "naming_scheme": "a2a", "extra": 1},
                 "Bad": {"home_system": "J1"},
                 "Corp": {"file": "maps/corp.db"}}}))
    settings = Settings.load()
    assert list(settings.maps) == ["Main", "Corp"]
    assert settings.maps["Main"] == {"file": "map.db", "home_system": "J161940", "naming_scheme": "a2a", "extra": 1}
    assert settings.active_map == "Main"


def test_dropped_map_entry_leaves_a_copy(data_dir) -> None:
    """The file as it was, byte for byte, is kept when a map entry is dropped."""
    original = json.dumps({"maps": {"Main": {"file": "map.db"}, "Bad": {"file": 5}}}, indent=4).encode()
    (data_dir / "settings.json").write_bytes(original)
    settings = Settings.load()
    assert list(settings.maps) == ["Main"] and settings.load_problem
    settings.save()
    copies = list(data_dir.glob("settings.bad-*.json"))
    assert len(copies) == 1 and copies[0].read_bytes() == original


def test_clean_and_wrong_typed_files_leave_no_copy(data_dir) -> None:
    """A clean file, and a wrong-typed top-level value, load silently: no copy and no problem."""
    (data_dir / "settings.json").write_text(json.dumps({"known_space_radius": "far", "naming_scheme": "a2a"}))
    settings = Settings.load()
    assert settings.naming_scheme == "a2a" and settings.load_problem == ""
    assert list(data_dir.glob("settings.bad-*.json")) == []


@pytest.mark.parametrize(("key", "bad", "default", "good"), [
    ("overlay_mode", "zzz", "follow", "pinned_always"),
    ("unknown_jump_default", "q", "ignore", "add"),
    ("naming_scheme", "q", "numeric", "a2a"),
])
def test_bad_choices_keep_the_defaults(data_dir, key, bad, default, good) -> None:
    """A choice setting with a value outside its choices keeps the default; a listed value loads."""
    (data_dir / "settings.json").write_text(json.dumps({key: bad}))
    assert getattr(Settings.load(), key) == default
    (data_dir / "settings.json").write_text(json.dumps({key: good}))
    assert getattr(Settings.load(), key) == good


def test_map_entry_with_unknown_scheme_gets_numeric(data_dir) -> None:
    """A map whose naming scheme is not a known one loads with the numeric scheme."""
    (data_dir / "settings.json").write_text(json.dumps({"maps": {"Main": {"file": "map.db", "naming_scheme": "q"}}}))
    assert Settings.load().maps["Main"]["naming_scheme"] == "numeric"


def test_copy_failure_does_not_stop_the_load(data_dir, monkeypatch) -> None:
    """If the copy aside fails, the settings still load with defaults and say the copy failed."""
    (data_dir / "settings.json").write_bytes(b"\xff\xfe{}")

    def refuse(source, target):
        raise OSError("disk full")

    monkeypatch.setattr(settings_module.shutil, "copy2", refuse)
    settings = Settings.load()
    assert settings.naming_scheme == "numeric" and "disk full" in settings.load_problem


def test_overlay_opacity_range_matches_the_tokens() -> None:
    """The overlay's opacity range (settings) is the one its tokens give, which the overlay paints with."""
    from homebound.theme.tokens import OPACITY
    assert (OPACITY["op-overlay-bg-min"], OPACITY["op-overlay-bg-max"]) == settings_module.OVERLAY_OPACITY_RANGE


def test_settings_scheme_list_matches_naming() -> None:
    """The scheme names the settings file accepts are the ones naming knows."""
    from homebound.mapping import naming
    assert settings_module.SCHEMES == tuple(naming.SCHEMES)


def test_route_preferences_match_routing() -> None:
    """The route preferences the settings file accepts are the ones routing knows."""
    from homebound import routing
    assert settings_module.ROUTE_PREFERENCES == routing.PREFERENCES


def test_detected_log_folder_is_used(data_dir, monkeypatch) -> None:
    """Without a saved folder, the first detected one is used."""
    monkeypatch.setattr(settings_module, "detect_log_directories", lambda: [Path("/found/Chatlogs")])
    assert Settings.load().log_directory == str(Path("/found/Chatlogs"))


def test_marker_slots_fill_up_then_share() -> None:
    """Characters get the first free slot; past six they share slots in turn."""
    settings = Settings()
    assert [settings.slot_for(character_id) for character_id in range(1, 7)] == [1, 2, 3, 4, 5, 6]
    assert settings.slot_for(3) == 3
    assert settings.slot_for(7) == 1 and settings.slot_for(8) == 2
    with pytest.raises(TypeError):
        settings.slot_for("7")


def test_detect_log_directories_in_a_fake_home(tmp_path, monkeypatch) -> None:
    """A Proton prefix in a Steam library listed in libraryfolders.vdf is found."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(settings_module, "is_windows", lambda: False)
    steam = tmp_path / ".local/share/Steam"
    other = tmp_path / "games"
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps/libraryfolders.vdf").write_text(f'"libraryfolders" {{ "1" {{ "path" "{other}" }} }}')
    chatlogs = other / "steamapps/compatdata/8500/pfx/drive_c/users/steamuser/Documents/EVE/logs/Chatlogs"
    chatlogs.mkdir(parents=True)
    assert settings_module.steam_libraries() == [steam.resolve(), other.resolve()]
    assert settings_module.detect_log_directories() == [chatlogs]


def test_detect_log_directories_skips_unreadable_mounts(tmp_path, monkeypatch) -> None:
    """A candidate whose stat fails (a dead automount: ENODEV) is skipped, not raised."""
    good = tmp_path / "Chatlogs"
    good.mkdir()
    dead = tmp_path / "dead"
    real_is_dir = Path.is_dir

    def is_dir(path: Path) -> bool:
        if path == dead:
            raise OSError(19, "No such device")
        return real_is_dir(path)

    monkeypatch.setattr(Path, "is_dir", is_dir)
    monkeypatch.setattr(settings_module, "is_windows", lambda: False)
    monkeypatch.setattr(settings_module, "linux_log_candidates", lambda: [dead, good])
    assert settings_module.detect_log_directories() == [good]


def test_detect_log_directories_on_windows(tmp_path, monkeypatch) -> None:
    """On Windows the Documents folder Windows reports comes first, then OneDrive's Documents/Dokumente."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(settings_module, "is_windows", lambda: True)
    moved = tmp_path / "D/Docs"
    monkeypatch.setattr(settings_module, "documents_directory", lambda: moved)
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive - Firma"))
    candidates = settings_module.windows_log_candidates()
    assert candidates[0] == moved / "EVE/logs/Chatlogs"
    assert tmp_path / "OneDrive - Firma/Dokumente/EVE/logs/Chatlogs" in candidates
    assert tmp_path / "OneDrive/Documents/EVE/logs/Chatlogs" in candidates
    assert candidates[-1] == tmp_path / "Documents/EVE/logs/Chatlogs"
    assert settings_module.detect_log_directories() == []
    german = tmp_path / "OneDrive/Dokumente/EVE/logs/Chatlogs"
    plain = tmp_path / "Documents/EVE/logs/Chatlogs"
    for folder in (german, plain):
        folder.mkdir(parents=True)
    assert settings_module.detect_log_directories() == [german, plain]
    monkeypatch.setattr(settings_module, "documents_directory", lambda: tmp_path / "Documents")
    assert settings_module.detect_log_directories() == [plain, german]  # no duplicates


def test_route_settings_defaults_and_bad_values(data_dir) -> None:
    """Routes default to the trade hubs with EVE-Scout on; unknown choices or names keep the defaults."""
    settings = Settings()
    assert settings.route_destinations == ["Jita", "Amarr", "Dodixie", "Rens", "Hek"]
    assert settings.eve_scout_enabled and settings.route_preference == "shortest" and settings.route_ship_size == "M"
    settings.route_destinations.append("J161940")
    assert Settings().route_destinations[-1] == "Hek"   # each Settings has its own list
    (data_dir / "settings.json").write_text(json.dumps({
        "route_preference": "fastest", "route_ship_size": "XXL", "route_destinations": ["Jita", 5]}))
    loaded = Settings.load()
    assert (loaded.route_preference, loaded.route_ship_size) == ("shortest", "M")
    assert loaded.route_destinations[0] == "Jita" and len(loaded.route_destinations) == 5
    (data_dir / "settings.json").write_text(json.dumps({
        "route_preference": "safer", "route_ship_size": "S", "route_destinations": ["J161940"],
        "eve_scout_enabled": False}))
    loaded = Settings.load()
    assert (loaded.route_preference, loaded.route_ship_size, loaded.route_destinations, loaded.eve_scout_enabled) \
        == ("safer", "S", ["J161940"], False)

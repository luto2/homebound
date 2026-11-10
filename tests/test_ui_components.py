"""Offscreen tests of the UI building blocks: state, toasts, menus, notifications, overlay, character dock."""
import json
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QProcess, QRect, Qt
from PySide6.QtGui import QAction, QActionGroup, QMouseEvent, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

import homebound.platform.focus_windows as focus_windows
import homebound.ui.notify as notify_module
import homebound.ui.overlay as overlay_module
import homebound.ui.toast as toast_module
from conftest import HOME, UNIVERSE, pick_system
from homebound.theme import painting
from homebound.theme.tokens import OPACITY
from homebound.logs.parser import SystemChange
from homebound.logs.watcher import CharacterState
from homebound.mapdb.store import Action, Store, ZkillStats, utc_now_iso
from homebound.mapping.tracker import AutoNamed, MapChanged, Moved, NamingRequest, UnknownJump
from homebound.ui import menus
from homebound.ui.character_dock import CharacterDock, CharacterDelegate
from homebound.ui.overlay import MODE_LABELS, Overlay, OverlayFonts
from homebound.ui.state import SCAN_AGE_INTERVAL_MS, AppState, MapOpenError, set_aside_damaged
from homebound.ui.toast import KINDS, MAX_TOASTS, Toast, ToastHost
from ui_helpers import FakeFocus, damage_systems_table, isolated_settings, make_state, write_garbage

LOG_HEADER = "\n\n  Channel Name:    Local\n  Listener:        {name}\n  Session started: 2026.10.28 16:51:48\n\n"


def write_character_log(folder, character_id: int, name: str, system: str, started: str = "20261028_165148") -> None:
    """Write a Local log of a character (UTF-16 LE with BOM) whose last location is ``system``."""
    body = LOG_HEADER.format(name=name) + (
        f"﻿[ 2026.10.28 16:51:53 ] EVE System > Channel changed to Local : {system}\n")
    (folder / "logs" / f"Local_{started}_{character_id}.txt").write_bytes(b"\xff\xfe" + body.encode("utf-16-le"))


def spy(signal) -> list:
    """Collect the arguments of every emission of a signal."""
    received: list = []
    signal.connect(lambda *arguments: received.append(arguments))
    return received


@pytest.fixture
def state(qt_application, monkeypatch, tmp_path) -> Iterator[AppState]:
    """An AppState on a temporary map; stopped afterwards."""
    app_state = make_state(monkeypatch, tmp_path)
    yield app_state
    app_state.stop()


# ---------------------------------------------------------------- state

def test_map_settings_follow_the_app_settings(state) -> None:
    """Opening a map writes home and naming scheme into it; a settings change is written on the next call."""
    assert state.store.map_setting("home_system") == state.settings.home_system == HOME.name
    assert state.store.map_setting("naming_scheme") == state.settings.naming_scheme
    other = pick_system("C3", exclude=(HOME.id,)).name
    state.settings.home_system = other
    assert state.store_map_settings() is True
    assert state.store.map_setting("home_system") == other
    assert state.store_map_settings() is False                # nothing changed the second time


def test_remote_members_show_in_systems_and_afk_is_marked(state) -> None:
    """Shared characters of other members are listed; AFK gets a mark; a character already listed isn't doubled."""
    system = HOME.id
    state.character_rows = lambda: [(91000001, "Kira", system, True, 1)]
    state.remote_presence = {91000001: {"name": "Kira", "system_id": system, "afk": False},
                             91000002: {"name": "Bob", "system_id": system, "afk": True},
                             91000003: {"name": "Elsewhere", "system_id": system + 1, "afk": False}}
    names = [name for _, name, _ in state.characters_in(system)]
    assert names == ["Kira", "Bob (AFK)"]


def test_state_construction(state, tmp_path) -> None:
    """The state opens the active map, puts home on it and starts nothing."""
    assert state.home_id == HOME.id and state.store.has_system(HOME.id)
    assert state.selected == 0 and state.followed is None
    assert state.zkill.enabled is False
    assert state.database_path == str(state.settings.map_path())
    assert state._expiry_timer is None
    with pytest.raises(TypeError):
        AppState(None, state.settings)
    with pytest.raises(TypeError):
        AppState(UNIVERSE, None)


def test_state_explicit_database_path(qt_application, monkeypatch, tmp_path) -> None:
    """A given database path wins over the active map's file."""
    settings = isolated_settings(monkeypatch, tmp_path)
    other = tmp_path / "other.db"
    state = AppState(UNIVERSE, settings, other)
    assert state.database_path == str(other) and other.exists()
    state.stop()


def test_home_id_unknown_or_unset(state) -> None:
    """No home or an unknown name gives None, and _ensure_home then adds nothing."""
    state.settings.home_system = ""
    assert state.home_id is None and state._ensure_home() is False
    state.settings.home_system = "Nowhere"
    assert state.home_id is None


def test_ensure_home(state) -> None:
    """A missing home is added once."""
    assert state._ensure_home() is False
    state.store.remove_system(HOME.id)
    assert state._ensure_home() is True and state.store.has_system(HOME.id)


def test_dispatch_emits_per_event(state) -> None:
    """Each tracker event type has its own signal; structure changes emit mapChanged once."""
    moved, naming = spy(state.characterMoved), spy(state.namingRequested)
    unknown, auto = spy(state.unknownJump), spy(state.autoNamed)
    changed, characters = spy(state.mapChanged), spy(state.charactersChanged)
    request = NamingRequest(1, 2, 3, None)
    jump = UnknownJump(7, "Pilot", 1, 2)
    named = AutoNamed(2, "A1", {})
    assert state.dispatch([Moved(7, "Pilot", 2, 1), MapChanged(), MapChanged(), request, jump, named]) is True
    assert moved == [(7,)] and naming == [(request,)] and unknown == [(jump,)] and auto == [(named,)]
    assert len(changed) == 1 and len(characters) == 1
    assert state.dispatch([jump]) is False
    assert unknown == [(jump,), (jump,)] and len(changed) == 1 and len(characters) == 2
    with pytest.raises(TypeError):
        state.dispatch(None)
    jumped = spy(state.wormholeJumped)
    state.dispatch([Moved(7, "Pilot", 2, 1)])
    state.dispatch([Moved(7, "Pilot", 3, 2, wormhole=True), MapChanged()])
    assert jumped == [(3,)]


def test_on_system_change_runs_the_tracker(state) -> None:
    """A log event moves the character and reaches the views."""
    c5 = pick_system("C5", exclude={HOME.id})
    moved = spy(state.characterMoved)
    state.on_system_change(SystemChange(7, "Pilot", HOME.name, datetime.now(timezone.utc), first=True))
    state.on_system_change(SystemChange(7, "Pilot", c5.name, datetime.now(timezone.utc)))
    assert moved and moved[0] == (7,)
    assert state.character_location(7) == c5.id


def test_expire(state) -> None:
    """Expiry tells the views only when connections went."""
    changed = spy(state.mapChanged)
    assert state.expire() == 0 and changed == []
    c5 = pick_system("C5", exclude={HOME.id})
    state.tracker.add_wormhole(HOME.id, c5.id)
    connection = state.store.connections()[0]
    state.store.update_connection(connection.id, created_at=(datetime.now(timezone.utc) - timedelta(days=5)).isoformat())
    assert state.expire() == 1 and len(changed) == 1


def test_start_and_stop(state, monkeypatch) -> None:
    """start() polls, starts the zKill thread and the expiry timer; stop() ends them."""
    started = []
    monkeypatch.setattr(state.zkill, "start", lambda: started.append("zkill"))
    monkeypatch.setattr(state.zkill, "stop", lambda: started.append("stop"))
    monkeypatch.setattr(state.eve_scout, "start", lambda: started.append("scout"))
    monkeypatch.setattr(state.eve_scout, "stop", lambda: started.append("scout stop"))
    status = spy(state.status)
    state.start()
    assert started == ["zkill", "scout"] and state._expiry_timer.isActive()
    assert state._scan_age_timer.isActive() and state._scan_age_timer.interval() == SCAN_AGE_INTERVAL_MS
    assert state.watcher.timer.isActive() and status[0][0] == "logs"
    state.stop()
    assert started == ["zkill", "scout", "stop", "scout stop"] and not state._expiry_timer.isActive() and not state.watcher.timer.isActive()
    assert not state._scan_age_timer.isActive()


def test_scan_status_and_mark_scanned(state) -> None:
    """The state's scan status uses the threshold from the settings; marking a system scanned makes it fresh."""
    assert state.scan_status(HOME.id).state == "never"
    assert state.scan_status(123456789).state == "fresh"            # unknown and not on the map
    state.store.update_system(HOME.id, scanned_at=(datetime.now(timezone.utc) - timedelta(hours=13)).isoformat())
    assert state.scan_status(HOME.id).state == "fresh"
    state.settings.rescan_due_hours = 12
    assert state.scan_status(HOME.id).state == "due"
    updated = spy(state.systemUpdated)
    assert state.mark_scanned(HOME.id) is True and updated == [(HOME.id,)]
    assert state.scan_status(HOME.id).state == "fresh"
    assert state.mark_scanned(123456789) is False and len(updated) == 1
    with pytest.raises(TypeError):
        state.scan_status("x")
    with pytest.raises(TypeError):
        state.mark_scanned("x")


def test_zkill_signals_are_forwarded(state) -> None:
    """The zKill thread's callbacks end up as systemUpdated and status."""
    updated, status = spy(state.systemUpdated), spy(state.status)
    state._zkillUpdated.emit(HOME.id)
    state._zkillStatus.emit("ok", "fine")
    assert updated == [(HOME.id,)] and status == [("zkill", "ok", "fine")]


def test_select_and_follow(state) -> None:
    """Selection and followed character emit only on a change and validate."""
    selected, followed = spy(state.selectionChanged), spy(state.followedChanged)
    assert state.select(HOME.id) is True and state.select(HOME.id) is False
    assert state.select(0) is True and selected == [(HOME.id,), (0,)]
    assert state.set_followed(5) is True and state.set_followed(5) is False
    assert state.set_followed(None) is True and followed == [(5,), (None,)]
    with pytest.raises(ValueError):
        state.select(-1)
    with pytest.raises(TypeError):
        state.select("1")
    with pytest.raises(TypeError):
        state.set_followed("5")


def test_switch_map_carries_characters(state, monkeypatch, tmp_path) -> None:
    """Switching maps keeps character locations, repoints zKill and resets the selection."""
    c5 = pick_system("C5", exclude={HOME.id})
    state.store.set_character(9, "Pilot", c5.id, "2026-10-30T12:00:00+00:00")
    state.select(HOME.id)
    first_path = state.database_path
    state.settings.add_map("Second", home_system=c5.name, naming_scheme="numeric")
    switched, mapped, selection = spy(state.mapSwitched), spy(state.mapChanged), spy(state.selectionChanged)
    assert state.switch_map("Second") is True
    assert state.database_path != first_path and state.zkill.database_path == state.database_path
    assert state.namer.store is state.store and state.tracker.store is state.store
    assert state.namer.home_id == c5.id and state.store.has_system(c5.id)
    assert state.character_location(9) == c5.id
    assert switched == [("Second",)] and len(mapped) == 1 and selection == [(0,)] and state.selected == 0
    assert state.switch_map("Second") is False and state.switch_map("Missing") is False
    assert (tmp_path / "settings.json").exists()


def saved_active_map(folder) -> str:
    """Return the open map that settings.json names."""
    return json.loads((folder / "settings.json").read_text())["active_map"]


def test_switch_to_corrupt_map_stays_on_the_open_map(state, tmp_path) -> None:
    """A map file that isn't a database can't be switched to; the open map stays open and saved as active."""
    state.settings.save()
    path = state.settings.add_map("Second")
    write_garbage(path)
    first = state.database_path
    with pytest.raises(MapOpenError) as raised:
        state.switch_map("Second")
    assert raised.value.damaged and "Second" in str(raised.value)
    assert state.settings.active_map == "Main" and saved_active_map(tmp_path) == "Main"
    assert state.database_path == first and state.zkill.database_path == first
    assert state.store.has_system(HOME.id) and state.tracker.store is state.store


def test_switch_to_map_with_damaged_table_stays_on_the_open_map(state, tmp_path) -> None:
    """A map that opens but whose systems table is damaged fails at the home check, before anything is switched."""
    c5 = pick_system("C5", exclude={HOME.id})
    state.settings.save()
    path = state.settings.add_map("Second", home_system=c5.name)
    with Store(path) as store:
        store.add_system(c5.id)
    damage_systems_table(path)
    with pytest.raises(MapOpenError) as raised:
        state.switch_map("Second")
    assert raised.value.damaged
    assert state.settings.active_map == "Main" and saved_active_map(tmp_path) == "Main"
    assert state.store.has_system(HOME.id)


def test_failed_switch_carries_no_characters_and_emits_nothing(state) -> None:
    """A failed switch sends no signals and leaves the characters on the open map."""
    state.store.set_character(9, "Pilot", HOME.id, "2026-10-30T12:00:00+00:00")
    write_garbage(state.settings.add_map("Second"))
    switched, mapped, selection = spy(state.mapSwitched), spy(state.mapChanged), spy(state.selectionChanged)
    with pytest.raises(MapOpenError):
        state.switch_map("Second")
    assert switched == [] and mapped == [] and selection == []
    assert state.character_location(9) == HOME.id


def test_switch_to_damaged_map_sets_the_file_aside(state) -> None:
    """The damaged file is kept as ``.damaged``; then backups work again and Replace brings the map back."""
    c5 = pick_system("C5", exclude={HOME.id})
    path = state.settings.add_map("Second")
    with Store(path) as store:
        store.add_system(c5.id)
    good = state.backup("manual")
    garbage = write_garbage(path)
    assert state.backup("manual") is None   # one bad map stops every backup
    with pytest.raises(MapOpenError, match=r"second\.db\.damaged"):
        state.switch_map("Second")
    assert path.with_name("second.db.damaged").read_bytes() == garbage and not path.exists()
    assert state.backup("manual") is not None
    state.restore_backup(good, ["Second"], replace=True)
    assert state.switch_map("Second") is True and state.store.has_system(c5.id)


def test_set_aside_moves_the_sidecars(tmp_path) -> None:
    """``-wal`` and ``-shm`` go with the file, so the copy stays openable and no stale WAL is left."""
    path = tmp_path / "map.db"
    for suffix, data in (("", b"main"), ("-wal", b"wal"), ("-shm", b"shm")):
        path.with_name(path.name + suffix).write_bytes(data)
    assert set_aside_damaged(path) == tmp_path / "map.db.damaged"
    assert sorted(entry.name for entry in tmp_path.iterdir()) == ["map.db.damaged", "map.db.damaged-shm",
                                                                  "map.db.damaged-wal"]
    assert (tmp_path / "map.db.damaged-wal").read_bytes() == b"wal"


def test_set_aside_keeps_an_earlier_damaged_file(tmp_path) -> None:
    """A second damaged file gets ``-2``, so the first one isn't overwritten."""
    path = tmp_path / "map.db"
    path.write_bytes(b"first")
    set_aside_damaged(path)
    path.write_bytes(b"second")
    assert set_aside_damaged(path) == tmp_path / "map.db.damaged-2"
    assert (tmp_path / "map.db.damaged").read_bytes() == b"first"
    assert (tmp_path / "map.db.damaged-2").read_bytes() == b"second"
    with pytest.raises(TypeError):
        set_aside_damaged(str(path))


def test_switch_map_skips_legacy_character_rows(state) -> None:
    """Character rows without a name or time (legacy data) are left behind; the switch and the other rows go on."""
    state.store.set_character(9, "Pilot", HOME.id, "2026-10-30T12:00:00+00:00")
    state.store.database.execute("INSERT INTO characters (id, name, system_id, seen_at) VALUES (10, NULL, ?, NULL)",
                                 (HOME.id,))
    state.store.database.execute("INSERT INTO characters (id, name, system_id, seen_at) VALUES (11, 'Old', ?, NULL)",
                                 (HOME.id,))
    state.store.database.commit()
    state.settings.add_map("Second", home_system=HOME.name, naming_scheme="numeric")
    assert state.switch_map("Second") is True
    assert [row["id"] for row in state.store.characters()] == [9]
    assert state.character_location(9) == HOME.id


def test_apply_settings(state, tmp_path) -> None:
    """New settings reach namer, tracker, zKill and the watcher (log folder change)."""
    settings = state.settings
    child = pick_system("C5", exclude={HOME.id})
    state.tracker.add_wormhole(HOME.id, child.id)
    assert state.namer.display(child.id) == "1"
    settings.naming_scheme = "a2a"
    settings.auto_name_when_certain = True
    settings.zkill_enabled = True
    new_logs = tmp_path / "other_logs"
    new_logs.mkdir()
    settings.log_directory = str(new_logs)
    changed, mapped = spy(state.settingsChanged), spy(state.mapChanged)
    state.apply_settings()
    assert state.namer.scheme_key == "a2a" and state.tracker.auto_when_certain is True
    assert state.namer.display(child.id) == "1"     # names never change on their own: renaming is asked for
    assert state.zkill.enabled is True and str(state.watcher.scanner.log_directory) == str(new_logs)
    assert len(changed) == 1 and len(mapped) == 1
    scanner = state.watcher.scanner
    state.apply_settings()
    assert state.watcher.scanner is scanner  # same folder: kept


def test_a_system_placed_from_elsewhere_gets_a_name_once(state) -> None:
    """A placement the tracker didn't make (a cloud member's, labels don't sync yet) is named when it lands, then kept."""
    first, second = pick_system("C2"), pick_system("C4")
    with state.store.batch(origin="remote"):
        state.store.add_system(first.id, parent_id=HOME.id, hole_index=3)
        state.store.add_system(second.id, parent_id=first.id, hole_index=1)
    assert (state.store.system(first.id).label, state.store.system(second.id).label) == ("3", "31")
    state.store.update_system(first.id, hole_index=4)       # a later move from elsewhere doesn't rename it
    assert state.namer.display(first.id) == "3"
    assert state._name_new_systems(Action()) == 0


def test_character_rows_merge_database_and_logs(state, tmp_path) -> None:
    """Rows combine mapped characters and scanned logs: online first, then by name."""
    c5 = pick_system("C5", exclude={HOME.id})
    state.store.set_character(2, "Zed", c5.id, "2026-10-30T12:00:00+00:00")
    state.store.set_character(3, "Amy", None, "2026-10-30T12:00:00+00:00")
    write_character_log(tmp_path, 3, "Amy", HOME.name)
    write_character_log(tmp_path, 4, "Bob", "Jita")
    state.watcher.poll()
    for character in state.watcher.characters.values():
        character.online = character.character_id == 4
    rows = state.character_rows()
    jita = UNIVERSE.by_name("Jita")
    # the polled logs also moved Amy and Bob through the tracker, so their locations are the log ones
    assert [row[:4] for row in rows] == [(4, "Bob", jita.id, True), (3, "Amy", HOME.id, False),
                                         (2, "Zed", c5.id, False)]
    assert rows[0][4] == state.settings.slot_for(4)
    state.watcher.characters[4].name = None  # the stored name stays
    assert state.character_rows()[0][1] == "Bob"
    state.watcher.characters[6] = CharacterState(6, online=True)  # scanned but nameless and unmapped
    assert state.character_rows()[0][:4] == (6, "6", None, True)
    assert state.character_location(6) is None
    assert state.character_by_name(" zed ") == 2 and state.character_by_name("nobody") is None
    assert state.character_location(2) == c5.id and state.character_location(77) is None
    with pytest.raises(TypeError):
        state.character_by_name(None)
    with pytest.raises(TypeError):
        state.character_location("2")


def test_characters_in_only_online(state) -> None:
    """Only online characters count as being in a system."""
    state.store.set_character(2, "Zed", HOME.id, "2026-10-30T12:00:00+00:00")
    assert state.characters_in(HOME.id) == []
    state.watcher.characters[2] = SimpleNamespace(character_id=2, name="Zed", online=True, last_seen=None)
    assert state.characters_in(HOME.id) == [(2, "Zed", state.settings.slot_for(2))]


def test_chain_for_character(state) -> None:
    """The chain follows the character's location; unknown ones have none."""
    assert state.chain_for_character(None) is None and state.chain_for_character(9) is None
    c5 = pick_system("C5", exclude={HOME.id})
    state.tracker.add_wormhole(HOME.id, c5.id)
    state.store.set_character(9, "Pilot", c5.id, "2026-10-30T12:00:00+00:00")
    chain = state.chain_for_character(9)
    assert chain.current_id == c5.id and {row.system_id for row in chain.rows} == {HOME.id, c5.id}


# ---------------------------------------------------------------- menus

def test_glyph_icon_cached_and_validated(qt_application) -> None:
    """Icons are cached per glyph and token; bad input is refused."""
    icon = menus.glyph_icon("✕", "danger")
    assert menus.glyph_icon("✕", "danger") is icon and not icon.isNull()
    assert menus.glyph_icon("✕", "accent") is not icon
    assert menus.danger_icon() is icon
    assert menus.confirm_icon() is menus.glyph_icon("✓", "success")
    with pytest.raises(ValueError):
        menus.glyph_icon("", "danger")
    with pytest.raises(ValueError):
        menus.glyph_icon("x", "nope")
    with pytest.raises(TypeError):
        menus.glyph_icon(None, "danger")


def test_copy_icon(qt_application) -> None:
    """The copy icon draws and validates its token."""
    assert not menus.copy_icon().isNull() and not menus.copy_icon("accent").isNull()
    with pytest.raises(ValueError):
        menus.copy_icon("nope")


def test_menu_section_and_items(qt_application) -> None:
    """Items carry shortcut text, danger icon, check state, group and a tooltip when disabled."""
    parent = QWidget()
    context = menus.menu(parent)
    assert context.toolTipsVisible()
    caption = menus.section(context, "Overlay mode")
    assert not caption.isEnabled() and caption.defaultWidget().text() == "OVERLAY MODE"
    called = []
    plain = menus.item(context, "Copy", lambda: called.append(1), "Ctrl+C")
    plain.trigger()
    assert called == [1] and plain.text() == "Copy\tCtrl+C" and not plain.isCheckable() and plain.icon().isNull()
    danger = menus.item(context, "Delete", danger=True)
    assert not danger.icon().isNull()
    danger.trigger()  # no callback: nothing happens
    group = QActionGroup(context)
    first = menus.item(context, "A", checked=True, group=group)
    second = menus.item(context, "B", checked=False, group=group)
    assert first.isCheckable() and first.isChecked() and group.checkedAction() is first
    second.trigger()
    assert group.checkedAction() is second
    free = menus.item(context, "Free", checked=True)
    assert free.isChecked() and free.actionGroup() is None
    disabled = menus.item(context, "Nope", enabled=False, why="Because.")
    assert not disabled.isEnabled() and disabled.toolTip() == "Because."
    assert menus.item(context, "Plain disabled", enabled=False).toolTip() == "Plain disabled"
    assert context.actions()[0] is caption
    with pytest.raises(TypeError):
        menus.item(context, "Bad", "not callable")
    with pytest.raises(TypeError):
        menus.item(None, "Bad")
    with pytest.raises(ValueError):
        menus.item(context, "")
    with pytest.raises(TypeError):
        menus.section(None, "x")
    with pytest.raises(ValueError):
        menus.section(context, "")
    assert isinstance(menus.menu(), QMenu)


# ---------------------------------------------------------------- notify

def test_gvariant_string_quotes() -> None:
    """Quotes and backslashes are escaped, numbers stay strings."""
    assert notify_module.gvariant_string("it's") == "'it\\'s'"
    assert notify_module.gvariant_string("a\\b") == "'a\\\\b'"
    assert notify_module.gvariant_string("42") == "'42'"
    with pytest.raises(TypeError):
        notify_module.gvariant_string(5)


def test_notification_arguments() -> None:
    """The Notify call has the app name, icon, summary, body and timeout in order."""
    arguments = notify_module.notification_arguments("Hi", "/tmp/icon.png", 1234)
    assert arguments[:2] == ["call", "--session"] and arguments[-1] == "1234"
    assert arguments[-4:] == ["'Hi'", "[]", "{}", "1234"]
    assert "'/tmp/icon.png'" in arguments and "org.freedesktop.Notifications.Notify" in arguments


def test_icon_file_cached(qt_application, monkeypatch, tmp_path) -> None:
    """The icon is written once to the cache folder and the path is remembered."""
    monkeypatch.setattr(notify_module, "_icon_path", None)
    monkeypatch.setattr(notify_module.QStandardPaths, "writableLocation", lambda location: str(tmp_path / "cache"))
    pixmap = QPixmap(8, 8)
    pixmap.fill(Qt.GlobalColor.red)
    path = notify_module._icon_file(pixmap)
    assert path == str(tmp_path / "cache" / "homebound.png") and (tmp_path / "cache" / "homebound.png").exists()
    (tmp_path / "cache" / "homebound.png").unlink()
    assert notify_module._icon_file(pixmap) == path  # cached: not saved again
    assert not (tmp_path / "cache" / "homebound.png").exists()


def test_icon_file_save_failure(qt_application, monkeypatch, tmp_path) -> None:
    """If the icon can't be saved the path is empty."""
    monkeypatch.setattr(notify_module, "_icon_path", None)
    monkeypatch.setattr(notify_module.QStandardPaths, "writableLocation", lambda location: str(tmp_path / "cache"))
    assert notify_module._icon_file(QPixmap()) == ""


def test_notify_paths(qt_application, monkeypatch) -> None:
    """gdbus is preferred, the tray is the fallback, and with neither nothing is shown."""
    started = []

    class FakeProcess:
        """Records the gdbus call instead of starting it."""

        nullDevice = staticmethod(QProcess.nullDevice)

        def setProgram(self, program: str) -> None:
            """Remember the program."""
            self.program = program

        def setStandardOutputFile(self, path: str) -> None:
            """Ignore the output redirection."""

        def setArguments(self, arguments: list) -> None:
            """Remember the arguments."""
            self.arguments = arguments

        def startDetached(self) -> None:
            """Record the arguments instead of starting a process."""
            started.append(self.arguments)

    monkeypatch.setattr(notify_module, "QProcess", FakeProcess)
    monkeypatch.setattr(notify_module, "_icon_path", "")
    monkeypatch.setattr(notify_module.shutil, "which", lambda name: "/usr/bin/gdbus")
    assert notify_module.notify("Hello", QPixmap(4, 4)) == "gdbus"
    assert notify_module.notify("Hello") == "gdbus"
    assert len(started) == 2 and "'Hello'" in started[0]
    monkeypatch.setattr(notify_module.shutil, "which", lambda name: None)
    assert notify_module.notify("Hello") == ""
    messages = []
    tray = SimpleNamespace(icon=lambda: "icon", showMessage=lambda *arguments: messages.append(arguments))
    assert notify_module.notify("Hello", tray=tray, timeout_ms=10) == "tray"
    assert messages == [("Homebound", "Hello", "icon", 10)]
    with pytest.raises(TypeError):
        notify_module.notify(1)
    with pytest.raises(ValueError):
        notify_module.notify("x", timeout_ms=-1)


# ---------------------------------------------------------------- toast

@pytest.fixture
def anchor(qt_application) -> Iterator[QWidget]:
    """A shown parent widget for toasts."""
    widget = QWidget()
    widget.resize(900, 600)
    widget.show()
    yield widget
    widget.close()


def test_toast_kinds_and_validation(anchor) -> None:
    """Every kind builds; an unknown kind, bad text or timeout is refused."""
    for kind in KINDS:
        toast = Toast(anchor, kind, "Text", detail="More")
        assert toast.title.text() == "Text"
        toast.grab()
        toast.dismiss()
    with pytest.raises(ValueError):
        Toast(anchor, "nope", "Text")
    with pytest.raises(TypeError):
        Toast(anchor, "ok", None)
    with pytest.raises(ValueError):
        Toast(anchor, "ok", "Text", timeout_ms=0)
    assert toast_module.Glyph("nope").glyph == "i"


def test_toast_actions_and_countdown(anchor) -> None:
    """An action runs its callback and closes the toast without the default; the label counts seconds."""
    answers, defaults = [], []
    toast = Toast(anchor, "ask", "Name it?", [("Yes", lambda: answers.append("yes"), True),
                                             ("No", lambda: answers.append("no"), False)],
                  timeout_ms=2500, default=lambda: defaults.append(1), countdown=True)
    assert len(toast.buttons) == 2 and toast.seconds_label.text() == "3s" and toast.show_bar
    toast.grab()
    closed = spy(toast.closed)
    toast.buttons[0].click()
    assert answers == ["yes"] and defaults == [] and len(closed) == 1 and not toast.timer.isActive()
    assert toast.dismiss() is False  # already closed
    assert len(closed) == 1


def test_toast_without_countdown_has_no_label_or_bar(anchor) -> None:
    """A plain toast has neither seconds label nor timeout bar."""
    toast = Toast(anchor, "info", "Hi")
    assert toast.seconds_label is None and not toast.show_bar
    toast.dismiss()


def test_toast_pause_and_timeout_runs_default(anchor) -> None:
    """The countdown pauses while hovered; at zero the default callback runs once."""
    defaults = []
    toast = Toast(anchor, "ask", "Q", [("A", lambda: None, False)], timeout_ms=100, default=lambda: defaults.append(1),
                  countdown=True)
    toast.enterEvent(QEvent(QEvent.Type.Enter))
    toast._tick()
    assert toast.left_ms == 100
    toast.leaveEvent(QEvent(QEvent.Type.Leave))
    toast._tick()
    assert toast.left_ms == 50 and toast.seconds_label.text() == "1s"
    toast._tick()
    assert defaults == [1] and not toast.timer.isActive()
    toast._tick()
    assert defaults == [1]


def test_toast_close_button_applies_default(anchor) -> None:
    """Closing a question with the x counts as no answer, so the default runs."""
    defaults = []
    toast = Toast(anchor, "ask", "Q", [("A", lambda: None, False)], default=lambda: defaults.append(1))
    toast.dismiss()
    assert defaults == [1]


def test_toast_host_layout_and_limit(anchor) -> None:
    """The host stacks toasts above the bottom-right corner and keeps at most three."""
    host = ToastHost(anchor)
    toasts = [host.show("info", f"T{index}", timeout_ms=60000) for index in range(MAX_TOASTS + 2)]
    assert len(host.toasts) == MAX_TOASTS and host.toasts == toasts[-MAX_TOASTS:]
    assert host.layout() == MAX_TOASTS
    tops = [toast.y() for toast in host.toasts]
    assert tops == sorted(tops) and all(toast.x() + toast.width() <= anchor.width() for toast in host.toasts)
    for toast in list(host.toasts):
        toast.dismiss()
    assert host.toasts == []
    with pytest.raises(TypeError):
        ToastHost(None)


def test_toast_host_default_timeouts(anchor) -> None:
    """Questions stay longer than plain messages."""
    host = ToastHost(anchor)
    assert host.show("info", "x").total_ms == 2500
    assert host.show("ask", "x", [("A", lambda: None, False)]).total_ms == 12000
    for toast in list(host.toasts):
        toast.dismiss()


def test_toast_host_undo(anchor) -> None:
    """Undo runs once, replaces a pending undo without running it, and clears when the toast ends."""
    host = ToastHost(anchor)
    undone = []
    assert host.trigger_undo() is False
    first = host.undo("Deleted", "A", lambda: undone.append("first"))
    second = host.undo("Deleted", "B", lambda: undone.append("second"), seconds=3)
    assert host.undo_toast is second and not first.timer.isActive() and second.total_ms == 3000
    assert second.seconds_label.text() == "3s"
    second.buttons[0].click()  # the Undo button
    assert undone == ["second"] and host.undo_toast is None and host.undo_callback is None
    assert host.trigger_undo() is False
    third = host.undo("Deleted", "C", lambda: undone.append("third"))
    assert host.trigger_undo() is True and undone == ["second", "third"] and not third.timer.isActive()
    fourth = host.undo("Deleted", "D", lambda: undone.append("fourth"))
    fourth.dismiss(apply_default=False)  # timed out: the action is committed, not undone
    assert host.undo_toast is None and host.trigger_undo() is False and undone == ["second", "third"]
    with pytest.raises(TypeError):
        host.undo("x", "y", "nope")
    with pytest.raises(ValueError):
        host.undo("x", "y", lambda: None, seconds=0)


def test_toast_host_mirrors_to_notifications_when_hidden(qt_application, monkeypatch) -> None:
    """With the window hidden or minimised a toast also becomes a desktop notification."""
    sent = []
    monkeypatch.setattr(toast_module, "notify", lambda text, pixmap, tray: sent.append((text, tray)))
    window = QWidget()
    window.resize(400, 300)
    tray = QSystemTrayIcon()
    host = ToastHost(window, tray)
    host.show("info", "hidden", timeout_ms=60000)
    assert sent == [("hidden", tray)]
    window.show()
    host.show("info", "visible", timeout_ms=60000)
    assert len(sent) == 1
    window.showMinimized()
    host.show("info", "minimised", timeout_ms=60000)
    assert [text for text, _ in sent] == ["hidden", "minimised"]
    for toast in list(host.toasts):
        toast.dismiss()
    window.close()


# ---------------------------------------------------------------- overlay

@pytest.fixture
def overlay_setup(state) -> Iterator[tuple[Overlay, FakeFocus, AppState]]:
    """An overlay on the temporary state with a fake focus tracker."""
    focus = FakeFocus()
    overlay = Overlay(state, focus)
    yield overlay, focus, state
    overlay.hide()
    overlay.deleteLater()


def put_character(state, character_id: int, name: str, system_id: int | None) -> None:
    """Record a character at a system in the map database."""
    state.store.set_character(character_id, name, system_id, utc_now_iso())
    state.charactersChanged.emit()


def test_overlay_fonts_clamp(qt_application) -> None:
    """The text size is clamped to 11-14 px."""
    assert OverlayFonts(5).row.pixelSize() == 11 and OverlayFonts(99).row.pixelSize() == 14
    assert OverlayFonts(12).current.weight() > OverlayFonts(12).row.weight()


def test_overlay_modes_and_should_show(overlay_setup) -> None:
    """Visibility per mode: off never, follow needs a known focused character, pinned modes the pin."""
    overlay, focus, state = overlay_setup
    settings = state.settings
    put_character(state, 5, "Pilot", HOME.id)
    settings.overlay_mode = "off"
    assert overlay.refresh() is False and not overlay.isVisible()
    settings.overlay_mode = "follow"
    assert overlay.refresh() is False and overlay.target is None
    focus.focusChanged.emit("Pilot", QRect(0, 0, 10, 10))
    assert overlay.focused_character == "Pilot" and overlay.target == 5 and overlay.isVisible()
    assert state.followed == 5
    focus.focusChanged.emit(None, QRect())
    assert not overlay.isVisible() and overlay.target is None
    focus.focusChanged.emit("Stranger", QRect())
    assert not overlay.isVisible()
    settings.overlay_mode, settings.overlay_pinned_character = "pinned_eve", 5
    assert overlay.refresh() is True  # an EVE client (any) is focused
    focus.focusChanged.emit(None, QRect())
    assert not overlay.isVisible()
    settings.overlay_mode = "pinned_always"
    assert overlay.refresh() is True and overlay.isVisible() and overlay.target == 5
    settings.overlay_pinned_character = None
    assert overlay.refresh() is True and overlay.chain is None
    assert overlay.refresh() is True  # already visible: stays
    settings.overlay_mode = "pinned_eve"
    focus.focusChanged.emit("", QRect())  # character select
    assert overlay.focused_character == "" and overlay.should_show() is False


def test_overlay_unlock_saves_position_and_signals(overlay_setup) -> None:
    """Unlocking shows it always and makes it clickable; locking saves the position per mode."""
    overlay, _, state = overlay_setup
    state.settings.overlay_mode = "follow"
    changes = spy(overlay.unlockedChanged)
    assert overlay.set_unlocked(True) is True and overlay.set_unlocked(True) is False
    assert overlay.isVisible() and overlay.should_show()
    assert not overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    overlay.move(333, 222)
    assert overlay.set_unlocked(False) is True
    assert changes == [(True,), (False,)]
    assert state.settings.overlay_positions["follow"] == [overlay.x(), overlay.y()]
    assert (state.settings.map_path().parent / "settings.json").exists()
    assert overlay.windowFlags() & Qt.WindowType.WindowTransparentForInput
    state.settings.overlay_positions["follow"] = [10, 20]
    assert overlay._restore_position() == QPoint(10, 20)
    state.settings.overlay_mode = "pinned_eve"
    assert overlay._restore_position() == QPoint(80, 120)


@pytest.mark.parametrize("platform, bypass", [("linux", True), ("win32", False)])
def test_overlay_flags_per_platform(overlay_setup, monkeypatch, platform, bypass) -> None:
    """Only X11 bypasses the window manager; Windows relies on topmost (re-applied on every refresh)."""
    overlay, _, state = overlay_setup
    monkeypatch.setattr(overlay_module.sys, "platform", platform)
    raised = []
    monkeypatch.setattr(focus_windows, "keep_on_top", raised.append)
    flags = overlay._apply_flags()
    assert bool(flags & Qt.WindowType.X11BypassWindowManagerHint) is bypass
    assert flags & Qt.WindowType.WindowStaysOnTopHint and flags & Qt.WindowType.WindowTransparentForInput
    state.settings.overlay_mode = "pinned_always"
    assert overlay.refresh() is True
    assert raised == ([] if bypass else [int(overlay.winId())])
    overlay.hide()


def test_overlay_refreshes_on_state_signals(overlay_setup, monkeypatch) -> None:
    """Map, character, settings and zKill changes all refresh the overlay."""
    overlay, _, state = overlay_setup
    calls = []
    original = overlay.refresh
    monkeypatch.setattr(Overlay, "refresh", lambda self: calls.append(1) or original())
    state.mapChanged.emit()
    state.charactersChanged.emit()
    state.settingsChanged.emit()
    state.systemUpdated.emit(HOME.id)
    assert len(calls) == 4


def test_overlay_geometry(overlay_setup) -> None:
    """Height follows the rows; the row height grows with the text; the opacity is clamped."""
    overlay, _, state = overlay_setup
    state.settings.overlay_mode = "pinned_always"
    state.settings.overlay_pinned_character = None
    overlay.refresh()
    empty_height = overlay.height()
    c5 = pick_system("C5", exclude={HOME.id})
    state.tracker.add_wormhole(HOME.id, c5.id)
    put_character(state, 5, "Pilot", HOME.id)
    state.settings.overlay_pinned_character = 5
    overlay.refresh()
    assert overlay.chain and len(overlay.chain.rows) == 2 and overlay.height() == empty_height
    c3 = pick_system("C3")
    state.tracker.add_wormhole(c5.id, c3.id)
    overlay.refresh()
    assert overlay.height() > empty_height
    state.settings.overlay_text_size = 14
    assert overlay._row_height() == 22
    state.settings.overlay_opacity = 5
    assert overlay._background_opacity() < 1.0
    state.settings.overlay_opacity = 0
    assert overlay._background_opacity() > 0
    overlay.unlocked = True
    assert overlay._background_opacity() == OPACITY["op-overlay-unlocked"]


def test_overlay_paints_empty_states(overlay_setup) -> None:
    """Not in a mapped chain: with a last known system, unknown location, or no client focused."""
    overlay, focus, state = overlay_setup
    state.settings.overlay_mode = "pinned_always"
    overlay.refresh()
    assert overlay.grab().width() > 0  # no character
    state.settings.overlay_pinned_character = 8
    put_character(state, 8, "Lost", None)
    overlay.refresh()
    assert overlay.chain is None and overlay.grab().width() > 0  # "No EVE client focused"
    focus.focusChanged.emit("", QRect())
    assert overlay.grab().width() > 0  # "Location unknown"
    put_character(state, 8, "Lost", pick_system("C2").id)
    overlay.refresh()
    assert overlay.chain is None and overlay.grab().width() > 0  # "Last known"


def test_overlay_paints_chain_rows(overlay_setup) -> None:
    """Chain rows with current marker, owner, zKill numbers, recent kill, effect and unlocked hints."""
    overlay, _, state = overlay_setup
    settings = state.settings
    settings.overlay_mode, settings.overlay_pinned_character = "pinned_always", 5
    settings.zkill_enabled = True
    c5 = pick_system("C5", exclude={HOME.id})
    state.tracker.add_wormhole(HOME.id, c5.id)
    put_character(state, 5, "Pilot", c5.id)
    state.store.update_system(HOME.id, owner_ticker="ABC", owner_slot=2)
    state.store.update_system(c5.id, owner_ticker="XYZ")
    state.store.set_zkill_stats(ZkillStats(HOME.id, 2, 7, 0, utc_now_iso(), utc_now_iso()))
    state.store.set_zkill_stats(ZkillStats(c5.id, 0, 0, 0, (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(),
                                           utc_now_iso()))
    assert overlay.refresh()
    assert [row.current for row in overlay.chain.rows] == [False, True]
    image = overlay.grab().toImage()
    assert not image.isNull() and image.width() == overlay.width()
    overlay.set_unlocked(True)
    assert overlay.grab().width() == overlay.width()
    settings.zkill_enabled = False
    assert overlay.grab().width() > 0


def test_overlay_paints_a_system_with_an_effect_and_no_name_room(overlay_setup) -> None:
    """Wormhole systems show their name and effect when there is room."""
    overlay, _, state = overlay_setup
    settings = state.settings
    settings.overlay_mode, settings.overlay_pinned_character = "pinned_always", 5
    effect_system = next(system for system in UNIVERSE.all_systems()
                         if system.effect and system.system_class == "C5" and system.id != HOME.id)
    state.tracker.add_wormhole(HOME.id, effect_system.id)
    put_character(state, 5, "Pilot", effect_system.id)
    overlay.refresh()
    assert overlay.grab().width() > 0
    state.store.update_system(effect_system.id, owner_ticker="A" * 12)
    assert overlay.grab().width() > 0


def test_overlay_paints_virtual_kspace_row(overlay_setup) -> None:
    """A character a few gates out of a mapped k-space entrance gets a virtual "Nj from" row."""
    overlay, _, state = overlay_setup
    settings = state.settings
    settings.overlay_mode, settings.overlay_pinned_character = "pinned_always", 5
    jita = UNIVERSE.by_name("Jita")
    state.tracker.add_wormhole(HOME.id, jita.id)
    out = next(system_id for system_id, jumps in sorted(UNIVERSE.gate_distances(jita.id, 2).items()) if jumps == 2)
    put_character(state, 5, "Pilot", out)
    overlay.refresh()
    virtual = [row for row in overlay.chain.rows if row.virtual]
    assert len(virtual) == 1 and virtual[0].jumps_from == 2
    assert overlay.grab().width() > 0
    # a virtual row whose entrance is unknown still paints
    virtual[0].entrance_id = None
    overlay.grab()
    # a row for a system the universe doesn't know is skipped
    overlay.chain.rows[0].system_id = 1
    overlay.grab()


def test_overlay_kspace_rows_show_name_without_brackets_and_closest_hub(overlay_setup, monkeypatch) -> None:
    """A mapped k-space row shows its real name plainly and the closest hub; the name gives way when there's no room."""
    overlay, _, state = overlay_setup
    settings = state.settings
    settings.overlay_mode, settings.overlay_pinned_character = "pinned_always", 5
    jita, perimeter = UNIVERSE.by_name("Jita"), UNIVERSE.by_name("Perimeter")
    state.tracker.add_wormhole(HOME.id, perimeter.id)
    put_character(state, 5, "Pilot", jita.id)       # one gate out: a virtual row with its hub too
    overlay.refresh()
    texts: list[str] = []
    original = painting.draw_halo_text
    monkeypatch.setattr(painting, "draw_halo_text",
                        lambda painter, x, baseline, text, *rest: texts.append(text) or original(painter, x, baseline,
                                                                                                  text, *rest))
    overlay.grab()
    assert "Perimeter" in texts and "Jita·1" in texts and "Jita·0" in texts
    assert not any(text.startswith("(") for text in texts)
    texts.clear()
    state.store.update_system(perimeter.id, owner_ticker="W" * 40)
    overlay.grab()
    assert "Perimeter" not in texts


def test_overlay_drag(overlay_setup) -> None:
    """Dragging moves the unlocked overlay and saves where it ends; a locked one ignores the mouse."""
    overlay, _, state = overlay_setup
    state.settings.overlay_mode = "follow"

    def mouse(kind: QEvent.Type, point: QPoint, button: Qt.MouseButton = Qt.MouseButton.LeftButton) -> QMouseEvent:
        """Build a mouse event at a global position."""
        return QMouseEvent(kind, QPointF(1, 1), QPointF(point), button, button, Qt.KeyboardModifier.NoModifier)

    overlay.move(100, 100)
    overlay.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, QPoint(110, 110)))
    overlay.mouseMoveEvent(mouse(QEvent.Type.MouseMove, QPoint(150, 160)))
    assert overlay.pos() == QPoint(100, 100)
    overlay.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, QPoint(150, 160)))
    assert "follow" not in state.settings.overlay_positions
    overlay.set_unlocked(True)
    overlay.move(100, 100)
    overlay.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, QPoint(110, 110), Qt.MouseButton.RightButton))
    overlay.mouseMoveEvent(mouse(QEvent.Type.MouseMove, QPoint(150, 160)))
    assert overlay.pos() == QPoint(100, 100)
    overlay.mousePressEvent(mouse(QEvent.Type.MouseButtonPress, QPoint(110, 110)))
    overlay.mouseMoveEvent(mouse(QEvent.Type.MouseMove, QPoint(150, 160)))
    assert overlay.pos() == QPoint(140, 150)
    overlay.mouseReleaseEvent(mouse(QEvent.Type.MouseButtonRelease, QPoint(150, 160)))
    assert state.settings.overlay_positions["follow"] == [140, 150] and overlay._drag_offset is None


def test_overlay_requires_a_state(qt_application) -> None:
    """The overlay needs an AppState."""
    with pytest.raises(TypeError):
        Overlay(None, FakeFocus())


def test_overlay_mode_labels_cover_the_modes() -> None:
    """Every visible mode has a header label."""
    assert set(MODE_LABELS) == {"follow", "pinned_eve", "pinned_always"}


# ---------------------------------------------------------------- character dock

@pytest.fixture
def dock_setup(state) -> Iterator[tuple[CharacterDock, AppState]]:
    """A shown character dock on the temporary state."""
    dock = CharacterDock(state)
    dock.resize(300, 300)
    dock.show()
    yield dock, state
    dock.close()


def test_dock_reload_and_paint_states(dock_setup, tmp_path) -> None:
    """Rows follow the state; online, offline with age, followed, unknown and k-space rows all paint."""
    dock, state = dock_setup
    assert dock.reload() == 0 and dock.grab().width() > 0
    c5 = pick_system("C5", exclude={HOME.id})
    state.tracker.add_wormhole(HOME.id, c5.id)
    state.store.update_system(c5.id, custom_name="Static") if "custom_name" in state.store.system(c5.id).__dict__ else None
    write_character_log(tmp_path, 3, "Amy", c5.name)
    put_character(state, 4, "Bob", None)
    put_character(state, 6, "Cat", UNIVERSE.by_name("Jita").id)
    state.watcher.poll()
    state.watcher.characters[3].online = True
    state.set_followed(3)
    assert dock.count() == 3 and dock.item(0).toolTip() == "Amy (3)"
    assert dock.grab().width() > 0
    state.watcher.characters[3].online = False
    dock.reload()
    delegate = dock.itemDelegate()
    assert isinstance(delegate, CharacterDelegate)
    assert delegate.online_text(3, True) == "● online"
    assert delegate.online_text(3, False).startswith("○ offline ")
    assert delegate.online_text(4, False) == "○ offline"
    assert delegate.sizeHint(None, None).height() == 44
    dock.setCurrentRow(1)
    dock.grab()
    state.mapChanged.emit()


def test_dock_show_on_map(dock_setup) -> None:
    """Double-click and Space select the character's system if it is on the map."""
    dock, state = dock_setup
    put_character(state, 4, "Bob", HOME.id)
    put_character(state, 5, "Amy", pick_system("C2").id)
    put_character(state, 6, "Cat", None)
    dock.reload()
    rows = {dock.item(index).data(Qt.ItemDataRole.UserRole)[1]: dock.item(index) for index in range(dock.count())}
    assert dock.show_on_map(rows["Bob"]) is True and state.selected == HOME.id
    state.select(0)
    assert dock.show_on_map(rows["Amy"]) is False and dock.show_on_map(rows["Cat"]) is False
    assert state.selected == 0
    dock.setCurrentItem(rows["Bob"])
    assert dock._show_current_on_map() is True and state.selected == HOME.id
    dock.setCurrentItem(None)
    assert dock._show_current_on_map() is False
    dock.itemDoubleClicked.emit(rows["Bob"])


def test_dock_space_shortcut(dock_setup, qt_application) -> None:
    """Space on the focused list shows the current character."""
    from PySide6.QtTest import QTest
    dock, state = dock_setup
    put_character(state, 4, "Bob", HOME.id)
    dock.reload()
    dock.setCurrentRow(0)
    dock.setFocus()
    qt_application.processEvents()
    QTest.keyClick(dock, Qt.Key.Key_Space)
    assert state.selected == HOME.id


def test_dock_context_menu(dock_setup, modal, monkeypatch) -> None:
    """The menu pins the overlay and shows on map; the latter is disabled off the map."""
    dock, state = dock_setup

    class AnsweringMenu(QMenu):
        """QMenu.exec can't be patched on the class (PySide ignores it), so the menu answers itself."""

        def exec(self, *arguments: object) -> QAction | None:
            """Let the modal responder pick the action."""
            return modal.answer_menu(self, *arguments)

    monkeypatch.setattr(menus, "QMenu", AnsweringMenu)
    put_character(state, 4, "Bob", HOME.id)
    put_character(state, 5, "Amy", None)
    dock.reload()
    pins = spy(dock.pinRequested)
    assert [dock.item(index).data(Qt.ItemDataRole.UserRole)[1] for index in range(2)] == ["Amy", "Bob"]
    first = dock.visualItemRect(dock.item(1)).center()   # Bob, on the map
    second = dock.visualItemRect(dock.item(0)).center()  # Amy, location unknown
    dock._context_menu(QPoint(5, 5000))  # empty area: no menu
    assert modal.menus == []

    def pick(text: str) -> Callable[[QMenu], QAction]:
        """Return a menu handler that picks the action starting with ``text``."""
        return lambda menu: next(action for action in menu.actions() if action.text().startswith(text))

    modal.menu_handler = pick("Pin overlay")
    dock._context_menu(first)
    assert pins == [(4,)]
    modal.menu_handler = pick("Show on map")
    dock._context_menu(first)
    assert state.selected == HOME.id
    state.select(0)
    modal.menu_handler = lambda menu: None
    dock._context_menu(second)
    show = [action for action in modal.menus[-1].actions() if action.text().startswith("Show on map")][0]
    assert not show.isEnabled() and show.toolTip() == "Not in a system on this map."
    assert show.text().endswith("Space")


def test_dock_delegate_paints_hover(dock_setup) -> None:
    """A hovered row gets the hover fill."""
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
    dock, state = dock_setup
    put_character(state, 4, "Bob", HOME.id)
    dock.reload()
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 300, 44)
    images = {}
    for flag in (QStyle.StateFlag.State_None, QStyle.StateFlag.State_MouseOver):
        option.state = flag
        pixmap = QPixmap(300, 44)
        pixmap.fill(Qt.GlobalColor.black)
        painter = QPainter(pixmap)
        dock.itemDelegate().paint(painter, option, dock.model().index(0, 0))
        painter.end()
        images[flag] = pixmap.toImage()
    plain, hovered = images.values()
    assert plain.pixelColor(150, 40) != hovered.pixelColor(150, 40)

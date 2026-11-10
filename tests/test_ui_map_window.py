"""Tests for the main window: toolbar, maps, naming queue, context menus, shortcuts, tray and legend."""
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from typing import NoReturn

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QAction, QCloseEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu, QPlainTextEdit, QSystemTrayIcon

from conftest import HOME, SCAN, pick_system
from homebound.backup import list_backups
from homebound.logs.parser import SystemChange
from homebound.mapdb.store import Store
from homebound.mapping import signature_parser
from homebound.mapping.tracker import AutoNamed, NamingRequest, UnknownJump
from homebound.theme.tokens import PIXELS
from homebound.ui import map_window as map_window_module
from homebound.ui.dialogs import ConnectionDialog, NamingDialog
from homebound.ui.map_window import Legend, MapArea, MapWindow, Scrim, app_mark
from homebound.ui.toast import Glyph
from homebound.universe.db import System
from ui_helpers import FakeFocus, make_state

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def window(qt_application, monkeypatch, tmp_path, modal) -> Iterator[MapWindow]:
    """The main window on a temporary map, shown offscreen, with the settings dialog stubbed out."""
    opened = []
    monkeypatch.setattr(map_window_module, "SettingsDialog",
                        lambda parent, state, page: type("Stub", (), {"exec": lambda self: opened.append(page) or 0})())
    state = make_state(monkeypatch, tmp_path)
    main_window = MapWindow(state, FakeFocus())
    main_window.opened_pages = opened
    main_window.show()
    qt_application.processEvents()
    yield main_window
    main_window.overlay.hide()
    main_window.hide()
    state.stop()


def add_child(window, system_class="C5", exclude=()) -> System:
    """Connect a new system of a class to home by wormhole and return it."""
    child = pick_system(system_class, exclude={HOME.id, *exclude})
    window.state.dispatch(window.state.tracker.add_wormhole(HOME.id, child.id))
    return child


def find_action(menu: QMenu, text: str) -> QAction:
    """Return the action of a menu whose label starts with ``text``."""
    for action in menu.actions():
        if action.text().replace("&", "").startswith(text):
            return action
    raise LookupError(text)


def toast_kind(toast) -> str:
    """Return the kind token (e.g. "danger") of a toast's glyph."""
    return toast.findChild(Glyph).token


def toast_text(toast) -> str:
    """Return the title line of a toast."""
    return toast.title.text()


def pick(text: str) -> Callable[[QMenu], QAction]:
    """A modal menu handler that picks the action starting with ``text``."""
    return lambda menu: find_action(menu, text)


# ---------------------------------------------------------------- helpers and widgets
def test_app_mark_draws_and_validates(qt_application) -> None:
    """The mark is a square pixmap, fainter when dimmed, and bad sizes are refused."""
    bright, dim = app_mark(32).toImage(), app_mark(32, dim=True).toImage()
    assert bright.width() == 32 and bright != dim
    with pytest.raises(TypeError):
        app_mark("big")
    with pytest.raises(ValueError):
        app_mark(0)


def test_scrim_paints(qt_application) -> None:
    """The scrim fills itself with a translucent colour."""
    scrim = Scrim()
    scrim.resize(20, 20)
    image = scrim.grab().toImage()
    assert image.pixelColor(5, 5).alpha() > 0


def test_map_area_zoom_label_and_tag(window) -> None:
    """Zoom changes update the label, and the map tag only shows for a name."""
    area = window.area
    window.view.set_zoom(1.5)
    assert area.zoom_label.text() == "150%"
    assert area.set_map_tag("Second") is True and not area.map_tag.isHidden()
    assert "Second" in area.map_tag.text()
    assert area.set_map_tag(None) is False and area.map_tag.isHidden()
    with pytest.raises(TypeError):
        area.set_map_tag(5)
    with pytest.raises(TypeError):
        MapArea("not a view")


def test_map_area_zoom_buttons_and_resize(window, qt_application) -> None:
    """The floating zoom buttons scale the view; resizing re-places toasts and the scrim."""
    area = window.area
    before = window.view.zoom()
    buttons = [child for child in area.zoom.children() if hasattr(child, "click")]
    buttons[1].click()
    assert window.view.zoom() > before
    buttons[0].click()
    window.toasts.show("ok", "hello")
    window.resize(1500, 900)
    qt_application.processEvents()
    assert area.scrim.geometry() == area.rect()
    area.toasts = None
    area.resize(800, 600)


def test_legend_toggles_and_paints(window, qt_application) -> None:
    """A click opens the legend; both states paint without error."""
    legend = window.area.legend
    assert legend.open is False and legend.width() == 84
    collapsed = legend.grab().toImage()
    event = QMouseEvent(QMouseEvent.Type.MouseButtonPress, QPointF(5, 5), QPointF(5, 5),
                        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    legend.mousePressEvent(event)
    assert legend.open and legend.width() == 170
    expanded = legend.grab().toImage()
    assert expanded.height() > collapsed.height()
    legend.mousePressEvent(event)
    assert not legend.open
    geometry = legend.reposition()
    assert geometry.left() == PIXELS["sp-16"] and geometry.bottom() < window.area.height()
    assert geometry.width() == 84
    assert all(key in legend.pens and label for key, label in Legend.ITEMS)


# ---------------------------------------------------------------- toolbar, settings sync
def test_toolbar_reflects_settings(window) -> None:
    """The toolbar shows map, home, scheme and overlay mode from the settings."""
    settings = window.state.settings
    assert HOME.name in window.home_button.text()
    assert settings.active_map in window.map_button.text()
    assert window.windowTitle() == "Homebound"
    assert window.area.map_tag.isHidden()
    settings.home_system = ""
    window.state.settingsChanged.emit()
    assert "Set home" in window.home_button.text()


def test_home_button_and_settings_button_open_settings(window) -> None:
    """The home button opens Settings on page 1, the settings button on page 0."""
    window.home_button.click()
    assert window.opened_pages == [1]
    assert window.open_settings() == 0
    assert window.opened_pages[-1] == 0
    with pytest.raises(TypeError):
        window.open_settings("one")
    with pytest.raises(ValueError):
        window.open_settings(-1)


def test_scheme_combo_applies_scheme(window) -> None:
    """Choosing a scheme in the toolbar stores it and renames systems."""
    combo = window.scheme_combo
    index = next(row for row in range(combo.count()) if combo.itemData(row) != window.state.settings.naming_scheme)
    combo.setCurrentIndex(index)
    window._scheme_changed(index)
    assert window.state.settings.naming_scheme == combo.itemData(index)


# ---------------------------------------------------------------- maps
def test_map_menu_lists_maps_and_commands(window) -> None:
    """The map menu shows the maps, new/rename and a disabled delete submenu for a single map."""
    menu = window._fill_map_menu()
    texts = [action.text() for action in menu.actions()]
    assert any(text.startswith("New map") for text in texts)
    assert any(text.startswith("Rename") for text in texts)
    delete = find_action(menu, "Delete map")
    assert not delete.menu().isEnabled()


def test_map_menu_marks_cloud_maps(window) -> None:
    """A cloud map's entry starts with the cloud mark; a local map's doesn't."""
    settings = window.state.settings
    settings.maps["Shared chain"] = {"file": "shared.sqlite", "home_system": HOME.name, "cloud": "m-123"}
    texts = [action.text() for action in window._fill_map_menu().actions()]
    assert any(text.startswith("☁ Shared chain") for text in texts)
    local = next(text for text in texts if settings.active_map in text and "·" in text)
    assert not local.startswith("☁")


def test_delete_map_menu_switch_note_only_with_other_maps(window) -> None:
    """The "X is open: switch first" row appears in the delete submenu only when other maps are listed."""
    settings = window.state.settings
    note = f"{settings.active_map} is open: switch first"
    alone = find_action(window._fill_map_menu(), "Delete map").menu()
    assert [action.text() for action in alone.actions()] == []
    settings.add_map("Other")
    listed = [action for action in find_action(window._fill_map_menu(), "Delete map").menu().actions()]
    assert [action.text() for action in listed] == ["Other", note]
    assert not listed[-1].isEnabled()


def test_new_map_creates_and_switches(window, modal) -> None:
    """Accepting the New map dialog creates the map, switches to it and opens Settings on Home."""
    modal.handler = lambda dialog: (dialog.field.setText("Second"), 1)[1]
    assert window.new_map() is True
    settings = window.state.settings
    assert settings.active_map == "Second" and "Second" in settings.maps
    assert window.opened_pages == [1]
    assert window.windowTitle() == "Homebound · Second"
    assert not window.area.map_tag.isHidden()


def test_new_map_cancel_and_error(window, modal, monkeypatch) -> None:
    """Rejecting does nothing; a failing add_map shows an error toast."""
    assert window.new_map() is False
    modal.handler = lambda dialog: (dialog.field.setText("Broken"), 1)[1]

    def refuse(name: str) -> NoReturn:
        """Refuse every new map name."""
        raise ValueError("no way")
    monkeypatch.setattr(window.state.settings, "add_map", refuse)
    assert window.new_map() is False
    toast = window.toasts.toasts[-1]
    assert toast_kind(toast) == "danger" and toast_text(toast) == "no way"


def test_map_name_validation(window) -> None:
    """Names must be present, unique (except the kept one) and short."""
    check = window._map_name_check()
    assert check("")[0] is False
    assert check("x" * 40)[0] is False
    assert check("Fine")[0] is True
    existing = window.state.settings.active_map
    assert check(existing)[0] is False
    assert window._map_name_check(keep=existing)(existing)[0] is True


def test_rename_map(window, modal) -> None:
    """Renaming the open map updates the settings and the toolbar."""
    old = window.state.settings.active_map
    assert window.rename_map() is False  # rejected
    modal.handler = lambda dialog: 1  # unchanged text
    assert window.rename_map() is False
    modal.handler = lambda dialog: (dialog.field.setText("Renamed"), 1)[1]
    assert window.rename_map() is True
    assert window.state.settings.active_map == "Renamed" and old not in window.state.settings.maps
    assert "Renamed" in window.map_button.text()


def test_rename_map_error(window, modal, monkeypatch) -> None:
    """A rename the settings refuse produces a toast, not an exception."""
    modal.handler = lambda dialog: (dialog.field.setText("Other"), 1)[1]
    monkeypatch.setattr(window.state.settings, "rename_map", lambda old, new: (_ for _ in ()).throw(ValueError("bad")))
    assert window.rename_map() is False
    toast = window.toasts.toasts[-1]
    assert toast_kind(toast) == "danger" and toast_text(toast) == "bad"


def test_switch_map_and_validation(window) -> None:
    """Switching to the open map does nothing; to another one changes the state."""
    settings = window.state.settings
    assert window.switch_map(settings.active_map) is False
    settings.add_map("Other")
    assert window.switch_map("Other") is True
    assert settings.active_map == "Other"
    with pytest.raises(TypeError):
        window.switch_map(3)
    with pytest.raises(ValueError):
        window.switch_map(" ")


def test_delete_map(window, modal) -> None:
    """Deleting another map asks first, removes its files and shows a toast."""
    settings = window.state.settings
    path = settings.add_map("Doomed")
    Store(path).close()
    wal = path.with_name(path.name + "-wal")
    wal.write_text("")
    assert window.delete_map("Doomed") is False  # cancelled
    assert "Doomed" in settings.maps and list_backups() == []
    modal.handler = lambda dialog: 1
    assert window.delete_map("Doomed") is True
    assert "Doomed" not in settings.maps and not path.exists() and not wal.exists()
    [kept] = list_backups()
    assert kept.reason == "delete" and "Doomed" in kept.maps
    with pytest.raises(TypeError):
        window.delete_map(None)


def test_delete_map_blocked_by_failed_backup(window, modal) -> None:
    """A map whose backup fails is kept, and the toast says why."""
    settings = window.state.settings
    path = settings.add_map("Broken")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a database")
    modal.handler = lambda dialog: 1
    assert window.delete_map("Broken") is False
    assert "Broken" in settings.maps and path.exists()
    assert "not a map file" in window.state.last_backup_error
    assert list_backups() == []


def test_delete_map_refused(window, modal) -> None:
    """The open map can't be deleted: the settings refuse and a toast says so."""
    modal.handler = lambda dialog: 1
    active = window.state.settings.active_map
    assert window.delete_map(active) is False
    toast = window.toasts.toasts[-1]
    assert toast_kind(toast) == "danger" and "Switch to another map" in toast_text(toast)
    assert active in window.state.settings.maps


def test_map_menu_actions_through_menu(window, modal) -> None:
    """The menu's rows switch and delete maps."""
    settings = window.state.settings
    settings.add_map("Other")
    menu = window._fill_map_menu()
    delete = find_action(menu, "Delete map").menu()
    assert delete.isEnabled()
    modal.handler = lambda dialog: 1
    find_action(delete, "Other").trigger()
    assert "Other" not in settings.maps
    settings.add_map("Third")
    menu = window._fill_map_menu()
    find_action(menu, "Third").trigger()
    assert settings.active_map == "Third"


# ---------------------------------------------------------------- overlay, tray, status
def test_set_overlay_mode_requires_pin(window) -> None:
    """Pinned modes need a pinned character; the others apply at once."""
    settings = window.state.settings
    assert window.set_overlay_mode("pinned_eve") is False
    assert window.set_overlay_mode("pinned_always") is False
    assert window.set_overlay_mode("off") is True and settings.overlay_mode == "off"
    assert window.set_overlay_mode("follow") is True and window.last_overlay_mode == "follow"
    with pytest.raises(ValueError):
        window.set_overlay_mode("sideways")
    with pytest.raises(ValueError):
        window.set_overlay_mode(None)


def test_pin_character_sets_mode_and_name(window) -> None:
    """Pinning stores the character, enables the pinned modes and names it in the toast."""
    state = window.state
    state.on_system_change(SystemChange(77, "Pilot Seven", HOME.name, __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc), first=True))
    assert window.pin_character(77) == "Pilot Seven"
    assert state.settings.overlay_pinned_character == 77
    assert state.settings.overlay_mode == "pinned_eve"
    assert window.pin_character(5) == "5"
    assert window.set_overlay_mode("pinned_always") is True
    with pytest.raises(TypeError):
        window.pin_character("77")


def test_mode_buttons_drive_overlay_mode(window) -> None:
    """Clicking a segment of the toolbar switches the overlay mode."""
    for button in window.mode_buttons.buttons():
        if button.property("mode") == "off":
            button.click()
    assert window.state.settings.overlay_mode == "off"
    assert window.mode_actions["off"].isChecked() and window.unlock_action.isEnabled() is False
    for button in window.mode_buttons.buttons():
        if button.property("mode") == "follow":
            button.click()
    assert window.mode_actions["follow"].isChecked() and not window.mode_actions["off"].isChecked()


def test_tray_mode_group_lists_every_toolbar_mode(window) -> None:
    """The tray's mode group has one action per toolbar mode, in the same order, and no separate toggle."""
    assert list(window.mode_actions) == [key for key, _ in map_window_module.MODES]
    assert window.mode_actions["off"].text() == "Off"
    tray_texts = [action.text() for action in window.tray.contextMenu().actions()]
    assert "Show overlay" not in tray_texts


def test_tray_off_mode_and_back(window) -> None:
    """The tray's Off action turns the overlay off; Follow afterwards brings it back."""
    window.mode_actions["follow"].trigger()
    window.mode_actions["off"].trigger()
    assert window.state.settings.overlay_mode == "off"
    assert window.mode_actions["off"].isChecked() and not window.mode_actions["follow"].isChecked()
    assert window.unlock_action.isEnabled() is False
    window.mode_actions["follow"].trigger()
    assert window.state.settings.overlay_mode == "follow"
    assert window.mode_actions["follow"].isChecked() and not window.mode_actions["off"].isChecked()
    assert window.unlock_action.isEnabled()


def test_tray_menu_toggle_and_modes(window) -> None:
    """The tray's mode group switches the overlay and respects pinning."""
    window.mode_actions["follow"].trigger()
    assert window.state.settings.overlay_mode == "follow"
    window.mode_actions["off"].trigger()
    assert window.state.settings.overlay_mode == "off"
    assert window.mode_actions["pinned_eve"].isEnabled() is False
    assert "Pin a character" in window.mode_actions["pinned_eve"].toolTip()
    window.state.settings.overlay_pinned_character = 1
    window.state.settingsChanged.emit()
    assert window.mode_actions["pinned_eve"].isEnabled()
    window.mode_actions["pinned_always"].trigger()
    assert window.state.settings.overlay_mode == "pinned_always"
    window.unlock_action.setChecked(True)
    assert window.overlay.unlocked
    window.unlock_action.setChecked(False)


def test_tray_activation_raises_window(window) -> None:
    """A plain click on the tray icon raises the window; other reasons don't."""
    window.hide()
    assert window._tray_activated(QSystemTrayIcon.ActivationReason.Context) is False
    assert window.isHidden()
    assert window._tray_activated(QSystemTrayIcon.ActivationReason.Trigger) is True
    assert window.isVisible()


def test_tray_menu_shows_version(window) -> None:
    """The tray menu shows the version from the package metadata as a disabled row."""
    from homebound import app_version
    assert window.version_action.text() == f"Homebound {app_version()}" and not window.version_action.isEnabled()


def test_status_bar_updates(window) -> None:
    """Status lines go to their label with a glyph; unknown sources are ignored."""
    assert window._status("logs", "ok", "Watching 2 logs") is True
    assert "Watching" in window.status_labels["logs"].text() and "✓" in window.status_labels["logs"].text()
    window.state.status.emit("zkill", "warn", "slow")
    assert "▲" in window.status_labels["zkill"].text()
    window.focus.status.emit("err", "no script")
    assert "✕" in window.status_labels["kwin"].text()
    assert window._status("logs", "other", "plain") is True
    assert window._status("nope", "ok", "x") is False


def test_backup_status_shows_label_and_warns(window) -> None:
    """The backup label is hidden until a backup reports; a warning also shows a toast."""
    label = window.status_labels["backup"]
    assert label.isHidden() and window.backup_separator.isHidden()
    window._status("backup", "ok", "Backed up 12:00")
    assert not label.isHidden() and not window.backup_separator.isHidden()
    assert not window.toasts.toasts
    window.state.status.emit("backup", "warn", "Backup failed: disk full")
    assert "disk full" in label.text() and len(window.toasts.toasts) == 1


# ---------------------------------------------------------------- find / fit
def test_find_selects_and_centres(window) -> None:
    """Typing a mapped system's name and pressing Enter selects it and clears the field."""
    child = add_child(window)
    window.find_field.setText(child.name.lower())
    assert window._do_find() is True
    assert window.state.selected == child.id and window.find_field.text() == ""
    window.find_field.setText("nowhere")
    assert window._do_find() is False
    window.find_field.clear()
    assert window._do_find() is False
    completer = window.find_field.completer()
    completer.activated.emit(child.name)
    assert window.state.selected == child.id


def test_fit_chain_returns_rectangle(window) -> None:
    """Fit chain frames the selected system's chain, or home's without a selection."""
    add_child(window)
    window.state.select(0)
    rectangle = window.fit_chain()
    assert not rectangle.isNull()
    window.state.select(HOME.id)
    assert window.fit_chain().width() == rectangle.width()


# ---------------------------------------------------------------- paste
def test_paste_shortcut_routes_by_focus(window, monkeypatch) -> None:
    """Paste goes to a focused text field, otherwise to the signature table."""
    pasted = []
    monkeypatch.setattr(window.panel, "paste", lambda: pasted.append("panel"))
    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: None))
    window._paste_shortcut()
    assert pasted == ["panel"]
    line, text = QLineEdit(), QPlainTextEdit()
    calls = []
    monkeypatch.setattr(line, "paste", lambda: calls.append("line"))
    monkeypatch.setattr(text, "paste", lambda: calls.append("text"))
    for widget in (line, text):
        monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda widget=widget: widget))
        window._paste_shortcut()
    assert calls == ["line", "text"] and pasted == ["panel"]


# ---------------------------------------------------------------- naming queue
def naming_request(window, system_class="C5") -> NamingRequest:
    """Return a NamingRequest for a fresh child of home."""
    simulation_child = pick_system(system_class, exclude={HOME.id})
    events = window.state.tracker.add_wormhole(HOME.id, simulation_child.id)
    window.state.dispatch(events)
    return next((event for event in events if isinstance(event, NamingRequest)), None) or NamingRequest(
        HOME.id, simulation_child.id, window.state.store.connection(HOME.id, simulation_child.id).id,
        __import__("homebound.mapping.naming", fromlist=["guess_static"]).guess_static(
            window.state.store, window.state.universe, HOME.id, simulation_child.id, strict=False))


def test_queue_naming_validates(window) -> None:
    """Only naming requests can be queued."""
    with pytest.raises(TypeError):
        window.queue_naming("request")


def test_naming_dialog_accepted_applies_answer(window, modal, qt_application) -> None:
    """Accepting the dialog applies the naming and records the auto-naming checkbox."""
    request = naming_request(window)
    window.naming_queue.clear()
    modal.handler = lambda dialog: (dialog.auto_naming.setChecked(True), 1)[1]
    assert window.queue_naming(request) == 1
    qt_application.processEvents()
    assert len(modal.dialogs) == 1 and isinstance(modal.dialogs[0], NamingDialog)
    assert window.naming_open is False
    assert window.state.settings.auto_name_when_certain is True
    assert window.area.scrim.isHidden()
    # toggled off again on the next question
    modal.handler = lambda dialog: (dialog.auto_naming.setChecked(False), 0)[1]
    window.queue_naming(request)
    qt_application.processEvents()
    assert window.state.settings.auto_name_when_certain is False


def test_naming_scrim_comes_down_when_the_system_expires(window, modal, qt_application) -> None:
    """The system expires while the question is open: the scrim comes down and the queue goes on."""
    request = naming_request(window)
    window.naming_queue.clear()

    def expire(dialog) -> int:
        """Take the child off the map while the dialog is open, then accept it."""
        window.state.store.remove_system(request.child_id)
        return 1

    modal.handler = expire
    window.queue_naming(request)
    qt_application.processEvents()
    assert len(modal.dialogs) == 1
    assert window.area.scrim.isHidden() and window.naming_open is False


def test_naming_scrim_comes_down_when_apply_raises(window, modal, qt_application, monkeypatch) -> None:
    """If applying the answer fails, the scrim still comes down and the next question is asked."""
    request = naming_request(window)
    second_child = pick_system("C3", exclude={HOME.id})
    window.state.dispatch(window.state.tracker.add_wormhole(HOME.id, second_child.id))
    second = NamingRequest(HOME.id, second_child.id, window.state.store.connection(HOME.id, second_child.id).id,
                           request.guess)
    real_apply = window.state.tracker.apply_naming
    calls = []

    def fail_once(*arguments, **keywords):
        """Raise on the first answer only."""
        calls.append(arguments)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return real_apply(*arguments, **keywords)

    monkeypatch.setattr(window.state.tracker, "apply_naming", fail_once)
    modal.handler = lambda dialog: 1
    window.naming_open = False
    window.naming_queue[:] = [request, second]
    modal.dialogs.clear()
    with pytest.raises(RuntimeError):
        window._next_naming()
    assert window.area.scrim.isHidden() and window.naming_open is False
    qt_application.processEvents()
    assert len(modal.dialogs) == 2 and window.naming_queue == []   # the failed one, then the next one
    assert len(calls) == 2


def test_naming_queue_skips_vanished_systems(window, modal, qt_application) -> None:
    """A request whose systems left the map is dropped without a dialog."""
    request = NamingRequest(HOME.id, 99999999, 1, None)
    window.queue_naming(request)
    qt_application.processEvents()
    assert modal.dialogs == [] and window.naming_queue == []
    assert window._next_naming() is False


def test_naming_queue_runs_in_order(window, modal, qt_application) -> None:
    """Several queued requests produce one dialog each, one at a time."""
    first = naming_request(window)
    second_child = pick_system("C3", exclude={HOME.id})
    window.state.dispatch(window.state.tracker.add_wormhole(HOME.id, second_child.id))
    second = NamingRequest(HOME.id, second_child.id, window.state.store.connection(HOME.id, second_child.id).id,
                           first.guess)
    window.naming_queue.clear()
    window.naming_open = True
    window.queue_naming(first)
    assert window.queue_naming(second) == 2
    window.naming_open = False
    qt_application.processEvents()
    qt_application.processEvents()
    qt_application.processEvents()
    assert len(modal.dialogs) == 2


def test_rename_hole_requeues_naming(window, modal, qt_application) -> None:
    """'Change static / hole' re-asks the naming question for the parent connection."""
    child = add_child(window)
    window.naming_queue.clear()
    assert window._rename_hole(child.id) is True
    qt_application.processEvents()
    assert len(modal.dialogs) == 1 and isinstance(modal.dialogs[0], NamingDialog)
    assert window._rename_hole(HOME.id) is False


def test_auto_named_toast_and_undo(window) -> None:
    """An automatic naming shows a toast whose Undo button restores the name."""
    child = add_child(window)
    event = AutoNamed(child.id, "H296", {"custom_name": None})
    calls = []
    window.state.tracker.undo_auto_naming = lambda auto_named: calls.append(auto_named)
    window.state.autoNamed.emit(event)
    toast = window.toasts.toasts[-1]
    assert toast_kind(toast) == "success" and toast_text(toast).startswith("Named ")
    from PySide6.QtWidgets import QPushButton
    next(button for button in toast.findChildren(QPushButton) if button.text() == "Undo").click()
    assert calls == [event]


def test_unknown_jump_toast_and_add(window) -> None:
    """An unknown jump asks; the Add button connects the two systems."""
    source = pick_system("C5", exclude={HOME.id})
    destination = pick_system("C3", exclude={HOME.id, source.id})
    window.state.store.add_system(source.id)
    window.state.store.add_system(destination.id)
    event = UnknownJump(1, "Pilot", source.id, destination.id)
    window.state.unknownJump.emit(event)
    toast = window.toasts.toasts[-1]
    assert toast_kind(toast) == "warning" and toast_text(toast).startswith("Unknown jump")
    from PySide6.QtWidgets import QPushButton
    next(button for button in toast.findChildren(QPushButton) if button.text() == "Add").click()
    assert window.state.store.connection(source.id, destination.id)


def test_unknown_jump_default_add(window) -> None:
    """With the 'add' default a toast offers the add action when it times out."""
    window.state.settings.unknown_jump_default = "add"
    source = pick_system("C5", exclude={HOME.id})
    destination = pick_system("C3", exclude={HOME.id, source.id})
    window.state.unknownJump.emit(UnknownJump(1, "Pilot", source.id, destination.id))
    toast = window.toasts.toasts[-1]
    assert toast_kind(toast) == "warning" and toast_text(toast).startswith("Unknown jump")
    assert callable(toast.default)
    toast.default()                      # the timeout answer is "add"
    assert window.state.store.connection(source.id, destination.id)


def test_wormhole_jump_focuses_the_new_system(window) -> None:
    """A wormhole jump selects and centres the new system unless Settings turns it off."""
    state = window.state
    assert state.settings.focus_on_wormhole_jump is True
    state.on_system_change(SystemChange(1, "Pilot", HOME.name, NOW, first=True))
    target = pick_system("C3", exclude={HOME.id})
    state.on_system_change(SystemChange(1, "Pilot", target.name, NOW + timedelta(minutes=1)))
    node = window.scene.nodes[target.id]
    assert state.selected == target.id
    centre = window.view.mapToScene(window.view.viewport().rect().center())
    assert (centre - node.sceneBoundingRect().center()).manhattanLength() <= 4
    state.select(0)
    state.settings.focus_on_wormhole_jump = False
    assert window.focus_jumped_system(target.id) is False and state.selected == 0


# ---------------------------------------------------------------- node menu and actions
def test_node_menu_actions(window, modal) -> None:
    """The node menu lists the actions; each of them works."""
    child = add_child(window)
    window.state.store.update_system(child.id, confirmed=0)
    window.scene.rebuild()
    modal.menu_handler = lambda menu: None
    window._node_menu(child.id, QPoint(0, 0))
    menu = modal.menus[-1]
    texts = [action.text() for action in menu.actions()]
    assert "Mark as confirmed" in texts  # the success-coloured ✓ is the icon, not part of the label
    assert not find_action(menu, "Mark as confirmed").icon().isNull()
    assert find_action(menu, "Set as home").isEnabled()
    assert find_action(menu, "Remove from map").isEnabled()
    window._node_menu(HOME.id, QPoint(0, 0))
    home_menu = modal.menus[-1]
    assert not find_action(home_menu, "Remove from map").isEnabled()
    assert not find_action(home_menu, "Set as home").isEnabled()
    assert not find_action(home_menu, "Change static").isEnabled()
    assert find_action(home_menu, "Remove from map").toolTip() == "Home can’t be removed. Set another home first."
    window._node_menu(123456789, QPoint(0, 0))  # not on the map: no menu
    assert len(modal.menus) == 2


def test_node_menu_confirm_and_zkill(window, modal, monkeypatch) -> None:
    """Confirm marks the system confirmed; zKill now forces a request."""
    child = add_child(window)
    window.state.store.update_system(child.id, confirmed=0)
    window.scene.rebuild()
    modal.menu_handler = pick("Mark as confirmed")
    window._node_menu(child.id, QPoint(0, 0))
    assert window.state.store.system(child.id).confirmed
    requests = []
    monkeypatch.setattr(window.state.zkill, "request", lambda ids, force=False: requests.append((ids, force)))
    modal.menu_handler = pick("Request zKill")
    window._node_menu(child.id, QPoint(0, 0))
    assert requests == [([child.id], True)]
    assert window._confirm(system_id=child.id) is False


def test_node_menu_mark_scanned(window, modal) -> None:
    """Mark as scanned now sets the last paste to now, so a never-scanned node loses its NO SCAN tab."""
    child = add_child(window)
    assert window.scene.nodes[child.id].scan_status.state == "never"
    modal.menu_handler = pick("Mark as scanned now")
    window._node_menu(child.id, QPoint(0, 0))
    assert window.state.store.system(child.id).scanned_at
    assert window.scene.nodes[child.id].scan_status.state == "fresh"


def test_node_menu_draw_connection_and_links(window, modal) -> None:
    """'Draw connection from here' starts a link; link_systems adds or reports a duplicate."""
    child = add_child(window)
    other = pick_system("C3", exclude={HOME.id, child.id})
    window.state.store.add_system(other.id)
    window.scene.rebuild()
    modal.menu_handler = pick("Draw connection")
    window._node_menu(HOME.id, QPoint(0, 0))
    window.scene.cancel_link()
    assert window.link_systems(HOME.id, other.id) is True
    assert window.state.store.connection(HOME.id, other.id)
    assert window.link_systems(HOME.id, other.id) is False
    with pytest.raises(TypeError):
        window.link_systems("a", 1)
    window.scene.linkRequested.emit(child.id, other.id)
    assert window.state.store.connection(child.id, other.id)


def test_rename_system_via_menu_and_key(window, modal) -> None:
    """Rename sets or clears a custom name; the F2 handler renames the selection."""
    child = add_child(window)
    modal.handler = lambda dialog: (dialog.field.setText("Mine"), 1)[1]
    modal.menu_handler = pick("Rename")
    window._node_menu(child.id, QPoint(0, 0))
    assert window.state.store.system(child.id).custom_name == "Mine"
    modal.handler = lambda dialog: (dialog.field.setText(""), 1)[1]
    window.state.select(child.id)
    window._key_rename()
    assert window.state.store.system(child.id).custom_name is None
    modal.handler = lambda dialog: 0
    assert window._rename(child.id) is False
    window.state.select(0)
    count = len(modal.dialogs)
    window._key_rename()
    assert len(modal.dialogs) == count


def test_set_home_reroots_chain(window) -> None:
    """Making a child home reparents the old home below it."""
    child = add_child(window)
    window._set_home(child.id)
    store = window.state.store
    assert store.system(child.id).parent_id is None
    assert store.system(HOME.id).parent_id == child.id
    assert window.state.settings.home_system == child.name


def test_node_menu_set_home(window, modal) -> None:
    """The menu entry re-roots as well."""
    child = add_child(window)
    modal.menu_handler = pick("Set as home")
    window._node_menu(child.id, QPoint(0, 0))
    assert window.state.home_id == child.id


# ---------------------------------------------------------------- remove / undo
def test_remove_system_and_undo(window, modal) -> None:
    """Remove deletes the system and its links; Ctrl+Z (trigger_undo) brings them back."""
    child = add_child(window)
    window.state.select(child.id)
    modal.menu_handler = pick("Remove from map")
    window._node_menu(child.id, QPoint(0, 0))
    store = window.state.store
    assert not store.has_system(child.id) and window.state.selected == 0
    assert "Removed" in window.toasts.toasts[-1].findChildren(type(window.area.zoom_label))[0].text()
    assert window.toasts.trigger_undo() is True
    assert store.has_system(child.id) and store.connection(HOME.id, child.id)


def test_remove_without_connections_wording(window) -> None:
    """A system with no connections says so in the toast."""
    lonely = pick_system("C3", exclude={HOME.id})
    window.state.store.add_system(lonely.id)
    window._remove(lonely.id)
    labels = " ".join(label.text() for label in window.toasts.toasts[-1].findChildren(type(window.area.zoom_label)))
    assert "no connections" in labels


def test_key_remove_ignores_home_and_missing(window) -> None:
    """Del never removes home or an unknown selection."""
    window.state.select(HOME.id)
    window._key_remove()
    assert window.state.store.has_system(HOME.id)
    window.state.select(0)
    window._key_remove()
    child = add_child(window)
    window.state.select(child.id)
    window._key_remove()
    assert not window.state.store.has_system(child.id)


def test_undo_refreshes_the_selected_system(window) -> None:
    """After Undo the panel of the selected system that survived is refreshed."""
    child = add_child(window)
    window._remove(child.id)
    window.state.select(HOME.id)
    updated = []
    window.state.systemUpdated.connect(updated.append)
    assert window.toasts.trigger_undo() is True
    assert updated == [HOME.id] and window.state.store.has_system(child.id)


def test_undo_on_another_map_is_refused(window, modal) -> None:
    """Undoing after a map switch shows an error instead of restoring into the wrong store."""
    child = add_child(window)
    window._remove(child.id)
    window.state.settings.add_map("Other")
    window.switch_map("Other")
    assert window.toasts.trigger_undo() is True
    toast = window.toasts.toasts[-1]
    assert toast_kind(toast) == "danger" and toast_text(toast) == "Can't undo: that was on another map"
    assert window.state.store.has_system(child.id) is False


def test_offer_undo_validates(window) -> None:
    """Undo toasts need text."""
    with pytest.raises(TypeError):
        window.offer_undo(None, "", {})
    with pytest.raises(TypeError):
        window.offer_undo("t", 5, {})


# ---------------------------------------------------------------- edges
def edge_id(window, child) -> int:
    """Return the ID of the connection between home and a child."""
    return window.state.store.connection(HOME.id, child.id).id


def test_edge_menu_sets_mass_and_lifetime(window, modal) -> None:
    """The edge menu sets mass and lifetime and marks the connection as confirmed."""
    child = add_child(window)
    connection_id = edge_id(window, child)
    modal.menu_handler = pick("Critical")
    window._edge_menu(connection_id, QPoint(0, 0))
    assert window.state.store.connection_by_id(connection_id).mass == "critical"
    modal.menu_handler = pick("◷ < 1h")
    window._edge_menu(connection_id, QPoint(0, 0))
    assert window.state.store.connection_by_id(connection_id).lifetime == 2
    window.state.store.update_connection(connection_id, confirmed=0)
    modal.menu_handler = pick("Mark as confirmed")
    window._edge_menu(connection_id, QPoint(0, 0))
    assert window.state.store.connection_by_id(connection_id).confirmed
    window._edge_menu(987654, QPoint(0, 0))  # unknown: no menu


def test_edit_connection_dialog_and_collapse(window, modal) -> None:
    """Accepting the dialog stores its values; its collapse signal collapses the connection."""
    child = add_child(window)
    connection_id = edge_id(window, child)
    modal.handler = lambda dialog: 1
    assert window.edit_connection(connection_id) is True
    modal.handler = lambda dialog: 0
    assert window.edit_connection(connection_id) is False
    assert window.edit_connection(424242) is False
    modal.handler = lambda dialog: (dialog.collapse.emit(), 0)[1]
    window.edit_connection(connection_id)
    assert not window.state.store.has_system(child.id)
    with pytest.raises(TypeError):
        window.edit_connection("one")


def test_collapse_via_menu_and_undo(window, modal) -> None:
    """Collapse removes the connection and its leaf system; Undo restores both."""
    child = add_child(window)
    connection_id = edge_id(window, child)
    modal.menu_handler = pick("Collapse")
    window._edge_menu(connection_id, QPoint(0, 0))
    store = window.state.store
    assert not store.has_system(child.id) and store.has_system(HOME.id)
    window.toasts.trigger_undo()
    assert store.has_system(child.id) and store.connection(HOME.id, child.id)
    assert window._collapse(31337) is False


def test_collapse_keeps_connected_and_deletes_signatures(window) -> None:
    """A system with other links stays; the collapsed hole's signatures are deleted."""
    first = add_child(window)
    second_child = pick_system("C3", exclude={HOME.id, first.id})
    window.state.dispatch(window.state.tracker.add_wormhole(first.id, second_child.id))
    signature_parser.merge_signatures(window.state.store, first.id, signature_parser.parse_scanner_text(SCAN))
    connection_id = edge_id(window, first)
    window.state.store.set_connection_signature(connection_id, first.id, "ABC-123")
    window._collapse(connection_id)
    store = window.state.store
    assert store.signature(first.id, "ABC-123") is None
    assert store.signature(first.id, "DEF-456") is not None      # only the hole's own signature goes
    assert store.has_system(first.id) and store.has_system(second_child.id)
    assert not store.connection(HOME.id, first.id) and store.connection(first.id, second_child.id)


def test_edge_activated_signal_opens_dialog(window, modal) -> None:
    """Double-clicking an edge (edgeActivated) opens the connection dialog."""
    child = add_child(window)
    window.scene.edgeActivated.emit(edge_id(window, child))
    assert len(modal.dialogs) == 1 and isinstance(modal.dialogs[0], ConnectionDialog)


def test_key_edit_edge(window, modal) -> None:
    """Enter edits the selected edge only."""
    child = add_child(window)
    window.scene.rebuild()
    modal.dialogs.clear()
    window._key_edit_edge()
    assert modal.dialogs == []
    edge = next(iter(window.scene.edges.values())) if isinstance(window.scene.edges, dict) else window.scene.edges[0]
    edge.setSelected(True)
    window._key_edit_edge()
    assert len(modal.dialogs) == 1 and isinstance(modal.dialogs[0], ConnectionDialog)
    assert window.state.store.has_system(child.id)


# ---------------------------------------------------------------- background menu and add system
def test_background_menu_add_and_fit(window, modal) -> None:
    """The map menu adds a system at the clicked position and links it to the selection."""
    window.state.select(HOME.id)
    target = pick_system("C3", exclude={HOME.id})
    modal.handler = lambda dialog: (dialog.field.setText(target.name), 1)[1]
    modal.menu_handler = pick("Add system")
    window._background_menu(QPointF(120, 80), QPoint(0, 0))
    store = window.state.store
    assert store.has_system(target.id) and store.connection(HOME.id, target.id)
    assert window.state.selected == target.id
    modal.menu_handler = pick("Fit chain")
    window._background_menu(QPointF(0, 0), QPoint(0, 0))


def test_add_system_standalone_cancelled_and_unknown(window, modal) -> None:
    """Without a selection the system is added alone; cancel or unknown names add nothing."""
    window.state.select(0)
    target = pick_system("C4", exclude={HOME.id})
    modal.handler = lambda dialog: 0
    assert window._add_system(QPointF(1, 2)) is False
    modal.handler = lambda dialog: (dialog.field.setText("not a system"), 1)[1]
    assert window._add_system(QPointF(1, 2)) is False
    modal.handler = lambda dialog: (dialog.field.setText(target.name), 1)[1]
    assert window._add_system(QPointF(1, 2)) is True
    assert window.state.store.has_system(target.id) and not window.state.store.connections_of_system(target.id)


def test_key_add_uses_view_centre(window, modal) -> None:
    """Ins adds a system in the middle of the view."""
    target = pick_system("C4", exclude={HOME.id})
    modal.handler = lambda dialog: (dialog.field.setText(target.name), 1)[1]
    window._key_add()
    assert window.state.store.has_system(target.id)


def test_add_existing_system_only_selects(window, modal) -> None:
    """Adding a system that is on the map is refused by the dialog's validator."""
    seen = []
    modal.handler = lambda dialog: (dialog.field.setText(HOME.name), seen.append(dialog.validate_now()), 0)[2]
    assert window._add_system(QPointF(0, 0)) is False
    assert seen == [False]


# ---------------------------------------------------------------- shortcuts and close
def test_scene_context_signals_open_menus(window, modal) -> None:
    """The scene's context-menu signals reach the window's menus."""
    child = add_child(window)
    window.scene.nodeContextMenu.emit(child.id, QPoint(0, 0))
    window.scene.edgeContextMenu.emit(edge_id(window, child), QPoint(0, 0))
    window.scene.backgroundContextMenu.emit(QPointF(0, 0), QPoint(0, 0))
    assert len(modal.menus) == 3


def test_close_hides_to_tray(window, monkeypatch) -> None:
    """Closing hides the window while the tray is visible, otherwise really closes."""
    monkeypatch.setattr(QSystemTrayIcon, "isVisible", lambda self: True)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted() and window.isHidden()
    monkeypatch.setattr(QSystemTrayIcon, "isVisible", lambda self: False)
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()


def test_raise_window_shows(window) -> None:
    """raise_window brings back a hidden window."""
    window.hide()
    window.raise_window()
    assert window.isVisible()

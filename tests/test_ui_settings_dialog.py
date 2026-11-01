"""Tests for the settings dialog: every page and saving."""
import json
from collections.abc import Callable

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog

import homebound.settings as settings_module
import homebound.ui.settings_dialog as dialog_module
from conftest import HOME, UNIVERSE, pick_system
from homebound.ui.settings_dialog import PAGES, SettingsDialog
from homebound.ui.state import AppState
from ui_helpers import make_state

LOG_NAME = "Local_20261028_165148_{}.txt"


@pytest.fixture
def state(monkeypatch, tmp_path, qt_application) -> AppState:
    """An AppState on a temporary folder."""
    return make_state(monkeypatch, tmp_path)


@pytest.fixture
def kwin(monkeypatch) -> dict[str, object]:
    """Scripted KWin functions; ``kwin["status"]`` and ``kwin["result"]`` can be changed, ``kwin["calls"]`` records."""
    control = {"status": "missing", "result": (True, ""), "calls": []}

    def record(name: str) -> Callable[[], tuple[bool, str]]:
        """Return a fake KWin function that records its name and returns the scripted result."""
        def call() -> tuple[bool, str]:
            """Record the call and return the scripted result."""
            control["calls"].append(name)
            return control["result"]
        return call

    monkeypatch.setattr(dialog_module.focus, "kwin_script_status", lambda: control["status"])
    for name in ("install_kwin_script", "enable_kwin_script", "remove_kwin_script"):
        monkeypatch.setattr(dialog_module.focus, name, record(name))
    return control


@pytest.fixture
def dialog(state, kwin) -> SettingsDialog:
    """A settings dialog on the first page."""
    return SettingsDialog(None, state)


def test_constructor_validation(state, kwin) -> None:
    """A non-AppState state or a non-integer page raises TypeError; a page outside PAGES raises ValueError."""
    with pytest.raises(TypeError):
        SettingsDialog(None, object())
    with pytest.raises(TypeError):
        SettingsDialog(None, state, "1")
    with pytest.raises(ValueError):
        SettingsDialog(None, state, len(PAGES))
    with pytest.raises(ValueError):
        SettingsDialog(None, state, -1)


@pytest.mark.parametrize("page", range(len(PAGES)))
def test_opens_on_page(state, kwin, page) -> None:
    """The dialog opens on the page index it is given."""
    assert SettingsDialog(None, state, page).stack.currentIndex() == page


def test_nav_switches_page(dialog) -> None:
    """Selecting a row in the navigation list shows that page."""
    dialog.nav.setCurrentRow(3)
    assert dialog.stack.currentIndex() == 3


def test_cancel_leaves_settings(dialog, state) -> None:
    """Cancel after editing a field leaves the saved home system unchanged."""
    dialog.home.setText("")
    dialog.cancel_button.click()
    assert state.settings.home_system == HOME.name


# ---------------------------------------------------------------- log folder
def test_log_folder_missing(dialog, tmp_path) -> None:
    """A folder that does not exist is marked invalid with a "Folder not found" hint."""
    dialog.log_directory.setText(str(tmp_path / "nope"))
    assert dialog.validate_log_directory() == "invalid"
    assert dialog.log_help.text().startswith("✕ Folder not found")
    assert dialog.log_directory.property("state") == "invalid"


def test_log_folder_empty(dialog, tmp_path) -> None:
    """A folder without Local_*.txt logs counts as empty and shows a warning."""
    (tmp_path / "logs" / "Local_garbage.txt").write_text("x")
    dialog.log_directory.setText(str(tmp_path / "logs"))
    assert dialog.validate_log_directory() == "empty"
    assert "no Local_*.txt" in dialog.log_help.text()
    assert dialog.log_help.property("role") == "help-warn"


def test_log_folder_with_logs(dialog, tmp_path) -> None:
    """A folder of Local logs is valid; the hint counts the logs and the distinct characters."""
    folder = tmp_path / "logs"
    for character in (111, 111, 222):
        (folder / f"Local_20261028_16{character % 100:02d}00_{character}.txt").write_text("x")
    (folder / "Local_20261029_000000_111.txt").write_text("x")
    dialog.log_directory.setText(str(folder))
    assert dialog.validate_log_directory() == "valid"
    assert dialog.log_help.text() == "✓ 3 Local logs from 2 characters"
    assert dialog.log_help.property("role") == "help-ok"
    assert dialog.log_directory.property("state") == "valid"


def test_log_timer_validates_after_typing(dialog, tmp_path) -> None:
    """Typing a path starts the debounce timer; when it fires, the folder is checked and the error shows."""
    dialog.log_directory.setText(str(tmp_path / "nope"))
    assert dialog.log_timer.isActive()
    dialog.log_timer.timeout.emit()
    assert dialog.log_help.property("role") == "help-err"


def test_log_folder_unset(dialog) -> None:
    """An empty field is "unset" (not the working directory), shown as a neutral hint."""
    dialog.log_directory.setText("   ")
    assert dialog.validate_log_directory() == "unset"
    assert dialog.log_help.property("role") == "help" and "No folder set" in dialog.log_help.text()
    assert dialog.log_directory.property("state") == ""


def test_save_blocked_by_missing_log_folder(dialog, state, tmp_path) -> None:
    """A folder that doesn't exist keeps the dialog open on the log page with the reason, and saves nothing."""
    before = state.settings.log_directory
    dialog.nav.setCurrentRow(PAGES.index("Overlay"))
    dialog.log_directory.setText(str(tmp_path / "nope"))
    dialog.home.setText("")
    assert dialog.save() is False
    assert dialog.result() != dialog.DialogCode.Accepted
    assert dialog.stack.currentIndex() == PAGES.index("Log folder")
    assert dialog.log_help.property("role") == "help-err" and "can't be saved" in dialog.log_help.text()
    assert state.settings.log_directory == before and state.settings.home_system == HOME.name
    assert not (tmp_path / "settings.json").exists() or "nope" not in (tmp_path / "settings.json").read_text()


def test_save_allows_empty_log_folder(dialog, state) -> None:
    """An empty log folder field saves (first start without EVE installed yet)."""
    dialog.log_directory.setText("")
    assert dialog.save() is True
    assert dialog.result() == dialog.DialogCode.Accepted and state.settings.log_directory == ""


def test_browse_sets_folder(dialog, monkeypatch, tmp_path) -> None:
    """Picking a folder in the file dialog puts it into the log folder field and returns it."""
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *arguments: str(tmp_path))
    assert dialog.browse_log_directory() == str(tmp_path)
    assert dialog.log_directory.text() == str(tmp_path)


def test_browse_cancelled_keeps_folder(dialog, monkeypatch) -> None:
    """Cancelling the file dialog returns "" and keeps the folder that was in the field."""
    before = dialog.log_directory.text()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *arguments: "")
    assert dialog.browse_log_directory() == ""
    assert dialog.log_directory.text() == before


def test_browse_starts_in_home_when_field_empty(dialog, monkeypatch) -> None:
    """With an empty field the file dialog still gets a start folder (the user's home)."""
    seen = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *arguments: seen.append(arguments) or "")
    dialog.log_directory.setText("")
    dialog.browse_log_directory()
    assert seen[0][2]


def test_autodetect_found(dialog, monkeypatch, tmp_path) -> None:
    """Autodetect fills in the first Chatlogs folder it finds and returns True."""
    monkeypatch.setattr(dialog_module, "detect_log_directories", lambda: [tmp_path])
    assert dialog.autodetect_log_directory() is True
    assert dialog.log_directory.text() == str(tmp_path)


def test_autodetect_nothing_found(dialog, monkeypatch) -> None:
    """Autodetect without a Chatlogs folder returns False and shows an error hint."""
    monkeypatch.setattr(dialog_module, "detect_log_directories", lambda: [])
    assert dialog.autodetect_log_directory() is False
    assert dialog.log_help.text().startswith("✕ No Chatlogs folder")
    assert dialog.log_help.property("role") == "help-err"


def test_buttons_trigger_browse_and_autodetect(dialog, monkeypatch, tmp_path) -> None:
    """The Browse and Autodetect buttons run their actions."""
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *arguments: str(tmp_path))
    dialog.browse_button.click()
    assert dialog.log_directory.text() == str(tmp_path)
    monkeypatch.setattr(dialog_module, "detect_log_directories", lambda: [])
    dialog.autodetect_button.click()
    assert "No Chatlogs" in dialog.log_help.text()


# ---------------------------------------------------------------- home system
def test_home_known(dialog) -> None:
    """A known home system is accepted; the hint shows its name and class."""
    dialog.home.setText(HOME.name)
    assert dialog.validate_home() is True
    text = dialog.home_help.text()
    assert text.startswith(f"✓ {HOME.name} · {HOME.system_class}")
    assert dialog.home_help.property("role") == "help-ok"


def test_home_unknown(dialog) -> None:
    """A name the universe doesn't know is rejected with "Unknown system"."""
    dialog.home.setText("Nowhereville")
    assert dialog.validate_home() is False
    assert dialog.home_help.text() == "✕ Unknown system"
    assert dialog.home_help.property("role") == "help-err"


def test_home_empty(dialog) -> None:
    """An empty home field is not valid and shows the neutral "No home set" hint."""
    dialog.home.setText("")
    assert dialog.validate_home() is False
    assert dialog.home_help.text().startswith("No home set")
    assert dialog.home_help.property("role") == "help"


def test_home_system_without_statics(dialog) -> None:
    """A home system without statics says "no statics" at the end of its hint."""
    system = next(candidate for candidate in UNIVERSE.all_systems() if not UNIVERSE.static_types(candidate.id))
    dialog.home.setText(system.name)
    dialog.validate_home()
    assert dialog.home_help.text().endswith("no statics")


def test_home_completer(dialog) -> None:
    """The home field completes system names case-insensitively from a lowercase prefix."""
    completer = dialog.home.completer()
    assert completer.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive
    completer.setCompletionPrefix(HOME.name[:6].lower())
    assert completer.completionCount() >= 1
    assert HOME.name in [completer.completionModel().index(row, 0).data() for row in range(completer.completionCount())]


# ---------------------------------------------------------------- naming scheme
def test_scheme_cards_and_previews(dialog) -> None:
    """There is one card per naming scheme with its "Home → …" preview, and numeric is checked by default."""
    from homebound.mapping.naming import SCHEMES
    assert set(dialog.scheme_cards) == set(SCHEMES)
    for key, card in dialog.scheme_cards.items():
        assert card.preview_label.text() == SettingsDialog.scheme_preview(key)
        assert card.preview_label.text().startswith("Home → ")
    assert dialog.scheme_cards["numeric"].radio.isChecked()


def test_scheme_preview_validation() -> None:
    """scheme_preview raises TypeError for a non-string key and ValueError for a blank one."""
    with pytest.raises(TypeError):
        SettingsDialog.scheme_preview(3)
    with pytest.raises(ValueError):
        SettingsDialog.scheme_preview(" ")


def test_scheme_preview_differs_between_schemes() -> None:
    """Every naming scheme has its own preview text."""
    previews = {SettingsDialog.scheme_preview(key) for key in dialog_module.SCHEMES}
    assert len(previews) == len(dialog_module.SCHEMES)


def test_card_click_selects_and_marks(dialog) -> None:
    """Clicking a scheme card checks its radio and moves the selected mark away from numeric."""
    key = next(key for key in dialog.scheme_cards if key != "numeric")
    card = dialog.scheme_cards[key]
    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    assert card.radio.isChecked()
    assert card.property("selected") == "true"
    assert dialog.scheme_cards["numeric"].property("selected") == "false"


def test_saved_unknown_scheme_falls_back_to_numeric(monkeypatch, tmp_path, kwin, qt_application) -> None:
    """A saved scheme that no longer exists opens with the numeric card checked."""
    state = make_state(monkeypatch, tmp_path, naming_scheme="gone")
    assert SettingsDialog(None, state).scheme_cards["numeric"].radio.isChecked()


# ---------------------------------------------------------------- overlay
def test_overlay_defaults(dialog) -> None:
    """The overlay page starts in follow mode, with no pinned character and the saved opacity as a percentage."""
    assert dialog.mode_cards["follow"].radio.isChecked()
    assert dialog.pinned.currentData() is None
    assert dialog.opacity.value() == round(dialog.state.settings.overlay_opacity * 100)
    assert dialog.opacity_label.text() == f"{dialog.opacity.value()}%"


def test_opacity_label_follows_slider(dialog) -> None:
    """Moving the opacity slider updates the percentage label."""
    dialog.opacity.setValue(70)
    assert dialog.opacity_label.text() == "70%"


def test_opacity_roundtrip_has_no_float_error(monkeypatch, tmp_path, kwin, qt_application) -> None:
    """A saved opacity of 0.57 shows as 57 on the slider, not 56."""
    state = make_state(monkeypatch, tmp_path, overlay_opacity=0.57)
    assert SettingsDialog(None, state).opacity.value() == 57


def test_pinned_character_from_rows(monkeypatch, tmp_path, kwin, qt_application) -> None:
    """The pinned-character list has one entry per character (the ID when the name is blank) and selects the saved one."""
    state = make_state(monkeypatch, tmp_path)
    monkeypatch.setattr(state, "character_rows", lambda: [(7, "Alice", None, True, 0), (8, "", None, False, 0)])
    state.settings.overlay_pinned_character = 8
    dialog = SettingsDialog(None, state)
    assert dialog.pinned.itemText(1) == "Alice"
    assert dialog.pinned.itemText(2) == "8"
    assert dialog.pinned.currentData() == 8


def test_overlay_ranges(dialog) -> None:
    """The text size, radius and opacity controls use the ranges defined in the module."""
    assert (dialog.text_size.minimum(), dialog.text_size.maximum()) == dialog_module.TEXT_SIZE_RANGE
    assert (dialog.radius.minimum(), dialog.radius.maximum()) == dialog_module.RADIUS_RANGE
    assert (dialog.opacity.minimum(), dialog.opacity.maximum()) == dialog_module.OPACITY_RANGE


def test_overlay_ranges_match_settings() -> None:
    """The spin boxes and the slider use the ranges the settings clamp loaded values to."""
    assert dialog_module.TEXT_SIZE_RANGE == settings_module.OVERLAY_TEXT_SIZE_RANGE
    assert dialog_module.RADIUS_RANGE == settings_module.KNOWN_SPACE_RADIUS_RANGE
    assert dialog_module.OPACITY_RANGE == (55, 92)


# ---------------------------------------------------------------- zKill
def test_zkill_checkbox_reflects_setting(monkeypatch, tmp_path, kwin, qt_application) -> None:
    """The zKill checkbox starts checked only when zKill is enabled in the settings."""
    state = make_state(monkeypatch, tmp_path)
    assert not SettingsDialog(None, state).zkill.isChecked()
    state.settings.zkill_enabled = True
    assert SettingsDialog(None, state).zkill.isChecked()


# ---------------------------------------------------------------- KWin
@pytest.mark.parametrize("status, button, remove_visible, banner", [
    ("active", "Reinstall", True, "success"),
    ("inactive", "Enable", True, "warning"),
    ("missing", "Install", False, "danger"),
    ("unavailable", "Install", False, "warning"),
])
def test_kwin_states(state, kwin, status, button, remove_visible, banner) -> None:
    """Each KWin script status sets the action button text, the Remove button and the banner style."""
    kwin["status"] = status
    dialog = SettingsDialog(None, state, 5)
    dialog.show()
    assert dialog.kwin_status == status
    assert dialog.kwin_button.text() == button
    assert dialog.kwin_remove_button.isVisible() is remove_visible
    assert dialog.kwin_banner.property("banner") == banner


def test_kwin_install_button(state, kwin) -> None:
    """The action button installs a missing script; refresh_kwin then reads the new status."""
    dialog = SettingsDialog(None, state)
    dialog.kwin_button.click()
    assert kwin["calls"] == ["install_kwin_script"]
    kwin["status"] = "active"
    assert dialog.refresh_kwin() == "active"


def test_kwin_inactive_enables(state, kwin) -> None:
    """With an installed but inactive script, the action enables it."""
    kwin["status"] = "inactive"
    dialog = SettingsDialog(None, state)
    assert dialog.run_kwin_action() is True
    assert kwin["calls"] == ["enable_kwin_script"]


def test_kwin_failure_message_shown(state, kwin) -> None:
    """A failed KWin action returns False and shows its error message."""
    dialog = SettingsDialog(None, state)
    kwin["result"] = (False, "boom")
    assert dialog.run_kwin_action() is False
    assert "boom" in dialog.kwin_text.text()


def test_kwin_remove(state, kwin) -> None:
    """The Remove button removes the script; a failed removal returns False and shows the error."""
    kwin["status"] = "active"
    dialog = SettingsDialog(None, state)
    dialog.kwin_remove_button.click()
    assert kwin["calls"] == ["remove_kwin_script"]
    kwin["result"] = (False, "denied")
    assert dialog.remove_kwin() is False
    assert "denied" in dialog.kwin_text.text()


# ---------------------------------------------------------------- saving
def test_save_writes_settings(dialog, state, tmp_path) -> None:
    """Save stores every edited field (trimmed folder, proper home name), emits settingsChanged and writes the file."""
    other = pick_system("C3")
    folder = tmp_path / "other"
    folder.mkdir()
    key = next(key for key in dialog.scheme_cards if key != "numeric")
    dialog.log_directory.setText(f"  {folder}  ")
    dialog.home.setText(other.name.lower())
    dialog.scheme_cards[key].radio.setChecked(True)
    dialog.auto_name.setChecked(not state.settings.auto_name_when_certain)
    dialog.mode_cards["pinned_always"].radio.setChecked(True)
    dialog.opacity.setValue(80)
    dialog.text_size.setValue(13)
    dialog.radius.setValue(4)
    dialog.zkill.setChecked(True)
    applied = []
    state.settingsChanged.connect(lambda: applied.append(True))
    dialog.save_button.click()
    assert dialog.result() == dialog.DialogCode.Accepted
    settings = state.settings
    assert settings.log_directory == str(folder)
    assert settings.home_system == other.name
    assert settings.naming_scheme == key
    assert settings.overlay_mode == "pinned_always"
    assert settings.overlay_opacity == 0.8
    assert settings.overlay_text_size == 13
    assert settings.known_space_radius == 4
    assert settings.zkill_enabled is True
    assert applied
    assert state.zkill.enabled is True
    saved = json.loads((tmp_path / "settings.json").read_text())
    assert saved["overlay_text_size"] == 13 and saved["naming_scheme"] == key


def test_save_clears_home_when_field_empty(dialog, state) -> None:
    """Saving with an empty home field clears the home system."""
    dialog.home.setText("")
    dialog.save()
    assert state.settings.home_system == ""


def test_save_keeps_home_when_text_unknown(dialog, state) -> None:
    """Saving with an unknown home name keeps the previous home system."""
    dialog.home.setText("Nowhereville")
    dialog.save()
    assert state.settings.home_system == HOME.name


def test_save_does_not_share_state_with_dialog(dialog, state) -> None:
    """After saving, the dialog's edited copy and its map list are separate objects from the live settings."""
    dialog.save()
    assert dialog.edited is not state.settings
    assert dialog.edited.maps is not state.settings.maps

"""Tests for the settings dialog: every page and saving."""
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog

import homebound.ui.settings_dialog as dialog_module
from conftest import HOME, UNIVERSE, pick_system
from homebound.ui.settings_dialog import PAGES, SettingsDialog
from ui_helpers import make_state

LOG_NAME = "Local_20261028_165148_{}.txt"


@pytest.fixture
def state(monkeypatch, tmp_path, qt_application):
    """An AppState on a temporary folder."""
    return make_state(monkeypatch, tmp_path)


@pytest.fixture
def kwin(monkeypatch):
    """Scripted KWin functions; ``kwin["status"]`` and ``kwin["result"]`` can be changed, ``kwin["calls"]`` records."""
    control = {"status": "missing", "result": (True, ""), "calls": []}

    def record(name):
        def call():
            control["calls"].append(name)
            return control["result"]
        return call

    monkeypatch.setattr(dialog_module.focus, "kwin_script_status", lambda: control["status"])
    for name in ("install_kwin_script", "enable_kwin_script", "remove_kwin_script"):
        monkeypatch.setattr(dialog_module.focus, name, record(name))
    return control


@pytest.fixture
def dialog(state, kwin):
    """A settings dialog on the first page."""
    return SettingsDialog(None, state)


def test_constructor_validation(state, kwin) -> None:
    """Constructor validation."""
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
    """Opens on page."""
    assert SettingsDialog(None, state, page).stack.currentIndex() == page


def test_nav_switches_page(dialog) -> None:
    """Nav switches page."""
    dialog.nav.setCurrentRow(3)
    assert dialog.stack.currentIndex() == 3


def test_cancel_leaves_settings(dialog, state) -> None:
    """Cancel leaves settings."""
    dialog.home.setText("")
    dialog.cancel_button.click()
    assert state.settings.home_system == HOME.name


# ---------------------------------------------------------------- log folder
def test_log_folder_missing(dialog, tmp_path) -> None:
    """Log folder missing."""
    dialog.log_directory.setText(str(tmp_path / "nope"))
    assert dialog.validate_log_directory() == "invalid"
    assert dialog.log_help.text().startswith("✕ Folder not found")
    assert dialog.log_directory.property("state") == "invalid"


def test_log_folder_empty(dialog, tmp_path) -> None:
    """Log folder empty."""
    (tmp_path / "logs" / "Local_garbage.txt").write_text("x")
    dialog.log_directory.setText(str(tmp_path / "logs"))
    assert dialog.validate_log_directory() == "empty"
    assert "no Local_*.txt" in dialog.log_help.text()
    assert dialog.log_help.property("role") == "help-warn"


def test_log_folder_with_logs(dialog, tmp_path) -> None:
    """Log folder with logs."""
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
    """Log timer validates after typing."""
    dialog.log_directory.setText(str(tmp_path / "nope"))
    assert dialog.log_timer.isActive()
    dialog.log_timer.timeout.emit()
    assert dialog.log_help.property("role") == "help-err"


def test_browse_sets_folder(dialog, monkeypatch, tmp_path) -> None:
    """Browse sets folder."""
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *arguments: str(tmp_path))
    assert dialog.browse_log_directory() == str(tmp_path)
    assert dialog.log_directory.text() == str(tmp_path)


def test_browse_cancelled_keeps_folder(dialog, monkeypatch) -> None:
    """Browse cancelled keeps folder."""
    before = dialog.log_directory.text()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *arguments: "")
    assert dialog.browse_log_directory() == ""
    assert dialog.log_directory.text() == before


def test_browse_starts_in_home_when_field_empty(dialog, monkeypatch) -> None:
    """Browse starts in home when field empty."""
    seen = []
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *arguments: seen.append(arguments) or "")
    dialog.log_directory.setText("")
    dialog.browse_log_directory()
    assert seen[0][2]


def test_autodetect_found(dialog, monkeypatch, tmp_path) -> None:
    """Autodetect found."""
    monkeypatch.setattr(dialog_module, "detect_log_directories", lambda: [tmp_path])
    assert dialog.autodetect_log_directory() is True
    assert dialog.log_directory.text() == str(tmp_path)


def test_autodetect_nothing_found(dialog, monkeypatch) -> None:
    """Autodetect nothing found."""
    monkeypatch.setattr(dialog_module, "detect_log_directories", lambda: [])
    assert dialog.autodetect_log_directory() is False
    assert dialog.log_help.text().startswith("✕ No Chatlogs folder")
    assert dialog.log_help.property("role") == "help-err"


def test_buttons_trigger_browse_and_autodetect(dialog, monkeypatch, tmp_path) -> None:
    """Buttons trigger browse and autodetect."""
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *arguments: str(tmp_path))
    dialog.browse_button.click()
    assert dialog.log_directory.text() == str(tmp_path)
    monkeypatch.setattr(dialog_module, "detect_log_directories", lambda: [])
    dialog.autodetect_button.click()
    assert "No Chatlogs" in dialog.log_help.text()


# ---------------------------------------------------------------- home system
def test_home_known(dialog) -> None:
    """Home known."""
    dialog.home.setText(HOME.name)
    assert dialog.validate_home() is True
    text = dialog.home_help.text()
    assert text.startswith(f"✓ {HOME.name} · {HOME.system_class}")
    assert dialog.home_help.property("role") == "help-ok"


def test_home_unknown(dialog) -> None:
    """Home unknown."""
    dialog.home.setText("Nowhereville")
    assert dialog.validate_home() is False
    assert dialog.home_help.text() == "✕ Unknown system"
    assert dialog.home_help.property("role") == "help-err"


def test_home_empty(dialog) -> None:
    """Home empty."""
    dialog.home.setText("")
    assert dialog.validate_home() is False
    assert dialog.home_help.text().startswith("No home set")
    assert dialog.home_help.property("role") == "help"


def test_home_system_without_statics(dialog) -> None:
    """Home system without statics."""
    system = next(s for s in UNIVERSE.all_systems() if not UNIVERSE.static_types(s.id))
    dialog.home.setText(system.name)
    dialog.validate_home()
    assert dialog.home_help.text().endswith("no statics")


def test_home_completer(dialog) -> None:
    """Home completer."""
    completer = dialog.home.completer()
    assert completer.caseSensitivity() == Qt.CaseSensitivity.CaseInsensitive
    completer.setCompletionPrefix(HOME.name[:6].lower())
    assert completer.completionCount() >= 1
    assert HOME.name in [completer.completionModel().index(row, 0).data() for row in range(completer.completionCount())]


# ---------------------------------------------------------------- naming scheme
def test_scheme_cards_and_previews(dialog) -> None:
    """Scheme cards and previews."""
    from homebound.mapping.naming import SCHEMES
    assert set(dialog.scheme_cards) == set(SCHEMES)
    for key, card in dialog.scheme_cards.items():
        assert card.preview_label.text() == SettingsDialog.scheme_preview(key)
        assert card.preview_label.text().startswith("Home → ")
    assert dialog.scheme_cards["numeric"].radio.isChecked()


def test_scheme_preview_validation() -> None:
    """Scheme preview validation."""
    with pytest.raises(TypeError):
        SettingsDialog.scheme_preview(3)
    with pytest.raises(ValueError):
        SettingsDialog.scheme_preview(" ")


def test_scheme_preview_differs_between_schemes() -> None:
    """Scheme preview differs between schemes."""
    previews = {SettingsDialog.scheme_preview(key) for key in dialog_module.SCHEMES}
    assert len(previews) == len(dialog_module.SCHEMES)


def test_card_click_selects_and_marks(dialog) -> None:
    """Card click selects and marks."""
    key = next(key for key in dialog.scheme_cards if key != "numeric")
    card = dialog.scheme_cards[key]
    QTest.mouseClick(card, Qt.MouseButton.LeftButton)
    assert card.radio.isChecked()
    assert card.property("selected") == "true"
    assert dialog.scheme_cards["numeric"].property("selected") == "false"


def test_saved_unknown_scheme_falls_back_to_numeric(monkeypatch, tmp_path, kwin, qt_application) -> None:
    """Saved unknown scheme falls back to numeric."""
    state = make_state(monkeypatch, tmp_path, naming_scheme="gone")
    assert SettingsDialog(None, state).scheme_cards["numeric"].radio.isChecked()


# ---------------------------------------------------------------- overlay
def test_overlay_defaults(dialog) -> None:
    """Overlay defaults."""
    assert dialog.mode_cards["follow"].radio.isChecked()
    assert dialog.pinned.currentData() is None
    assert dialog.opacity.value() == round(dialog.state.settings.overlay_opacity * 100)
    assert dialog.opacity_label.text() == f"{dialog.opacity.value()}%"


def test_opacity_label_follows_slider(dialog) -> None:
    """Opacity label follows slider."""
    dialog.opacity.setValue(70)
    assert dialog.opacity_label.text() == "70%"


def test_opacity_roundtrip_has_no_float_error(monkeypatch, tmp_path, kwin, qt_application) -> None:
    """Opacity roundtrip has no float error."""
    state = make_state(monkeypatch, tmp_path, overlay_opacity=0.57)
    assert SettingsDialog(None, state).opacity.value() == 57


def test_pinned_character_from_rows(monkeypatch, tmp_path, kwin, qt_application) -> None:
    """Pinned character from rows."""
    state = make_state(monkeypatch, tmp_path)
    monkeypatch.setattr(state, "character_rows", lambda: [(7, "Alice", None, True, 0), (8, "", None, False, 0)])
    state.settings.overlay_pinned_character = 8
    dialog = SettingsDialog(None, state)
    assert dialog.pinned.itemText(1) == "Alice"
    assert dialog.pinned.itemText(2) == "8"
    assert dialog.pinned.currentData() == 8


def test_overlay_ranges(dialog) -> None:
    """Overlay ranges."""
    assert (dialog.text_size.minimum(), dialog.text_size.maximum()) == dialog_module.TEXT_SIZE_RANGE
    assert (dialog.radius.minimum(), dialog.radius.maximum()) == dialog_module.RADIUS_RANGE
    assert (dialog.opacity.minimum(), dialog.opacity.maximum()) == dialog_module.OPACITY_RANGE


# ---------------------------------------------------------------- zKill
def test_zkill_checkbox_reflects_setting(monkeypatch, tmp_path, kwin, qt_application) -> None:
    """Zkill checkbox reflects setting."""
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
    """Kwin states."""
    kwin["status"] = status
    dialog = SettingsDialog(None, state, 5)
    dialog.show()
    assert dialog.kwin_status == status
    assert dialog.kwin_button.text() == button
    assert dialog.kwin_remove_button.isVisible() is remove_visible
    assert dialog.kwin_banner.property("banner") == banner


def test_kwin_install_button(state, kwin) -> None:
    """Kwin install button."""
    dialog = SettingsDialog(None, state)
    dialog.kwin_button.click()
    assert kwin["calls"] == ["install_kwin_script"]
    kwin["status"] = "active"
    assert dialog.refresh_kwin() == "active"


def test_kwin_inactive_enables(state, kwin) -> None:
    """Kwin inactive enables."""
    kwin["status"] = "inactive"
    dialog = SettingsDialog(None, state)
    assert dialog.run_kwin_action() is True
    assert kwin["calls"] == ["enable_kwin_script"]


def test_kwin_failure_message_shown(state, kwin) -> None:
    """Kwin failure message shown."""
    dialog = SettingsDialog(None, state)
    kwin["result"] = (False, "boom")
    assert dialog.run_kwin_action() is False
    assert "boom" in dialog.kwin_text.text()


def test_kwin_remove(state, kwin) -> None:
    """Kwin remove."""
    kwin["status"] = "active"
    dialog = SettingsDialog(None, state)
    dialog.kwin_remove_button.click()
    assert kwin["calls"] == ["remove_kwin_script"]
    kwin["result"] = (False, "denied")
    assert dialog.remove_kwin() is False
    assert "denied" in dialog.kwin_text.text()


# ---------------------------------------------------------------- saving
def test_save_writes_settings(dialog, state, tmp_path) -> None:
    """Save writes settings."""
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
    """Save clears home when field empty."""
    dialog.home.setText("")
    dialog.save()
    assert state.settings.home_system == ""


def test_save_keeps_home_when_text_unknown(dialog, state) -> None:
    """Save keeps home when text unknown."""
    dialog.home.setText("Nowhereville")
    dialog.save()
    assert state.settings.home_system == HOME.name


def test_save_does_not_share_state_with_dialog(dialog, state) -> None:
    """Save does not share state with dialog."""
    dialog.save()
    assert dialog.edited is not state.settings
    assert dialog.edited.maps is not state.settings.maps

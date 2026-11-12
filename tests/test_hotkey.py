"""Tests for the global overlay shortcut: parsing, the backends with stand-ins, the cycle and the settings."""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence

from homebound.platform import hotkey as hotkey_module
from homebound.platform.hotkey import (
    ACTION_ID, DEFAULT_SHORTCUT, HOTKEY_ID, MOD_CONTROL, MOD_NOREPEAT, MOD_SHIFT, WM_HOTKEY, GlobalHotkey, KdeBackend,
    WindowsBackend, normalize_shortcut, parse_shortcut, windows_modifiers, windows_virtual_key,
)
from homebound.settings import Settings
from homebound.ui.map_window import MapWindow
from homebound.ui.settings_dialog import SettingsDialog
from ui_helpers import FakeFocus, isolated_settings, make_state


class FakeBackend:
    """Records registrations; ``reason`` is what the next ``register`` returns."""

    def __init__(self, pressed) -> None:
        self.pressed, self.reason, self.calls, self.released = pressed, "", [], 0

    def register(self, modifiers, key, combined) -> str:
        self.calls.append((modifiers, key, combined))
        return self.reason

    def unregister(self) -> None:
        self.released += 1


@pytest.fixture
def fake_hotkey(qt_application) -> GlobalHotkey:
    """A GlobalHotkey on a fake backend."""
    return GlobalHotkey(FakeBackend)


@pytest.fixture
def window(qt_application, monkeypatch, tmp_path, fake_hotkey):
    """The main window with the fake hotkey."""
    state = make_state(monkeypatch, tmp_path)
    main_window = MapWindow(state, FakeFocus(), hotkey=fake_hotkey)
    main_window.show()
    qt_application.processEvents()
    yield main_window
    main_window.overlay.hide()
    main_window.hide()
    state.stop()


# ---------------------------------------------------------------- parsing
def test_parse_default_shortcut() -> None:
    modifiers, key, combined = parse_shortcut(DEFAULT_SHORTCUT)
    assert modifiers == Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    assert key == Qt.Key.Key_O and combined == QKeySequence(DEFAULT_SHORTCUT)[0].toCombined()


@pytest.mark.parametrize("text", ["", "O", "Shift+O", "Ctrl+Shift", "Ctrl+O, Ctrl+P", "Ctrl+Space", "nonsense", "Ctrl+Tab"])
def test_parse_rejects_unusable(text) -> None:
    assert parse_shortcut(text) is None


@pytest.mark.parametrize("text, expected", [("ctrl+shift+o", "Ctrl+Shift+O"), ("Alt+F9", "Alt+F9"), ("Meta+1", "Meta+1"),
                                            ("Shift+O", "")])
def test_normalize(text, expected) -> None:
    assert normalize_shortcut(text) == expected


def test_windows_codes() -> None:
    assert windows_modifiers(Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier) == MOD_CONTROL | MOD_SHIFT
    assert windows_virtual_key(int(Qt.Key.Key_O)) == ord("O")
    assert windows_virtual_key(int(Qt.Key.Key_F1)) == 0x70 and windows_virtual_key(int(Qt.Key.Key_F12)) == 0x7B


# ---------------------------------------------------------------- GlobalHotkey
def test_set_shortcut_registers_and_remembers_text(fake_hotkey) -> None:
    assert fake_hotkey.set_shortcut("ctrl+shift+o") == ""
    assert fake_hotkey.text == "Ctrl+Shift+O" and len(fake_hotkey._backend.calls) == 1


def test_failed_registration_returns_reason_and_keeps_running(fake_hotkey) -> None:
    fake_hotkey._backend.reason = "the key is already used by another program"
    assert fake_hotkey.set_shortcut("Ctrl+Shift+O") == "the key is already used by another program"
    assert fake_hotkey.text == ""


def test_backend_exception_is_reported(fake_hotkey) -> None:
    def boom(*_):
        raise OSError("no bus")
    fake_hotkey._backend.register = boom
    assert "no bus" in fake_hotkey.set_shortcut("Ctrl+Shift+O")
    assert fake_hotkey.text == ""


def test_invalid_and_empty_shortcut(fake_hotkey) -> None:
    fake_hotkey.set_shortcut("Ctrl+Shift+O")
    assert "Ctrl, Alt or Meta" in fake_hotkey.set_shortcut("Shift+O")
    assert fake_hotkey.text == "" and fake_hotkey._backend.released == 1
    assert fake_hotkey.set_shortcut("") == "" and fake_hotkey.text == ""


def test_no_backend(qt_application) -> None:
    hotkey = GlobalHotkey(lambda pressed: None)
    assert "not available" in hotkey.set_shortcut("Ctrl+Shift+O")
    hotkey.release()


def test_pressed_signal(fake_hotkey) -> None:
    seen = []
    fake_hotkey.pressed.connect(lambda: seen.append(1))
    fake_hotkey._backend.pressed()
    assert seen == [1]


def test_platform_backend_choice(monkeypatch, qt_application) -> None:
    monkeypatch.setattr(hotkey_module.sys, "platform", "darwin")
    assert hotkey_module._platform_backend(lambda: None) is None
    monkeypatch.setattr(hotkey_module.sys, "platform", "linux")
    assert isinstance(hotkey_module._platform_backend(lambda: None), KdeBackend)
    monkeypatch.setattr(hotkey_module.sys, "platform", "win32")
    assert isinstance(hotkey_module._platform_backend(lambda: None), WindowsBackend)


# ---------------------------------------------------------------- Windows backend
class FakeUser32:
    def __init__(self, ok=True) -> None:
        self.ok, self.registered, self.unregistered = ok, [], []

    def RegisterHotKey(self, window, hotkey_id, modifiers, virtual_key):  # noqa: N802
        self.registered.append((window, hotkey_id, modifiers, virtual_key))
        return self.ok

    def UnregisterHotKey(self, window, hotkey_id):  # noqa: N802
        self.unregistered.append(hotkey_id)


def test_windows_backend_registers_and_filters(qt_application) -> None:
    user32, seen = FakeUser32(), []
    backend = WindowsBackend(lambda: seen.append(1), user32)
    modifiers, key, combined = parse_shortcut("Ctrl+Shift+O")
    assert backend.register(modifiers, key, combined) == ""
    assert user32.registered == [(None, HOTKEY_ID, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, ord("O"))]
    assert backend.filter.handle(WM_HOTKEY, HOTKEY_ID) is True and seen == [1]
    assert backend.filter.handle(WM_HOTKEY, 5) is False and backend.filter.handle(0x100, HOTKEY_ID) is False
    backend.unregister()
    assert user32.unregistered == [HOTKEY_ID] and not backend.registered


def test_windows_backend_key_taken(qt_application) -> None:
    backend = WindowsBackend(lambda: None, FakeUser32(ok=False))
    assert backend.register(Qt.KeyboardModifier.ControlModifier, int(Qt.Key.Key_O), 0)
    assert not backend.registered


def test_windows_backend_without_user32(qt_application, monkeypatch) -> None:
    monkeypatch.delattr(hotkey_module.ctypes, "windll", raising=False)
    backend = WindowsBackend(lambda: None)
    assert backend.register(Qt.KeyboardModifier.ControlModifier, int(Qt.Key.Key_O), 0) == "not available here"
    backend.unregister()


# ---------------------------------------------------------------- KDE backend
class FakeReply:
    def __init__(self, ok=True, arguments=()) -> None:
        self.ok, self._arguments = ok, list(arguments)

    def type(self):
        from PySide6.QtDBus import QDBusMessage
        return QDBusMessage.MessageType.ReplyMessage if self.ok else QDBusMessage.MessageType.ErrorMessage

    def arguments(self):
        return self._arguments


class FakePath:
    def path(self) -> str:
        return "/component/homebound"


class FakeBus:
    def __init__(self, connected=True, fail=(), stored=None) -> None:
        self.connected, self.fail, self.calls, self.connects, self.stored = connected, fail, [], [], stored

    def isConnected(self):  # noqa: N802
        return self.connected

    def call(self, message):
        method = message.member()
        self.calls.append(method)
        if method in self.fail:
            return FakeReply(False)
        if method == "getComponent":
            return FakeReply(arguments=[FakePath()])
        if method == "shortcut":
            return FakeReply(arguments=[self.stored if self.stored is not None else []])
        return FakeReply()

    def connect(self, *arguments):
        self.connects.append(arguments)
        return True


def kde(bus, seen=None) -> KdeBackend:
    return KdeBackend((lambda: seen.append(1)) if seen is not None else (lambda: None), bus)


def test_kde_backend_registers_and_listens(qt_application) -> None:
    combined = parse_shortcut("Ctrl+Shift+O")[2]
    bus, seen = FakeBus(stored=[combined]), []
    backend = kde(bus, seen)
    assert backend.register(*parse_shortcut("Ctrl+Shift+O")) == ""
    assert bus.calls == ["doRegister", "getComponent", "setForeignShortcut", "shortcut"]
    assert bus.connects[0][3] == "globalShortcutPressed"
    backend.key_pressed("homebound", ACTION_ID[1], 0)
    backend.key_pressed("homebound", "other", 0)
    assert seen == [1]
    backend.register(*parse_shortcut("Ctrl+Shift+O"))
    assert len(bus.connects) == 1                      # listening once is enough
    backend.unregister()
    assert bus.calls[-1] == "unregister" and not backend.registered


@pytest.mark.parametrize("bus, text", [
    (FakeBus(connected=False), "no KDE global shortcut service"),
    (FakeBus(fail=("doRegister",)), "no KDE global shortcut service"),
    (FakeBus(fail=("getComponent",)), "refused the component"),
    (FakeBus(fail=("setForeignShortcut",)), "refused the key"),
    (FakeBus(stored=[]), "already used"),
])
def test_kde_backend_failures(qt_application, bus, text) -> None:
    backend = kde(bus)
    assert text in backend.register(*parse_shortcut("Ctrl+Shift+O"))
    assert not backend.registered
    backend.unregister()
    assert "unregister" not in bus.calls


# ---------------------------------------------------------------- the window
def test_window_registers_the_setting_and_shows_it_in_the_tray(window, fake_hotkey) -> None:
    assert fake_hotkey.text == "Ctrl+Shift+O"
    assert window.shortcut_action.isVisible()
    assert window.shortcut_action.text().endswith("Ctrl+Shift+O")
    assert window.status_labels["hotkey"].isHidden()


def test_failed_registration_hides_tray_text_and_says_so(window, fake_hotkey) -> None:
    fake_hotkey._backend.reason = "the key is already used by another program"
    window.state.settings.overlay_shortcut = "Ctrl+Alt+K"
    window.state.apply_settings()
    assert window.shortcut_action.isVisible() is False
    label = window.status_labels["hotkey"]
    assert not label.isHidden() and "Ctrl+Alt+K" in label.text() and "already used" in label.text()
    fake_hotkey._backend.reason = ""
    window.state.settings.overlay_shortcut = "Ctrl+Alt+L"
    window.state.apply_settings()
    assert label.isHidden() and window.shortcut_action.text().endswith("Ctrl+Alt+L")
    assert window.shortcut_action.isVisible()


def test_unchanged_setting_does_not_register_again(window, fake_hotkey) -> None:
    calls = len(fake_hotkey._backend.calls)
    window.state.apply_settings()
    assert len(fake_hotkey._backend.calls) == calls


def test_empty_setting_releases_the_key(window, fake_hotkey) -> None:
    window.state.settings.overlay_shortcut = ""
    window.state.apply_settings()
    assert fake_hotkey.text == "" and window.shortcut_action.isVisible() is False
    assert window.status_labels["hotkey"].isHidden()


def test_cycle_order_without_pin_skips_pinned_modes(window) -> None:
    window.state.settings.overlay_mode = "off"
    seen = [window.cycle_overlay_mode() for _ in range(4)]
    assert seen == ["follow", "off", "follow", "off"]


def test_cycle_order_with_pin(window) -> None:
    window.state.settings.overlay_pinned_character = 7
    window.set_overlay_mode("off")
    seen = [window.cycle_overlay_mode() for _ in range(5)]
    assert seen == ["follow", "pinned_eve", "pinned_always", "off", "follow"]
    assert window.mode_actions["follow"].isChecked()


def test_pressing_the_key_cycles(window, fake_hotkey) -> None:
    window.set_overlay_mode("off")
    fake_hotkey._backend.pressed()
    assert window.state.settings.overlay_mode == "follow"
    window.shortcut_action.trigger()
    assert window.state.settings.overlay_mode == "off"


def test_refused_mode_makes_no_toast_when_quiet(window) -> None:
    shown = []
    window.toasts.show = lambda *args, **kwargs: shown.append(args)
    window.set_overlay_mode("off")
    window.cycle_overlay_mode()
    assert shown == []
    window.set_overlay_mode("pinned_eve")
    assert shown


def test_window_without_hotkey(qt_application, monkeypatch, tmp_path) -> None:
    state = make_state(monkeypatch, tmp_path)
    main_window = MapWindow(state, FakeFocus())
    assert main_window._apply_hotkey() == "" and not main_window.shortcut_action.isVisible()
    main_window.overlay.hide()
    state.stop()


# ---------------------------------------------------------------- settings
def test_setting_default_and_loading(monkeypatch, tmp_path) -> None:
    settings = isolated_settings(monkeypatch, tmp_path)
    assert settings.overlay_shortcut == "Ctrl+Shift+O"
    settings.overlay_shortcut = "Ctrl+Alt+K"
    settings.save()
    assert Settings.load().overlay_shortcut == "Ctrl+Alt+K"
    (tmp_path / "settings.json").write_text('{"overlay_shortcut": 5}')
    assert Settings.load().overlay_shortcut == "Ctrl+Shift+O"


def test_dialog_edits_the_shortcut(qt_application, monkeypatch, tmp_path) -> None:
    state = make_state(monkeypatch, tmp_path)
    dialog = SettingsDialog(None, state)
    assert dialog.shortcut.keySequence().toString() == "Ctrl+Shift+O" and dialog.validate_shortcut() == "valid"
    dialog.shortcut.setKeySequence(QKeySequence("Shift+O"))
    assert dialog.validate_shortcut() == "invalid" and dialog.save() is False
    dialog.shortcut.setKeySequence(QKeySequence("Ctrl+Alt+K"))
    assert dialog.save() is True and state.settings.overlay_shortcut == "Ctrl+Alt+K"
    dialog = SettingsDialog(None, state)
    dialog.shortcut.clear()
    assert dialog.validate_shortcut() == "empty" and dialog.save() is True
    assert state.settings.overlay_shortcut == ""
    state.stop()

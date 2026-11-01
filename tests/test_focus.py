"""The focus tracker and the Windows backend (user32 faked, so this runs on every platform)."""
import ctypes
import os

import pytest
from PySide6.QtCore import QObject, QRect

import homebound.platform.focus_windows as windows_module
import homebound.ui.focus as focus_module


def spy(signal) -> list:
    """Collect the arguments of every emission of a signal."""
    received: list = []
    signal.connect(lambda *arguments: received.append(arguments))
    return received


class IdleBackend(QObject):
    """A backend that does nothing, so the tracker can be tested alone."""

    def start(self) -> str:
        """Report a made-up status."""
        return "idle"


@pytest.fixture
def tracker(qt_application, monkeypatch) -> focus_module.FocusTracker:
    """A FocusTracker with an idle backend."""
    monkeypatch.setattr(focus_module, "_backend", lambda owner: IdleBackend(owner))
    return focus_module.FocusTracker()


def test_eve_character_captions() -> None:
    """Only "EVE" and "EVE - Name" captions are clients."""
    assert focus_module.eve_character("EVE - Ijomo Ohaya") == "Ijomo Ohaya"
    assert focus_module.eve_character("  EVE - Pilot  ") == "Pilot"
    assert focus_module.eve_character("EVE") == ""
    assert focus_module.eve_character("EVE Online Launcher") is None
    assert focus_module.eve_character("Firefox") is None
    with pytest.raises(TypeError):
        focus_module.eve_character(None)


def test_set_detects_changes(tracker) -> None:
    """Only a different character or rectangle emits."""
    changes = spy(tracker.focusChanged)
    assert tracker._set("A", QRect(0, 0, 1, 1)) is True
    assert tracker._set("A", QRect(0, 0, 1, 1)) is False
    assert tracker._set("A", QRect(0, 0, 2, 2)) is True
    assert tracker._set(None, QRect(0, 0, 2, 2)) is True
    assert len(changes) == 3


def test_report_skips_own_windows(tracker) -> None:
    """report() parses the caption; Homebound's own windows keep the previous state."""
    assert tracker.start() == "idle"
    assert tracker.report("EVE - Pilot", False, QRect(0, 0, 5, 5)) is True
    assert tracker.report("Homebound · Settings", True, QRect()) is False
    assert tracker.character == "Pilot" and tracker.rect == QRect(0, 0, 5, 5)
    tracker.report("Firefox", False, QRect())
    assert tracker.character is None


@pytest.mark.parametrize("platform, backend", [("win32", "WindowsFocus"), ("linux", "LinuxFocus")])
def test_backend_by_platform(qt_application, monkeypatch, platform, backend) -> None:
    """The backend is picked by sys.platform."""
    monkeypatch.setattr(focus_module.sys, "platform", platform)
    if platform == "linux":
        pytest.importorskip("PySide6.QtDBus")
        import homebound.platform.focus_linux as linux_module
        monkeypatch.setattr(linux_module.QDBusConnection, "sessionBus", lambda: FakeBus())
    assert type(focus_module.FocusTracker().backend).__name__ == backend


class FakeBus:
    """A session bus that is not connected."""

    @staticmethod
    def isConnected() -> bool:
        """Report no connection."""
        return False


# ---------------------------------------------------------------- Windows backend

class FakeUser32:
    """Stands in for user32: one scripted foreground window."""

    def __init__(self, window: int = 7, title: str = "EVE - Pilot", process_id: int = 4242,
                 rect: tuple[int, int, int, int] = (10, 20, 1930, 1100), rect_ok: bool = True) -> None:
        """Script the foreground window (0 = none) and what the calls answer."""
        self.window, self.title, self.process_id, self.rect, self.rect_ok = window, title, process_id, rect, rect_ok
        self.topmost: list[tuple] = []

    def GetForegroundWindow(self) -> int:  # noqa: N802 (Win32 name)
        """Return the scripted window handle."""
        return self.window

    def GetWindowThreadProcessId(self, window: int, process_id) -> int:  # noqa: N802
        """Write the process id through the pointer."""
        process_id._obj.value = self.process_id
        return 1

    def GetWindowTextLengthW(self, window: int) -> int:  # noqa: N802
        """Return the title length."""
        return len(self.title)

    def GetWindowTextW(self, window: int, buffer, size: int) -> int:  # noqa: N802
        """Copy the title into the buffer."""
        buffer.value = self.title[:size - 1]
        return len(buffer.value)

    def GetWindowRect(self, window: int, rect) -> bool:  # noqa: N802
        """Fill in the rectangle (left, top, right, bottom)."""
        if self.rect_ok:
            rect._obj.left, rect._obj.top, rect._obj.right, rect._obj.bottom = self.rect
        return self.rect_ok

    def SetWindowPos(self, *arguments) -> int:  # noqa: N802
        """Remember the call."""
        self.topmost.append(arguments)
        return 1


def test_foreground_window_reads_title_process_and_rect() -> None:
    """The title, owning process and window rectangle come from user32."""
    assert windows_module.foreground_window(FakeUser32()) == ("EVE - Pilot", 4242, QRect(10, 20, 1920, 1080))
    assert windows_module.foreground_window(FakeUser32(window=0)) is None
    assert windows_module.foreground_window(FakeUser32(rect_ok=False))[2] == QRect()


def test_windows_backend_polls(tracker) -> None:
    """Polling reports the foreground window; our own process is skipped; no window means no client."""
    user32 = FakeUser32()
    backend = windows_module.WindowsFocus(tracker, user32)
    statuses = spy(tracker.status)
    assert backend.start() == "windows" and backend._timer.isActive()
    assert statuses == [("ok", "Windows focus: active")]
    backend._poll()
    assert tracker.character == "Pilot" and tracker.rect == QRect(10, 20, 1920, 1080)
    user32.process_id, user32.title = os.getpid(), "Homebound"
    backend._poll()
    assert tracker.character == "Pilot"
    user32.window = 0
    backend._poll()
    assert tracker.character is None
    backend._timer.stop()


def test_windows_backend_ignores_errors_and_missing_user32(tracker, monkeypatch) -> None:
    """A failing call changes nothing; without user32 start() reports it and doesn't poll."""
    backend = windows_module.WindowsFocus(tracker, FakeUser32())
    tracker._set("Keep", QRect())

    def failing(user32) -> None:
        """Fail like a ctypes call can."""
        raise OSError("access denied")

    monkeypatch.setattr(windows_module, "foreground_window", failing)
    backend._poll()
    assert tracker.character == "Keep"
    monkeypatch.setattr(windows_module, "_user32", lambda: None)
    missing = windows_module.WindowsFocus(tracker)
    statuses = spy(tracker.status)
    assert missing.start() == "unavailable" and not missing._timer.isActive()
    assert statuses == [("err", "Windows focus: unavailable")]


def test_keep_on_top(monkeypatch) -> None:
    """keep_on_top puts the window in the topmost band without moving or activating it."""
    user32 = FakeUser32()
    assert windows_module.keep_on_top(99, user32) is True
    window, after, *_, flags = user32.topmost[0]
    assert window.value == 99 and after.value in (windows_module.HWND_TOPMOST, ctypes.c_size_t(-1).value)
    assert flags == windows_module.SWP_NOMOVE | windows_module.SWP_NOSIZE | windows_module.SWP_NOACTIVATE
    monkeypatch.setattr(windows_module, "_user32", lambda: None)
    assert windows_module.keep_on_top(99) is False

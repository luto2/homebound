"""The focus tracker and the Windows backend (user32 faked, so this runs on every platform)."""
import ctypes
import os

import pytest
from PySide6.QtCore import QObject, QRect

import homebound.platform.focus_windows as windows_module
import homebound.ui.focus as focus_module
from ui_helpers import spy


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


def test_open_characters_from_captions() -> None:
    """Two clients count, character select, the launcher and other windows don't."""
    captions = ["EVE - Kira", "EVE - Bob Two", "EVE", "EVE Launcher", "EVE Online Launcher", "Firefox", "EVE - Kira"]
    assert focus_module.eve_characters(captions) == {"Kira", "Bob Two"}
    assert focus_module.eve_characters([]) == set()
    with pytest.raises(TypeError):
        focus_module.eve_characters("EVE - Kira")


def test_tracker_polls_the_open_clients(tracker) -> None:
    """A backend that can list windows feeds open_characters; a change emits once; None means "can't tell"."""
    changes = spy(tracker.openCharactersChanged)
    assert tracker.open_characters is None and tracker.poll_windows() is False   # the idle backend can't tell
    tracker.backend.open_eve_characters = lambda: {"Kira"}
    assert tracker.poll_windows() is True and tracker.open_characters == {"Kira"}
    assert tracker.poll_windows() is False
    tracker.backend.open_eve_characters = lambda: None
    assert tracker.poll_windows() is True and tracker.open_characters is None
    assert changes == [({"Kira"},), (None,)]
    tracker.start()
    assert tracker.window_timer.isActive() and tracker.window_timer.interval() == focus_module.WINDOW_POLL_MS
    tracker.window_timer.stop()


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


class ListingUser32(FakeUser32):
    """A user32 with several top-level windows: ``{handle: (title, visible)}``."""

    def __init__(self, windows: dict[int, tuple[str, bool]], fail: bool = False) -> None:
        """Script the windows EnumWindows walks through."""
        super().__init__()
        self.windows, self.fail = windows, fail

    def EnumWindows(self, callback, parameter) -> int:  # noqa: N802
        """Call back for every window, as Windows does (stop when the callback says so)."""
        if self.fail:
            return 0
        for window in self.windows:
            if not callback(window, parameter):
                break
        return 1

    def IsWindowVisible(self, window: int) -> bool:  # noqa: N802
        """Return the scripted visibility."""
        return self.windows[window][1]

    def GetWindowTextLengthW(self, window: int) -> int:  # noqa: N802
        """Return the length of the window's title."""
        return len(self.windows[window][0])

    def GetWindowTextW(self, window: int, buffer, size: int) -> int:  # noqa: N802
        """Copy the window's title into the buffer."""
        buffer.value = self.windows[window][0][:size - 1]
        return len(buffer.value)


def test_window_titles_lists_the_visible_top_level_windows() -> None:
    """Titles of visible windows; a failing EnumWindows gives None (can't tell), not an empty list."""
    user32 = ListingUser32({1: ("EVE - Kira", True), 2: ("EVE - Hidden", False), 3: ("EVE", True), 4: ("", True),
                            5: ("EVE - Bob", True)})
    assert windows_module.window_titles(user32) == ["EVE - Kira", "EVE", "EVE - Bob"]
    assert windows_module.window_titles(ListingUser32({}, fail=True)) is None
    assert windows_module.window_titles(ListingUser32({})) == []


def test_windows_backend_lists_open_clients(tracker) -> None:
    """open_eve_characters names the open clients; no user32 or an error is None."""
    backend = windows_module.WindowsFocus(tracker, ListingUser32({1: ("EVE - Kira", True), 2: ("Notepad", True)}))
    assert backend.open_eve_characters() == {"Kira"}
    assert windows_module.WindowsFocus(tracker, ListingUser32({}, fail=True)).open_eve_characters() is None
    backend.user32 = None
    assert backend.open_eve_characters() is None


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

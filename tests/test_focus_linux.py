"""Linux focus tracking: the KWin script over D-Bus, xprop polling and KWin script management."""
import subprocess
import sys
from types import SimpleNamespace
from typing import NoReturn

import pytest
from PySide6.QtCore import QRect

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="KWin, D-Bus and xprop are Linux only")

if sys.platform == "linux":
    import homebound.platform.focus_linux as linux_module
from homebound.ui.focus import FocusTracker


def spy(signal) -> list:
    """Collect the arguments of every emission of a signal."""
    received: list = []
    signal.connect(lambda *arguments: received.append(arguments))
    return received


class FakeBus:
    """A session bus that is not connected, so nothing is registered."""

    @staticmethod
    def isConnected() -> bool:
        """Report no connection."""
        return False


class FakeBusConnection:
    """Stands in for QDBusConnection in the focus module."""

    @staticmethod
    def sessionBus() -> FakeBus:
        """Return a bus that is not connected."""
        return FakeBus()


class ConnectedBus:
    """A bus that accepts every registration and remembers it."""

    def __init__(self) -> None:
        """Start with no registrations."""
        self.services, self.objects = [], []

    def isConnected(self) -> bool:
        """Report a connection."""
        return True

    def registerService(self, name: str) -> bool:
        """Remember the service name and succeed."""
        self.services.append(name)
        return True

    def registerObject(self, path: str, target: object, option: object) -> bool:
        """Remember the object path and target and succeed."""
        self.objects.append((path, target))
        return True


@pytest.fixture
def tracker(qt_application, monkeypatch) -> FocusTracker:
    """A FocusTracker with the Linux backend that never touches the real session bus."""
    monkeypatch.setattr(FakeBusConnection, "RegisterOption", linux_module.QDBusConnection.RegisterOption, raising=False)
    monkeypatch.setattr(linux_module, "QDBusConnection", FakeBusConnection)
    return FocusTracker()


@pytest.fixture
def backend(tracker) -> linux_module.LinuxFocus:
    """The tracker's Linux backend."""
    return tracker.backend




def test_is_own_window() -> None:
    """Our window class or caption keeps the previous focus state."""
    assert linux_module.is_own_window("python3") and linux_module.is_own_window("Homebound")
    assert linux_module.is_own_window(None, "Homebound - Map")
    assert not linux_module.is_own_window("exefile.exe", "EVE - A") and not linux_module.is_own_window(None)


def test_tracker_without_a_session_bus(tracker, backend) -> None:
    """A missing bus only disables the KWin path."""
    assert not backend.dbus_ok and tracker.character is None


def test_tracker_registers_on_the_bus(qt_application, monkeypatch) -> None:
    """With a bus the service and the adaptor object are registered."""
    bus = ConnectedBus()
    monkeypatch.setattr(linux_module, "QDBusConnection", SimpleNamespace(
        sessionBus=lambda: bus, RegisterOption=linux_module.QDBusConnection.RegisterOption))
    created = FocusTracker().backend
    assert created.dbus_ok and bus.services == [linux_module.SERVICE]
    assert bus.objects == [(linux_module.OBJECT_PATH, created._adaptor)]


def test_adaptor_forwards_activation(tracker, backend) -> None:
    """The D-Bus slot hands the window to the tracker."""
    changes = spy(tracker.focusChanged)
    backend._adaptor.Activated("EVE - Pilot", "exefile", 1, 2, 300, 200)
    assert changes == [("Pilot", QRect(1, 2, 300, 200))]
    assert backend._kwin_seen


def test_activated_ignores_own_windows_and_stops_polling(tracker, backend) -> None:
    """The first KWin report ends X11 polling; our own windows change nothing."""
    statuses = spy(tracker.status)
    backend._x11_timer.start()
    backend._activated("EVE - Pilot", "exefile", QRect(0, 0, 5, 5))
    assert not backend._x11_timer.isActive() and statuses == [("ok", "KWin focus script active")]
    assert tracker.character == "Pilot" and tracker.rect == QRect(0, 0, 5, 5)
    backend._activated("Homebound", "homebound", QRect(0, 0, 1, 1))
    assert tracker.character == "Pilot" and tracker.rect == QRect(0, 0, 5, 5)
    backend._activated("Firefox", "firefox", QRect(0, 0, 7, 7))
    assert tracker.character is None and tracker.rect == QRect(0, 0, 7, 7)
    backend._activated("EVE", "exefile", QRect(0, 0, 5, 5))
    assert tracker.character == ""
    assert len(statuses) == 1




def test_apply_x11_properties(tracker, backend) -> None:
    """xprop output gives the caption; our own window class is ignored."""
    backend._apply_x11_properties('_NET_WM_NAME(UTF8_STRING) = "EVE - Pilot"\nWM_CLASS(STRING) = "exefile.exe", "steam_app_8500"')
    assert tracker.character == "Pilot"
    backend._apply_x11_properties('_NET_WM_NAME(UTF8_STRING) = "Homebound"\nWM_CLASS(STRING) = "homebound", "Homebound"')
    assert tracker.character == "Pilot"
    backend._apply_x11_properties("")
    assert tracker.character is None


def test_poll_x11(tracker, backend, monkeypatch) -> None:
    """The active window id is looked up with xprop; failures change nothing."""
    outputs = iter([" _NET_ACTIVE_WINDOW(WINDOW): window id # 0x4a00007",
                    '_NET_WM_NAME(UTF8_STRING) = "EVE - Pilot"\nWM_CLASS(STRING) = "exefile", "x"'])
    commands = []

    def fake_run(command: list, **options: object) -> SimpleNamespace:
        """Record the command and answer with the next scripted output."""
        commands.append(command)
        return SimpleNamespace(stdout=next(outputs))

    monkeypatch.setattr(linux_module.subprocess, "run", fake_run)
    backend._poll_x11()
    assert tracker.character == "Pilot" and commands[1][:3] == ["xprop", "-id", "0x4a00007"]
    monkeypatch.setattr(linux_module.subprocess, "run", lambda *arguments, **options: SimpleNamespace(stdout="x: 0x0"))
    backend._poll_x11()
    assert tracker.character is None
    monkeypatch.setattr(linux_module.subprocess, "run", lambda *arguments, **options: SimpleNamespace(stdout=""))
    tracker._set("Keep", QRect())
    backend._poll_x11()  # empty output: IndexError, ignored
    assert tracker.character == "Keep"

    def failing(*arguments: object, **options: object) -> NoReturn:
        """Fail as if xprop were not installed."""
        raise FileNotFoundError("xprop")

    monkeypatch.setattr(linux_module.subprocess, "run", failing)
    backend._poll_x11()
    assert tracker.character == "Keep"
    monkeypatch.setattr(linux_module.subprocess, "run", lambda *arguments, **options: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("xprop", 1)))
    backend._poll_x11()
    assert tracker.character == "Keep"


@pytest.mark.parametrize("script, dbus_ok, xprop, polling, level, text", [
    ("active", True, "/usr/bin/xprop", False, "ok", "KWin focus script active"),
    ("missing", True, "/usr/bin/xprop", True, "warn", "KWin focus script not installed · using X11 fallback"),
    ("inactive", True, "/usr/bin/xprop", True, "warn", "KWin script installed but not enabled · using X11 fallback"),
    ("inactive", True, None, False, "warn", "Focus tracking unavailable: no KWin script and no xprop"),
    ("unavailable", True, "/usr/bin/xprop", True, "warn", "Focus: X11 fallback"),
    ("active", False, "/usr/bin/xprop", True, "warn", "Focus: X11 fallback"),
])
def test_tracker_start(tracker, backend, monkeypatch, script, dbus_ok, xprop, polling, level, text) -> None:
    """start() trusts the KWin script when it is active, else falls back to polling."""
    monkeypatch.setattr(linux_module, "kwin_script_status", lambda: script)
    monkeypatch.setattr(linux_module.shutil, "which", lambda name: xprop)
    backend.dbus_ok = dbus_ok
    statuses = spy(tracker.status)
    assert tracker.start() == script
    assert statuses == [(level, text)] and backend._x11_timer.isActive() == polling
    backend._x11_timer.stop()


class CommandRecorder:
    """Replaces _run_command: records calls and answers from a script."""

    def __init__(self, answers: dict | None = None) -> None:
        """Answer commands containing a key of ``answers`` with its values; anything else succeeds silently."""
        self.calls: list[tuple] = []
        self.answers = answers or {}

    def __call__(self, *command: str) -> SimpleNamespace:
        """Record the command and return the first matching scripted result."""
        self.calls.append(command)
        for part, answer in self.answers.items():
            if part in command:
                return SimpleNamespace(**{"returncode": 0, "stdout": "", "stderr": "", **answer})
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def test_kwin_script_status(monkeypatch) -> None:
    """The status comes from kpackagetool6's list and kreadconfig6's flag."""
    monkeypatch.setattr(linux_module.shutil, "which", lambda name: None)
    assert linux_module.kwin_script_status() == "unavailable"
    monkeypatch.setattr(linux_module.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(linux_module, "_run_command", CommandRecorder({"--list": {"stdout": "other"}}))
    assert linux_module.kwin_script_status() == "missing"
    listed = {"--list": {"stdout": f"x\n{linux_module.SCRIPT_ID}\n"}}
    monkeypatch.setattr(linux_module, "_run_command", CommandRecorder({**listed, "kreadconfig6": {"stdout": "true\n"}}))
    assert linux_module.kwin_script_status() == "active"
    monkeypatch.setattr(linux_module, "_run_command", CommandRecorder({**listed, "kreadconfig6": {"stdout": "false"}}))
    assert linux_module.kwin_script_status() == "inactive"


def test_install_enable_remove_kwin_script(monkeypatch) -> None:
    """Install falls back to upgrade, then enables; failures report the error text."""
    recorder = CommandRecorder({"--install": {"returncode": 1, "stderr": "exists"}})
    monkeypatch.setattr(linux_module, "_run_command", recorder)
    assert linux_module.install_kwin_script() == (True, "")
    commands = [call[0] + " " + " ".join(call[1:3]) for call in recorder.calls]
    assert any("--install" in call for call in recorder.calls) and any("--upgrade" in call for call in recorder.calls)
    assert commands[-1].startswith("qdbus6") and any("kwriteconfig6" in command for command in commands)
    assert any("true" in call for call in recorder.calls if call[0] == "kwriteconfig6")

    failing = CommandRecorder({"--install": {"returncode": 1, "stderr": "x"}, "--upgrade": {"returncode": 2, "stderr": "boom"}})
    monkeypatch.setattr(linux_module, "_run_command", failing)
    assert linux_module.install_kwin_script() == (False, "boom")
    monkeypatch.setattr(linux_module, "_run_command", CommandRecorder(
        {"--install": {"returncode": 1}, "--upgrade": {"returncode": 2, "stdout": "from stdout"}}))
    assert linux_module.install_kwin_script() == (False, "from stdout")

    monkeypatch.setattr(linux_module, "_run_command", CommandRecorder({"reconfigure": {"returncode": 1, "stderr": " no kwin "}}))
    assert linux_module.enable_kwin_script() == (False, "no kwin")

    recorder = CommandRecorder()
    monkeypatch.setattr(linux_module, "_run_command", recorder)
    assert linux_module.remove_kwin_script() == (True, "")
    assert any("false" in call for call in recorder.calls if call[0] == "kwriteconfig6")
    assert any("--remove" in call for call in recorder.calls)
    monkeypatch.setattr(linux_module, "_run_command", CommandRecorder({"--remove": {"returncode": 1, "stderr": "gone"}}))
    assert linux_module.remove_kwin_script() == (False, "gone")


def test_script_directory_and_run_command(monkeypatch) -> None:
    """The bundled script package exists; _run_command passes the command through to subprocess."""
    assert (linux_module._script_directory() / "metadata.json").is_file()
    seen = []
    monkeypatch.setattr(linux_module.subprocess, "run", lambda command, **options: seen.append((command, options)) or "done")
    assert linux_module._run_command("echo", "hi") == "done"
    assert seen[0][0] == ("echo", "hi") and seen[0][1]["timeout"] == linux_module.COMMAND_TIMEOUT_SECONDS


def test_run_command_failures_are_a_failed_result(monkeypatch) -> None:
    """A missing tool or a timeout gives a failed result with the reason, not an exception."""
    def missing(command, **options):
        """Simulate a tool that isn't installed."""
        raise FileNotFoundError("qdbus6")

    def slow(command, **options):
        """Simulate a command that runs past its timeout."""
        raise linux_module.subprocess.TimeoutExpired(command, linux_module.COMMAND_TIMEOUT_SECONDS)

    monkeypatch.setattr(linux_module.subprocess, "run", missing)
    result = linux_module._run_command("qdbus6", "org.kde.KWin")
    assert result.returncode == 1 and result.stdout == "" and "qdbus6" in result.stderr
    monkeypatch.setattr(linux_module.subprocess, "run", slow)
    result = linux_module._run_command("qdbus6", "org.kde.KWin")
    assert result.returncode == 1 and result.stdout == "" and "timed out" in result.stderr


def test_kwin_available(monkeypatch) -> None:
    """KWin scripts are managed only where kpackagetool6 exists."""
    monkeypatch.setattr(linux_module.shutil, "which", lambda name: None)
    assert linux_module.kwin_available() is False
    monkeypatch.setattr(linux_module.shutil, "which", lambda name: "/usr/bin/" + name)
    assert linux_module.kwin_available() is True

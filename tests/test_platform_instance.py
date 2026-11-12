"""The single-instance guard: lock file, show request, stale lock, separate folders."""
import subprocess
import sys
import time

import pytest
from PySide6.QtCore import QCoreApplication

from homebound.platform.instance import LOCK_FILE, acquire_single_instance, server_name


@pytest.fixture
def guards(qt_application):
    """Collect guards so each one is released after the test."""
    held = []
    yield held
    for guard in held:
        guard.release()


def wait_for(condition, seconds: float = 5.0) -> bool:
    """Run the event loop until ``condition()`` holds or the time is up."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def test_second_guard_is_refused_and_the_first_is_told_to_show(tmp_path, guards) -> None:
    """The second start gets None; its ``show`` reaches the running one."""
    first = acquire_single_instance(tmp_path)
    assert first is not None
    guards.append(first)
    shown = []
    first.showRequested.connect(lambda: shown.append(True))
    assert acquire_single_instance(tmp_path) is None
    assert wait_for(lambda: shown)
    assert shown == [True]


def test_release_frees_the_folder(tmp_path, guards) -> None:
    """After release a new start gets the folder."""
    acquire_single_instance(tmp_path).release()
    second = acquire_single_instance(tmp_path)
    assert second is not None
    guards.append(second)


def test_a_lock_of_a_dead_process_is_taken_over(tmp_path, guards) -> None:
    """A lock file left by a process that died without unlocking does not block the next start."""
    script = ("import os, sys\nfrom PySide6.QtCore import QLockFile\n"
              "lock = QLockFile(sys.argv[1]); lock.setStaleLockTime(0)\n"
              "assert lock.tryLock(0)\nos._exit(0)\n")
    subprocess.run([sys.executable, "-c", script, str(tmp_path / LOCK_FILE)], check=True, timeout=60)
    assert (tmp_path / LOCK_FILE).exists()
    guard = acquire_single_instance(tmp_path)
    assert guard is not None
    guards.append(guard)


def test_different_folders_do_not_block_each_other(tmp_path, guards) -> None:
    """Each data folder has its own lock and socket."""
    one, two = tmp_path / "one", tmp_path / "two"
    assert server_name(one) != server_name(two)
    first = acquire_single_instance(one)
    second = acquire_single_instance(two)
    guards.extend([first, second])
    assert first is not None and second is not None

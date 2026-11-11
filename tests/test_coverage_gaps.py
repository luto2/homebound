"""Edge cases that the main test files don't reach: vanished logs, quiet replay, missing Steam files and more."""
import runpy
import sys
import threading
import warnings
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conftest import HOME, UNIVERSE
from homebound import zkill
from homebound.logs import replay
from homebound.logs.parser import LogFile
from homebound.logs.watcher import LogScanner
from homebound.theme import tokens
from homebound.universe.db import WormholeType
import homebound.settings as settings_module

HEADER = """
          Channel ID:      local
          Channel Name:    Local
          Listener:        Ijomo Ohaya
          Session started: 2026.10.28 16:51:48
        ---------------------------------------------------------------

"""


def log_line(timestamp: str, system_name: str) -> str:
    """One "Channel changed" line as the client writes it."""
    return f"﻿[ {timestamp} ] EVE System > Channel changed to Local : {system_name}\n"


def write_log(path: Path, body: str) -> None:
    """Write a log file in the client's encoding (UTF-16 LE with a byte order mark)."""
    path.write_bytes(b"\xff\xfe" + body.encode("utf-16-le"))


def test_scanner_survives_a_log_that_vanishes_between_listing_and_reading(tmp_path, monkeypatch) -> None:
    """A log deleted after the folder listing yields no changes, no error, and keeps the recent state."""
    path = tmp_path / "Local_20261028_165148_7.txt"
    write_log(path, HEADER + log_line("2026.10.28 16:51:53", "Jita"))
    scanner = LogScanner(tmp_path)
    first = scanner.poll(now=datetime.now(timezone.utc))
    assert [change.system for change in first.changes] == ["Jita"] and scanner.characters[7].recent
    listing = scanner.newest_files()
    path.unlink()
    monkeypatch.setattr(scanner, "newest_files", lambda: listing)  # the listing still has the file
    result = scanner.poll(now=datetime.now(timezone.utc))
    assert result.changes == [] and not result.characters_changed
    assert scanner.characters[7].recent and scanner.characters[7].system == "Jita"


def test_scanner_registers_a_character_whose_first_log_cannot_be_read(tmp_path, monkeypatch) -> None:
    """A character whose only log can't be opened is still listed, offline, with no system."""
    missing = LogFile(tmp_path / "Local_20261028_165148_9.txt", 9, datetime(2026, 10, 28, 16, 51, 48, tzinfo=timezone.utc))
    scanner = LogScanner(tmp_path)
    monkeypatch.setattr(scanner, "newest_files", lambda: {9: missing})
    result = scanner.poll(now=datetime(2026, 10, 28, 17, tzinfo=timezone.utc))
    assert result.changes == [] and result.characters_changed
    state = scanner.characters[9]
    assert state.file == missing and state.offset == 0 and state.system is None
    assert not state.recent and state.last_written is None


def test_replay_quiet_prints_only_the_summary_and_tree(tmp_path, capsys) -> None:
    """With --quiet, naming and unknown-jump lines are left out but the summary and tree are printed."""
    c5 = next(system for system in UNIVERSE.all_systems() if system.system_class == "C5" and system.id != HOME.id)
    log = tmp_path / "Local_20261028_165148_7.txt"
    write_log(log, HEADER + log_line("2026.10.28 16:51:53", HOME.name) + log_line("2026.10.28 16:52:53", c5.name)
              + log_line("2026.10.28 16:53:53", "Jita") + log_line("2026.10.28 16:54:53", "Amarr"))
    assert replay.main([str(log), "--home", HOME.name, "--quiet"], universe=UNIVERSE) == 0
    output = capsys.readouterr().out
    assert " new " not in output and "unknown jump" not in output
    assert "4 system changes, 3 systems, 2 connections" in output
    assert "Home" in output and c5.name in output


def test_replay_runs_as_a_module(tmp_path, monkeypatch, capsys) -> None:
    """``python -m homebound.logs.replay`` exits with main's code (2 for an unknown home)."""
    log = tmp_path / "Local_20261028_165148_7.txt"
    write_log(log, HEADER)
    monkeypatch.setattr(sys, "argv", ["replay", str(log), "--home", "Nowhere"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # the module is already imported by this test file
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_module("homebound.logs.replay", run_name="__main__")
    assert exit_info.value.code == 2
    assert "unknown home system: Nowhere" in capsys.readouterr().err


def test_steam_root_without_libraryfolders_is_still_a_library(tmp_path, monkeypatch) -> None:
    """A Steam folder without steamapps/libraryfolders.vdf counts as a library on its own."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    steam = tmp_path / ".steam/steam"
    steam.mkdir(parents=True)
    assert settings_module.steam_libraries() == [steam.resolve()]


def test_fetcher_thread_starts_and_stops(tmp_path, monkeypatch) -> None:
    """start() runs the worker thread, which fetches queued systems; stop() ends it."""
    monkeypatch.setattr(zkill, "REQUEST_INTERVAL", 0)
    fetched: list[int] = []
    fetch_done = threading.Event()

    def fake_fetch(self, client, store, system_id: int, force: bool = False) -> bool:
        """Record the fetch on the worker thread and signal the test."""
        fetched.append(system_id)
        fetch_done.set()
        return True

    monkeypatch.setattr(zkill.ZkillFetcher, "_fetch", fake_fetch)
    fetcher = zkill.ZkillFetcher(str(tmp_path / "map.db"), lambda system_id: None)
    fetcher.start()
    assert fetcher.thread.is_alive()
    fetcher.request([HOME.id], force=True)
    assert fetch_done.wait(timeout=5)
    fetcher.stop()
    fetcher.thread.join(timeout=5)
    assert not fetcher.thread.is_alive() and fetched == [HOME.id]


def test_wormhole_types_fall_back_to_spawn_data(monkeypatch) -> None:
    """Types whtype.info doesn't know use Wanderer's spawn classes, but foreign statics are left out."""
    home_class = HOME.system_class
    wandering = WormholeType("Z001", ("C1",), 16, None, None, False, spawns_in=(home_class,))
    other_static = WormholeType("Z002", ("C1",), 16, None, None, True, spawns_in=(home_class,))
    elsewhere = WormholeType("Z003", ("C1",), 16, None, None, False, spawns_in=("C1" if home_class != "C1" else "C2",))
    for wormhole_type in (wandering, other_static, elsewhere):
        monkeypatch.setitem(UNIVERSE._types, wormhole_type.name, wormhole_type)
    names = [wormhole_type.name for wormhole_type in UNIVERSE.wormhole_types_in(HOME.id)]
    assert "Z001" in names and "Z002" not in names and "Z003" not in names


def test_colour_variants_pick_the_theme_and_fall_back_to_the_first(monkeypatch) -> None:
    """A light/dark token value resolves to the current theme's variant, or the first one if it has none."""
    monkeypatch.setitem(tokens.RAW_COLORS, "test-base", "#123456")
    variants = {"light": "#ffffff", "dark": "{test-base}"}
    assert tokens._resolve(variants) == "#123456"
    monkeypatch.setattr(tokens, "THEME", "high-contrast")
    assert tokens._resolve(variants) == "#ffffff"

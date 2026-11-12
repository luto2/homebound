"""Local log parsing, tailing, the Qt watcher and the replay tool (synthetic logs only)."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import HOME, UNIVERSE, write_log
from homebound.logs import replay
from homebound.logs.parser import (SYSTEM_SENDERS, LogFile, decode_log_bytes, parse_header, parse_log_file,
                                   parse_log_text, parse_system_change, parse_timestamp, unlisted_system_sender)
from homebound.logs.watcher import RECENT_WINDOW, LogScanner, LogWatcher

HEADER = """


        ---------------------------------------------------------------

          Channel ID:      local
          Channel Name:    Local
          Listener:        Ijomo Ohaya
          Session started: 2026.10.28 16:51:48
        ---------------------------------------------------------------

"""


def line(timestamp: str, message: str, sender: str = "EVE System") -> str:
    """One log line as the client writes it."""
    return f"﻿[ {timestamp} ] {sender} > {message}\n"


def test_parse_system_change() -> None:
    """Channel changes are found, other lines and senders are not; a trailing * is dropped."""
    assert parse_system_change(line("2026.10.28 16:51:53", "Channel changed to Local : J161940")) == (
        datetime(2026, 10, 28, 16, 51, 53, tzinfo=timezone.utc), "J161940")
    assert parse_system_change(line("2026.10.28 16:51:53", "o7", sender="Some Pilot")) is None
    assert parse_system_change(line("2026.10.28 16:51:53", "Channel changed to Local : Jita*"))[1] == "Jita"
    assert parse_system_change(line("2026.10.28 16:51:53", "Kanal gewechselt zu Lokal (local) : Amarr"))[1] == "Amarr"
    assert parse_system_change(line("2026.10.28 16:51:53", "Something else")) is None
    assert parse_system_change("no timestamp here") is None
    with pytest.raises(TypeError):
        parse_system_change(None)


def test_only_listed_system_senders_count() -> None:
    """A player named "EVE ..." who types the system text is not the system; every listed name is."""
    stamp = "2026.10.28 16:51:53"
    assert parse_system_change(line(stamp, "anyone in local : Amarr", sender="EVE Fan")) is None
    assert parse_system_change(line(stamp, "Channel changed to Local : Amarr", sender="EVE Fan")) is None
    assert unlisted_system_sender(line(stamp, "anyone in local : Amarr", sender="EVE Fan")) == "EVE Fan"
    assert unlisted_system_sender(line(stamp, "Channel changed to Local : Amarr", sender="EVE Fan")) == "EVE Fan"
    assert unlisted_system_sender(line(stamp, "o7", sender="EVE Fan")) is None
    assert unlisted_system_sender(line(stamp, "Channel changed to Local : Amarr")) is None
    assert unlisted_system_sender(line(stamp, "Channel changed to Local : Amarr", sender="Some Pilot")) is None
    for name in sorted(SYSTEM_SENDERS):
        assert parse_system_change(line(stamp, "Channel changed to Local : Jita", sender=name))[1] == "Jita"
        assert unlisted_system_sender(line(stamp, "Channel changed to Local : Jita", sender=name)) is None


def test_system_sender_spacing_variants_match() -> None:
    """Two spaces, a no-break space or a padded name still give the system; the loose message match stays."""
    stamp = "2026.10.28 16:51:53"
    assert parse_system_change(line(stamp, "Channel changed to Local : Jita", sender="EVE  System"))[1] == "Jita"
    assert parse_system_change(line(stamp, "Channel changed to Local : Jita", sender="EVE\u00a0System"))[1] == "Jita"
    assert parse_system_change(line(stamp, "Channel changed to Local : Jita", sender="EVE システム"))[1] == "Jita"
    assert parse_system_change(line(stamp, "Kanal gewechselt zu Lokal (local) : Amarr", sender="EVE-System"))[1] \
        == "Amarr"
    assert parse_system_change(line(stamp, "Something else : Amarr")) is None


def test_timestamps_and_decoding() -> None:
    """Timestamps are UTC; decoding drops the BOM and a half character."""
    assert parse_timestamp("2026.10.28 16:51:53").tzinfo is timezone.utc
    with pytest.raises(ValueError):
        parse_timestamp("yesterday")
    assert decode_log_bytes(b"\xff\xfe" + "Jita".encode("utf-16-le") + b"\x00") == "Jita"
    with pytest.raises(TypeError):
        decode_log_bytes("Jita")


def test_parse_text_header_and_first() -> None:
    """The header names the character; only the first change is marked first."""
    text = HEADER + line("2026.10.28 16:51:53", "Channel changed to Local : J161940") \
        + line("2026.10.28 16:55:00", "hello", sender="Pilot") \
        + line("2026.10.28 16:58:00", "Channel changed to Local : J100551")
    changes = parse_log_text(text, 42)
    assert [(change.system, change.first, change.character_name) for change in changes] == [
        ("J161940", True, "Ijomo Ohaya"), ("J100551", False, "Ijomo Ohaya")]
    assert parse_header(HEADER)[1] == datetime(2026, 10, 28, 16, 51, 48, tzinfo=timezone.utc)
    assert parse_log_text(line("2026.10.28 16:51:53", "Channel changed to Local : Jita"), 7)[0].character_name == "7"
    with pytest.raises(ValueError):
        parse_log_text(text, -1)


def test_log_file_name() -> None:
    """Local log names give the character and start time; other channels give None."""
    log_file = LogFile.from_path(Path("Local_20261028_165148_2122853402.txt"))
    assert log_file.character_id == 2122853402 and log_file.started.hour == 16
    assert LogFile.from_path(Path("Corp_20261028_165148_1.txt")) is None
    # digits that are not a date: month 13, day 32, hour 24 (no ValueError, just None)
    assert LogFile.from_path(Path("Local_20261399_000000_1.txt")) is None
    assert LogFile.from_path(Path("Local_20261028_240000_1.txt")) is None
    assert LogFile.from_path(Path("Local_20261232_000000_1.txt")) is None
    with pytest.raises(TypeError):
        LogFile.from_path("Local_20261028_165148_1.txt")


def test_parse_log_file(tmp_path) -> None:
    """A whole file is parsed; non-log files give nothing."""
    path = tmp_path / "Local_20261028_165148_7.txt"
    write_log(path, HEADER + line("2026.10.28 16:51:53", "Channel changed to Local : Jita"))
    assert [change.system for change in parse_log_file(path)] == ["Jita"]
    assert parse_log_file(tmp_path / "notes.txt") == []
    bad_name = tmp_path / "Local_20261399_000000_1.txt"
    write_log(bad_name, HEADER + line("2026.10.28 16:51:53", "Channel changed to Local : Jita"))
    assert parse_log_file(bad_name) == []


def test_scanner_tail(tmp_path) -> None:
    """Existing history collapses to the last location; new lines are jumps."""
    path = tmp_path / "Local_20261028_165148_7.txt"
    write_log(path, HEADER + line("2026.10.28 16:51:53", "Channel changed to Local : Jita")
              + line("2026.10.28 16:52:53", "Channel changed to Local : Perimeter"))
    scanner = LogScanner(tmp_path)
    result = scanner.poll(now=datetime(2026, 10, 28, 17, tzinfo=timezone.utc))
    assert [(change.system, change.first) for change in result.changes] == [("Perimeter", True)]
    assert scanner.characters[7].name == "Ijomo Ohaya" and result.characters_changed
    with open(path, "ab") as log:
        log.write(line("2026.10.28 17:00:00", "Channel changed to Local : J161940").encode("utf-16-le"))
        log.write(line("2026.10.28 17:00:01", "Channel changed to Local : J161940").encode("utf-16-le"))
    result = scanner.poll()
    assert [(change.system, change.first) for change in result.changes] == [("J161940", False)]
    assert scanner.poll().changes == []
    with pytest.raises(TypeError):
        scanner.poll(now="now")


def test_scanner_keeps_when_each_log_was_last_written(tmp_path) -> None:
    """last_written is the newer of the file time and the session start; "recent" means the last 30 minutes."""
    import os
    path = tmp_path / "Local_20261028_165148_7.txt"
    write_log(path, HEADER + line("2026.10.28 16:51:53", "Channel changed to Local : Jita"))
    written = datetime(2026, 10, 28, 17, 0, tzinfo=timezone.utc)
    os.utime(path, (written.timestamp(), written.timestamp()))
    scanner = LogScanner(tmp_path)
    result = scanner.poll(now=written + RECENT_WINDOW / 2)
    character = scanner.characters[7]
    assert character.last_written == written and character.recent and result.characters_changed
    assert not scanner.poll(now=written + RECENT_WINDOW / 2).characters_changed
    result = scanner.poll(now=written + RECENT_WINDOW * 2)
    assert not character.recent and result.characters_changed      # the change from recent to quiet is news
    assert character.last_written == written
    assert RECENT_WINDOW == timedelta(minutes=30)


def test_scanner_skips_a_bad_file_name(tmp_path) -> None:
    """A log whose name has digits that are no date is skipped; the other characters still track."""
    path = tmp_path / "Local_20261028_165148_7.txt"
    write_log(path, HEADER)
    write_log(tmp_path / "Local_20261399_000000_1.txt", HEADER)
    scanner = LogScanner(tmp_path)
    assert set(scanner.newest_files()) == {7}
    scanner.poll()
    with open(path, "ab") as log:
        log.write(line("2026.10.28 17:00:00", "Channel changed to Local : J161940").encode("utf-16-le"))
    assert [change.system for change in scanner.poll().changes] == ["J161940"]


def test_scanner_partial_write(tmp_path) -> None:
    """A half-written line waits for the rest."""
    path = tmp_path / "Local_20261028_165148_7.txt"
    write_log(path, HEADER)
    scanner = LogScanner(tmp_path)
    scanner.poll()
    data = line("2026.10.28 17:00:00", "Channel changed to Local : J161940").encode("utf-16-le")
    with open(path, "ab") as log:
        log.write(data[:15])           # odd byte count, mid-line
    assert scanner.poll().changes == []
    with open(path, "ab") as log:
        log.write(data[15:])
    assert [change.system for change in scanner.poll().changes] == ["J161940"]


def test_new_session_file(tmp_path) -> None:
    """A newer file of the same character starts a new session."""
    write_log(tmp_path / "Local_20261028_165148_7.txt",
              HEADER + line("2026.10.28 16:51:53", "Channel changed to Local : Jita"))
    scanner = LogScanner(tmp_path)
    scanner.poll()
    write_log(tmp_path / "Local_20261029_100000_7.txt",
              HEADER + line("2026.10.29 10:00:01", "Channel changed to Local : Amarr"))
    assert [(change.system, change.first) for change in scanner.poll().changes] == [("Amarr", True)]


def test_rewritten_shorter_file_is_read_from_the_top(tmp_path) -> None:
    """A log rewritten under the same name with less data is read again: header and last location, no jumps."""
    path = tmp_path / "Local_20261028_165148_7.txt"
    write_log(path, HEADER + line("2026.10.28 16:51:53", "Channel changed to Local : Jita")
              + line("2026.10.28 16:52:53", "Channel changed to Local : Perimeter")
              + line("2026.10.28 16:53:53", "Channel changed to Local : Amarr"))
    scanner = LogScanner(tmp_path)
    scanner.poll()
    write_log(path, HEADER.replace("Ijomo Ohaya", "Other Pilot")
              + line("2026.10.28 18:00:00", "Channel changed to Local : J161940")
              + line("2026.10.28 18:01:00", "Channel changed to Local : J100551"))
    assert path.stat().st_size < scanner.characters[7].offset
    changes = scanner.poll().changes
    assert [(change.system, change.first, change.character_name) for change in changes] == [
        ("J100551", True, "Other Pilot")]
    with open(path, "ab") as log:
        log.write(line("2026.10.28 18:02:00", "Channel changed to Local : Jita").encode("utf-16-le"))
    assert [(change.system, change.first) for change in scanner.poll().changes] == [("Jita", False)]


def test_scanner_without_a_folder(tmp_path) -> None:
    """A missing folder is not valid and finds nothing."""
    scanner = LogScanner(tmp_path / "missing")
    assert not scanner.valid() and scanner.newest_files() == {} and scanner.poll().changes == []
    with pytest.raises(TypeError):
        LogScanner(None)


def test_watcher_emits_signals(tmp_path, qt_application) -> None:
    """The Qt watcher re-emits changes, character updates and a status line."""
    write_log(tmp_path / "Local_20261028_165148_7.txt",
              HEADER + line("2026.10.28 16:51:53", "Channel changed to Local : Jita"))
    watcher = LogWatcher(str(tmp_path / "missing"), interval_ms=60000)
    changes, statuses, updates = [], [], []
    watcher.systemChanged.connect(changes.append)
    watcher.statusChanged.connect(lambda level, text: statuses.append(level))
    watcher.charactersChanged.connect(lambda: updates.append(True))
    assert watcher.poll() is None and statuses == ["err"]
    watcher.set_log_directory(str(tmp_path))
    assert [change.system for change in changes] == ["Jita"] and updates and statuses[-1] == "ok"
    assert 7 in watcher.characters
    watcher.start()
    assert watcher.timer.isActive()
    watcher.timer.stop()
    with pytest.raises(ValueError):
        LogWatcher(str(tmp_path), interval_ms=0)


def test_watcher_warns_once_about_an_unlisted_system_sender(tmp_path, caplog) -> None:
    """An EVE-prefixed sender that looks like the system is ignored, logged and warned about once per session."""
    import logging
    write_log(tmp_path / "Local_20261028_165148_7.txt", HEADER
              + line("2026.10.28 16:51:53", "Channel changed to Local : Jita", sender="EVE Fan")
              + line("2026.10.28 16:52:53", "Channel changed to Local : Amarr", sender="Système EVE"))
    watcher = LogWatcher(str(tmp_path), interval_ms=60000)
    changes, notices = [], []
    watcher.systemChanged.connect(changes.append)
    watcher.notice.connect(lambda level, text: notices.append((level, text)))
    with caplog.at_level(logging.WARNING, logger="homebound.logs"):
        watcher.poll()
        with open(tmp_path / "Local_20261028_165148_7.txt", "ab") as log:
            log.write(line("2026.10.28 17:00:00", "Channel changed to Local : Jita", sender="EVE Fan")
                      .encode("utf-16-le"))
        watcher.poll()
    assert [change.system for change in changes] == ["Amarr"]      # the listed sender still counts
    assert len(notices) == 1 and notices[0][0] == "warn" and "EVE Fan" in notices[0][1]
    assert "English" in notices[0][1]
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
    assert "EVE Fan" in caplog.records[0].getMessage()


def test_replay_prints_jumps_and_the_tree(tmp_path, capsys) -> None:
    """Replay answers questions with the guess and prints the tree."""
    c5 = next(system for system in UNIVERSE.all_systems() if system.system_class == "C5" and system.id != HOME.id)
    log = tmp_path / "Local_20261028_165148_7.txt"
    write_log(log, HEADER + line("2026.10.28 16:51:53", f"Channel changed to Local : {HOME.name}")
              + line("2026.10.28 16:52:53", f"Channel changed to Local : {c5.name}")
              + line("2026.10.28 16:53:53", "Channel changed to Local : Jita")
              + line("2026.10.28 16:54:53", "Channel changed to Local : Amarr"))
    assert replay.main([str(log), "--home", HOME.name], universe=UNIVERSE) == 0
    output = capsys.readouterr().out
    assert f"new {c5.name} -> 1" in output and "unknown jump Jita -> Amarr" in output
    assert "4 system changes, 3 systems, 2 connections" in output and "Home" in output
    assert replay.main([str(log), "--quiet"], universe=UNIVERSE) == 0
    assert replay.main([str(log), "--home", "Nowhere"], universe=UNIVERSE) == 2


def test_replay_reports_an_unreadable_log(tmp_path, capsys) -> None:
    """A log file that doesn't exist ends the replay with exit code 2 and a message, not a traceback."""
    assert replay.main([str(tmp_path / "Local_20261028_165148_7.txt")], universe=UNIVERSE) == 2
    assert "can't read the log" in capsys.readouterr().err

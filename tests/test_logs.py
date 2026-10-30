from datetime import datetime, timezone

from homebound.logs.parser import LogFile, parse_change, parse_text
from homebound.logs.watcher import LogScanner

HEADER = """


        ---------------------------------------------------------------

          Channel ID:      local
          Channel Name:    Local
          Listener:        Ijomo Ohaya
          Session started: 2026.10.28 16:51:48
        ---------------------------------------------------------------

"""


def line(ts, msg, sender="EVE System"):
    return f"﻿[ {ts} ] {sender} > {msg}\n"


def write_log(path, body: str):
    path.write_bytes(b"\xff\xfe" + body.encode("utf-16-le"))


def test_parse_change():
    r = parse_change(line("2026.10.28 16:51:53", "Channel changed to Local : J161940"))
    assert r == (datetime(2026, 10, 28, 16, 51, 53, tzinfo=timezone.utc), "J161940")
    assert parse_change(line("2026.10.28 16:51:53", "o7", sender="Some Pilot")) is None
    assert parse_change(line("2026.10.28 16:51:53", "Channel changed to Local : Jita*"))[1] == "Jita"


def test_parse_text_header_and_first():
    text = HEADER + line("2026.10.28 16:51:53", "Channel changed to Local : J161940") \
        + line("2026.10.28 16:55:00", "hello", sender="Pilot") \
        + line("2026.10.28 16:58:00", "Channel changed to Local : J100551")
    ch = parse_text(text, 42)
    assert [(c.system, c.first, c.char_name) for c in ch] == [
        ("J161940", True, "Ijomo Ohaya"), ("J100551", False, "Ijomo Ohaya")]


def test_logfile_name():
    from pathlib import Path
    lf = LogFile.from_path(Path("Local_20261028_165148_2122853402.txt"))
    assert lf.char_id == 2122853402 and lf.started.hour == 16
    assert LogFile.from_path(Path("Corp_20261028_165148_1.txt")) is None


def test_scanner_tail(tmp_path):
    p = tmp_path / "Local_20261028_165148_7.txt"
    write_log(p, HEADER + line("2026.10.28 16:51:53", "Channel changed to Local : Jita")
              + line("2026.10.28 16:52:53", "Channel changed to Local : Perimeter"))
    s = LogScanner(tmp_path)
    r = s.poll(now=datetime(2026, 10, 28, 17, tzinfo=timezone.utc))
    # existing history collapses to the last location, as a non-jump
    assert [(c.system, c.first) for c in r.changes] == [("Perimeter", True)]
    assert s.chars[7].name == "Ijomo Ohaya"
    with open(p, "ab") as f:
        f.write(line("2026.10.28 17:00:00", "Channel changed to Local : J161940").encode("utf-16-le"))
    r = s.poll()
    assert [(c.system, c.first) for c in r.changes] == [("J161940", False)]
    assert s.poll().changes == []


def test_scanner_partial_write(tmp_path):
    p = tmp_path / "Local_20261028_165148_7.txt"
    write_log(p, HEADER)
    s = LogScanner(tmp_path)
    s.poll()
    data = line("2026.10.28 17:00:00", "Channel changed to Local : J161940").encode("utf-16-le")
    with open(p, "ab") as f:
        f.write(data[:15])           # odd byte count, mid-line
    assert s.poll().changes == []
    with open(p, "ab") as f:
        f.write(data[15:])
    assert [c.system for c in s.poll().changes] == ["J161940"]


def test_new_session_file(tmp_path):
    write_log(tmp_path / "Local_20261028_165148_7.txt", HEADER + line("2026.10.28 16:51:53", "Channel changed to Local : Jita"))
    s = LogScanner(tmp_path)
    s.poll()
    write_log(tmp_path / "Local_20261029_100000_7.txt", HEADER + line("2026.10.29 10:00:01", "Channel changed to Local : Amarr"))
    r = s.poll()
    assert [(c.system, c.first) for c in r.changes] == [("Amarr", True)]

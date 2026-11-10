# Plan: one badly named log file stops all tracking (E6)

Status: implemented (2026-11-10, commit `841647f`). Deviations: none.

Depends on: nothing.

## Problem (verified)

`FILE_PATTERN` (`logs/parser.py:18`) only checks digits: `^Local_(\d{8})_(\d{6})_(\d+)\.txt$`. A name such as
`Local_20261399_000000_1.txt` matches it, and `LogFile.from_path` then calls `strptime` on `20261399000000`,
which raises `ValueError` (month 13). The docstring promises None for anything that is not a Local log name.

Reached from three places, all with `glob("Local_*.txt")` over the user's folder:

- `LogScanner.newest_files` (`watcher.py:62`), called by `LogScanner.poll` (`watcher.py:83`) and so by
  `LogWatcher.poll` (`watcher.py:200`). The timer fires every 1000 ms (`watcher.py:166`) and `start()` /
  `set_log_directory()` poll directly. The exception comes before any character is read, so one bad file
  stops tracking for every character, on every tick, for as long as the file is in the folder.
- `SettingsDialog.validate_log_directory` (`settings_dialog.py:223`): the exception goes out of the
  validation that runs while the user picks a folder, so the Log page breaks on the same folder.
- `parse_log_file` (`parser.py:159`), used by the replay CLI (`replay.py:65`). Same exception, for a file the
  user named explicitly.

## Root cause

`parser.py:52`: `datetime.strptime(...)` is unguarded, and the regex cannot tell a valid date from digits.
Other bad shapes that pass the regex and fail the same way: day 32, hour 25, month 00, `_240000_`.

## Change

1. `homebound/logs/parser.py`, `LogFile.from_path`: wrap the `strptime` line in `try / except ValueError:
   return None`. Keep the regex as it is; the date check belongs to `strptime`. Adjust the docstring: "or None
   if the name is not a Local log name or its date is not a real date".
2. Nothing else in `homebound/`. No caller needs a code change (see the next section).

## Callers of the new return value

`from_path` already returns `LogFile | None` for a wrong prefix, so the type stays the same and every caller
already copes. Checked each one:

| Caller | Handles None | Note |
|---|---|---|
| `watcher.py:62` | yes, `if log_file and ...` | a bad file is skipped; the rest of the folder still tracks |
| `settings_dialog.py:223-224` | yes, `[file for file in files if file]` | a bad file no longer counts toward "N Local logs" |
| `parser.py:159` (`parse_log_file`) | yes, `if not log_file: return []` | replay skips the file silently, as it does for `notes.txt` |

## Tests (write first, watch them fail)

In `tests/test_logs.py`, next to `test_log_file_name` (line 74) and `test_parse_log_file` (line 83):

1. `test_log_file_name`: add `assert LogFile.from_path(Path("Local_20261399_000000_1.txt")) is None`, plus day 32
   and hour 24. Fails today with `ValueError`.
2. New `test_scanner_skips_a_bad_file_name(tmp_path)`: a valid log for character 7 and `Local_20261399_000000_1.txt`
   in the same folder. `scanner.newest_files()` has only character 7, and `scanner.poll()` still reports
   the change from the good file. Fails today in `newest_files`.
3. `test_parse_log_file`: add `parse_log_file(tmp_path / "Local_20261399_000000_1.txt") == []` (create the file, so
   the name is the only reason for the empty list).
4. Settings dialog: a case in the existing validation test (find it with `grep validate_log_directory tests`) with
   one good and one bad name: result `"valid"`, help text counts one log. If no such test exists, skip this one;
   the `from_path` test covers the cause.

## Risks

- Low. The only behavior change is that a name which used to raise is now ignored.
- A bad file is skipped without any message. If the user's real log has a bad name they get no hint why the
  character is missing (see decisions).
- The same digits-only check exists in no other place; `parse_header` (`parser.py:74`) also calls `strptime`, but
  `parse_log_text` was not part of E6 and is not touched here.

## Open decisions

- Report skipped files? Default: no. The EVE client writes these names, so a bad one means a hand-renamed
  file, which is rare. A status-line count would need a new field on `ScanResult`, which is more than the
  fix needs.

## Stale or not verified

- TODO.md and E6 are accurate: `parser.py:52` and `settings_dialog.py:223` match the current code, and the
  1 second poll is right. The TODO does not mention that `parse_log_file` / the replay CLI is a third path; the
  fix covers it anyway.
- Not run: I did not execute the failing case here (read-only task). The E6 finding is marked `ran`, and
  the code path is unchanged since.

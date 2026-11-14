# Plan: corrupt map locks the app out

Status: implemented (2026-11-10, commit `7be8fbb`). Deviations: the start-up error uses `QMessageBox.critical` (the plan said the app has none; it already had one). The damaged-file warning repeats at every start while a `.damaged` file exists; Settings opens by itself only on the start that set it aside.

- **Audience:** the owner and coding agents who need to know why something was built the way it is.
- **Scope:** the record of "Plan: corrupt map locks the app out", finished. The status line says where the build differs from the plan; where the code lives now is in [`code-map.md`](../../code-map.md).
- **Kept current:** archived with the plan, not updated after it was implemented (see the status line). The rest of the text is the plan as it was written.

Depends on: nothing. Touches the same files as the damaged-backup, prune-error and backup URI plans (see "Overlaps").

## Problem, as verified

No file under `homebound/` or `tests/` changed between the audited commit `582ee55` and `HEAD`, so the findings' line numbers still hold. `state.py` in the findings means `homebound/ui/state.py`.

Run on Python 3.13.13 and SQLite 3.50.4 through the repo's `.venv`, with throwaway scripts and a temporary `HOMEBOUND_HOME` per case. The scripts stub `detect_log_directories`, which is slow on this machine. They are in the session scratchpad and nothing in the repo changed. "Garbage" means a 10 KB file of text.

| Case | Result |
|---|---|
| Start on a garbage active map | `AppState(...)` raises `DatabaseError` (`SQLITE_NOTADB`). No window. Same as E5. |
| Start on a map with the `systems` root page damaged, home set | `DatabaseError` (`SQLITE_CORRUPT`) from `_ensure_home` (`state.py:95`). The file opens, the first read fails. |
| Same damage, no home set | The app opens. `store.systems()` raises later. |
| `switch_map` to a garbage map | `DatabaseError`. `settings.json` already says `Second`, old store closed, `tracker.store` raises `ProgrammingError`. The next start raises `DatabaseError`. E5a was `[read]`; it is now `[ran]` and correct. |
| Restore "replace" with a garbage inactive map | `BackupError: second.db is not a map file`, file untouched. |
| Restore "as new map" in the same state | Works, as `Second (6 Nov)`. |
| `_write_into` on a garbage target, on a valid header with a damaged body, on 3 bytes of junk | `BackupError: ... file is not a database`. Only a missing or empty target works. |
| Restore fails and the reopen in `finally` fails | `DatabaseError` reaches the caller, `BackupError` is only in `__context__`. Store left closed. E18 confirmed. |
| Set the garbage file aside by hand (with sidecars), start, Restore "replace" | Works: 1 system after start, 401 after the restore, `restore` safety backup made, garbage file kept. |
| Old-schema map opens while another map is garbage | Migrates with 0 backups. The status message says the backup failed. |
| Failed `Store()` on a garbage file | The file descriptor stays open after the exception. |
| Map path is a folder, map file read-only | `SQLITE_CANTOPEN`, `SQLITE_READONLY` (both `OperationalError`, a subclass of `DatabaseError`). |

Corrections to TODO.md and the findings:

- E5 and E18 are right, and `ui/` has no `sqlite3` handling at all. The raise itself is one level down, in `Store._migrate` (`store.py:301`), the first read of the file.
- "Until `settings.json` is edited by hand" holds for a failed switch. Deleting or moving the bad file also gets the app started.
- E18 understates one effect and overstates another. The original error isn't lost, Python keeps it in `__context__`. But the caller at `settings_dialog.py:624` catches only `BackupError`, so the user sees nothing and the click handler dies. An exception out of a Qt slot prints a traceback and the app carries on (ran), now on a closed store.
- **Not in the TODO:** reaching Restore isn't enough. A window and the Backups page would still fail on "replace" for two separate reasons, see below.
- **Not in the TODO:** one bad map stops every backup, for all maps. That includes the `delete` backup, so a corrupt inactive map can't be deleted from the app (`map_window.py:666`, read, not run through the UI).

## Root cause

1. `AppState.switch_map` commits before it knows the new map works: `state.py:156-157` changes and saves the settings, `state.py:158` closes the old store, `state.py:159` tries the open.
2. Nothing handles a failed open. `_open_store` (`state.py:102`) and `__main__.py:60` let it through, and `MapWindow.switch_map` (`map_window.py:572`) doesn't catch it.
3. Opening succeeds on partly damaged files, so a `try` around `Store()` alone isn't enough. `_ensure_home` (`state.py:180`, `:86`) and the character loop (`state.py:172-175`) are the first real reads and writes.
4. `create_backup` copies every existing map and stops at the first bad one (`backup.py:125-130`). `restore_maps` needs that backup before a replace (`backup.py:326-327`), so the damaged map blocks its own repair.
5. `_write_into` (`backup.py:360`) writes through SQLite's backup API, which refuses a target that isn't a database.
6. `restore_backup` reopens in a `finally` (`state.py:252-254`), so a reopen failure replaces the restore failure.
7. `Store.__init__` (`store.py:282-285`) leaves its connection open if `_migrate` or the schema script raises. On Windows that handle would stop the file being renamed (not run on Windows).

## Design

A damaged map file gets renamed, and the app carries on. The rename removes causes 4 and 5 without changing `create_backup` or `_write_into`: a missing map file is skipped by the backup (`backup.py:127`), and the normal Restore "replace" then works. The prototype row in the table shows this for the active map.

- **Damaged** means `SQLITE_CORRUPT` or `SQLITE_NOTADB`, from `error.sqlite_errorcode & 0xff`. Those attributes need Python 3.11, and the project requires 3.12. Locked, read-only and missing-folder errors don't count, because the file is fine and moving it would be wrong.
- The file goes to `map.db.damaged`, then `map.db.damaged-2` and so on. `-wal` and `-shm` go with it as `map.db.damaged-wal`, so the copy stays openable for salvage and no stale WAL sits next to the new file. Nothing deletes these files.
- At start, a damaged active map is set aside and the app opens on a new empty file at the same path, then shows Settings → Backups.
- On a switch, a damaged target is set aside too, and the app stays on the current map with an error toast. The switched-to map is empty next time, and backups work again.
- An open that fails for any other reason never moves anything. At start the app shows the error and exits with 1. On a switch it stays where it is.

## Changes

### `homebound/ui/state.py`

- New `class MapOpenError(Exception)`, with a user-facing message like `BackupError` and a `damaged: bool`.
- `_open_store(path)` takes the path, and wraps `sqlite3.Error` and `OSError` into `MapOpenError`.
- New `_load_map(path, characters, home_id) -> Store`: `_open_store`, the character loop, and the home check, on the new store and not yet on `self.store`. On failure it closes the new store and raises `MapOpenError`. Keep `_ensure_home()` as it is, since `apply_settings` (`state.py:310`) and `test_ensure_home` call it with no arguments; `_load_map` does the same two lines on the new store.
- `switch_map`: read the characters, get the target's path and home from `settings.maps[name]`, call `_load_map`. On `MapOpenError` with `damaged`, set the file aside, then re-raise. Only after a good open: `settings.switch_map(name)`, point `self.store`, `database_path`, namer, tracker and zKill at the new store, close the old store, `settings.save()`, then the signals as now.
- `_reopen_store` becomes `_load_map` plus the same rewiring. Share it with `switch_map`.
- `__init__` (`state.py:68`): open through `_load_map`. If that raises with `damaged`, set the file aside, open again, keep a line in `self.recovered_map` and queue it with `_report_backup("warn", ...)`. If not `damaged`, let `MapOpenError` go up. Line 86 can stay; it is harmless once the open has checked the home.
- `restore_backup`: replace the `finally`.

  ```python
  try:
      restored = ...
      ...
  except Exception as error:
      if reopen:
          self._reopen_after_failed_restore(characters, error)   # raises BackupError naming both, or returns
      raise
  if reopen:
      self._reopen_store(characters)   # MapOpenError -> BackupError
  ```

  If the reopen fails too, the `BackupError` gives both reasons and says to restart, `from` the original error. Restart then takes the start path. `restore_backup` doesn't set a file aside itself, because the zKill thread can hold its own connection to that path (`zkill.py:149`), and a rename there would leave it writing to the moved file.
- A module-level `set_aside_damaged(path) -> Path`, no Qt. It uses `.replace`; an `OSError` becomes a non-damaged `MapOpenError`, so the file stays put and the user is told.

### `homebound/mapdb/store.py`

Wrap lines 283-285 in `Store.__init__` so a failure closes the connection and re-raises. Nothing else.

### `homebound/ui/map_window.py`

- `switch_map` (572): catch `MapOpenError`, show the error toast, return False. The docstring already says "Whether the map changed".
- `new_map` (615): stop if the switch failed, so Settings doesn't open on the wrong map's Home page.

### `homebound/__main__.py`

- Around line 60: catch `MapOpenError`, show it in a small dialog, return 1. (Correction while implementing: `__main__.py:56` already uses `QMessageBox.critical` for a universe that still fails after the build, so the same box is used here rather than a new `BaseDialog`. The StartupError spec would replace both.)
- Around line 66: if `state.recovered_map`, call `window.open_settings(PAGES.index("Backups"))`, otherwise the existing log-folder check.

### `homebound/ui/settings_dialog.py`

When `state.recovered_map` is set, `_build_backups_page` shows it in `backup_help` with `help-warn`, because the toast is behind the modal dialog. The text says which file was kept and which backup choice to use (Replace).

### Docs

`HOW_TO.md`, backups section: what `.damaged` files are and how to recover. Remove the TODO.md entry when done, and note it under "Fixed" as the file does for others.

## Tests

First failing test, in `tests/test_ui_components.py`, next to `test_switch_map_carries_characters`:

1. `test_switch_to_corrupt_map_stays_on_the_open_map`: write garbage into the second map, `pytest.raises(MapOpenError)`, then assert the old store still reads, `settings.active_map` is unchanged, and `settings.json` still names the old map. Fails today with `DatabaseError`.

Then, in the order of the change:

2. `test_switch_to_map_with_damaged_table_stays_on_the_open_map`: valid header, `systems` root page overwritten, home set on that map. It covers cause 3. A helper in the test file damages the page, as the probe did.
3. `test_failed_switch_carries_no_characters_and_emits_nothing` (`mapSwitched`, `mapChanged`).
4. `test_failed_store_open_closes_the_connection` in `tests/test_store.py`: a `Store` subclass whose `_migrate` keeps `self` and raises; afterwards `database.execute` raises `ProgrammingError`.
5. `test_switch_to_damaged_map_sets_the_file_aside`: bytes unchanged in `.damaged`, then `create_backup` works and `restore_backup(..., ["Second"], replace=True)` brings the rows back. "Set aside on an inactive map, then restore" has only been read, not run, so this test also proves that path.
6. `test_set_aside_moves_the_sidecars` and `test_set_aside_keeps_an_earlier_damaged_file` (`-2` suffix).
7. `test_start_on_corrupt_map_opens_empty_and_keeps_the_file` in `tests/test_backup.py`, through `make_state`, asserting the warning is queued for `start()`.
8. `test_restore_after_start_on_corrupt_map`: the whole path, ending with the rows back and a `restore` safety backup.
9. `test_start_on_unopenable_map_moves_nothing`: map path is a folder, so `MapOpenError` with `damaged` False and the folder still there. It works as root and on Windows, unlike a chmod test.
10. `test_restore_failure_is_not_hidden_by_the_reopen`: `restore_maps` fails and the file goes bad, assert `BackupError` with both reasons and the original as `__cause__`.
11. `test_restore_reports_a_failed_reopen`: `restore_maps` succeeds and the reopen fails, assert `BackupError`.
12. `test_failed_switch_shows_a_toast` and `test_new_map_stops_when_the_switch_fails` in `tests/test_ui_map_window.py`.
13. `test_backups_page_shows_the_recovered_map` in `tests/test_ui_settings_dialog.py`.
14. `test_main_opens_backups_after_recovery` and `test_main_reports_an_unopenable_map` in `tests/test_build_and_main.py`.

Existing tests that should still pass as they are: `test_switch_map_carries_characters`, `test_old_map_gets_one_migration_backup`, `test_failed_safety_backup_blocks_replace`, `test_restore_replacing_open_map`.

## Risks and data safety

- **The app renames a user's file without asking.** It is a rename, the bytes stay, and only two error codes trigger it. A synced data folder could produce a half-written file that reads as `NOTADB`; the user then sees an empty map and a `.damaged` file holding the partial copy. See decision 1.
- **An empty map can look like lost data.** That's why start opens Settings → Backups instead of relying on the toast.
- **Good backups can age out.** Once the app runs on an empty map, each new `auto` backup holds that empty map. After 7 of them the good `auto` backups are pruned (`backup.py:250`). `manual` backups stay and safety backups last longer, but a user who ignores the warning for a week loses the daily ones. The `.damaged` file doesn't help if it can't be salvaged. See decision 3.
- **Character locations are lost in the recovery.** The damaged store can't be read, so they come back from the logs on the next jump.
- **The carry-over now writes before the switch is final.** If `_load_map` fails after copying characters, the target map keeps them. Harmless, since a successful switch does the same.
- **`settings.save()` can still raise** after the switch, as it can today. The app is then on the new map with the old one on disk, which opens fine next start. The views miss their signals until the next refresh.
- **Migration backup timing changes.** It now runs with the old store open and the settings still on the old map. `create_backup` handles that (`backup.py:133`), and it uses the online backup API, so this should be fine. `test_old_map_gets_one_migration_backup` covers it.
- **Partial damage can still get through.** A map with no home and a damaged table opens, then fails in slots, same as today. Damage that appears during a session also isn't covered, except by a restart. See decision 2.
- **A zero-byte map opens as a new empty map with no warning.** Unchanged, and nothing here detects it.
- **Windows:** the rename fails if anything holds the file. The `Store` fix removes our own leak, and at start no thread is running yet. Antivirus or sync clients can still hold it, and that ends in the error dialog with the file untouched. None of this was run on Windows.

## Decided by the owner (2026-11-06)

Decision 1: set the damaged file aside on its own, at start and on switch, with no dialog.

Decision 3: repeat the warning at every start while a `*.damaged` file exists.

## Open decisions for the owner

1. **Set aside on its own, or ask first?** Recommended: on its own, for both start and switch, because the rename can be undone and it needs no new dialog. Asking at start needs a dialog with Restore, New map and Quit, which grows into a second Restore UI, since `SettingsDialog` needs an `AppState`.
2. **Add `PRAGMA quick_check` on open?** It would catch the "no home" gap and would let `_load_map` shrink. The plan leaves it out because, as far as I know, it also reports NOT NULL and CHECK violations, and `state.py:173` shows legacy rows exist. A false alarm here would move a healthy map. Not tested, and its cost wasn't measured.
3. **Protect good backups after a recovery?** Options: leave it and rely on the warning, repeat the warning at every start while a `.damaged` file exists, or skip `auto` backups until the user restores or dismisses. Recommended: repeat the warning. It's the smallest, and a `*.damaged` glob finds the files.
4. **Should one bad map keep stopping all backups?** This plan only clears the file when the user tries to open it. An untouched bad map still stops every backup, and the only sign is a status warning. Letting `create_backup` skip bad maps would fix that, but `test_unreadable_and_damaged_maps` pins the current behaviour and a backup that leaves out a map is a real trade-off. Left as is here.
5. **Should a migration go ahead when its backup failed?** It does today (ran), and `plan-backups.md` chose that for start-up. A bad map elsewhere makes this more likely. Not changed here.

## Overlaps

- **Damaged-backup restore** (`plan-damaged-backup-restore.md`): it widens the `except` in `restore_maps` so raw errors become `BackupError`, and says `state.py` needs no change. This plan does change `restore_backup`, and catches `Exception` so it works with or without that fix. Both rely on the catch at `settings_dialog.py:624`, and neither changes it.
- **Prune errors** (`plan-prune-oserror.md`): edits `AppState.backup` and `create_backup`, and names the `state.py:102` lambda as an `OSError` path. Here `_open_store` turns an `OSError` into a non-damaged `MapOpenError`, so without the prune fix a prune failure would now stop the map opening with a dialog instead of a traceback. Whichever lands second re-reads `_open_store`.
- **Backup URI handling** (`plan-backup-uri.md`): touches `store.py:238,781` and `backup.py:73`. This plan's only `store.py` edit is in `__init__`, right after the `needs_migration` call, so expect a nearby merge at most. `_copy_database` must keep rejecting bad files the same way, because cause 4 relies on missing files being skipped and bad ones failing.
- **Bad settings** (TODO item 10): also about start-up, at `__main__.py:59`, the line before this plan's catch.
- **zKill worker** (TODO item 7): `zkill.py:149,163` open their own `Store` and would hit the same errors. After this plan the worker's path always points at a map that opened, and a failed switch no longer moves it.
- **Test segfault** (TODO item 3): new tests in `test_ui_*.py` run under the same flaky suite, so a crash there may have nothing to do with this change.

# Plan: prune must not raise out of a finished backup

Status: proposed. Audit item: `TODO.md` "To do" 6, finding E9 in `docs/findings/findings.md`.

Depends on: nothing. Touches the same functions as the corrupt-map, backup-URI and damaged-backup plans, see "Overlaps".

## Problem as verified

A backup that was written successfully can still end in an uncaught `OSError`, because the cleanup step afterwards deletes old zips and nothing guards that.

- `backup.create_backup` calls `prune(folder)` at `backup.py:147`, after the `try` that turns `OSError` into `BackupError` (lines 117-146). The function's docstring says it raises only `BackupError`.
- `backup.copy_to_folder` does the same at `backup.py:188`, after its own `try` (lines 182-186).
- `prune` (`backup.py:243-255`) unlinks each old zip with `info.path.unlink(missing_ok=True)` at line 253. `missing_ok` only covers a missing file. A read-only folder, a locked file or a sync client holding the file raises `PermissionError`/`OSError`.
- `AppState.backup` (`ui/state.py:191`) documents "A failure never raises" and catches only `BackupError` (lines 207 and 215). An `OSError` from prune goes straight through it.

I ran the trigger: unlinking a file in a `chmod 0o500` folder raises `PermissionError` as uid 1000 on Linux. So it does not need Windows or OneDrive, though those are the likelier real cause. The extra-copy folder is the most exposed one (synced or network folder, `settings_dialog.py:676`).

What the failure does, by caller:

- **Start-up auto backup**: `AppState.__init__` calls `self.backup("auto")` at `state.py:65-66`. An `OSError` escapes the constructor and the app does not start. The finding doesn't list this path; it is the worst one.
- **Migration backup**: `AppState._open_store` (`state.py:102`) passes `lambda path: self.backup("migration")` to `Store`, which calls it at `mapdb/store.py:280-281` inside `__init__`. The exception aborts `Store()`, so the map does not open. The backups plan ("A failed `migration` backup doesn't stop the map from opening") assumes this can't happen; it can, for this one error.
- **Delete** (`map_window.py:666`) and **manual** (`settings_dialog.py:599`) backups: the exception reaches the Qt slot. Neither catches `OSError`.
- **Restore safety backups** call `create_backup` directly (`backup.py:327` in `restore_maps`, `state.py:243` in `restore_backup`). Their contract is `BackupError`, so an `OSError` there skips the UI's handler. See overlaps.

### Stale or wrong in the audit entries

- Line numbers 147 and 188 in E9 and the TODO text are still right. Line 253 (the actual raise) isn't named; add it.
- "During a migration backup it would abort opening the map" is correct, but the entry misses the start-up path above.
- "Locked file (Windows, OneDrive) is the likely trigger" is plausible and unverified on Windows. The Linux read-only-folder case is reproduced.
- `list_backups` already catches `OSError` per file (`backup.py:~232-237`), so the scan half of prune is safe. Only the unlink loop is exposed.

## Root cause

`prune` treats deleting old backups as something that can't fail, and both callers run it outside their `OSError` handling (`backup.py:147`, `backup.py:188`, `backup.py:253`). The new backup is already on disk by then, so the right result is "backed up, cleanup failed", not an exception.

## Proposed change

### `homebound/backup.py`

1. **`prune`**: wrap the unlink in `try/except OSError`. A file that can't be deleted is skipped and the loop goes on, so one locked zip doesn't stop the others. It still returns the number actually deleted. Keep the signature compatible (`prune(folder=None) -> int`) so `test_retention_per_reason` (`prune() == 5 + 2`) is untouched. Add an optional `failed: list[str] | None = None` argument; each failure appends `"<name>: <error>"`.
2. **`create_backup`** and **`copy_to_folder`**: take an optional `warnings: list[str] | None = None` and hand it to `prune`. Neither raises for a prune failure. The docstrings say so ("old backups that can't be deleted are added to `warnings`, the backup still counts"). With the argument left out the failure is dropped, which is the right default for the restore safety backups at `backup.py:327` and `state.py:243`, where the caller has nothing to show.
3. Leave `partial_cleanup` (`backup.py:151-161`, unlink at 159) alone in this change. See open decisions.

### `homebound/ui/state.py`

4. **`AppState.backup`**: pass a `warnings` list to `create_backup` and to `copy_to_folder`. If either list has entries after a successful backup, report once as `warn`: `Backed up, but old backups couldn't be removed: <first error>` (plus ` (+n more)` if several). This extends the existing "Backed up, but not copied" pattern at line 216. The backup then returns `path`, not None, so `last_backup_error` stays empty and the delete flow (`map_window.py:666`) and manual flow (`settings_dialog.py:599`) treat it as a success.
5. If the copy also failed (`BackupError` branch) the existing "not copied" warning wins; the prune warning from the main folder is folded into the same message rather than shown as a second one, so the status label isn't overwritten. Keep it to one `_report_backup` call per `backup()`.

The docstring of `AppState.backup` stays true as written. No change to `store.py`, `map_window.py` or the dialogs.

## How a failed prune is reported

The backup succeeded, so it is reported as success with a warning: status-bar `backup` label in the warn style (which also shows a toast, per the backups plan), text `Backed up, but old backups couldn't be removed: …`. It is not a failure: the return value is the path, `last_backup_error` is cleared, migration and delete go on. It repeats on the next backup if the cause is still there, which is what you want from a folder that keeps filling up.

## Tests to add

All in `tests/test_backup.py`, under the existing `settings` fixture. Write the first one before touching `backup.py` and watch it fail.

1. **`test_prune_error_does_not_fail_the_backup`** (first, fails today with `OSError`): seed `backups_directory()` with 8 `fake_backup` auto zips, monkeypatch `Path.unlink` (or use a `chmod 0o500` folder, skipped under `CANNOT_LOCK_FOLDERS` like the neighbours) so deleting raises `PermissionError`. `create_backup("manual", settings)` returns a path that exists, and `warnings` holds one entry per undeletable zip. Prefer the monkeypatch variant so it runs as root and on Windows too; add one `chmod` variant beside `test_read_only_folder_fails_the_backup`.
2. **`test_prune_skips_the_locked_file_and_deletes_the_rest`**: unlink fails for one name only. `prune` returns the count of the others, `failed` names the one, and the retention result for the rest is as in `test_retention_per_reason`.
3. **`test_copy_to_folder_survives_a_prune_error`**: same for the extra folder.
4. **`test_state_backup_reports_prune_warning`** (`make_state`, `qt_application`): `state.backup("manual")` returns the path, `last_backup_error == ""`, and one `warn` status starting `Backed up, but old backups couldn't be removed`. Capture it by connecting to `state.status`, as the existing UI tests do.
5. **`test_migration_backup_with_prune_error_still_opens_the_map`**: an old-schema map file plus a prune that raises; `make_state` opens it and the map has the new schema. This is the case the audit entry names.
6. **`test_startup_backup_with_prune_error`**: `AppState(..., auto_backup=True)` with a failing prune constructs and queues the warning for `start()` (`_backup_messages`).

## Risks

- Swallowing `OSError` per file also hides a real permission problem for good if nobody reads the status bar. The warning on every backup is the mitigation; there is no log in this app to fall back on.
- `warnings` as an out-parameter is a little clumsy. It keeps `create_backup` returning a `Path` (about 12 call sites in tests and 4 in the app). A result object would touch all of them.
- The status label holds one message. Two problems in one backup (copy failed and prune failed) lose detail; step 5 merges them into one line.
- The monkeypatched-`unlink` tests depend on `prune` calling `Path.unlink`; if it moves to `os.remove` they need updating.

## Decided by the owner (2026-11-06)

Fix `partial_cleanup` in this work too (`backup.py:159`), with the same skip for files that won't delete.

## Open decisions

- **Out-parameter vs. raising a subclass.** Alternative: `prune` raises `PruneError(BackupError)` after finishing, and `AppState.backup` calls `prune` itself instead of `create_backup`/`copy_to_folder`. Cleaner types, but `create_backup` stops pruning and the restore safety backups would need their own prune call. I picked the out-parameter as the smaller change.
- **`partial_cleanup` has the same flaw** (`backup.py:159`, called from the `except` blocks at 142 and 145): an `OSError` there replaces the `BackupError` that was about to be raised. Same one-line fix (skip files that won't delete). It is a neighbour of E9, not E9. Include it here, or leave it for the item that owns `create_backup` error handling?
- **Warning wording and level.** `warn` as proposed, or `ok` with a note. A permanent failure being quiet seems wrong, so I chose `warn`.
- **Whether the restore safety backups should report prune failures.** As proposed they stay silent; the user is already reading a restore result.

## Overlaps

Other plans are touching `backup.py` at the same time. Noted, not coordinated.

- **Backup URI handling** (E3, E3a, `backup.py:73`): edits `_copy_database` (lines 62-80), a different function, but the same file and the same `create_backup` loop is nearby (line 126). Merge conflicts are unlikely, only if lines move.
- **Damaged-backup restore** (E8, E8a, `backup.py:333,347`): edits `restore_maps` and the UI's exception handling. Both plans change which exceptions the `restore_*` callers see. My change doesn't alter `restore_maps`, but the `create_backup("restore", …)` call at `backup.py:327` and `state.py:243` is where a prune `OSError` would escape today, and the fix there makes it moot. If that plan widens the UI catch to `OSError`, it duplicates part of this.
- **Corrupt-map lockout** (E4, E5, E5a, `state.py:102,158`): edits the `_open_store`/`switch_map` path that holds the migration-backup lambda (`state.py:102`) and the `Store.__init__` call. My plan doesn't change that line, but a test written against `make_state` with a migrating map (test 5) may need rebasing on whatever that plan does to opening a store.
- **`AppState.backup`** (`state.py:191-218`) is the one function where my step 4 and any of the others could collide; whoever lands second rebases.

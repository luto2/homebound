# Plan: damaged backups and Restore

Status: implemented (2026-11-10, commit `f2497b3`). Deviations: none; a malformed map entry is dropped and the rest kept (owner decision).

Depends on: nothing. Touches the same file as the corrupt-map lockout, backup URI handling and prune-error plans (see "Overlaps").

## Problem, as verified

All of it was checked against the current code. The two repros marked "ran" were run on throwaway zips in the scratchpad.

- A bad `manifest.json` entry gets past `read_manifest`/`backup_info`, which only check that `maps` is a dict. Then `restore_maps` fails with a raw exception, not `BackupError` (ran):
  - an entry that isn't a dict (`{"Main": "oops"}`, `[]`): `TypeError` at `backup.py:334` (`entry["file"]`)
  - a dict without `file`: `KeyError: 'file'` at `backup.py:334`
  - a non-string `file`: already a `BackupError` ("no file 3"), so fine
  - not yet tried: a non-string `home_system`/`naming_scheme`. By reading, `settings.add_map` (`settings.py:126`) calls `require_type`/`require_choice`, which should raise `TypeError`/`ValueError`. Not run.
- A damaged zip member raises `zlib.error` out of `archive.read` (ran): in `_extract_map` (`backup.py:292`, only `KeyError` is caught), in `read_manifest` (`backup.py:201`, not in the tuple at 202) and in `restore_settings` (`backup.py:398`, same tuple). Other things `ZipFile.read` can raise and that nothing catches: `EOFError` (truncated stream), `RuntimeError` (encrypted member), `NotImplementedError` (unsupported compression). I only ran the `zlib.error` case.
- The UI catches only `BackupError` (`settings_dialog.py:624`), so any of the above kills the click handler. `AppState.backup`/`restore_backup` (`state.py:221`) add no catch.
- The undo branch (`backup.py:347-356`) is `except (OSError, zipfile.BadZipFile, BackupError)`. Every exception above skips it. Result: `settings.maps`, `home_system` and `naming_scheme` stay half-changed in memory, and files of maps already added as new stay on disk. The settings aren't saved by `restore_maps`, but the live `Settings` object is what `apply_settings` and the next Save use.
- **Not in the TODO, found while checking:** `list_backups` (`backup.py:236-239`) catches only `BackupError` and `OSError`. A backup whose *manifest* member is damaged raises `zlib.error` from `read_manifest`, so `refresh_backups` (`settings_dialog.py:564`) throws and the whole Backups page fails to load. One bad zip hides every good one. `auto_backup_due` (`backup.py:260`) and `prune` (`backup.py:249`) also call `list_backups`, so the start-up auto backup and every `create_backup` would crash too. Ran: `backup_info` on a zip with a damaged manifest raises `zlib.error`.
- E8a: `RestoreBackupDialog` (`dialogs.py:941`). The loop is at `dialogs.py:958` and the crash is `entry.get(...)` at `dialogs.py:959`. Stale by one line: TODO says 959 for the class, the class is at 941 and 959 is the `.get` line. It raises `AttributeError` for a string or list entry. `info.maps` goes there unchecked.

Stale or imprecise in the findings: E8 says `backup.py:333,347`. 333 is `entry = info.maps[name]` (can't fail, the name was checked at 323-325); the failing lines are 334/335. 347 is correct (the `except`). The TODO text ("a bad manifest raises `KeyError` or `TypeError`") is accurate.

## Root cause

1. `read_manifest` (`backup.py:204`) validates only the top level. Entry shape is trusted by `restore_maps` (`backup.py:333-335`) and `RestoreBackupDialog` (`dialogs.py:958-959`).
2. The zip read sites catch a hand-picked exception list that omits what a damaged deflate stream raises (`backup.py:202`, `293`, `347`, `399`).
3. `list_backups` has the same gap one level up (`backup.py:238`), because it relies on `read_manifest` converting everything to `BackupError`.

## Proposed change

`homebound/backup.py`

- Add one module constant for what reading a zip member can raise, e.g. `ZIP_READ_ERRORS = (OSError, KeyError, zipfile.BadZipFile, zlib.error, EOFError, RuntimeError, NotImplementedError)`. Use it in `read_manifest` (202) and `restore_settings` (399). `import zlib` is new.
- `read_manifest`: after the `maps` dict check, check each entry: a dict, `file` a non-empty string, `home_system` a string if present, `naming_scheme` a string in `settings.SCHEMES` if present. On failure raise `BackupError(f"{path.name} has a damaged entry for map {name!r}")`. This one place covers `list_backups` (skips the backup), `backup_info`, `restore_maps` (refuses before the safety backup at 327) and, because the dialog only gets listed infos, the dialog.
- `_extract_map` (292): catch the same tuple around `archive.read`, with the `KeyError` message kept for the missing member and a "damaged" message for the rest.
- `restore_maps` (347): add `zlib.error`, `EOFError`, `RuntimeError`, `NotImplementedError`, `ValueError`, `TypeError`, `KeyError` to the undo `except` so a surprise from a future manifest field still undoes the changes. The existing `raise BackupError(...) from error` at 356 then wraps them. This is the data-safety line: the undo must run for anything that can happen between the `before` snapshot (329) and the end of the loop.
- `list_backups` (238): no change if `read_manifest` and `backup_info` are now airtight; add `zlib.error` anyway? Decision below.

`homebound/ui/dialogs.py`: no change planned for `RestoreBackupDialog`; the validated `info.maps` is what it needs. A test pins that.

`homebound/ui/settings_dialog.py`, `state.py`: no change. The catch at `settings_dialog.py:624` is right once the backup layer only raises `BackupError`.

## Tests

In `tests/test_backup.py`, using the `settings` fixture and `fake_backup`-style zips written with `zipfile`. Each one is written to fail on the current code first.

1. `test_restore_refuses_damaged_manifest_entry` (parametrised: string entry, list entry, missing `file`, `home_system` as a number, unknown `naming_scheme`): `restore_maps` raises `BackupError`. Fails today with `TypeError`/`KeyError`.
2. `test_failed_restore_undoes_settings_and_files`: a two-map backup where the second map's member has damaged deflate data (flip bytes after the local header). Restore as new map. Assert `BackupError`, `settings.maps`/`home_system`/`naming_scheme` equal their values before, and the first map's new file is gone. Fails today with a raw `zlib.error` and leaves the new entry in `settings.maps`. This is the undo-branch test.
3. Same as 2 with `replace=True`, active map untouched in the list, plus an assertion that a `restore` backup exists (so the user's data is recoverable).
4. `test_damaged_manifest_member_is_skipped_in_list`: a good backup next to one with a damaged manifest member; `list_backups()` returns only the good one, `auto_backup_due()` and `create_backup` don't raise. Fails today with `zlib.error`.
5. `test_restore_settings_with_damaged_member`: `restore_settings` raises `BackupError`.
6. In `tests/test_ui_settings_dialog.py`: with a damaged backup in the folder, `refresh_backups` still lists the good one; and `restore_selected_backup` on a backup that fails mid-way sets the red help text ("Nothing restored") instead of raising. Constructing `RestoreBackupDialog` with a string entry is not a reachable state after the change, so no direct test of that.

## Risks and data safety

- The undo branch deletes files (`backup.py:349-352`). It removes only maps that are in `settings.maps` after and not before, by path from `settings.map_path`. Widening the `except` makes that code run in more cases, so test 2 must confirm it never touches an existing map's file. Check that `add_map` can't hand out the file of a map that existed before (it picks an unused slug, `settings.py:150`).
- Replace mode writes into live map files one at a time (`_write_into`, 346). If map 2 fails after map 1 was overwritten, the undo restores settings but not map 1's content. The `restore` backup made at 327 is the way back. The error message should say so when `replace` is true. Not a redesign, one sentence in the message.
- Validating in `read_manifest` makes a backup with one bad entry unlistable, which hides its good maps and its settings from the user. See decision 1.
- A broad `except` around the loop can hide real bugs as "couldn't be restored". The message keeps the original text and `from error` keeps the chain.
- The safety backup at 327 runs before any per-map check today, so a refused restore still costs a `restore` backup and can prune an older safety one. After the manifest check moves earlier, this mostly goes away; a damaged member is only found after the safety backup. Accepted.

## Decided by the owner (2026-11-06)

Leave a damaged entry out and list the others (open decision 1). This replaces the reject-the-whole-backup behaviour in the proposed change above, so `read_manifest` drops the bad entry and keeps the rest.

## Open decisions

1. Bad entry in an otherwise readable backup: reject the whole backup (plan above, simple, hides the rest) or drop the bad entry and list the others? Dropping keeps good data reachable but means the manifest lies about what it holds.
2. Should `list_backups` also get `zlib.error` etc. in its own `except`, as a second line of defence, or trust `read_manifest`? A second tuple is duplication but a crash on the Backups page is expensive.
3. Is a backup with a damaged manifest worth showing as "damaged, can't restore" so the user can delete it? Today it is silently hidden (same as junk files). That needs UI, so left out.

## Overlaps

Not coordinated, only noted.

- **Corrupt-map lockout**: also deals with damaged data near `_copy_database`/`_extract_map` (`backup.py:57`, `285`). If it changes `_extract_map`'s error handling, merge with mine at line 292.
- **Backup URI handling**: `_copy_database` opens `file:{source}?mode=ro` (`backup.py:74`); any change to how paths are quoted touches the same function that `_extract_map` returns through (297).
- **Prune errors**: `prune` (`backup.py:243-254`) and `list_backups` (226-240). Both plans may edit `list_backups`' `except` or the call order in `create_backup`/`copy_to_folder` (188). A change to what `list_backups` skips affects my test 4.
- `restore_maps` is also the home of the undo branch; nothing else in the TODO lists plans a change there.

## Implementation notes (2026-11-12, TODO item 19)

- `create_backup` no longer stops at a damaged map: a map whose copy raises `BackupError` is left out, named in
  the optional `skipped` argument and in the manifest (`"skipped": {name: why}`), and every other map and the
  settings are backed up. A zip that can't be written still raises. `AppState.backup` keeps `last_backup_skipped`
  and reports "Backed up, but the map “X” is damaged and was left out (why)" on the status line.
- `delete_map` refuses a map that was left out of its safety backup (it has no backup to fall back on).
  Replace over a damaged map now works too, because the `restore` safety backup no longer fails on it.
- `list_backups` lists a zip with a backup name that can't be read, marked (`BackupInfo.damaged`,
  `readable=False`, date from the file, reason from the name); `prune` leaves such a file alone and
  `auto_backup_due` ignores damaged ones. `list_backups(check=True)` (the Backups page) also tests every member's
  checksum; a manifest entry that was dropped as malformed makes the backup "damaged" but still restorable.
- Settings → Backups: "▲ damaged" in the Maps column with the reason as tooltip; selecting it says why under the
  list; Restore is disabled for an unreadable one.

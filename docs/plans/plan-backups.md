# Plan: backups

Status: implemented (2026-11-01). See "Implementation notes" at the end for where it differs from
the plan.

Depends on: nothing. Should ship before [packaging](plan-packaging.md), since packaged users need it most.

Build order across the plans: [rescan status](plan-rescan-status.md) → [backups](plan-backups.md) → [Windows support](plan-windows-support.md) → [packaging](plan-packaging.md) → [code signing](plan-code-signing.md).

## What exists

Nothing automatic. `TODO.md` tells the user to copy `~/.local/share/homebound/` by hand before a
schema migration. Deleting a map is permanent (it only asks first). Maps are small SQLite files:
`map.db`, `maps/*.db`, plus `settings.json`.

## Design

- **One zip per backup** in `DATA_DIR/backups/`, e.g. `2026-11-01_1612_auto.zip`, holding
  `settings.json`, every map file and a `manifest.json` (created at, app version, reason, map
  names → files, home systems). The map files are copied with SQLite's online backup API
  (`sqlite3.Connection.backup`) into a temp file first, so a backup is consistent even while the
  map is open. Each copy is checked with `PRAGMA integrity_check` before it goes in the zip.
- `data/universe.sqlite` isn't backed up: `build_static` rebuilds it.
- **When**:
  | Reason | Trigger |
  |---|---|
  | `auto` | at start, if the newest automatic backup is older than 24h |
  | `migration` | before `Store` migrates a file that needs it (see below) |
  | `delete` | before a map is deleted |
  | `restore` | before a restore replaces anything |
  | `manual` | "Back up now" |
- **Retention**: keep the newest 7 `auto`, the newest 10 safety backups (`migration`, `delete`,
  `restore`), and every `manual` backup until the user deletes it.
- **Extra copy (optional)**: Settings field "Also copy backups to …", e.g. a Nextcloud folder. Off
  by default. A local folder is still on the PC; whether it syncs anywhere is the user's choice.
- **Restore**: Settings → Backups lists the backups (date, reason, maps, size). **Restore …** opens
  a dialog:
  - pick the maps from that backup
  - the user picks, each time, either "Restore as new map" (nothing is overwritten; name
    `Main (31 Oct)`) or "Replace the map" (takes a `restore` backup first, then closes, swaps and
    reopens the file, using the same close/reopen steps as `AppState.switch_map`). Neither is
    preselected; Restore stays disabled until one is chosen.
  - "Also restore settings" (off by default)
- The delete-map confirmation gets a line: "A backup is kept in Settings → Backups."

## Changes

1. **New `homebound/backup.py`**: `create_backup(reason, settings) -> Path`,
   `list_backups() -> list[BackupInfo]`, `prune() -> int`, `restore_maps(backup, names, replace)`,
   `read_manifest(path)`. No Qt, so it can be tested directly.
2. **`Store`**: split the migration check from the migration. A read-only
   `needs_migration(path) -> bool` runs first; if it's true, a `migration` backup of that file is
   made before `_migrate()`.
3. **`AppState` / `__main__`**: the start-up `auto` backup, after settings load and before the map
   opens. If a backup fails, the app still starts, and a warning toast and the status bar say why.
4. **Map deletion** (`map_window.py` / `Settings.remove_map` caller): `delete` backup first. If it
   fails, the map isn't deleted.
5. **Settings dialog**: a **Backups** page with the list, Back up now, Restore …, Open folder, and the
   extra-copy folder.
6. **Docs**: user guide §14 (data) gets a backup section; the TODO.md "back up by hand" entry goes.

## Tests (`tests/test_backup.py`, all under a temp `HOMEBOUND_HOME`)

- Backing up an open map with uncommitted rows gives a consistent copy.
- Manifest contents; zip contains every map in `settings.maps`.
- Retention per reason; manual backups survive pruning.
- An old-schema map triggers exactly one `migration` backup, and the backup has the old schema.
- Restore as new map: new entry in `settings.maps`, new file, original untouched.
- Restore replacing: safety backup exists, store reopened, characters carried over.
- A failing backup (read-only folder) blocks deletion but not start-up.

## Decisions (2026-11-01)

- Restore: the user picks "as new map" or "replace" each time; nothing is preselected.

## Implementation notes (2026-11-01)

- **Every backup holds every map**, also the `migration` and `delete` ones: the files are small,
  and one kind of zip is simpler to restore. A map that was never opened (no file yet) is left out.
  A map that fails the integrity check, or isn't an SQLite file, fails the whole backup.
- **Replacing** doesn't swap files. The backup's copy is written into the existing file through
  SQLite's backup API, so the zKill thread's own connection to the same file stays valid. The open
  map's `Store` is still closed first and reopened after (`AppState._reopen_store`, shared with
  `switch_map`), so an old-schema copy gets its `migration` backup and is migrated on reopen.
- **Replacing** also brings back that map's home system and naming scheme from the manifest. A
  map that was deleted since comes back under its own name.
- **A failed restore** undoes the settings changes and removes the files of maps it added.
- **Settings restore** keeps `maps`, `active_map`, `home_system` and `naming_scheme`; it takes
  everything else. It also makes a `restore` backup first.
- **A failed `migration` backup** doesn't stop the map from opening: it is reported like a failed
  start-up backup. Refusing would leave the app unable to open the map at all.
- **Status bar**: a fourth label, `backup`, hidden until a backup reports. Warnings also show a
  toast. A failed `delete` backup shows one error toast ("not deleted: …") instead.
- **Dates** in restored map names use English month names whatever the locale (`Main (31 Oct)`).
- `MAP_NAME_LIMIT` moved from `ui/map_window.py` to `settings.py` (restored names respect it).
- The extra copy folder is pruned with the same rules as the backups folder. A missing folder is
  allowed in the settings (an unmounted drive); the copy then fails with a status-bar warning.
- `TODO.md` had no "back up by hand" entry any more; the user guide's line about copying map files
  by hand is replaced by the backup section.

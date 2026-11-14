# Where your data lives

- **Audience:** players using Homebound.
- **Scope:** The data folder, backups, restore and network access. The other topics are on the [contents page](README.md).
- **Kept current:** checked against `702f2d5` (2026-11-14). Update it when the behaviour it describes changes.

The data folder is `~/.local/share/homebound/` on Linux and `%APPDATA%\Homebound\` on Windows.

| What | Where |
|---|---|
| Settings | `<data folder>/settings.json` |
| Main map | `<data folder>/map.db` |
| Other maps | `<data folder>/maps/<name>.db` |
| Backups | `<data folder>/backups/` |
| Static universe data | `<data folder>/universe.sqlite`, built on first start (packages); `data/universe.sqlite` in the project folder when run from source. The pinned statics answers (`statics_resolved.json`) ship with the package. |

Set `HOMEBOUND_HOME` to use a different folder for settings and maps, for example for a test setup. `HOMEBOUND_UNIVERSE` points at a different universe file.

Each map is a plain SQLite file.

## Backups

A backup is one zip, such as `2026-11-01_1612_auto.zip`, with the settings, every map and a
`manifest.json`. Maps are copied safely while they are open. The universe data isn't included;
it can be built again.

| Made | Kept |
|---|---|
| once a day, at start | the newest 7 |
| before a map is updated to a new file format, deleted, or replaced by a restore | the newest 10 of these together |
| when you click *Back up now* | until you delete them in the folder |

If a backup fails at start, Homebound starts anyway and says why in a toast and the status bar.

**A damaged map is left out of a backup**; every other map and the settings are still backed up. The
status bar says which map was left out and why, and the Backups list shows it when you select that
backup. A map you want to delete that is damaged is not deleted, because it can't be backed up first.
A backup file that is itself damaged (an unreadable zip, a map inside that fails its checksum) is
listed with a "▲ damaged" marker and the reason as a tooltip; one that can't be read at all can't be
restored, and is left in the folder for you to delete.

**Also copy backups to** (Settings → Backups, off by default) puts a copy of each new backup in a
second folder, for example one that Nextcloud or Syncthing syncs. Old copies there are pruned the
same way. If the folder is missing, for example a drive that isn't mounted, the backup is still
made and the status bar says it wasn't copied.

**Restore …** asks which maps to restore from the selected backup and how. Neither way is
preselected:
- *Restore as new map* adds it next to the others as, for example, `Main (31 Oct)`. Nothing is
  overwritten.
- *Replace the map* backs up the current state first, then puts the backup's copy in place,
  together with that map's home system and naming scheme. The open map is closed and opened again;
  characters stay where they are.

*Also restore settings* (off by default) takes the overlay, naming, zKill and other choices from
the backup. The list of maps stays as it is. Settings closes afterwards so it shows the restored
values when opened again.

**A damaged map file** (one SQLite can't read, for example after a crash or a half-synced copy) is
renamed to `map.db.damaged` (or `.damaged-2` …, with its `-wal` and `-shm`) and never deleted.
At start, Homebound then opens that map empty and shows Settings → Backups; when you switch to a
damaged map, you stay on the open one and the map is empty next time. To get it back, pick a backup
and restore the map with *Replace*. Homebound warns at every start while a `.damaged` file is
there; delete it once the map is back. A map that can't be opened for another reason, such as a
read-only or locked file, is never moved: at start Homebound says why and quits.

**Network**: apart from the map data download on first start (and `--build-static`), the only requests go to zKillboard, for systems in visible chains, and to EVE-Scout (api.eve-scout.com), for the public list of Thera and Turnur connections. zKill requests are rate-limited to one per second and cached for 10 minutes; turn them off in Settings → zKill. EVE-Scout is asked once every 5 minutes and nothing about you or your map is sent; turn it off in Settings → Routes.

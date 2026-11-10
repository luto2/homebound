# Plan: persistent system aliases

Status: not started. Owner task "persistent system alias" (`TODO.md`, human-added task 7).

Depends on: nothing. Touches `mapdb/store.py`, `mapping/naming.py`, the rename dialog and the system
panel. If the alias should sync on cloud maps, it also touches `sync/protocol.py` and the server (see
open decision 2).

## What there is now

A system can get a custom name (`MapSystem.custom_name`, set from Rename, the naming dialog or the
system panel's name field). It lives in the `systems` row, so it is lost when the system leaves the map:
on expiry, collapse or removal. Next time the same system is mapped it gets the scheme name again, and
names like "Staging", "Farm C5" or "Hostile static" have to be typed again.

## Behaviour

- An **alias** belongs to a solar system, not to its place on the map. It survives the system leaving the
  map and comes back when the system is mapped again.
- Rename gets a check box: **"Remember this name for J123456"**. Checked, the name is saved as an alias as
  well as the custom name. The system panel shows a small ⟲ mark next to a remembered name, with a
  tooltip saying so.
- When a system is added to the map (`Store.add_system`, from tracking or by hand) and it has an alias,
  the alias becomes its `custom_name`. The naming dialog shows it as the resulting name.
- **Forget**: Rename with the box unchecked deletes the alias; clearing the name deletes both.
- Aliases are listed in Settings → Naming (a new "Remembered names" table: system, alias, delete button), so
  stale ones can be cleaned up without the system being on the map.

## Where to store it

A new table in the map file, `aliases(system_id INTEGER PRIMARY KEY, name TEXT, set_at TEXT)`, written
only through `Store` (`set_alias`, `alias`, `aliases`, `delete_alias`). Per map, because maps are often
for different groups and the same system means different things to them; it is also backed up and
restored with the map without extra work. `Store.remove_system` must not touch it.

## Steps

1. `Store`: table, accessors, and `add_system` filling `custom_name` from the alias when none is given.
   Undo only covers the entities in `ENTITIES` (`Store.batch` records changes per entity), so a rename's
   undo would leave the alias behind. Either add `alias` to `ENTITIES` with a flag that keeps it out of
   sync (`protocol.ops_from_change` skips it) or record the old alias in the batch by hand. Check how
   `Change`/`Action` are built before choosing; the first is cleaner and makes step 4 small.
2. UI: the check box in the rename `PromptDialog` (`ui/map_window.py` `_rename`) and in the system panel
   (a small toggle next to the name field); the ⟲ mark.
3. Settings → Naming: the "Remembered names" table (`ui/settings_dialog.py` `_build_naming_page`).
4. Sync (only if open decision 2 says yes): a new entity `alias` keyed by system ID, added to
   `ENTITIES`, `ENTITY_TABLES`, `ENTITY_WHERE`, `protocol.py` field lists and the server's `MapState`.

## Tests

- An alias survives `remove_system`, `Tracker.expire` and `_collapse`, and comes back as `custom_name`
  when the system is added again (by tracking and by hand).
- `Namer.display` shows it like any custom name.
- Undo of a rename that set an alias removes the alias again.
- A backup restore brings the aliases back.
- Old map files get the empty table on open (migration test like the other `COLUMN_ADDITIONS`).

## Open decisions

1. Per map (recommended, above) or global across all maps (in `settings.json`)?
2. Sync aliases on cloud maps? Recommended: yes, eventually, because a group's "Staging" is shared
   knowledge; but as a second step, since it changes the protocol on client and server.
3. Should an alias also replace the scheme name in bookmark names (the copy-bookmark button)? Recommended:
   no; bookmarks follow the scheme so they sort in the client.

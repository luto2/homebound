# Plan: persistent system aliases

Status: implemented (2026-11-10, commit `34c17f6`), see "As built". Deviations: an alias is a remembered `custom_name` and never touches `label`; aliases are local-only until the protocol gate exists (TODO at `LOCAL_ENTITIES` in `mapdb/store.py`); Delete in Settings acts at once, not on Save.

Depends on: nothing. Touches `mapdb/store.py`, `mapping/naming.py`, the rename dialog and the system
panel. If the alias should sync on cloud maps, it also touches `sync/protocol.py` and the server (see
open decision 2).

## What there is now

A system gets its scheme name once, when it is attached, and stores it in `MapSystem.label`
([`done/plan-stable-hole-names.md`](plan-stable-hole-names.md)); the user's own name is
`MapSystem.custom_name` (set from Rename, the naming dialog or the system panel's name field) and wins
over the label. Both live in the `systems` row, so both are lost when the system leaves the map: on
expiry, collapse or removal. Next time the same system is mapped it gets a new scheme name and no
custom name, and names like "Staging", "Farm C5" or "Hostile static" have to be typed again.

An alias is only ever a remembered **custom name**. It never writes `label`: the label stays what the
scheme gave the system when it was attached, so the numbers and the "names never change on their own"
rule are untouched, and clearing the custom name still shows the scheme name.

## Behaviour

- An **alias** belongs to a solar system, not to its place on the map. It survives the system leaving the
  map and comes back when the system is mapped again.
- Rename gets a check box: **"Remember this name for J123456"**. Checked, the name is saved as an alias as
  well as the custom name. The system panel shows a small ⟲ mark next to a remembered name, with a
  tooltip saying so.
- When a system is added to the map (`Store.add_system`, from tracking or by hand) and it has an alias,
  the alias becomes its `custom_name` (a given `custom_name` wins). That is the moment the system is
  named, so it fits the stable-names rule. An answer to the naming dialog without a typed name keeps it.
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
   undo would leave the alias behind. Done as a second tuple, `LOCAL_ENTITIES = ("alias",)`, that the
   store's batch, undo and row helpers know but `ENTITIES`/`ENTITY_TABLES` (what sync iterates) don't;
   `Action.synced()` drops those changes before `ops_from_action`.
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

## As built

- **Alias and naming**: the alias fills `custom_name` only, never `label` (see "What there is now").
  `naming.rename_system(store, system_id, name, remember)` is the one action behind Rename and the
  system panel (name, alias and Undo in one batch); `Tracker.apply_naming` no longer overwrites an
  existing `custom_name` with None when the dialog had no typed name.
- **Storage**: table `aliases` in `schema.sql`, `Store.alias/aliases/set_alias/delete_alias`,
  `Alias` dataclass. A new table needs no `COLUMN_ADDITIONS` entry: `CREATE TABLE IF NOT EXISTS` gives
  an old file the empty table when it is opened, nothing is rewritten, so no migration backup is made.
- **Sync (decision 2)**: local only for now. `LOCAL_ENTITIES` in `mapdb/store.py` carries a TODO; the
  custom name that an alias produces does sync like any custom name. Adding the entity needs the
  protocol version gate first (same reason as `protocol.LOCAL_SYSTEM_FIELDS`).
- **UI**: the Rename dialog has the check box (starts checked when the system has an alias); the system
  panel has a checkable ⟲ button next to the name; Settings → Naming scheme has a "Remembered names"
  table whose Delete acts at once (map data, not a setting).
- **Not done**: the naming dialog ("was this the static?") doesn't show the alias as its resulting name,
  and has no remember box; it keeps the name the system already has.
- Decisions taken as recommended: 1 per map, 2 sync later, 3 bookmarks keep following the scheme.
- **2026-11-11: aliases sync** (decision 2, step 4). `alias` is a synced entity (`ENTITIES`, `protocol.FIELDS`),
  sent only to servers and clients of protocol 2 (`protocol.GATED_ENTITIES`, see `sync-protocol.md` §3).
  `LOCAL_ENTITIES` is empty but stays for the next local-only entity. Aliases made while the server spoke
  protocol 1 stayed local and are not sent later; the next change to one sends it.

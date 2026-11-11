# Plan: structure list per system

Status: not started. Owner task 9 in `TODO.md` (human-added): "rework/add structure list, combat scanned
structures automatically go in there".

Depends on: [`plan-combat-scan.md`](plan-combat-scan.md) (the parser has to recognise structure rows
first).

## What there is now

No structure list. A system has one owner plate (`MapSystem.owner_ticker`, `owner_slot`) and one
free-text `owner_note` whose placeholder says "Note (structure, timers …)". So structures are typed into a
note by hand, and a combat-probe paste of structures ends up in the sig table as wrong "signatures" (see
the combat-scan plan).

## Behaviour

- **System panel**: a "Structures" section under the details, above the signatures. One row per
  structure: type badge (`Astrahus`, `Fortizar`, `POCO`, `Skyhook`, `POS`), name, owner ticker (typed or
  taken from the name), state, and a timer if one is set.
- **From a paste**: structure rows of a combat paste are added or updated by name + type (structures have
  no stable probe ID across scans), with `last_seen_at`. A structure missing from a later combat paste
  that scanned *that system* is marked "not seen" (dimmed), not deleted: structures don't despawn, the
  probes may just not have reached it.
- **By hand**: "Add structure" button (type, name, owner); right-click → edit, set timer, delete.
- **Timers**: optional `timer_at` and kind (armor / hull / anchoring / unanchoring). The node shows a
  `⏱ 4h` top-edge tab when a timer is under 24 h away (same tab style as `SCAN 26h`), and a toast fires
  15 minutes before.
- The owner plate stays: it is the *system* owner. Offer "Use as system owner" on a structure row to set
  it from the structure's ticker.
- **Persistence**: structures survive the system leaving the map (like aliases in
  [`plan-system-alias.md`](done/plan-system-alias.md)): when the system is mapped again, its known structures
  are there.

## Where it lives

- `mapdb/store.py`: a `structures` table, key `(system_id, type_name, name)`, fields `owner_ticker`,
  `state`, `timer_at`, `timer_kind`, `note`, `first_seen_at`, `last_seen_at`, `source` (`paste`/`hand`).
  A new sync entity `structure` so groups share it (protocol and server change; see open decision 1).
  `remove_system` must not delete it.
- `mapping/signature_parser.py`: the merge sends structure rows to `Store.upsert_structure` instead of
  the signature table.
- `ui/structure_list.py` (new): model, delegate and the add/edit dialog (reuse `BaseDialog`,
  `PromptDialog` style). Mounted in `ui/system_panel.py`.
- `ui/map_scene.py`: the timer tab.
- Migrate `owner_note`? No: keep it, it holds more than structures.

## Tests

- A combat paste with two structures adds two rows; the same paste again only updates `last_seen_at`;
  a later paste without one marks it not seen and keeps it.
- Structures survive `remove_system` and come back when the system is mapped again.
- Timer tab: shown under 24 h, gone after the timer passes; toast once, 15 min before.
- Undo of "delete structure" brings it back (one batch).
- Old map files get the empty table.

## Open decisions

1. Sync structures on cloud maps? Recommended: yes (structures are group intel and last), in the same
   release as the combat-scan protocol change.
2. Match by name + type, or let the user merge duplicates by hand when a structure is renamed? Recommended:
   name + type, plus a "Merge into …" action later if renames turn out common.
3. Should structures also be listed across all systems (a "Structures" dock or Settings page with timers
   sorted by time)? Useful for timers; a second step.

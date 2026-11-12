# Plan: combat-probe results (ships, structures, deployables) in the paste

Status: implemented (2026-11-12, `0402ae7`). Deviations: no universe schema bump (generated scan_type_table.py); ships in their own section; unknown scan groups still become sigs; the probe-scanner combat format is a guess.

Depends on: nothing. Do it before the structure list. Touches `mapping/signature_parser.py`,
`mapdb/store.py`, `sync/protocol.py` (and so the server), `ui/signature_table.py` and
`ui/system_panel.py`.

## Problem (verified in the code, 2026-11-09)

`parse_scanner_text` knows two scan groups: "Cosmic Anomaly" becomes kind `anom`, and *everything else*
becomes kind `sig`. Combat-probe results (scan group "Ship", "Structure", "Deployable", "Drone", …) go
through the same path and do damage:

1. **They are filed as cosmic signatures under the wrong group.** `group_for_text` matches substrings, so
   (checked by calling it): `Frigate`, `Astrahus`, `Force Auxiliary` and `Skyhook` → `unknown`
   ("Unscanned"); `Combat Recon Ship` → `combat`; `Customs Office` → `ice` (because of "off**ice**").
2. **A combat-only paste marks every real signature missing.** `_merge` marks as missing all existing rows
   whose kind is in the paste. The combat rows are kind `sig`, so pasting a combat scan with no cosmic
   signatures in it flags every wormhole and site in the system as gone, and the "missing from the last
   paste" banner offers to delete them.
3. **They stay forever.** A ship that warped off is a row in the sig table until someone deletes it.

## The paste format (to check first)

Cosmic rows are `ID, scan group, group, name, signal, distance`. Combat rows are believed to have the
same columns, with scan group `Ship` / `Structure` / `Deployable` / `Drone` / `Fighter` / `Starbase`, the
ship or structure group (`Frigate`, `Citadel`, …) and the type or name in the name column. **Before
writing code, paste a real combat scan (one ship, one Upwell structure, one deployable, one POCO and one
Skyhook) from the English and German clients into `tests/data/` and adjust this plan to what it shows.**
The parser must not guess this format.

## Behaviour

- New kinds next to `sig` and `anom`: `ship`, `structure`, `deployable` (drones and fighters are dropped:
  they are noise). The kind comes from the scan group column, through a lookup table like `ANOMALY_WORDS`
  (English and German).
- `group_for_text` is only used for `sig` and `anom` rows. Ship and structure rows keep the client's group
  text (`Frigate`, `Citadel`) in `group`, and the type or name in `name`.
- **Missing marks stay per kind** (the existing rule), so a combat paste never touches cosmic rows, and a
  cosmic paste never touches ships.
- **Ships are transient.** A combat paste replaces the ship rows of the system: ships not in it are
  deleted, not marked missing. Ship rows older than `SHIP_TTL` (15 min) are dropped by the expiry timer.
  They are never linked to connections and never count toward the rescan status (`scanned_at` is only
  set by pastes with `sig` rows).
- **Signature table**: a "Ships" group (one row per ship: group, type, distance-free age) and a
  "Deployables" group, shown after the anomalies, folded by default. Structures go to the structure list
  (task 9); until that exists, a "Structures" group.
- The node gets a small `⚔ 3` chip while the system has ship rows younger than 5 minutes, so a
  combat scan shows up on the map.

## Steps

1. Record real pastes in `tests/data/` (see above). Write the parser tests against them first.
2. Parser: `SCAN_GROUP_KINDS` table; `parse_scanner_text` sets kind from it, skips drones and fighters,
   and only calls `group_for_text` for cosmic rows. Unknown scan groups are skipped, not stored as `sig`.
3. Merge: ship rows are replaced per system, the other kinds keep the existing missing rule;
   `scanned_at` only for `sig`/`anom` pastes. `Tracker.expire` (or a separate timer in `AppState`) drops
   old ship rows.
4. Protocol: add the new kinds to the `kind` choice in `sync/protocol.py`. **Compatibility:** an old client
   rejects an op with an unknown kind, so either ship this in a release that every cloud user updates to
   (server checks the client version in `hello`), or keep ships local-only (open decision 1).
5. Table and node: groups, titles and the `⚔` chip.

## Tests

- Parser on the recorded pastes: kinds, groups and names as the paste shows; drones skipped; `Customs
  Office` is a structure, not ice.
- A combat-only paste leaves every `sig` and `anom` row untouched (state, `seen_at`) and doesn't change
  `scanned_at`. This is the regression test for problem 2; write it first and watch it fail.
- A second combat paste without a ship deletes that ship; a ship row past `SHIP_TTL` is removed by expiry.
- A mixed paste (cosmic + combat) treats each kind by its own rule.
- Protocol: the new kinds pass `op_from_json`; an unknown kind still fails.

## Open decisions

1. Sync ship rows on cloud maps? They are useful intel for a group but go stale in minutes, and they need
   the protocol change. Recommended: sync structures and deployables (they last), keep ships local.
2. `SHIP_TTL` 15 minutes?
3. Show the `⚔` chip in the overlay too?

## Owner decisions (2026-11-12)

- Ships are synced like sigs (so the new kinds go through the protocol 2 version gate, see
  `done/sync-protocol.md`).
- No 15-minute expiry: ship rows stay until the next combat paste of that system replaces them (or they
  are deleted by hand).
- No ⚔ chip in the overlay.

## Implementation notes (2026-11-12)

- **Paste formats.** The owner's real paste (`tests/data/dscan_j161940_structures.txt`) is the directional
  scan format: `type ID, name, type, distance`. `signature_parser.parse_paste` reads it and the probe
  scanner format in one pass. A d-scan row's type ID decides what it is: a ship if the universe file's
  `ship_types` has it, a structure or deployable if `mapping/scan_type_table.py` has it, else it is left
  out (celestials, drones, wrecks, NPCs). Type names are taken in English from those tables, so keys match
  across client languages.
- **No universe schema bump.** Structures, POCOs, Skyhooks, control towers and deployables aren't in
  `universe.sqlite`; instead `tools/make_scan_types.py` writes them from the SDE into a Python module that
  ships with the app (147 types; unpublished ones kept, since the Customs Office, type 2233, is
  unpublished). POS modules are left out (noise); Mercenary Dens count as structures.
- **Combat-probe rows** (`ABC-123, Ship/Structure/Deployable/Drone/Fighter/Starbase, group, name, …`) become
  objects by their scan group (English and a few unverified German words). Deviation: a scan group we
  don't know still becomes a cosmic signature as before, not skipped, so French/Russian/… clients keep
  pasting sigs. Not checked against a real combat-probe paste: the name column is taken as the type.
- **Merge** (`merge_scan`, one batch): sigs only go through `merge_signatures` when the paste has some (fixes
  the "everything missing" bug and doesn't touch `scanned_at`); any d-scan or combat paste replaces the
  system's ships and deployables (keys: probe ID, or `<type ID>-<count>` so the same scan gives the same
  keys). No TTL (owner decision). Ships go with their system.
- **Sync:** new entity `ship`, gated at protocol 2 (`GATED_ENTITIES`); protocol 1 peers get none.
- **UI:** ships are a folded "Ships" section in the system panel (not rows of the sig table, which holds
  only `Signature` rows), opened by a paste with ships. The node shows `⚔ 3` while ships were seen in the
  last 5 minutes; not in the overlay.

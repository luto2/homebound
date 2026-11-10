# Plan: combat-probe results (ships, structures, deployables) in the paste

Status: not started. Owner task 8 in `TODO.md` (human-added): "category for combat scanned ships". It is
also the groundwork for task 9, the structure list ([`plan-structure-list.md`](plan-structure-list.md)).

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

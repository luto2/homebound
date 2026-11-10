# Plan: show what the zKill activity was, not just how much

Status: implemented (2026-11-10, commits `7e99e8d`, `0ae364d`, `1665163`). Deviations: NPC kills count toward the meter and the map like any other kill (owner, 2026-11-10: the "Count NPC kills" setting from the first build was removed; the list still dims NPC rows); the tooltip rows show on any hover of the node; no copy button, double-click/Enter opens the kill; fold state not saved; `tests/data/zkill_sample.json` is hand-built, not recorded. Universe schema 4: every user rebuilds the game data once.

Depends on: nothing. Touches the zKill worker, the map file schema, the universe build, the system panel
and the node tooltip. Four commits, in the order of the steps below.

## What there is now

`zkill.py` `summarize` reduces the API answer to four numbers (`ZkillStats`: kills 1h, kills 24h, pod
kills 1h, time of the last kill), stored in the map's `zkill` table. The node and the overlay show a
three-bar meter and `1h/24h`; nobody can see *what* died.

## What the API already gives us (checked 2026-11-09)

`https://zkillboard.com/api/kills/solarSystemID/<id>/pastSeconds/86400/` returns full killmails, so no
extra request (and no ESI call) is needed. Each entry has:

- `killmail_id`, `killmail_time`
- `victim`: `ship_type_id`, `character_id`, `corporation_id`, `alliance_id`
- `attackers[]`: `ship_type_id`, `corporation_id`, `alliance_id`, `final_blow`, and `faction_id` for NPCs
- `zkb`: `totalValue`, `npc`, `solo`, `awox`, `labels`

What we can't show without ESI: character, corp and alliance *names*. IDs only, or a link to zKill.

## Behaviour

- **System panel**, under the details: an "Activity · last 24h" section, newest first, at most 10 rows,
  folded by default like a signature group. One row per kill:
  `12m ago · Loki (Strategic Cruiser) · 1.2B · 6 attackers` with an `NPC` or `SOLO` chip where zKill
  says so, and the attackers' most common ship type when there are several (`mostly Legion`).
  Double-click (or the row's ⧉ button) opens `https://zkillboard.com/kill/<id>/` in the browser.
- **Node tooltip** on the zKill meter: the three newest rows in the same format.
- **NPC kills** are listed but dimmed, and don't count toward the meter. Today a rat killing a ratter
  makes the system look hot. Whether this changes the meter is an open decision.
- Nothing new in the overlay: it stays a glance view.

## Steps

1. **Ship names in the universe file.** `universe/build_static.py` reads `types.jsonl` and `groups.jsonl`
   from the SDE zip it already downloads, and writes `ship_types(id, name, group_name)` for category 6
   (ships) only, about 500 rows. `universe/db.py` `Universe.ship_name(type_id)` returns `"Loki"` or
   `None`. This is a universe schema change, so bump its version: every user gets the rebuild dialog
   once (`UniverseDataError`). Ship it in the same release as the SSL fix
   ([`done/plan-appimage-ssl.md`](plan-appimage-ssl.md)) so that rebuild works on Arch.
2. **Keep the kills.** In `zkill.py`, `summarize` also returns a list of `KillSummary` rows
   (`killmail_id, killed_at, victim_ship_id, attacker_count, top_attacker_ship_id, value, npc, solo`).
   `mapdb/store.py` gets a `zkill_kills` table keyed by `(system_id, killmail_id)` (added in
   `COLUMN_ADDITIONS` style, as a new table), replaced per system on each fetch, so it never holds more
   than the last 24 h. Local only: like `zkill` it isn't a synced entity.
   Skip malformed entries the way `summarize` already skips bad times; add a `try` for non-dict
   `victim`/`attackers` (this overlaps with [`plan-zkill-worker.md`](plan-zkill-worker.md); do that plan
   first or in the same branch).
3. **System panel section.** A small model/delegate in `ui/system_panel.py` (or a new
   `ui/activity_list.py` if it grows past ~150 lines), painted with the existing chip and row helpers
   from `theme/painting.py`. Format value with the ISK formatter in `ui/cloud.py` `isk` (move it to
   `ui/widgets.py` so both use it).
4. **Tooltip.** `ui/map_scene.py` `SystemNode` tooltip adds the three newest rows when the cursor is over
   the meter.

## Tests

- `summarize` on the recorded sample (save one real answer as `tests/data/zkill_sample.json`):
  counts as before, plus the kill rows with the right ship IDs, attacker count, NPC and solo flags.
- Malformed `victim`, `attackers` or `zkb` entries are skipped, not raised.
- `Store.replace_zkill_kills` drops older rows of the same system and leaves other systems alone.
- `Universe.ship_name` for a known ship, an unknown ID and a non-ship type.
- UI: the panel shows one row per stored kill, NPC rows dimmed, and double-click calls
  `QDesktopServices.openUrl` with the kill URL (patched).

## Risks

- zKill can change its answer format again (it used to return IDs only). Parse defensively and fall back
  to the counts when the details are missing.
- One more universe rebuild for every user.

## Open decisions

1. Should NPC kills stop counting toward the meter and the recent-kill mark? Recommended: yes, with a
   setting "Count NPC kills" (off by default) on the zKill page.
2. Show victim corp/alliance as IDs, or not at all? Recommended: not at all in the row; the zKill link
   has the names.

# ZkillMeter

A three-bar meter (none, low, medium, high) plus a separate diamond for a kill in the last 10 minutes.

- Shows the level by the **number of filled bars** (0–3), not colour; the fill colours `zk-low` → `zk-high` only back that up.
- **Recent kill (<10 min)**: a `zk-recent` filled diamond with "!" in `ink-inverse`, beside the bars. It is brighter than every other mark on purpose.
- Where there's room (table, overlay), show the counts `kills 1h/24h` in `mono-data` after the meter: "3/41".
- zKill disabled in settings: hide the meter; never show stale numbers without the `stale` treatment.
- Suggested thresholds (24h kills): low 1–4, medium 5–19, high ≥ 20, or ≥ 3 in the last hour.

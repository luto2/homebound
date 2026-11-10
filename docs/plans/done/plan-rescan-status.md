# Plan: rescan status on systems

Status: implemented 2026-11-01, except the optional overlay marker. Deviations from the plan:
- `AppState.scan_status(system_id)` / `AppState.mark_scanned(system_id)` are the one entry point for node, table and panel.
- The tab age is whole hours (`SCAN 26h`), days from 100 h on (`SCAN 5d`).
- On home with UNCONFIRMED *and* characters there is no room for the compact `SCAN` tab and the `+n` marker chip,
  so the tab is left out there (the hatch and the tooltip remain).
- The `NO SCAN` set is `System.wormhole_space` (not `not known_space`, which would include Pochven).

Depends on: nothing.

Build order across the plans: [rescan status](plan-rescan-status.md) → [backups](plan-backups.md) → [Windows support](plan-windows-support.md) → [packaging](plan-packaging.md) → [code signing](../plan-code-signing.md).

## What exists

- `ui/map_scene.py` marks a node `stale` when its last paste (`systems.scanned_at`, set by
  `signature_parser.merge`) is older than `STALE_AFTER = 24h`: hatched body, content at
  `op-stale`, and `stale 26h` right-aligned on line 3.
- `ui/signature_table.py` has its own copy of `STALE_AFTER` and draws rows faint.
- `ui/system_panel.py` shows `last paste 26h ago` / `never pasted` in the sig summary.

Gaps:
- A system that was **never pasted** isn't marked at all (`scanned_at` is NULL → not stale).
- The marker is a small grey text on line 3, easy to miss next to the owner. The UNCONFIRMED tab
  on the top edge is what catches the eye.
- The 24h threshold is hard-coded, in two places.
- The state is only computed in `SystemNode.refresh()`. A node that crosses the threshold while
  nothing else happens isn't repainted until the next map change.
- An empty system can't be marked as scanned: Ctrl+A/Ctrl+C on an empty scanner gives nothing to
  paste.

## Design

A top-edge tab like `▲ UNCONFIRMED`, in the `stale` colour (grey, per the design tokens: hatch plus
label, never colour alone):

| State | When | Tab | Body |
|---|---|---|---|
| Fresh | last paste younger than the threshold | none | normal |
| Rescan due | last paste older than the threshold | `SCAN 26h` | hatched, content at `op-stale` (as now) |
| Never scanned | no paste yet, **w-space only** | `NO SCAN` | normal (nothing is out of date yet) |

- `NO SCAN` only appears on wormhole systems (`not known_space`: C1–C6, C13, Thera, Drifter). K-space ends
  of a chain never get it. `SCAN 26h` still shows on any system that has been pasted at least once.
- The line-3 `stale 26h` text goes away; the tab replaces it.
- Order on the top edge: `▲ UNCONFIRMED`, then the scan tab, then the character markers
  (`_character_marker_start` already moves along after each tab). HOME stays on the right.
- Width: the node is 168 px and HOME sits at x = 122. With both UNCONFIRMED and a scan tab, the
  scan tab drops its word (`26h` / `—`) so it ends before HOME.
- Tooltip line: "Last paste 26h ago: rescan due (threshold 12h)" or "Never scanned".
- Optional, later: a small `scan 26h` marker in the overlay rows.

## Changes

1. **New `homebound/mapping/scan_status.py`** (pure, no Qt):
   `scan_status(scanned_at: str | None, now: datetime, due_after: timedelta) -> ScanStatus`
   with `state` (`"fresh" | "due" | "never"`) and `age_text`. Move `age_text` here from
   `map_scene.py` (keep a re-export). Both `STALE_AFTER` copies go away.
2. **Settings** (`settings.py`): `rescan_due_hours: int = 24`, set by the user in whole hours,
   with a range (1–72) in `SETTING_RANGES`. Settings dialog: a new **Signatures** page with an
   hours spin box ("Rescan due after [24] h").
3. **`SystemNode`** (`ui/map_scene.py`): replace `self.stale` with `self.scan_status`; draw the tab
   in `_paint_tabs`, keep the hatch for `"due"`, remove the line-3 text, extend the tooltip.
4. **Signature table / panel**: rows faint for `"due"` (as now, via `scan_status`); the summary
   becomes `last paste 26h ago · rescan due` in the warning style.
5. **Repaint on time**: a 60 s timer in `AppState` (or the existing expiry timer if its interval
   is close) that emits a new `scanAgeTick`. Nodes recompute their status and only call `update()`
   when the state or the shown text changed (after the first hour the label changes once an hour,
   so this is cheap).
6. **"Mark as scanned now"**: system context menu item, sets `scanned_at` to now. It's for empty
   systems and for k-space ends you don't care about. It isn't undoable; it only changes a timestamp.
7. **Docs**: user guide §2 (node table) and §5, README sigs bullet, `design-system/README.md` stale
   row (tab instead of line-3 label).

## Tests

- `tests/test_scan_status.py`: fresh / due / never, exactly on the threshold, future timestamps
  (clock skew → fresh), threshold from settings, `never` only for w-space systems.
- `test_ui_map_scene.py`: tab drawn for due and never, not for fresh; compact tab next to
  UNCONFIRMED; character markers start after it; tick repaints only changed nodes.
- `test_settings.py`: range clamping of `rescan_due_hours`.

## Decisions (2026-11-01)

- Rescan threshold: set by the user in whole hours (default 24, range 1–72).
- `NO SCAN` only on w-space systems.

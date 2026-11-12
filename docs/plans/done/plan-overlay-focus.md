# Plan: the overlay shows the nearby part of a big chain

Status: implemented (2026-11-12, `48a1e2e`). Deviations: the way home folds into "⋯ N systems on the way home"; everything below the character is shown past the radius; row cap 5–40; distances along the shown tree; HOME chip on summary rows.

Depends on: nothing. Touches `mapping/chain.py`, `ui/overlay.py`, `settings.py`, the settings dialog's
overlay page and their tests.

## What there is now

`resolve_chain` (`mapping/chain.py`) returns every system of the character's chain as tree rows
(`_chain_rows`). Only depth is limited (`max_depth`, deeper rows fold into `…`); breadth is not.
`Overlay._layout` sizes the window to `len(chain.rows)`, so a chain of 30 systems is a 30-row window
over the game.

## Behaviour

- **Focus around the character.** By default the overlay shows:
  - the systems within **2 jumps** of the character's system (setting: 1, 2 or "whole chain");
  - the **path to home** (or to the chain's root), always, so you can see the way out. Systems on it
    beyond the radius are folded into one `⋯ 3 jumps` row;
  - every other branch as **one summary row** where it leaves the shown part: `├ ⋯ B-chain · 7 systems`
    plus markers (below). The summary row's text follows the stored names.
- **Highlights**, on a shown row or a summary row whose hidden part contains it:
  - **k-space exit**: a hole into high-, low- or nullsec (class from the universe), shown with the
    security colour dot already used for k-space;
  - **home**: the branch that leads home (the path above already shows it; the marker is for when home
    is reached through a summary row);
  - optionally **activity**: zKill kills in the last hour (reuses the existing zKill mark).
- **Height cap.** Even with "whole chain", the window stops growing at a maximum row count (setting,
  default 15) and folds the farthest rows into summary rows first, so it never covers the screen.
- The map window is unchanged; this is only the overlay.

## Where it lives

- `mapping/chain.py`: a pure function `focus_rows(rows, store, universe, current, radius, max_rows)` that
  takes today's full rows and returns the folded list with `ChainRow` gaining `summary_count`,
  `kspace_exit` and `leads_home` fields. Testable without Qt.
- `ui/overlay.py`: paint summary rows (muted, `⋯`), the markers, and size from the folded rows.
- `settings.py`: `overlay_focus_radius` (1, 2, 0 = whole chain; default 2) and `overlay_max_rows`
  (default 15), with range checks like the other overlay settings; the overlay settings page gets both.

## Tests

- A 30-system chain with the character 3 deep: rows are the radius-2 neighbourhood, the path to home
  folded, and one summary row per other branch with the right counts.
- A hidden branch with a hole to highsec: its summary row has the k-space marker; same for a nullsec
  exit two systems down.
- Radius "whole chain" with 40 systems: no more than `overlay_max_rows` rows.
- Character in k-space next to the chain (the virtual row): still shown, with its entrance.
- Overlay height follows the folded row count.

## Open decisions

1. Default radius 2? Recommended: 2 (the user said "1–2 jumps").
2. Should the summary row be clickable to unfold it while the overlay is unlocked? Recommended: later.
3. Count wormhole-to-wormhole holes leading to C1–C6 as "exits"? Recommended: no, only k-space and home,
   as the user asked.

## Owner decisions (2026-11-12)

- Defaults: radius 2 jumps, at most 15 rows.
- Summary rows are not clickable.
- Go deeper along the branch the character is in: that branch is shown beyond the radius (up to the row
  cap), the other branches stay folded at the radius.
- Only k-space and home count as markers; holes into w-space (C1–C6, Thera, etc.) never do.
- Highsec, lowsec and nullsec exits are three separate markers (each in its security colour), so a summary
  row can show any combination of them. `ChainRow` gets `exits: frozenset` of `"high" | "low" | "null"`
  instead of one `kspace_exit` flag; the tests check each class on its own and a branch with two classes.

## Implementation notes

- `focus_rows(rows, store, universe, current, radius, max_rows, home_id)` in `mapping/chain.py`; `AppState.chain_for_character(id, focus=True)` calls it for the overlay (settings `overlay_focus_radius` 0/1/2, `overlay_max_rows` 5-40).
- `ChainRow` gained `kind` (`""`, `"branch"`, `"path"`, `"more"`), `summary_count`, `exits` (frozenset of `high|low|null`) and `leads_home`.
- "The branch the character is in" is read as everything below the character (the way on); the way home stays folded at the radius into one `path` row (a single system is shown instead of folded).
- Under the row cap the farthest rows fold first; then a system's hidden branches merge into one `more` row. The minimum cap is 5 so home, the path row, the character and two merged rows always fit.
- The path row reads "⋯ N systems on the way home" (N folded systems), not "N jumps".
- Follow mode: `Overlay._target` returns no target when the focused character is not in `AppState.open_characters` (only when the platform can list windows).

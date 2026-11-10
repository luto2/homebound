# SystemNode

A draggable `QGraphicsItem` for one system, 168×62 at 100% zoom (`size-node-w`, `size-node-h`).

**Content layout**
1. Custom name in `node-name` (14px/700), e.g. `A2a`, `123`, `8 Jita` — the k-space real name follows without brackets in `ink-muted`; high-/low-sec nodes end the line with the closest hub, `Jita·4`. zKill meter right-aligned. Character markers sit on the top edge.
2. Class badge · effect icon · real J-name (`mono-small`, `ink-muted`) · statics right-aligned.
3. Owner tag (only when one is set) and the stale tag.

**Top-edge tabs** (13px, drawn on the node's top edge, left to right): `▲ UNCONFIRMED` (`warning`), the rescan tab, `⌂ HOME` (`accent`, at the right end), or on a hole flagged as its parent's static `STATIC` (`bg-4` with `ink`, in the same place; home is never behind a static). Characters' markers start after the last tab. The rescan tab:
- `SCAN 26h` in `stale` grey: the system was not pasted for longer than the rescan threshold (Settings → Signatures). The node also gets the 45° hatch.
- `NO SCAN`: a wormhole system that was never pasted. No hatch, only the tab.
- Nothing when the scan is fresh.
- Next to UNCONFIRMED and HOME or STATIC the tab is compact: `SCAN` with no age, or `NO` dropped, so it doesn't collide. If characters are on the node too, the compact tab is left out (the hatch and tooltip remain).

**States**: default `bg-2` + 1px `line-2`; hover `bg-4`; selected 2px `accent` outline; home gets the `⌂ HOME` tab; k-space nodes sit on `bg-1` with a `line-1` border so the w-space chain stands out; a system due for a rescan gets a 45° `line-1` hatch with its content at `op-stale` (the tabs stay at full strength).

**Tabs** are 13px chips in mono 9px/700 with `ink-inverse` text and radius 2, drawn 7px above the node's top edge (`painting.draw_chip`). Markers that don't fit become 2 markers and a `+n` chip on `bg-4`.

**Level of detail** (`QStyleOptionGraphicsItem.levelOfDetailFromTransform`):
- ≥ 0.6: full node.
- 0.35–0.6: compact — name + class badge only, height 34.
- < 0.35: minimal — name only, at a fixed 13px *screen* size (`ItemIgnoresTransformations` on a child label) so it never goes below legibility.

Consumer provides: system model, position, selection, characters present, zKill stats. The node emits `moved`, `selected`, `contextMenuRequested`.

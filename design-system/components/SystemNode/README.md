# SystemNode

A draggable `QGraphicsItem` for one system, 168×62 at 100% zoom (`size-node-w`, `size-node-h`).

**Content layout**
1. Custom name in `node-name` (14px/700), e.g. `A2a`, `123`, `8 (Jita)` — the k-space suffix is `ink-muted`. zKill meter right-aligned. Character markers sit on the top edge.
2. Class badge · effect icon · real J-name (`mono-small`, `ink-muted`) · statics right-aligned.
3. Owner tag (only when one is set) and the stale tag.

**States**: default `bg-2` + 1px `line-2`; hover `bg-4`; selected 2px `accent` outline; home gets a "HOME" tab on the top edge; k-space nodes sit on `bg-1` with a `line-1` border so the w-space chain stands out; stale nodes get a 45° `line-1` hatch, content at `op-stale`, and a "stale 3h" tag.

**Level of detail** (`QStyleOptionGraphicsItem.levelOfDetailFromTransform`):
- ≥ 0.6: full node.
- 0.35–0.6: compact — name + class badge only, height 34.
- < 0.35: minimal — name only, at a fixed 13px *screen* size (`ItemIgnoresTransformations` on a child label) so it never goes below legibility.

Consumer provides: system model, position, selection, characters present, zKill stats. The node emits `moved`, `selected`, `contextMenuRequested`.

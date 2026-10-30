# StaticBadge

A small "→ C5" chip that names where a system's static wormholes lead.

- `bg-3` chip with a `line-1` border; the arrow is `ink-faint` and the class label is drawn in that class's `class-*` colour (text, not fill), so a row of statics stays quieter than the system's own filled class badge.
- Order: w-space statics first, then k-space; at most 3 per node.
- Right-aligned on the node's second line; in the overlay they're omitted.

# ConnectionEdge

A `QGraphicsPathItem` between two nodes, with a WH-type label plate at its midpoint. Every state differs by **line width and dash pattern** first, colour second, and a label chip third.

| State | Pen | Label chip |
|---|---|---|
| Fresh | `edge-fresh`, `edge-w-fresh` 2px, solid | — |
| Reduced (<50%) | `edge-reduced`, 2.5px, solid | **R** |
| Critical (<10%) | `edge-critical`, 3.5px, dash 8/4 | **CRIT** |
| Lifetime < 4h | extra 6px `edge-eol` *dotted casing* **under** the mass pen, a dot every 9px | **◷ <4h** |
| Lifetime < 1h | the same casing, a dot every 5px (denser = sooner) | **◷ <1h** |
| Expired | no casing; the mass pen drawn at 40% opacity, so the hole reads as fading out | **✕ EXP** in `danger` |
| Frigate-only | `edge-frig`, 1.5px, diamond at midpoint | **F** |
| Unknown | `edge-unknown`, 2px, dash 3/5 | **?** |

- Lifetime combines with any mass state; frigate-only combines with mass and lifetime.
- **Expired** is kept on the map for 10 minutes after it was set, then removed. Its tooltip counts down ("Expired · removed in 7 min"). The ✕ glyph and the faded line carry the state without the colour. The chip is the only `danger` on an edge, so it stands out from the purple lifetime chips.
- Label plate: `edge-label-bg` with a 1px `line-1` border, `edge-label` (10px mono bold); it carries the WH type (`B274`, `K162`) and the chips. Labels can be hidden below 0.5 zoom; chips stay.
- Hover: pen +1px and the plate border turns `accent`. Selected: 2px `accent` halo pen under the line.
- Qt: `QPen(color, w, Qt.CustomDashLine)` with `setDashPattern([8/w, 4/w])` (Qt dash units are multiples of pen width). Lifetime casing: `Qt.CustomDashLine`, `Qt.RoundCap`, `setDashPattern([0.01, 9/6])` for < 4h and `[0.01, 5/6]` for < 1h. Expired: `p.setOpacity(0.4)` around the mass stroke.

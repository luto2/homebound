# ConnectionEdge

A `QGraphicsPathItem` between two nodes, with a WH-type label plate at its midpoint. Every state differs by **line width and dash pattern** first, colour second, and a label chip third.

| State | Pen | Label chip |
|---|---|---|
| Fresh | `edge-fresh`, `edge-w-fresh` 2px, solid | — |
| Reduced (<50%) | `edge-reduced`, 2.5px, solid | **R** |
| Critical (<10%) | `edge-critical`, 3.5px, dash 8/4 | **CRIT** |
| EOL | extra 6px `edge-eol` *dotted casing* drawn **under** the mass pen | **◷ EOL** |
| Frigate-only | `edge-frig`, 1.5px, diamond at midpoint | **F** |
| Unknown | `edge-unknown`, 2px, dash 3/5 | **?** |

- EOL combines with any mass state; frigate-only combines with mass and EOL.
- Label plate: `edge-label-bg` with a 1px `line-1` border, `edge-label` (10px mono bold); it carries the WH type (`B274`, `K162`) and the chips. Labels can be hidden below 0.5 zoom; chips stay.
- Hover: pen +1px and the plate border turns `accent`. Selected: 2px `accent` halo pen under the line.
- Qt: `QPen(color, w, Qt.CustomDashLine)` with `setDashPattern([8/w, 4/w])` (Qt dash units are multiples of pen width). EOL casing: `Qt.DotLine`, `Qt.RoundCap`.

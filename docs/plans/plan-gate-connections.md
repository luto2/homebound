# Plan: gate connections (TODO human-added task 20)

Status: implemented.

Goal: when two joined systems are also stargate neighbours, draw the connection with 90 degree corners
instead of the wormhole curve, and leave out wormhole state.

- **Derived, not stored.** `ConnectionEdge.adjust` asks `Universe.gate_adjacent(a, b)`; the result is `edge.is_gate`.
  No schema, store or sync change (sync protocol 2 stays as is). `refresh` calls `adjust`, so it follows the data.
- **Path.** `map_scene.orthogonal_path(start, end, horizontal)`: horizontal, vertical, horizontal when the nodes
  are side by side (the same test the curve uses for its ports), vertical, horizontal, vertical when stacked.
  Level ends collapse to a straight line.
- **Drawing.** A gate edge uses a solid grey pen (`edge_pens()["gate"]`, token `edge-unknown`), the same selection
  underlay and the hover widening. No label plate, mass, frigate, lifetime or unconfirmed decorations.
  `shape()` is the stroked path alone; the tooltip says "Stargate".
- **Editing.** The edge menu offers only Lost connection and Collapse for a gate. `ConnectionDialog` shows a
  banner instead of `WormholeFields` and `values()` is empty, so `edit_connection` saves nothing.
- **Tests.** Pure path function, gate check, paint and hit shape, dialog.

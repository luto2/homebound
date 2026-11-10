# Plan: select several systems and move them together

Status: implemented (2026-11-10, commit `1fe3fd2`). Deviations: the suspected bug was real (only the grabbed node was stored). The whole selection moves as one block instead of Qt's per-node move, so offsets don't drift; `AppState` got a selected count for the panel's "+N selected". Multi-delete not included.

Depends on: nothing. One commit: `ui/map_scene.py` (nodes, scene and view) and its tests.

## What there is now

- `SystemNode` is `ItemIsMovable | ItemIsSelectable`, and `MapView` uses `ScrollHandDrag`, so a drag on
  empty canvas pans and Shift+click starts a link (`MapScene.mousePressEvent`). There is no rubber-band
  selection.
- Qt's default Ctrl+click on an item toggles its selection, so two nodes can probably already be
  selected, and dragging one moves both on screen. But only the grabbed node stores its position:
  `SystemNode.mouseReleaseEvent` calls `update_system` for itself only. The others snap back on the next
  `MapScene` refresh and aren't synced. Check this in the app first; if true it's a bug today, and this
  plan fixes it.
- Each moved node is its own store change, so a moved group would need one undo per node.

## Behaviour

- **Select:** Ctrl+click toggles a system in the selection. Ctrl+drag on empty canvas draws a selection
  rectangle (plain drag still pans, Shift+click still links). Ctrl+A selects all systems, Escape clears.
- **Move:** dragging any selected node moves all selected nodes, snapped to the grid as one block (the
  grabbed node snaps, the others keep their offsets).
- **Store:** on release, every moved node's position is stored in one `Store.batch()`: one undo step,
  one set of sync ops. On cloud maps with a local layout, `update_system` already routes `x`/`y` into
  `layout_override`; nothing changes there.
- **System panel:** shows the last clicked system, as now. With more than one selected, the panel's
  header says "+2 selected".
- Keyboard: arrow keys move the selection by one grid step (Shift+arrow by five), also in one batch per
  key press. Optional, see open decision 1.

## Where it lives

- `ui/map_scene.py`:
  - `MapView.mousePressEvent`/`mouseReleaseEvent`: switch to `RubberBandDrag` while Ctrl is held on empty
    canvas, back to `ScrollHandDrag` after.
  - `SystemNode.itemChange`: snap only the grabbed node; Qt moves the others by the same delta.
  - `SystemNode.mouseReleaseEvent` → a `MapScene.store_moved_nodes()` that writes every selected node
    whose position changed, in one batch.
  - `MapScene._select`: don't clear a multi-selection when the clicked node is already in it.
- `ui/system_panel.py`: the "+n selected" note.

## Tests

- Ctrl+click two nodes, drag one: both positions stored, one undo restores both.
- Rubber band over three nodes selects exactly those; a plain drag on empty canvas still pans.
- Shift+click on a node still starts a link with a selection active.
- A moved block stays on the grid, offsets between nodes unchanged.
- Cloud map with local layout: the moves land in `layout_override`, not the shared position.
- Regression: with two nodes selected via Ctrl+click (today's code), the non-grabbed node's position is
  stored (fails today if the bug above is real).

## Open decisions

1. Arrow-key moves: worth it now? Recommended: yes, it's small once the batch move exists.
2. Should Delete remove all selected systems (with the existing undo toast)? Recommended: yes, but as a
   separate change, since a multi-delete needs its own confirm text.

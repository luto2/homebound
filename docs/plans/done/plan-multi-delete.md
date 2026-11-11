# Plan: Delete removes every selected system

Status: implemented (2026-11-11, commit `4885d92`). Deviations: none; HOW_TO describes it too.

Depends on: nothing. Touches `ui/map_window.py`, `ui/map_scene.py` (selection helper exists:
`MapScene.selected_nodes`) and tests.

## What there is now

`MapWindow._key_remove` (Del) and the node menu's "Remove from map" call `_remove(system_id)` for the one
selected system (`_selected_system`). The rest of the selection is ignored.

## Behaviour

- **Del with several systems selected** removes all of them except home, in **one** Store batch: one
  Undo toast ("Removed 4 systems from the map", detail: how many connections went with them) and one
  undo step that brings all of them back.
- **"Remove from map" in a node's menu**: when the right-clicked node is part of a multi-selection, the
  entry reads "Remove 4 systems" and removes the selection; otherwise it removes that one node, as now.
- Home is never removed; if home is in the selection the toast says "(home kept)".
- With one system selected nothing changes.

## Where it lives

- `ui/map_window.py`: `_remove_systems(system_ids)` (one batch, one toast) used by `_key_remove` and the
  menu; `_remove` becomes the one-system case of it.
- `ui/map_scene.py`: nothing new beyond `selected_nodes()`.

## Tests

- Select three systems, press Del: all three are gone, one undo brings all three back with their
  connections and names.
- Home in the selection: kept, the toast says so.
- Right-click a node inside a selection: the menu text counts the selection and removes all; right-click
  outside the selection: removes only that node.
- One selected system: same behaviour and toast as before.

## Open decisions

1. Ask for confirmation above some number (say 5 systems)? Recommended: no, the Undo toast is enough.

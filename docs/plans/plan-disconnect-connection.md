# Plan: remove a connection but keep its systems ("lost connection")

Status: not started. Owner task 5.1 in `TODO.md` (human-added): "add ability to delete connection without
deleting the systems, allow for exfilling out of a chain if the connection is lost, maybe stop mapping
additional connections there after ~1h".

Depends on: nothing. Touches `ui/map_window.py`, `mapping/tracker.py`, `mapdb/store.py` and their tests.

## What there is now

- **Collapse** (`MapWindow._collapse`, edge menu and connection dialog) removes the connection, its sigs, and
  each end that is left without a connection. The far side usually goes, so a pilot stuck behind a closed
  hole loses the systems they are in.
- **Deleting a wormhole sig** (`SystemPanel`) removes its connection and keeps both systems. That is close
  to what the task wants, but it only works when the connection has a sig, and it isn't discoverable.
- **Expiry** (`Tracker.expire`) already keeps a chain past an expired link as its own root, but removes an
  end with no connection left unless home, pinned or occupied.

## Behaviour

1. A new edge menu item and connection dialog button, **"Lost connection"** (not red: nothing is deleted
   but the link). It removes the connection and its two sigs, keeps both systems and everything behind
   them, and offers Undo like Collapse does.
2. The part of the chain that is now cut off from home becomes its own root, as after expiry. It is drawn
   with a `CUT OFF 12m` top-edge tab on its root (same tab style as `SCAN 26h`) so it is clear this piece
   is no longer reachable from home.
3. While a character is inside the cut-off piece, mapping goes on as usual: that is the exfil case (find a
   way out, map it, get home).
4. **After about an hour** with no character inside, the piece stops growing and then goes:
   - New jumps *into* systems of the piece from outside still update the map (someone found it again: the
     cut-off mark clears if it reconnects to home).
   - `Tracker.expire` removes the piece's systems once it has had no character for `CUT_OFF_GRACE`
     (1 h), unless pinned. That reuses the existing orphan rules.

## Where it lives

- `Store`: a system column `cut_off_at TEXT` (in `COLUMN_ADDITIONS`) set on the root of the cut-off piece.
  It is part of `MapSystem`, so it is recorded in batches (undo works) and syncs on cloud maps as a normal
  field; add it to the synced field list in `sync/protocol.py` and to the server's schema.
- `mapping/tracker.py`: `Tracker.disconnect(connection_id) -> list[int]` (one `@_one_action`): removes the
  connection and its sigs, finds the component that no longer reaches home (`mapping/chain.py`
  `connected_component`), sets `cut_off_at` on its root, returns that component.
  `Tracker.expire` gets a second pass for cut-off pieces past the grace time. `on_system_change` /
  `add_wormhole` clear `cut_off_at` when a new connection joins the piece back to home's component.
- `ui/map_window.py`: menu item and handler `_lost_connection`, Undo toast "Lost B2 ↔ B2a · 4 systems stay,
  removed in 1h unless someone is inside".
- `ui/map_scene.py`: the `CUT OFF` tab, next to `scan_tab_label`.

## Tests

- `disconnect` keeps both ends and the systems behind, removes the sigs, and marks only the root of the
  piece away from home. Disconnecting a link inside a piece that doesn't contain home marks nothing new.
- With a character inside, `expire` keeps the piece past the grace time; one hour after the character
  leaves (or with nobody inside from the start), it is removed; a pinned system in it stays.
- A new jump that links the piece back to home clears `cut_off_at`.
- Undo restores the connection, the sigs and clears the mark.
- Old map files get the column on open.

## Open decisions

1. "Stop mapping additional connections there after ~1h": does that mean the piece is removed after an
   hour (above), or that it stays but new jumps out of it are no longer mapped? Recommended: removal, because
   a frozen piece that still sits on the map is confusing.
2. Grace time: 1 h fixed, or a setting?
3. Should "Lost connection" replace "Collapse" in the edge menu, with Collapse moved into the connection
   dialog? Both are common; keep both for now.

# Plan: remove a connection but keep its systems ("lost connection")

Status: implemented (2026-11-11, commit `530ad2d`). Deviations: the cut-off mark is a local key_value entry (not synced); a cut-off piece is removed an hour after the last pilot left, pinned systems stay.

- **Audience:** the owner and coding agents who need to know why something was built the way it is.
- **Scope:** the record of "Plan: remove a connection but keep its systems ("lost connection")", finished. The status line says where the build differs from the plan; where the code lives now is in [`code-map.md`](../../code-map.md).
- **Kept current:** archived with the plan, not updated after it was implemented (see the status line). The rest of the text is the plan as it was written.

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

- `mapping/tracker.py`: `Tracker.disconnect(connection_id, now=None) -> list[int]` (one `@_one_action`):
  deletes the connection and its sigs, detaches the far side (`Store.clear_broken_parents`), finds the
  piece that no longer reaches home (`mapping/chain.py` `connected_component`), marks it and returns it.
  A system that loses its parent **keeps its hole number and name** (stable-names rules: only explicit
  actions rename), so nothing is renamed when a link is lost or when it is found again.
  `Tracker.expire` gets a second pass, `_expire_cut_off_pieces`, for pieces past `CUT_OFF_GRACE`.
- **The mark is local, not a system column.** The plan wanted `cut_off_at` on the system and in the
  sync protocol. A new synced field needs a protocol gate (see the stable-names plan, "As built"), so
  the mark is a local `key_value` entry (`{system id: time}`, key `cut_off`) on the system at the cut.
  It lapses by itself: it only counts while the system exists and its piece does not reach home, so
  a new connection to home, an Undo, or removal of the system clears it without a hook in
  `add_wormhole`. Cost: on a cloud map other members don't see the tab or run the countdown; every
  install that gets the removal still keeps the systems (they are just no longer connected).
- `ui/map_window.py`: menu item and handler `_lost_connection`, Undo toast "Lost B2 ↔ B2a · 4 systems
  cut off from home, removed in 1h unless someone is inside"; `ui/dialogs.py` `ConnectionDialog` has a
  plain "Lost connection" button beside the red Collapse.
- `ui/map_scene.py`: the `CUT OFF 12m` tab before `SCAN`, which turns compact next to it;
  `AppState.cut_off_age` gives the text.
- "Gone an hour after the last pilot left": the characters table keeps when each pilot left their
  previous system (`seen_at` with `previous_system_id`), so the clock is the later of the cut and that.

## Tests

- `disconnect` keeps both ends and the systems behind, removes the sigs, and marks only the root of the
  piece away from home. Disconnecting a link inside a piece that doesn't contain home keeps that piece's countdown; the part
  that has no mark yet gets its own, so it can go too.
- With a character inside, `expire` keeps the piece past the grace time; one hour after the character
  leaves (or with nobody inside from the start), it is removed; a pinned system in it stays.
- A new jump that links the piece back to home ends the cut-off mark.
- Undo restores the connection, the sigs and clears the mark.

## Open decisions

Decided as recommended when built: 1 removal, 2 fixed 1 h, 3 keep both items.

1. "Stop mapping additional connections there after ~1h": does that mean the piece is removed after an
   hour (above), or that it stays but new jumps out of it are no longer mapped? Recommended: removal, because
   a frozen piece that still sits on the map is confusing.
2. Grace time: 1 h fixed, or a setting?
3. Should "Lost connection" replace "Collapse" in the edge menu, with Collapse moved into the connection
   dialog? Both are common; keep both for now.

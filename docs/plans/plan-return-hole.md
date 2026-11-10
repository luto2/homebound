# Plan: mark a sig as the return hole in one click

Status: not started. Owner task 12 in `TODO.md` (human-added): "option to instantly mark a sig or WH as
return wormhole and set the WH type for that WH with regard to the other side and the classes".

Depends on: nothing. Ties into [`plan-stable-hole-names.md`](done/plan-stable-hole-names.md) (task 14): marking
a sig as the return hole is an explicit user action, so it may change that sig's bookmark number (see
below), and that plan's rule 1 should list it. Touches `mapping/tracker.py`, `ui/signature_table.py`,
`ui/system_panel.py` and their tests.

## Meaning (decided by the owner, 2026-11-10)

The return hole of a system B is the wormhole in B that leads **back to the system you jumped in from**
(A), not the one towards home. Usually A is B's parent on the map, but not always (you can come in from a
child, or after "Make home" re-rooted the chain).

## What there is now

When a character jumps A → B, the tracker adds the connection and links **A's** sig to it
(`Tracker._attach` → `link_signature`, only when exactly one unlinked WH sig fits, or through the naming
dialog). **B's** side of the connection never gets a sig automatically. After pasting B's scan, the
return hole is just one more unlinked WH sig: it gets a fresh bookmark number, no type and no
destination, and the table doesn't show where it leads. There is no action to link a sig to a
connection by hand from B's side.

## Behaviour

- **One click.** Right-click a WH sig in B → "Return hole to A" (A's name as the namer shows it). Also
  a keyboard shortcut on the selected row (`R`). If there is no known origin, the entry is a submenu of
  B's connections that have no sig on B's side yet.
- **Which A.** The system the tracked character came from on their last jump into B. The tracker keeps
  that in memory per character (`Tracker` already sees `previous` in its move handling); if the active
  character didn't jump into B, use B's parent; if B has no parent, the submenu.
- **What marking does**, in one batch (one undo step, one set of sync ops):
  1. Link the sig to the A–B connection on B's side (`Store.set_connection_signature(connection, B,
     sig)`), unlinking any other B-side sig that was on it.
  2. Set the sig's destination to A's class.
  3. Set its type from the other side:
     - A's sig (or the connection's `wormhole_type`) is a real type such as `D382`: B's side is `K162`.
     - A's sig is `K162`: B's side is the real type. Candidates are `Universe.wormhole_types_in(B)` that
       lead to A's class. Exactly one candidate (usually B's static): set it. More than one: leave the
       type empty and show the candidates first in the type picker. None: leave it empty.
     - A's side unknown: only the destination.
  4. Copy the connection's mass, lifetime, size and frigate-only onto the sig (the reverse of what
     `link_signature` does for A's side), so both ends of the hole show the same state.
  5. The sig's bookmark becomes the return bookmark: `-0` when A is B's parent
     (`Namer.bookmark_home`), else A's own number under B. The number it had from the paste is freed but
     not reused (see the stable-names plan).
- **Table.** The row shows `→ A` like any linked sig and gets a small "return" marker.
- **Suggestion after a paste.** If B has exactly one unlinked WH sig that fits (a K162 when A's side is a
  real type, or a type leading to A's class), the row shows a "Return hole?" hint with a one-click
  confirm. Never linked without the click.

## Where it lives

- `mapping/tracker.py`: `Tracker.mark_return(system_id, signature_id, connection_id)` (an `_one_action`)
  doing steps 1–5; `Tracker.return_origin(system_id, character_id)` for "which A"; the per-character last
  jump kept in memory.
- `mapping/naming.py`: a `return_type(universe, connection, from_side, to_side)` helper for step 3, so
  the type rule is testable without the UI.
- `ui/signature_table.py`: context menu entry, `R` shortcut, the marker and the hint.
- `ui/system_panel.py`: passes the selected character for "which A".

## Tests

- A's sig `D382` (C2 static into a C3 B): marking B's sig sets `K162`, destination C2, links it, `-0`.
- A's sig `K162`, B has exactly one type leading to A's class: that type is set.
- More than one candidate: type empty, candidates first in the picker.
- Mass, lifetime and size carry over from the connection.
- Came in from a child (not the parent): the sig links to that connection and takes that number, not `-0`.
- Marking another sig as the return hole of the same connection unlinks the first.
- Undo restores the sig and the connection in one step.
- The hint shows only for exactly one fitting sig, and does nothing until clicked.

## Open decisions

1. Should marking also fill in A's side when A's sig is missing (A has one unlinked WH sig that fits)?
   Recommended: no, only suggest it there the same way, to keep one click = one sig.
2. Shortcut `R` free in the signature table? Check `ui/menus.py` and the table's key handling first.

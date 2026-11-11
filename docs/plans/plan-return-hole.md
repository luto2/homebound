# Plan: mark a sig as the return hole in one click

Status: built (2026-11-11), see "As built" for where it differs from the first plan. Owner task 12 in
`TODO.md` (human-added): "option to instantly mark a sig or WH as return wormhole and set the WH type for
that WH with regard to the other side and the classes".

Depends on: nothing. Ties into [`plan-stable-hole-names.md`](done/plan-stable-hole-names.md) (task 14):
marking a sig as the return hole is an explicit user action, so it may rename (to `-0`, see below), and
that plan's rule 1 lists it. Touches `mapping/tracker.py`, `mapping/naming.py`, `ui/signature_table.py`,
`ui/system_panel.py`, `ui/wormhole_fields.py`, `ui/dialogs.py` and their tests.

## Meaning (decided by the owner, 2026-11-10)

The return hole of a system B is the wormhole in B that leads **back to the system you jumped in from**
(A), not the one towards home. Usually A is B's parent on the map, but not always (you can come in from a
child, or after "Make home" re-rooted the chain).

## What there was before

When a character jumps A → B, the tracker adds the connection and links **A's** sig to it
(`Tracker._attach` → `link_signature`, only when exactly one unlinked WH sig fits, or through the naming
dialog). **B's** side of the connection never got a sig automatically. After pasting B's scan, the
return hole was just one more unlinked WH sig: no type, no destination, and the table didn't show where
it leads. There was no action to link a sig to a connection by hand from B's side.

## Behaviour

- **One click.** Right-click a WH sig in B → "Return hole to A" (A's name as the namer shows it). Also
  a keyboard shortcut on the selected row (`R`). If there is no known origin, the entry is a submenu of
  B's connections that have no sig on B's side yet.
- **Which A.** The system the followed character came from on their last wormhole jump into B (the
  tracker keeps that in memory per character and system, as long as the two are still connected); if
  they didn't jump into B, B's parent; if B has no parent, the submenu.
- **What marking does**, in one batch (one undo step, one set of sync ops), `Tracker.mark_return`:
  1. Set the sig's destination to A's class.
  2. Set its type from the other side (`naming.return_type`):
     - A's sig (or the connection's `wormhole_type`) is a real type such as `D382`: B's side is `K162`.
     - A's sig is `K162`: B's side is the real type. If the connection already has one, that. Else the
       candidates are `Universe.wormhole_types_in(B)` that lead to A's class: exactly one sets it; more
       than one leave the type as it was and the type picker lists them first; none leaves it as it was.
     - A's side unknown: only the destination.
  3. Link the sig to the A–B connection on B's side (`link_signature`), replacing a sig that was on it
     (that one stays a sig). `link_signature` first carries what the sig already knew (mass, lifetime,
     size, a real type) to the connection.
  4. Copy the connection's mass, lifetime, size and frigate-only onto the sig (the reverse of what
     `link_signature` does for A's side), so both ends of the hole show the same state.
  5. The bookmark: `-0` when A is B's parent (`Namer.bookmark_home`), A's own hole number under B when
     A is a child of B, else the sig's own number. See "As built".
- **Table.** The row shows `→ A · return` when A is the parent (the table has no other way to tell a
  return hole from the hole to a child).
- **Suggestion after a paste.** If the connection has no sig on B's side and exactly one unlinked WH sig
  fits (a K162 when A's side is a real type; a type leading to A's class when A's side is a K162; either
  when it is unknown), that row shows a "↩ Return hole?" button. Never linked without the click.

## As built

- **No number is freed or stored differently.** Under the stable-names rules a sig keeps the number it got
  at paste. A linked hole's bookmark is derived from the link (`SignatureModel.bookmark_for`): `-0` towards
  the parent, the child's number towards a child. So marking a sig towards the parent shows `-0` without
  touching its stored number (the first plan said the number is freed; keeping it means an undo, or marking
  another sig, brings the old bookmark back unchanged). Nothing else is renamed.
- **From a child** the sig takes the child's hole number, because that is what its bookmark shows. The sig
  that had that number (the one the child came through, now replaced) gets the marked sig's old number, so
  no number is on two sigs.
- **Where there is no parent link** (A is neither parent nor child, e.g. a loop) the sig keeps its number.
- **Type not settled keeps what was there.** The first plan said "leave empty"; a type the user typed
  before marking is not wiped.
- **A's side missing** is not filled in (open decision 1, as recommended): only suggested through the hint
  when you are in A.
- `R` was free in the signature table (no other shortcut there but Del); it only works with the table
  focused (a widget shortcut), like Del.
- `return_type(universe, near_type, connection_type, origin_id, system_id)` takes the two types instead of
  a connection, so the rule is testable without a store.
- The "which A" memory is not saved: after a restart it is the parent (or the submenu).

## Tests

`tests/test_naming.py` `test_return_type_from_the_other_side`; `tests/test_tracker.py` the return hole group
(real type, K162 with one and with several candidates, unknown side, state copy, carry-over, replacing,
from a child, origin, one undo, arguments, candidates); `tests/test_ui_panels.py` (hint, button, menu, `R`,
panel action and toasts); `tests/test_ui_dialogs.py` (candidates first in the picker).

## Open decisions

Decided as recommended when built.

1. Should marking also fill in A's side when A's sig is missing (A has one unlinked WH sig that fits)?
   Recommended and built: no, only suggest it there the same way, to keep one click = one sig.
2. Shortcut `R` free in the signature table? Yes.

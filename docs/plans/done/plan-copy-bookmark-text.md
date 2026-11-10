# Plan: copy button gives the full bookmark text

Status: implemented (2026-11-10, commit `915fc63`). Deviations: Thera is written `TH` (the app's class label), not `Thera`; `<sig>` is the three letters.

Depends on: nothing. Works with [`plan-stable-hole-names.md`](../plan-stable-hole-names.md) (task 14) in
either order. One commit: `ui/signature_table.py` and its tests.

## What there is now

The copy button in a WH sig row (`SignatureTable.copy_row`, painted by `_paint_copy_button`) copies only
the bookmark label from `SignatureModel.bookmark_for`, for example `123-2`. The sig ID and the class are
typed into the in-game bookmark by hand.

## Behaviour

- The button copies `<number> <sig> <class>`, for example `123-2 ABC C3`.
  - `<number>`: the bookmark label exactly as today (`bookmark_for`, including `-0` for the return hole).
  - `<sig>`: the signature's letters (see open decision 1).
  - `<class>`: where the hole leads, from `SignatureModel.lead_class` (the linked system's class, else
    the destination set on the sig, else the known type's destination). Written as the app's class
    badges write it (`C3`, `HS`, `LS`, `NS`, `Thera`, …).
- No class known (an unidentified K162 with no destination set): copy `<number> <sig>`, with no trailing
  space.
- The button keeps showing just the number (`⧉ 123-2`) so the column stays narrow; its tooltip shows the
  full text that will be copied.
- The "✓ Copied" flash and the index reservation on copy stay as they are.

## Where it lives

- `ui/signature_table.py`: a `SignatureModel.bookmark_text(signature)` that builds the string from
  `bookmark_for`, the sig letters and `lead_class`; `copy_row` copies that instead of `bookmark_for`;
  `_tooltip` for the action column shows it.

## Tests

- Linked hole to a C3: copies `<label> ABC C3`.
- Unlinked sig with a known type (`D382` → C2): copies `<label> ABC C2`.
- K162 with a destination set by hand: that class.
- K162 without one: `<label> ABC`, no trailing space.
- Return hole (`-0`): the number is `-0`, the class is the class of the system it leads back to.
- Tooltip of the action cell equals what the button copies.

## Open decisions

1. `<sig>` as the three letters (`ABC`) or the full ID (`ABC-123`)? Recommended: the three letters,
   since that's what people match against the probe scanner and bookmark names stay short. A setting
   isn't worth it.

# Plan: signature groups that won't collapse

Status: implemented (2026-11-10, commit `3ee53e2`). Deviations: none; the cause was confirmed by the tests failing on the old code. Open decision 1 (auto-clearing "new") left open.

- **Audience:** the owner and coding agents who need to know why something was built the way it is.
- **Scope:** the record of "Plan: signature groups that won't collapse", finished. The status line says where the build differs from the plan; where the code lives now is in [`code-map.md`](../../code-map.md).
- **Kept current:** archived with the plan, not updated after it was implemented (see the status line). The rest of the text is the plan as it was written.

Depends on: nothing. One commit: `ui/signature_table.py` and its tests.

## Cause (from the code, not reproduced in the app yet)

`SignatureModel._grouped_rows` only collapses a group when none of its rows has a `state`:

```python
shut = key in collapsed and not any(signature.state for signature in members)
```

`state` is `"new"` or `"missing"`, set by `signature_parser._merge`, and it is sticky:

- `"new"` is set when a sig first appears and is only cleared by a **later paste that contains it**. After
  the first paste of a system, every group is full of new rows, so none of them collapses until the
  second paste.
- `"missing"` stays until the row is deleted or pasted again. Anomalies that despawn (combat and ore sites
  are the usual ones) stay missing for good, so **their groups never collapse again**. That fits "some sig
  types".

Clicking the header of such a group still flips the stored collapsed flag (`SignatureModel.toggle`), but
nothing visible changes. The next click flips it back. So the header seems to ignore clicks, or to work
only every other time.

Check this in the app before fixing: paste a system, delete one anomaly in game (or paste without it),
then try to collapse that anomaly group.

## Change

The rule that keeps groups open ("a group with new or missing rows always stays open") was meant to make
sure a paste's changes are seen. Keep that, but only for the paste that caused them:

1. **Collapse always works.** When the user collapses a group, it collapses, whatever its rows' state.
   The header shows the counts it shows today (`3 · 1 new · 2 missing`) so nothing is hidden.
2. **A paste opens the groups it changed, once.** After `merge_signatures`, the table opens the groups that
   got new or missing rows (remove those keys from `signature_collapsed`), so the user sees them. If the
   user collapses one again, it stays collapsed until the next paste changes it.
3. **Missing rows are not kept open forever.** Independently of collapse, the "missing from the last
   paste" banner already has Keep and Delete missing (`SystemPanel`); leave those as they are.

So `shut` becomes simply `key in collapsed`, and step 2 lives in the paste handler
(`MapWindow._paste_shortcut` → `SystemPanel`/`SignatureModel`), which knows the `MergeResult`.

## Tests

- A group whose rows are all `"missing"` collapses on a header click and opens on the next one (fails today).
- Same for a group of `"new"` rows.
- A paste that adds a row to a collapsed group opens that group; collapsing it again sticks until another
  paste changes it.
- A paste that changes nothing in a collapsed group leaves it collapsed.

## Open decisions

1. Should `"new"` also clear on its own after a while (say 10 minutes), so the NEW marker means "new
   since you last looked" rather than "not pasted twice yet"? Recommended: yes, as a separate small change,
   because it also affects the row highlight.

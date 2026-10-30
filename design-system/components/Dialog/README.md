# Dialog

A modal `QDialog` for the few decisions the app can't make for the user: naming a new system, choosing a sig, editing a connection.

- 400–440px wide, `bg-2`, `radius-4`, 1px `line-2`, `shadow-dialog`. Header `ui-title`; footer on `bg-1` with a `line-1` rule.
- **Preselect the best guess** and mark it with a "BEST GUESS" caption in `accent`, so Enter alone finishes the common case. Esc maps to the non-destructive answer.
- Choices are full-width rows (`lm-choice`): radio + mono sig ID + type + scan %; the selected row gets `accent-dim` and an `accent` border.
- Destructive options (Collapse) are `danger` and sit far left, away from the primary.
- Variants: naming ("Was this the static?" — see the *Naming dialog* screen), sig picker, connection editor.
- Never more than one dialog at a time. If a second jump arrives while one is open, queue it and show "1 more pending" in the footer.

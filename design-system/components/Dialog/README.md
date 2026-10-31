# Dialog

A modal `QDialog` for the few decisions the app can't make for the user: naming a new system, choosing a sig, editing a connection.

- 400–440px wide, `bg-2`, `radius-4`, 1px `line-2`, `shadow-dialog`. Header `ui-title`; footer on `bg-1` with a `line-1` rule.
- **Preselect the best guess** and mark it with a "BEST GUESS" caption in `accent`, so Enter alone finishes the common case. Esc maps to the non-destructive answer.
- Choices are full-width rows (`lm-choice`): radio + mono sig ID + type + scan %; the selected row gets `accent-dim` and an `accent` border.
- Destructive options (Collapse) are `danger` and sit far left, away from the primary.
- Variants: naming ("Was this the static?" — see the *Naming dialog* screen), sig picker, connection editor, signature editor (SigEditor), text prompt (PromptDialog), confirm (ConfirmDialog), startup error (StartupError).
- The **connection editor** uses the same Type / Size / Lifetime / Mass fields as the signature editor (`homebound/ui/wormhole_fields.py`), without Leads to (both ends are known); Size only for a K162. Footer: `✕ Collapse` far left.
- Header may carry a one-line subtitle under the title (`ui-small`, `ink-muted`) for context: the system, the sig a connection came through.
- Never more than one dialog at a time. If a second jump arrives while one is open, queue it and show "1 more pending" in the footer.

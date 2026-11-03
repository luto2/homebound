# ConfirmDialog

A Dialog variant for the one action that asks first: **deleting a map**. It replaces `QMessageBox.question`. Undo can't bring a map back, but a `before delete` backup is made first, so it can be restored from Settings → Backups.

- Title asks the question with the object in it: `Delete the map “Corp”?`. Subtitle: "This deletes its file."
- Body: a three-cell summary of what will be lost (systems, signatures, last activity), mono numbers on `bg-1`, then a help line with the home system and file name, what is kept, and where the backup is: `Home J120617 · file maps/corp.db (+ -wal, -shm). Characters and settings are kept. A backup is kept in Settings → Backups.`
- Footer: `✕ Delete map` (danger, far left) · **Cancel is the default button and has focus**, so Enter and Esc both keep the map. There is no primary button. `QPushButton.setDefault(True)` on Cancel; Delete needs a click or Alt+D.
- Afterwards: toast `Map “Corp” deleted` (`success` glyph). The active map can't be deleted, so it never appears in the Delete map submenu, and nothing switches.
- If the backup fails, the map is kept and an error toast says why: `Map “Corp” not deleted: <reason>`.

## Everything else gets Undo, not a question

Collapse connection, Remove from map, Delete selected sigs and Delete missing sigs act at once and show an **Undo toast**, like auto-naming does:
- `↶` glyph on `ink-muted`, title says what happened with names ("Collapsed A2 ↔ C"), second line says the knock-on effect ("C and 2 systems behind it were removed").
- `Undo` (primary, small) with `Ctrl+Z`, seconds left on the right, a 2px timer bar in `ink-muted`. 8 seconds; hovering the toast pauses it.
- One level of undo is enough: the store keeps a snapshot of the affected rows until the toast closes. A second destructive action replaces the toast (and commits the first).

# RestoreBackupDialog

Opened from Settings → Backups → **Restore …** (or by double-clicking a backup). It restores one backup, from a list of the maps in it.

**Dialog:** 440px, `bg-2`, `radius-4`, `shadow-dialog`, header `ui-title` "Restore the backup of 3 Nov 2026, 09:41", subtitle in `ink-muted`: `Reason: daily · 2 maps`. Footer `Cancel` (ghost) · `Restore` (primary).

**Contents, top to bottom:**
| Part | Control | Notes |
|---|---|---|
| Maps caption | `ui-label` "MAPS" | |
| One checkbox per map | `Corp  ·  J161940` | Name, then the home system (or "no home"). All ticked when the dialog opens. |
| How caption | `ui-label` "HOW" | |
| Two radios | "Restore as new map (nothing is overwritten)" · "Replace the map (the current state is backed up first)" | **Neither is preselected**: Restore stays disabled until one is picked. |
| Note | help line, shown only when the open map is in the backup | "Replacing “Main” closes and reopens it." |
| Settings | checkbox "Also restore settings (the list of maps stays)" | |

**Enable rule:** Restore is enabled when at least one map is ticked **and** a way is picked, or when only "Also restore settings" is ticked. Nothing ticked and no settings = disabled.

**Behaviour:** "as new map" keeps the existing maps and names each restored map `Main (31 Oct)`, adding ` 2`, ` 3` … when that name is taken. "Replace" overwrites the map of the same name; whatever is replaced is first backed up with reason *before restore*. Replacing the open map closes it, writes the backup's copy and opens it again, with its characters carried over. "Also restore settings" takes the settings from the backup and keeps the current list of maps.

**Qt:** `BaseDialog` subclass; checkboxes in a `QVBoxLayout`, radios in a `QButtonGroup` with no checked button; the Restore button comes from `add_buttons(primary_text="Restore")` and is toggled by `_sync()`.

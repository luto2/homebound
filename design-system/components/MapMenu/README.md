# MapMenu

Switches between maps and manages them. Each map is its own SQLite file with its own systems, sigs, home system and naming scheme; jumps are tracked on the active map only, character locations carry over.

**Toolbar button:** `▦ Corp ▾`, left of the home combo. It's styled as the one "selected" control in the toolbar (`accent-dim` fill, 1px `accent` border, semibold name) so the current map is the first thing on the bar. `QToolButton` with `setPopupMode(InstantPopup)` and `setProperty("variant", "map")`. With more than one map the window title becomes `Homebound · Corp`.

**Menu (300px):**
- `MAPS` section caption, then one row per map: ● for the active one (exclusive `QActionGroup`), **name** semibold, home system in mono `ink-faint` (or "no home"), and on the right `mono-small` stats: systems count · last activity. Picking a row switches maps and shows a toast `Map “Main”`.
- Separator · `New map …` · `Rename “Corp” …` (both open the PromptDialog) · `Delete map ▸`.
- **Delete map submenu:** every map except the active one, each with a `danger` ✕ icon; a disabled last row says "Corp is open: switch first". Picking one opens the ConfirmDialog. The whole item is disabled when there is only one map.
- Paint the stats with a `QWidgetAction` row or set them as the action's shortcut text (`QAction.setShortcut` can't hold free text; use `setText("Main\t12 sys · now")`: QMenu right-aligns the part after the tab).

**Seeing which map you're on (answer to the open question):** a menu is enough for switching; you don't switch often. For "where am I", add a **map tag** in the canvas's top-right corner, `MAP Corp` in `ui-label` caps + name, on `bg-1` with a `line-1` border. Show it only when more than one map exists. It's a plain `QLabel` child of the `QGraphicsView` viewport, so it doesn't move with the scene. A left-dock map list isn't worth the space.

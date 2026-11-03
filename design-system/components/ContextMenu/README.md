# ContextMenu

All right-click menus are plain `QMenu`s styled by the global QSS (see TrayMenu for the base look). One set of rules covers all of them.

| Where | Items |
|---|---|
| System node | ✓ Mark as confirmed (only while unconfirmed) · ― · Rename … `F2` · Draw connection from here `Shift+drag` · Set as home · Change static / hole … · ― · Mark as scanned now · Request zKill now · Add as route destination · ― · ✕ Remove from map `Del` |
| Connection edge | ✓ Mark as confirmed (only while unconfirmed) · Edit connection … `Enter` · ― · MASS: Fresh / Reduced · R / Critical · CRIT (exclusive ●) · ― · LIFETIME: Stable / ◷ < 4h / ◷ < 1h / ✕ Expired (exclusive ●) · ― · ✕ Collapse |
| Map background | Add system … `Ins` · Fit chain `F` |
| Character row | Pin overlay to this character · Show on map `Space` |
| Route destination (Routes dock) | Copy route `Double-click` · Move up · Move down · ― · ✕ Remove destination |
| Map menu (toolbar) | see MapMenu |

**Rules:**
- Items 26px, hover is a full `accent` bar with `ink-inverse` text, 14px check column (✓ toggles, ● exclusive groups), shortcuts right-aligned in `mono-small` `ink-faint`. `…` means a dialog follows.
- Section captions (`MASS`, `LIFETIME`) in `ui-label` `ink-faint` above each exclusive group, the same as the tray's OVERLAY MODE. Unknown mass is only in the editor. The lifetime items carry the same glyphs as the edge chips (◷, ✕).
- **Destructive items** go last, after a separator, with a `✕` icon in `danger` in the check column. The text stays `ink`: QSS can't style one `QMenu` item differently, so the colour comes from the action's `QIcon` (painted from the `danger` token), and the ✕ shape carries the meaning without colour.
- **Disabled items stay visible** in `ink-disabled` and say why in a tooltip. Only the reason is written here; the examples:
  - Set as home: "This is already home."
  - Change static / hole …: "Home has no parent hole." or "Not connected to a parent system."
  - Remove from map: "Home can't be removed. Set another home first."
  - Copy route: "There is no known route."
  - Move up / Move down: disabled at the first and last destination.
  - Map menu, Delete map: "There's only one map." and "Corp is open: switch first".
  Call `menu.setToolTipsVisible(True)` and `action.setToolTip(...)`; Qt doesn't show action tooltips in menus by default.
- **Mark as scanned now** and **Request zKill now** are plain actions (no dialog). Mark as scanned sets the system's last paste to now, for an empty system that gives nothing to paste; it clears the SCAN tab.
- Mark as confirmed sits at the top, above a separator, because it's the one action an unconfirmed system needs first.
- No undo prompts here; see ConfirmDialog for the Undo toast after Collapse and Remove.

# ContextMenu

All right-click menus are plain `QMenu`s styled by the global QSS (see TrayMenu for the base look). One set of rules covers all of them.

| Where | Items |
|---|---|
| System node | Rename … `F2` · Set as home · Change static / hole … (only with a parent; reopens the NamingDialog) · ― · Request zKill now · ― · ✕ Remove from map `Del` |
| Connection edge | Edit connection … `Enter` · ― · MASS: Fresh / Reduced · R / Critical · CRIT (exclusive ●) · ― · LIFETIME: Stable / ◷ < 4h / ◷ < 1h / ✕ Expired (exclusive ●) · ― · ✕ Collapse |
| Map background | Add system … `Ins` · Fit chain `F` |
| Character row | Pin overlay to this character · Show on map `Space` |

**Rules:**
- Items 26px, hover is a full `accent` bar with `ink-inverse` text, 14px check column (✓ toggles, ● exclusive groups), shortcuts right-aligned in `mono-small` `ink-faint`. `…` means a dialog follows.
- Section captions (`MASS`, `LIFETIME`) in `ui-label` `ink-faint` above each exclusive group, the same as the tray's OVERLAY MODE. Unknown mass is only in the editor. The lifetime items carry the same glyphs as the edge chips (◷, ✕).
- **Destructive items** go last, after a separator, with a `✕` icon in `danger` in the check column. The text stays `ink`: QSS can't style one `QMenu` item differently, so the colour comes from the action's `QIcon` (painted from the `danger` token), and the ✕ shape carries the meaning without colour.
- **Disabled items stay visible** in `ink-disabled` and say why in a tooltip ("Home can't be removed. Set another home first."). Call `menu.setToolTipsVisible(True)` and `action.setToolTip(...)`; Qt doesn't show action tooltips in menus by default.
- No undo prompts here; see ConfirmDialog for the Undo toast after Collapse and Remove.

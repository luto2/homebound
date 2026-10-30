# TrayMenu

The `QSystemTrayIcon` context menu: overlay on/off, unlock, overlay mode and window actions.

- `QMenu` on `bg-2`, `radius-4`, items 26px; hover is a full `accent` bar with `ink-inverse` text.
- Toggles show ✓, the exclusive overlay mode group shows ●, in a 14px check column (`QActionGroup`, checkable actions).
- Section caption "OVERLAY MODE" in `ui-label` `ink-faint`.
- Disabled items (e.g. modes that need a pinned character but none is chosen) use `ink-disabled` and say why in their tooltip.
- Plasma draws tray menus with the desktop's Qt style; apply the app QSS to the menu explicitly (`menu.setStyleSheet(...)`) so it matches.
- Tray icon: the app mark in `ink` on transparent; when the overlay is off, show it at `op-disabled`.

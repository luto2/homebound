# Plan: the tray menu's "OVERLAY MODE" caption is an empty row on Plasma

Status: not started (owner screenshot, 2026-11-13).

- **Audience:** the owner and the coding agents that build this plan.
- **Scope:** the plan "Plan: the tray menu's "OVERLAY MODE" caption is an empty row on Plasma", not implemented yet. The state of every plan is in [`README.md`](README.md).
- **Kept current:** written against `cb44ac6` (2026-11-13). Update it when the plan changes or the code it describes moves.

Depends on: nothing. One commit: `homebound/ui/map_window.py`, its test and the TrayMenu spec.

## Problem (verified in the code)

On KDE Plasma the tray menu shows a blank row between the separator under "Unlock overlay" and the
"Off" radio item, where the "OVERLAY MODE" caption should be.

`MapWindow._build_tray_menu` (`homebound/ui/map_window.py`) builds the caption as a `QWidgetAction`
whose default widget is a `QLabel`. On Linux desktops with a StatusNotifierItem host (Plasma, and
GNOME with the AppIndicator extension), Qt does not show its own `QMenu` for the tray: it exports the
menu over DBus (`com.canonical.dbusmenu`) and the desktop draws it natively (the Breeze radio buttons
in the screenshot are Plasma's, not ours). dbusmenu carries text, icons, check state and enabled
state, but no widgets, so a `QWidgetAction` arrives as an item with no label. The `build_qss()`
stylesheet on the menu is also ignored there.

Only the tray menu is affected. The in-app menus use `menus.section()` (also a `QWidgetAction`), but
they are real `QMenu` popups and render the caption fine; leave them alone.

On Windows (and X11 without an SNI host) the tray menu is a real `QMenu`, where the widget caption
works today.

## Change

1. In `_build_tray_menu`, replace the `QWidgetAction` caption with a plain disabled action:
   `caption = menu.addAction("Overlay mode"); caption.setEnabled(False)`. dbusmenu exports it as a
   greyed text row, which is what a caption looks like in a native Plasma menu. Use normal case,
   not "OVERLAY MODE": native menus don't letter-space small caps, and all caps in a native row
   reads as shouting.
   - Don't use `QMenu.addSection()`: a section is a separator with text, and dbusmenu separators most
     likely lose that text on Plasma, so the caption would disappear instead of showing as blank. Not
     checked; if someone tries it on Plasma and the title shows, it's the cleaner option.
   - One path for all platforms. On Windows the disabled action shows as grey text in the Qt menu,
     close enough to the `ink-faint` caption; not worth a platform branch.
2. Drop `QWidgetAction` (and `QLabel`, if nothing else uses it) from the `map_window.py` imports if
   they become unused (`menus.section` callers in the file still need nothing from here).
3. Update `design-system/components/TrayMenu/README.md` line 7: the caption is a disabled plain item
   "Overlay mode", because the tray menu is drawn by the desktop on Linux and can't host widgets.
   Note this once in the README so the next tray item doesn't use a widget action either. Update
   `preview.html` if it shows the caption in caps.

## Tests

- Tray menu test (`tests/` file that covers `_build_tray_menu`): the action before the first mode
  action is a plain `QAction` (not a `QWidgetAction`), its text is "Overlay mode" and it is disabled.
- A guard: no action in the tray menu is a `QWidgetAction`, so the bug can't come back through a new
  item.

## By hand

- Plasma (Wayland): right-click the tray icon; the row above "Off" reads "Overlay mode" in grey and
  can't be clicked.
- Windows: the same row shows as grey text in the Qt menu.

Effort: 1/5 · Haiku. File and fix are named; most of the work is looking at it on Plasma.

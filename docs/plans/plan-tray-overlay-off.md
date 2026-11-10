# Plan: "Off" in the tray's overlay mode list

Status: not started. Owner task "taskbar rightclick doesnt have the overlay off option just the other 3"
(`TODO.md`, human-added task 4).

Depends on: nothing. One commit: `homebound/ui/map_window.py`, its tests and the TrayMenu spec.

## Problem (verified in the code)

The toolbar's overlay segments come from `MODES` in `ui/map_window.py`: Off, Follow, Pinned, Always.
The tray menu (`MapWindow._build_tray_menu`) builds its OVERLAY MODE group from its own hard-coded list
of three (`follow`, `pinned_eve`, `pinned_always`). Off is only reachable through the separate
"Show overlay" check item above it, which users don't read as a mode. So the two places that pick the
mode offer different choices.

## Change

1. Build the tray's mode group from the same list as the toolbar. Keep the tray's longer labels with a
   dict keyed by mode, so both stay in step when a mode is added:
   `TRAY_LABELS = {"off": "Off", "follow": "Follow active client", "pinned_eve": "Pinned character · over all clients", "pinned_always": "Pinned character · always shown"}`,
   iterated in `MODES` order.
2. Drop the "Show overlay" check item. With Off in the group it says the same thing twice. Keep
   `last_overlay_mode` and `_toggle_overlay`: the planned global shortcut
   ([`plan-overlay-shortcut.md`](plan-overlay-shortcut.md)) and the tray icon's dimmed state still use
   them.
3. `_sync_tray`: check the action of the current mode, including `off`; disable "Unlock overlay" while
   the mode is off (as now).
4. Update `design-system/components/TrayMenu/README.md` (and its mockup if it shows the check item) so
   the spec matches.

## Tests

- The tray's mode group has one action per entry of `MODES`, in the same order.
- Triggering the Off action sets `settings.overlay_mode == "off"`, dims the tray icon and unchecks the
  others; triggering Follow afterwards brings the overlay back.
- Changing the mode on the toolbar checks the matching tray action.

## Open decisions

1. Remove "Show overlay" (recommended, above), or keep it as a quick toggle next to the full list? Keeping
   both is what confused the report.

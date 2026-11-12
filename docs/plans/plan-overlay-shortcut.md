# Plan: global shortcut to cycle the overlay mode

Status: not started. Owner request, 2026-11-06. This is a new feature, not an audit finding.

Depends on: nothing.

## Behaviour

`Ctrl+Shift+O` works while EVE has focus and moves to the next entry of `OVERLAY_MODES`
(`homebound/settings.py:15`): off, follow, pinned_eve, pinned_always, then back to off. It goes through
`MapWindow.set_overlay_mode` (`homebound/ui/map_window.py:706`), so the tray menu and mode buttons stay in step.

`set_overlay_mode` returns a bool, so it can refuse a mode. Read it before building: if a pinned mode is
refused, for example with no pinned character, the cycle should move on to the next mode.

## Why this needs platform code

`QShortcut` only fires while Homebound has focus, and PySide6 has no global hotkey API. Each platform needs
its own backend, in the same split as `ui/focus.py`, `focus_linux.py` and `focus_windows.py`. Neither option
below has been tried yet.

- Windows: `RegisterHotKey` through `ctypes`, with a native event filter for `WM_HOTKEY`.
- Linux: the KDE global shortcut service or the XDG GlobalShortcuts portal over QtDBus. Wayland doesn't let an
  app grab keys itself, and a portal may ask the user to confirm the binding.

Keep the dependency list as it is unless both backends turn out to be impractical.

## Tests

Test the cycle order and the skip over a refused mode without a real hotkey, by calling the handler directly.
Fake the backend, and check that a failed registration leaves the app running.

## Risks

Another app may already hold the key, so registration has to fail quietly and say so on the status line.

## Open decisions

1. Should the key be configurable in Settings? It conflicts with nothing in the app, but it can in other apps.
2. Should the tray menu show the shortcut text? The mockups do. Show it only where registration worked.

## Owner decisions (2026-11-12)

- The key can be changed in Settings.
- The tray menu shows the shortcut text (TODO item 19).

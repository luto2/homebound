# Plan: global shortcut to cycle the overlay mode

Status: implemented (2026-11-12, `d28cf66`). Deviations: Linux only through KDE's KGlobalAccel (the XDG portal can't be called from PySide6); other desktops say 'not available'.

- **Audience:** the owner and coding agents who need to know why something was built the way it is.
- **Scope:** the record of "Plan: global shortcut to cycle the overlay mode", finished. The status line says where the build differs from the plan; where the code lives now is in [`code-map.md`](../../code-map.md).
- **Kept current:** archived with the plan, not updated after it was implemented (see the status line). The rest of the text is the plan as it was written.

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

## Implementation notes (2026-11-12)

- `homebound/platform/hotkey.py`: `GlobalHotkey` over a backend per platform. Windows uses `RegisterHotKey` and a
  native event filter for `WM_HOTKEY`. Linux uses the KDE global shortcut service (`org.kde.kglobalaccel`,
  `doRegister`, `setForeignShortcut`, then `globalShortcutPressed`); the key is read back to detect a key that
  another shortcut holds. The XDG GlobalShortcuts portal was tried: `CreateSession` works from QtDBus but
  `BindShortcuts` needs an `a(sa{sv})` argument that PySide6 can't build, so other desktops than KDE say
  "not available". No new dependency.
- The key is the setting `overlay_shortcut` (default `Ctrl+Shift+O`, empty = none), edited in Settings > Overlay.
  Usable: Ctrl, Alt or Meta (plus Shift) with a letter, digit or F1 to F12.
- `MapWindow.cycle_overlay_mode` goes through `set_overlay_mode(quiet=True)` and moves on past a refused mode.
- A failed registration shows on a status-bar label that is hidden otherwise, and the tray row "Next overlay
  mode" with the shortcut text only shows where it worked.
- Not tested by machine: a real key press on KDE or Windows.

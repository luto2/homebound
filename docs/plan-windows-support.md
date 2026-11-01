# Plan: Windows support

Status: implemented on branch `windows-support` (2026-11-01); not yet tried on a real Windows machine.

Depends on: nothing. [Packaging](plan-packaging.md) builds on it.

Build order across the plans: [rescan status](plan-rescan-status.md) → [backups](plan-backups.md) → [Windows support](plan-windows-support.md) → [packaging](plan-packaging.md) → [code signing](plan-code-signing.md).

## What breaks on Windows today

| Area | Where | Problem |
|---|---|---|
| Qt platform | `__main__.py` | forces `QT_QPA_PLATFORM=xcb` |
| Data folder | `settings.py` `DATA_DIR` | `~/.local/share/homebound`; should be `%APPDATA%\Homebound` |
| Log auto-detect | `settings.detect_log_directories` | only Steam/Proton/Wine paths; Windows uses `Documents\EVE\logs\Chatlogs`, often moved by OneDrive (`OneDrive\Documents`, `OneDrive\Dokumente`) |
| Focus tracking | `ui/focus.py` | KWin script, D-Bus, `xprop`: none exist; `QtDBus` is imported at module top |
| Overlay | `ui/overlay.py` | relies on `X11BypassWindowManagerHint` to stay above fullscreen clients |
| Notifications | `ui/notify.py` | `gdbus`; the tray fallback already works, so only the code path needs testing |
| KWin settings page / status item | `settings_dialog.py`, status bar | shouldn't show on Windows (or on Linux without KDE) |
| Fonts | `theme/qss.py`, `tokens.json` | Noto Sans / Hack aren't on Windows; fallback fonts change widths in a 168 px node |
| Static data path | `universe/db.py`, `build_static.py` | `data/universe.sqlite` next to the source tree; doesn't exist in an installed app |
| Map file names | `Settings.add_map` | the slug is `[a-z0-9-]`, so it's already safe (no `CON`, `:` …). No change needed |
| Log reading | `logs/watcher.py` | already polls with `stat`/`fstat` and decodes UTF-16 LE. Needs a check that reading works while the EVE client has the file open (it should: Python opens with shared read/write) |

## Changes

1. **New `homebound/platform/`** package:
   - `paths.py`: `data_directory()` (Windows `%APPDATA%\Homebound`; Linux unchanged at
     `~/.local/share/homebound`, so existing data stays where it is; `HOMEBOUND_HOME` still
     wins), `documents_directory()` (Windows: `SHGetKnownFolderPath(FOLDERID_Documents)` via
     `ctypes`, which handles OneDrive and localised folders), `static_data_path()` (see [packaging](plan-packaging.md#static-data-in-packages)).
   - `focus_windows.py`: polls `GetForegroundWindow` / `GetWindowTextW` / `GetWindowRect` via
     `ctypes` every ~500 ms (same model as the X11 fallback). Own windows are recognised by process
     id (`GetWindowThreadProcessId`) instead of window class. It only reads the window title, not
     the client: the same OS information a task bar uses.
   - `focus_linux.py`: today's KWin/X11 code moved out of `ui/focus.py`, with the `QtDBus` import
     inside it.
   - `ui/focus.py` keeps `FocusTracker` and `eve_character` and picks the backend by `sys.platform`.
2. **`__main__.py`**: set `QT_QPA_PLATFORM=xcb` only on Linux.
3. **Overlay**: on Windows use `FramelessWindowHint | WindowStaysOnTopHint | Tool |
   WindowDoesNotAcceptFocus` (+ `WindowTransparentForInput` when locked). This stays above
   windowed and borderless-fullscreen clients, but **not above exclusive fullscreen**. The user
   guide must say to use EVE's windowed/borderless mode. Re-raise the overlay when focus moves to
   an EVE client, in case Windows lowers it.
4. **Log auto-detect**: add `documents_directory() / "EVE/logs/Chatlogs"` and the OneDrive
   variants on Windows.
5. **Settings / status bar**: the KWin page and status item only appear when `kpackagetool6`
   exists; on Windows the focus status says "Windows focus: active".
6. **Fonts**: bundle Noto Sans and Hack in `homebound/theme/fonts/` and load them with
   `QFontDatabase.addApplicationFont` before the first widget. The fonts are private to
   Homebound: nothing is installed and the system fonts stay untouched. The family names in
   `qss.py`/`tokens.json` stay as they are. Done on Linux too, so both platforms look the same.
   Ship the licence files (Noto: SIL OFL; check Hack's) and list them as PyInstaller data files.
7. **Tests**: mark Linux-only tests (`xprop`, KWin, `gdbus`) with `skipif`; add tests for path
   choice per platform (with `sys.platform` / environment patched) and for the Windows focus
   backend (ctypes calls faked).

## Decisions (2026-11-01)

- Overlay: windowed/borderless EVE clients only; exclusive fullscreen isn't supported.
- Fonts: bundled and loaded privately on Windows and Linux; nothing is installed system-wide.

## Implementation notes (2026-11-01)

- Status bar: the focus item stays on every platform with platform-correct text (KWin script,
  "Focus: X11 fallback", "Focus tracking unavailable: no KWin script and no xprop", or
  "Windows focus: active"). Only the KWin settings page is hidden without `kpackagetool6`.
- On Windows the overlay calls `SetWindowPos(HWND_TOPMOST, …, SWP_NOACTIVATE)` on every refresh,
  and a refresh happens on every focus change, so it is re-raised when an EVE client gets focus.
- Fonts bundled: Noto Sans Regular/Medium/Bold/Black and Hack Regular/Bold, the same files the
  Linux system had, so the weights Qt picks (600 → Medium/Bold, 800 → Bold/Black) match.
  Licences: `homebound/theme/fonts/OFL-NotoSans.txt`, `LICENSE-Hack.txt` (MIT + Bitstream Vera).
- `static_data_path()` is in place and used by `universe/db.py`; `build_static` still writes to
  `data/` in the source tree (writing to the data folder in packaged mode is part of packaging).
- Still to check on Windows: reading the chat logs while the client has them open, the tray
  notification path, and whether `PySide6.QtDBus` imports there (only `focus_linux` needs it,
  and it is never imported on Windows).

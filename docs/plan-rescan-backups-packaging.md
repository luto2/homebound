# Plan: rescan status, backups, Windows support and packaging

Status: plan only, nothing implemented yet (2026-11-01).

Three independent parts. Each can ship on its own; the order below is the suggested build order
(smallest first, and backups before packaging, since packaged users need them most).

---

## 1. Rescan status on systems

### What exists

- `ui/map_scene.py` marks a node `stale` when its last paste (`systems.scanned_at`, set by
  `signature_parser.merge`) is older than `STALE_AFTER = 24h`: hatched body, content at
  `op-stale`, and `stale 26h` right-aligned on line 3.
- `ui/signature_table.py` has its own copy of `STALE_AFTER` and draws rows faint.
- `ui/system_panel.py` shows `last paste 26h ago` / `never pasted` in the sig summary.

Gaps:
- A system that was **never pasted** isn't marked at all (`scanned_at` is NULL → not stale).
- The marker is a small grey text on line 3, easy to miss next to the owner. The UNCONFIRMED tab
  on the top edge is what catches the eye.
- The 24h threshold is hard-coded, in two places.
- The state is only computed in `SystemNode.refresh()`. A node that crosses the threshold while
  nothing else happens isn't repainted until the next map change.
- An empty system can't be marked as scanned: Ctrl+A/Ctrl+C on an empty scanner gives nothing to
  paste.

### Design

A top-edge tab like `▲ UNCONFIRMED`, in the `stale` colour (grey, per the design tokens: hatch plus
label, never colour alone):

| State | When | Tab | Body |
|---|---|---|---|
| Fresh | last paste younger than the threshold | none | normal |
| Rescan due | last paste older than the threshold | `SCAN 26h` | hatched, content at `op-stale` (as now) |
| Never scanned | no paste yet, **w-space only** | `NO SCAN` | normal (nothing is out of date yet) |

- `NO SCAN` only appears on wormhole systems (`not known_space`: C1–C6, C13, Thera, Drifter). K-space ends
  of a chain never get it. `SCAN 26h` still shows on any system that has been pasted at least once.
- The line-3 `stale 26h` text goes away; the tab replaces it.
- Order on the top edge: `▲ UNCONFIRMED`, then the scan tab, then the character markers
  (`_character_marker_start` already moves along after each tab). HOME stays on the right.
- Width: the node is 168 px and HOME sits at x = 122. With both UNCONFIRMED and a scan tab, the
  scan tab drops its word (`26h` / `—`) so it ends before HOME.
- Tooltip line: "Last paste 26h ago: rescan due (threshold 12h)" or "Never scanned".
- Optional, later: a small `scan 26h` marker in the overlay rows.

### Changes

1. **New `homebound/mapping/scan_status.py`** (pure, no Qt):
   `scan_status(scanned_at: str | None, now: datetime, due_after: timedelta) -> ScanStatus`
   with `state` (`"fresh" | "due" | "never"`) and `age_text`. Move `age_text` here from
   `map_scene.py` (keep a re-export). Both `STALE_AFTER` copies go away.
2. **Settings** (`settings.py`): `rescan_due_hours: int = 24`, set by the user in whole hours,
   with a range (1–72) in `SETTING_RANGES`. Settings dialog: a new **Signatures** page with an
   hours spin box ("Rescan due after [24] h").
3. **`SystemNode`** (`ui/map_scene.py`): replace `self.stale` with `self.scan_status`; draw the tab
   in `_paint_tabs`, keep the hatch for `"due"`, remove the line-3 text, extend the tooltip.
4. **Signature table / panel**: rows faint for `"due"` (as now, via `scan_status`); the summary
   becomes `last paste 26h ago · rescan due` in the warning style.
5. **Repaint on time**: a 60 s timer in `AppState` (or the existing expiry timer if its interval
   is close) that emits a new `scanAgeTick`. Nodes recompute their status and only call `update()`
   when the state or the shown text changed (after the first hour the label changes once an hour,
   so this is cheap).
6. **"Mark as scanned now"**: system context menu item, sets `scanned_at` to now. It's for empty
   systems and for k-space ends you don't care about. It isn't undoable; it only changes a timestamp.
7. **Docs**: user guide §2 (node table) and §5, README sigs bullet, `design-system/README.md` stale
   row (tab instead of line-3 label).

### Tests

- `tests/test_scan_status.py`: fresh / due / never, exactly on the threshold, future timestamps
  (clock skew → fresh), threshold from settings, `never` only for w-space systems.
- `test_ui_map_scene.py`: tab drawn for due and never, not for fresh; compact tab next to
  UNCONFIRMED; character markers start after it; tick repaints only changed nodes.
- `test_settings.py`: range clamping of `rescan_due_hours`.

---

## 2. Backups

### What exists

Nothing automatic. `TODO.md` tells the user to copy `~/.local/share/homebound/` by hand before a
schema migration. Deleting a map is permanent (it only asks first). Maps are small SQLite files:
`map.db`, `maps/*.db`, plus `settings.json`.

### Design

- **One zip per backup** in `DATA_DIR/backups/`, e.g. `2026-11-01_1612_auto.zip`, holding
  `settings.json`, every map file and a `manifest.json` (created at, app version, reason, map
  names → files, home systems). The map files are copied with SQLite's online backup API
  (`sqlite3.Connection.backup`) into a temp file first, so a backup is consistent even while the
  map is open. Each copy is checked with `PRAGMA integrity_check` before it goes in the zip.
- `data/universe.sqlite` isn't backed up: `build_static` rebuilds it.
- **When**:
  | Reason | Trigger |
  |---|---|
  | `auto` | at start, if the newest automatic backup is older than 24h |
  | `migration` | before `Store` migrates a file that needs it (see below) |
  | `delete` | before a map is deleted |
  | `restore` | before a restore replaces anything |
  | `manual` | "Back up now" |
- **Retention**: keep the newest 7 `auto`, the newest 10 safety backups (`migration`, `delete`,
  `restore`), and every `manual` backup until the user deletes it.
- **Extra copy (optional)**: Settings field "Also copy backups to …", e.g. a Nextcloud folder. Off
  by default. A local folder is still on the PC; whether it syncs anywhere is the user's choice.
- **Restore**: Settings → Backups lists the backups (date, reason, maps, size). **Restore …** opens
  a dialog:
  - pick the maps from that backup
  - the user picks, each time, either "Restore as new map" (nothing is overwritten; name
    `Main (31 Oct)`) or "Replace the map" (takes a `restore` backup first, then closes, swaps and
    reopens the file, using the same close/reopen steps as `AppState.switch_map`). Neither is
    preselected; Restore stays disabled until one is chosen.
  - "Also restore settings" (off by default)
- The delete-map confirmation gets a line: "A backup is kept in Settings → Backups."

### Changes

1. **New `homebound/backup.py`**: `create_backup(reason, settings) -> Path`,
   `list_backups() -> list[BackupInfo]`, `prune() -> int`, `restore_maps(backup, names, replace)`,
   `read_manifest(path)`. No Qt, so it can be tested directly.
2. **`Store`**: split the migration check from the migration. A read-only
   `needs_migration(path) -> bool` runs first; if it's true, a `migration` backup of that file is
   made before `_migrate()`.
3. **`AppState` / `__main__`**: the start-up `auto` backup, after settings load and before the map
   opens. If a backup fails, the app still starts, and a warning toast and the status bar say why.
4. **Map deletion** (`map_window.py` / `Settings.remove_map` caller): `delete` backup first. If it
   fails, the map isn't deleted.
5. **Settings dialog**: a **Backups** page with the list, Back up now, Restore …, Open folder, and the
   extra-copy folder.
6. **Docs**: user guide §14 (data) gets a backup section; the TODO.md "back up by hand" entry goes.

### Tests (`tests/test_backup.py`, all under a temp `HOMEBOUND_HOME`)

- Backing up an open map with uncommitted rows gives a consistent copy.
- Manifest contents; zip contains every map in `settings.maps`.
- Retention per reason; manual backups survive pruning.
- An old-schema map triggers exactly one `migration` backup, and the backup has the old schema.
- Restore as new map: new entry in `settings.maps`, new file, original untouched.
- Restore replacing: safety backup exists, store reopened, characters carried over.
- A failing backup (read-only folder) blocks deletion but not start-up.

---

## 3. Windows support

### What breaks on Windows today

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

### Changes

1. **New `homebound/platform/`** package:
   - `paths.py`: `data_directory()` (Windows `%APPDATA%\Homebound`; Linux unchanged at
     `~/.local/share/homebound`, so existing data stays where it is; `HOMEBOUND_HOME` still
     wins), `documents_directory()` (Windows: `SHGetKnownFolderPath(FOLDERID_Documents)` via
     `ctypes`, which handles OneDrive and localised folders), `static_data_path()` (see §4).
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

---

## 4. Packaging (Linux and Windows)

### Static data in packages

The app can't ask a packaged user to run `build_static`. Instead:
- CI runs `build_static` and bundles `universe.sqlite` (plus `statics_resolved.json`) inside the
  package.
- `static_data_path()` looks, in order, at `HOMEBOUND_UNIVERSE`, a rebuilt copy in the data
  folder, then the bundled copy (`importlib.resources` / PyInstaller's bundle dir).
- In packaged mode, `build_static` writes to the data folder. A "Rebuild static data" button in
  Settings is optional, and is the only other thing that would go online.

### Tooling

- **PyInstaller, one-folder mode**, for both systems. It's the most widely used tool with
  PySide6, and one-folder mode starts faster and triggers fewer antivirus false positives than
  one-file. `pyside6-deploy` (Nuitka) is the alternative if size or start time becomes a problem.
- Depend on **`PySide6-Essentials`** instead of the full `PySide6` (the app uses only
  QtCore/Gui/Widgets/DBus). That makes the package much smaller. Check first that QtDBus is in
  Essentials.
- Version from `pyproject.toml` (`importlib.metadata.version("homebound")`), shown in Settings
  and the tray menu. Fix the Python mismatch: `requires-python >=3.12` vs "Python 3.14" in the
  guide. Build with the newest version PyInstaller supports.

### Linux

- **AppImage** (PyInstaller folder + `.desktop` + icon, built on the oldest supported runner
  for a low glibc baseline). It runs on any distro, from anywhere, with no install.
- **AUR `PKGBUILD`** (for CachyOS/Arch): installs the wheel and depends on the distro's
  `pyside6` and `python-httpx`. It's tiny and updates with the system.
- Install `homebound.desktop` and an icon in both cases. `setDesktopFileName("homebound")` is
  already set; Plasma uses it for notifications and the tray.
- **Not Flatpak**: the sandbox blocks the Steam/Proton prefix paths by default, `kpackagetool6`
  for the KWin script, and the override-redirect overlay.

### Windows

- PyInstaller folder → **Inno Setup** installer, per-user (`%LOCALAPPDATA%\Programs\Homebound`,
  no admin rights), with a Start-menu entry and optional autostart. Also a **portable zip** of
  the same folder.
- **Signing**: see "Windows code signing" below. Until then, builds are unsigned and SmartScreen
  warns on first start.

### CI and releases (GitHub Actions on `luto2/homebound`)

- `test.yml`: pytest on `ubuntu-latest` and `windows-latest` (Qt offscreen) for every push.
- `release.yml` on a `v*` tag: build static data → PyInstaller on both runners → AppImage,
  installer and zip → attach to a **draft** release. Publishing it stays a manual step.
- PyInstaller doesn't cross-compile, so Windows builds need the Windows runner (or a Windows VM).

### Notices

A `THIRD_PARTY_NOTICES` file in every package: Qt/PySide6 (LGPLv3; one-folder mode keeps the Qt
libraries replaceable, as the LGPL requires), httpx, Wanderer data (MIT), anoik.is and
whtype.info data (check their terms), CCP SDE and icons (CCP licence), Noto Sans and Hack.

---

## Windows code signing

**Unsigned, from the user's side**: SmartScreen shows "Windows protected your PC". The user clicks
*More info* → *Run anyway*. That needs **no admin rights**; the per-user installer doesn't either.
Edge may also flag the download as "not commonly downloaded" (→ *Keep*). Only PCs where an admin
enforces SmartScreen by policy block it entirely.

**Signing doesn't remove the warning at once**: SmartScreen trusts by reputation, which builds up
as people download and run files signed with the same certificate. Since 2024, EV certificates no
longer skip that. What signing gives right away is a named publisher instead of "Unknown".

Options, cheapest first (prices and eligibility to check when it's time):
1. **SignPath Foundation**: free signing for open-source projects. It needs an OSI licence, a
   public repo, and builds from CI. It plugs into GitHub Actions. Best fit.
2. **Certum Open Source Code Signing**: low-cost certificate for open-source developers, available
   to individuals in the EU. It needs an ID check and a hardware key or their cloud signing.
3. **Microsoft Trusted Signing** (Azure): monthly subscription, cloud signing from GitHub Actions.
   Check whether it's open to individuals in Germany.
4. A regular OV certificate from a commercial CA: a few hundred € a year, hardware key required.

Plan: ship the first releases unsigned, apply to SignPath Foundation once the repo is public with a
licence, and add the signing step to `release.yml`.

## Decisions (2026-11-01)

1. Rescan threshold: set by the user in whole hours (default 24, range 1–72).
2. `NO SCAN` only on w-space systems.
3. Restore: the user picks "as new map" or "replace" each time; nothing is preselected.
4. Windows overlay: windowed/borderless mode only is fine.
5. Signing: see above.
6. Releases will be public. Licence: **GPL-3.0-or-later** (`LICENSE`, declared in `pyproject.toml`).

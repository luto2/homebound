# Plan: packaging for Linux and Windows

Status: implemented (2026-11-01), merged into `master`. The PyInstaller folder (including the first-start build into an empty data folder) and the wheel/AUR layout were tried locally on Linux; the AppImage, the Windows build and the workflows have not run yet (they run on the first `v*` tag).

- **Audience:** the owner and coding agents who need to know why something was built the way it is.
- **Scope:** the record of "Plan: packaging for Linux and Windows", finished. The status line says where the build differs from the plan; where the code lives now is in [`code-map.md`](../../code-map.md).
- **Kept current:** archived with the plan, not updated after it was implemented (see the status line). The rest of the text is the plan as it was written.

Depends on: [Windows support](plan-windows-support.md) for the Windows build. Signing: [code signing](../plan-code-signing.md).

Build order across the plans: [rescan status](plan-rescan-status.md) → [backups](plan-backups.md) → [Windows support](plan-windows-support.md) → [packaging](plan-packaging.md) → [code signing](../plan-code-signing.md).

## Static data in packages

The app can't ask a packaged user to run `build_static`. Instead:
- ~~CI runs `build_static` and bundles `universe.sqlite` inside the package.~~ Changed
  (2026-11-01): the data is EVE game data, so packages don't ship it. On first start Homebound
  offers to download the sources and build `universe.sqlite` in the data folder. Only
  `statics_resolved.json` ships.
- `static_data_path()` looks, in order, at `HOMEBOUND_UNIVERSE`, a built copy in the data
  folder, then the checkout's `data/` (packages have no bundled copy; see the implementation notes).
- In packaged mode, `build_static` writes to the data folder. A "Rebuild static data" button in
  Settings is optional, and is the only other thing that would go online.

## Tooling

- **PyInstaller, one-folder mode**, for both systems. It's the most widely used tool with
  PySide6, and one-folder mode starts faster and triggers fewer antivirus false positives than
  one-file. `pyside6-deploy` (Nuitka) is the alternative if size or start time becomes a problem.
- Depend on **`PySide6-Essentials`** instead of the full `PySide6` (the app uses only
  QtCore/Gui/Widgets/DBus). That makes the package much smaller. Check first that QtDBus is in
  Essentials.
- Version from `pyproject.toml` (`importlib.metadata.version("homebound")`), shown in Settings
  and the tray menu. Fix the Python mismatch: `requires-python >=3.12` vs "Python 3.14" in the
  guide (fixed: 3.12 works, the guide says so). Build with the newest version PyInstaller supports.

## Linux

- **AppImage** (PyInstaller folder + `.desktop` + icon, built on the oldest supported runner
  for a low glibc baseline). It runs on any distro, from anywhere, with no install.
- **AUR `PKGBUILD`** (for CachyOS/Arch): installs the wheel and depends on the distro's
  `pyside6` and `python-httpx`. It's tiny and updates with the system.
- Install `homebound.desktop` and an icon in both cases. `setDesktopFileName("homebound")` is
  already set; Plasma uses it for notifications and the tray.
- **Not Flatpak**: the sandbox blocks the Steam/Proton prefix paths by default, `kpackagetool6`
  for the KWin script, and the override-redirect overlay.

The bundled fonts from [Windows support](plan-windows-support.md) (item 6) go in as data files, with their licences.

## Windows

- PyInstaller folder → **Inno Setup** installer, per-user (`%LOCALAPPDATA%\Programs\Homebound`,
  no admin rights), with a Start-menu entry and optional autostart. Also a **portable zip** of
  the same folder.
- **Signing**: see [code signing](../plan-code-signing.md). Until then, builds are unsigned and SmartScreen
  warns on first start.

## CI and releases (GitHub Actions on `luto2/homebound`)

- `test.yml`: pytest on `ubuntu-latest` and `windows-latest` (Qt offscreen) for every push.
- `release.yml` on a `v*` tag: build static data → PyInstaller on both runners → AppImage,
  installer and zip → attach to a **draft** release. Publishing it stays a manual step.
- PyInstaller doesn't cross-compile, so Windows builds need the Windows runner (or a Windows VM).

## Notices

A `THIRD_PARTY_NOTICES` file in every package: Qt/PySide6 (LGPLv3; one-folder mode keeps the Qt
libraries replaceable, as the LGPL requires), httpx, Wanderer data (MIT), anoik.is and
whtype.info data (check their terms), EVE SDE and icons (Fenris Creations, formerly CCP), Noto Sans and Hack.

## Decisions (2026-11-01)

- Releases are public, on GitHub.
- Licence: GPL-3.0-or-later (`LICENSE`, declared in `pyproject.toml`).

## Implementation notes (2026-11-01)

- **Files**: `packaging/homebound.spec` (PyInstaller, both systems), `packaging/launch.py`,
  `packaging/linux/` (`homebound.desktop`, `AppRun`, `build_appimage.sh`, `aur/PKGBUILD.in`),
  `packaging/windows/homebound.iss`, `packaging/icons/` (rendered from `app_mark` by
  `packaging/make_icons.py`), `.github/workflows/test.yml` and `release.yml`, `THIRD_PARTY_NOTICES.md`.
- **Static data lookup** (`platform/paths.py`): `HOMEBOUND_UNIVERSE`, else a built copy in the
  data folder, else a bundled one: the checkout's `data/` (packages have none). Where
  `statics_resolved.json` ships: PyInstaller's `_internal/data/`, or `<prefix>/share/homebound/`
  for a system install (AUR: `/usr/share/homebound/`). `db.open_universe()` tries the candidates in
  order, so an outdated copy gives way to a current one when there is one.
- **First start** (`ui/dialogs.BuildDataDialog`, replacing `StartupErrorDialog`): when no universe
  file loads, the dialog ("Download map data", or "Map data needs an update" for an older schema)
  runs `build_static.build()` in a `QThread` with a progress bar and the current step. Errors show
  in the dialog and Download turns into Retry; Quit or Escape cancels at the next step or 1 MB
  download chunk. The terminal command stays in the dialog with a copy button. In a package the
  downloads (~110 MB) are deleted after the build; a checkout keeps `data/cache`. Downloads and the
  output are written to `.part` files first, so a cancelled build never leaves a broken file.
  `homebound --build-static [--refresh]` does the same from a terminal (keeps the cache).
  `homebound --version` prints the version.
- **Owner**: EVE Online belongs to Fenris Creations (formerly CCP Games); notices, legal pages and
  docs say so.
- **Version**: `homebound.app_version()` (package metadata; the spec copies it into the bundle),
  shown in the Settings footer and as a disabled row in the tray menu.
- **Python**: `requires-python >=3.12` stays; the full test suite passes on 3.12 and 3.14. The guide
  now says 3.12 or newer. Packages are built with 3.14 (PyInstaller 6.22 supports it). CI uses
  `actions/setup-python` (shared `libpython`, which PyInstaller needs) with uv set to system Python only.
- **PySide6-Essentials** has QtCore/Gui/Widgets/Svg/DBus; the dependency is switched. A local
  one-folder build is 147 MB. The spec drops the GTK3 platform-theme plugin (it pulled in GTK,
  Cairo, Pango and a second ICU, about 60 MB) and the eglfs/linuxfb/vnc platforms, then every
  top-level library nothing kept still needs (walked through `DT_NEEDED`).
- **AUR**: the release attaches a `git archive` source tarball and a filled-in `PKGBUILD` (version
  and checksum). Publishing: copy the `PKGBUILD` into the AUR repo, run
  `makepkg --printsrcinfo > .SRCINFO`, commit, push. The wheel declares `PySide6-Essentials`, but
  `python-installer` doesn't resolve dependencies, so the system `pyside6` is used.
- **Windows installer**: per-user, `{localappdata}\Programs\Homebound`, Start-menu entry, optional
  desktop shortcut and autostart (`HKCU\...\Run`, removed on uninstall). The previous `_internal`
  folder is deleted on upgrade. The data folder `%APPDATA%\Homebound` stays after uninstall.
- **AppImage**: built on `ubuntu-22.04` (glibc 2.35) with `appimagetool` (continuous build,
  `APPIMAGE_EXTRACT_AND_RUN=1` because runners have no FUSE). The KWin script is copied out of the
  mounted image by `kpackagetool6`, so installing it from the AppImage works.
- **Notices**: anoik.is, whtype.info and Wanderer data all come from EVE game files, so shipping
  them would need permission from Fenris Creations; that's why nothing of it ships.
  `THIRD_PARTY_NOTICES.md` lists the sources as downloaded at first start.
- **Not done**: Windows version resource on the exe, a Settings "Rebuild static data" button, and
  code signing ([its own plan](../plan-code-signing.md)).

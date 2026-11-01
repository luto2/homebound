# Plan: packaging for Linux and Windows

Status: plan only, nothing implemented yet (2026-11-01).

Depends on: [Windows support](plan-windows-support.md) for the Windows build. Signing: [code signing](plan-code-signing.md).

Build order across the plans: [rescan status](plan-rescan-status.md) → [backups](plan-backups.md) → [Windows support](plan-windows-support.md) → [packaging](plan-packaging.md) → [code signing](plan-code-signing.md).

## Static data in packages

The app can't ask a packaged user to run `build_static`. Instead:
- CI runs `build_static` and bundles `universe.sqlite` (plus `statics_resolved.json`) inside the
  package.
- `static_data_path()` looks, in order, at `HOMEBOUND_UNIVERSE`, a rebuilt copy in the data
  folder, then the bundled copy (`importlib.resources` / PyInstaller's bundle dir).
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
  guide. Build with the newest version PyInstaller supports.

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
- **Signing**: see [code signing](plan-code-signing.md). Until then, builds are unsigned and SmartScreen
  warns on first start.

## CI and releases (GitHub Actions on `luto2/homebound`)

- `test.yml`: pytest on `ubuntu-latest` and `windows-latest` (Qt offscreen) for every push.
- `release.yml` on a `v*` tag: build static data → PyInstaller on both runners → AppImage,
  installer and zip → attach to a **draft** release. Publishing it stays a manual step.
- PyInstaller doesn't cross-compile, so Windows builds need the Windows runner (or a Windows VM).

## Notices

A `THIRD_PARTY_NOTICES` file in every package: Qt/PySide6 (LGPLv3; one-folder mode keeps the Qt
libraries replaceable, as the LGPL requires), httpx, Wanderer data (MIT), anoik.is and
whtype.info data (check their terms), CCP SDE and icons (CCP licence), Noto Sans and Hack.

## Decisions (2026-11-01)

- Releases are public, on GitHub.
- Licence: GPL-3.0-or-later (`LICENSE`, declared in `pyproject.toml`).

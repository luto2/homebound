# Packaging: agent rules

How the AppImage, the AUR package and the Windows installer and portable zip are made. Start with [`README.md`](README.md), then `linux/README.md` and `windows/README.md`. CI builds all of them from a `v*` tag in `.github/workflows/release.yml`.

## Rules

- **Host tools get the user's `LD_LIBRARY_PATH`, not the bundle's** (`25bffb7`). `kpackagetool6`, `kreadconfig6`, `qdbus6`, `xprop`, `wmctrl` and `gdbus` run through `host_environment()` in `homebound/platform/paths.py`; in the AppImage they failed on the bundle's older libraries otherwise.
- **The packages never contain EVE game data.** `universe.sqlite` is built on first start. Only `data/statics_resolved.json`, `LICENSE`, `THIRD_PARTY_NOTICES.md` and the package data ship.
- **PyInstaller builds a folder, not a single file**, so the Qt libraries stay replaceable as the LGPL requires.
- **Code that depends on being packaged** (`homebound/platform/paths.py`, `build_command` in `homebound/universe/db.py`, `app_version` in `homebound/__init__.py`) must agree with the spec and the scripts here; change them together.
- **Install or build changes also update** the [`install page`](../docs/user/install.md) and, for a release, `CHANGELOG.md`.

## What only a hand test can show

CI builds the packages but cannot run them on a desktop. Test by hand: the AppImage on KDE Plasma (Wayland) and on Arch, the KWin focus script, the app-menu icon, and the Windows installer, overlay and SmartScreen warning on a real machine ([`docs/testing/windows.md`](../docs/testing/windows.md)). Say in the commit or plan which of these were not tested.

# Linux packaging

- **Audience:** anyone building or changing the Linux packages.
- **Scope:** the AppImage and the AUR package, the desktop entry and the code that has to agree with them. The shared build is in [`../README.md`](../README.md).
- **Kept current:** checked against `8c371b5` (2026-11-14). Update it when the AppImage scripts, the desktop entry or the install script change.

The Linux packages are an AppImage and an AUR package.

`build_appimage.sh` wraps the PyInstaller folder `dist/homebound` into
`dist/Homebound-<version>-x86_64.AppImage`. `AppRun` is the image's entry point.

`aur/PKGBUILD.in` is the template for the AUR package. The release workflow fills in the version and
checksum. It installs the wheel, and puts `statics_resolved.json` in `/usr/share/homebound`.

`homebound.desktop` is the desktop entry both packages install. Its `Icon=homebound` uses the files in
`packaging/icons/`.

Code that has to agree with these files:

- `setDesktopFileName("homebound")` in `main` in `homebound/__main__.py` and `StartupWMClass` in the desktop
  entry link the running window to its launcher.
- `build_command` in `homebound/universe/db.py` reads the `APPIMAGE` variable to show the right command.
- `bundled_data_directory` in `homebound/platform/paths.py` expects `<prefix>/share/homebound` on a system
  install.
- The `LinuxFocus` backend in `homebound/platform/focus_linux.py` falls back to `xprop`, which the
  PKGBUILD lists as an optional dependency.

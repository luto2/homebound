# Packaging

- **Audience:** anyone building or changing the packages.
- **Scope:** how the PyInstaller folder build works and which code depends on being packaged. The Linux and Windows specifics are in the READMEs of their folders; agent rules are in [`CLAUDE.md`](CLAUDE.md).
- **Kept current:** checked against `8c371b5` (2026-11-14). Update it when `homebound.spec`, the entry point or the packaged-path code change.

How Homebound is built into the packages it ships as. The platform folders are described in
`linux/README.md` and `windows/README.md`, the app icons in `icons/README.md`.

`homebound.spec` is the PyInstaller spec for Linux and Windows. It builds one folder, so the Qt libraries
stay replaceable as the LGPL requires. The entry point is `launch.py`, which calls `main` in
`homebound/__main__.py`. Its output is `dist/homebound/` on Linux and `dist/Homebound/` on Windows.

The spec bundles `data/statics_resolved.json`, `LICENSE`, `THIRD_PARTY_NOTICES.md` and the package data of
`homebound`. It does not bundle `universe.sqlite`, because that is EVE game data. The app builds it at first
start through `BuildDataDialog` in `homebound/ui/dialogs.py`.

Code that depends on being packaged:

- `source_tree`, `is_packaged`, `bundled_data_directory` and `static_build_directory` in
  `homebound/platform/paths.py` look at `sys.frozen` and `sys._MEIPASS`.
- `build_command` in `homebound/universe/db.py` picks the command to show for each kind of install.
- `app_version` in `homebound/__init__.py` reads the metadata that the spec copies in.

`.github/workflows/release.yml` runs these builds for a release.

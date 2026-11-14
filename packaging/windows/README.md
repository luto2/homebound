# Windows packaging

`homebound.iss` is the Inno Setup script for the per-user installer. It packs the PyInstaller folder
`dist\Homebound` into `dist\Homebound-<version>-windows-setup`, installing to
`%LOCALAPPDATA%\Programs\Homebound` without admin rights. Maps, settings and backups stay in
`%APPDATA%\Homebound` when Homebound is uninstalled, which is where `data_directory` in
`homebound/platform/paths.py` puts them.

The installer also offers a desktop shortcut and starting with Windows. The release workflow additionally
makes a portable zip of the same folder.

Code that has to agree with this script:

- `APP_USER_MODEL_ID` in `homebound/__main__.py` must equal the `AppUserModelID` of the shortcuts, so pinned
  shortcuts group with the running app. `tests/test_build_and_main.py` checks this.
- `WindowsFocus` in `homebound/platform/focus_windows.py` is the Windows focus backend. `homebound.spec`
  leaves out `PySide6.QtDBus` on Windows, because only the Linux backend uses it.
- `SetupIconFile` and the spec's `icon` use `packaging/icons/homebound.ico`.

`docs/testing/windows.md` describes how to test a build.

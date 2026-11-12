"""Homebound: offline wormhole mapper. Run with `python -m homebound`."""
import os
import sys

APP_USER_MODEL_ID = "Homebound.Homebound"   # also in packaging/windows/homebound.iss
ALLOW_SECOND_INSTANCE = "--allow-second-instance"   # development: skip the one-instance guard


def main() -> int:
    """Start the app and run the Qt event loop, or handle ``--version`` / ``--build-static [--refresh]``.

    A second start on the same data folder raises the running window and returns 0, unless
    ``--allow-second-instance`` is given.

    Returns:
        The exit code: 1 if the static universe data is missing or outdated and the user quits instead of
        building it, or if the open map can't be opened; else Qt's exit code.
    """
    arguments = sys.argv[1:]
    if arguments[:1] == ["--version"]:
        from . import app_version
        print(f"Homebound {app_version()}")
        return 0
    if arguments[:1] == ["--build-static"]:
        from .universe import build_static
        return build_static.main(arguments[1:])
    if sys.platform == "linux":
        # The overlay needs always-on-top, click-through and move(); those work reliably on KWin via XWayland.
        # Overrides a desktop-wide "wayland;xcb" (Hyprland configs set it): on native Wayland the overlay
        # is a plain toplevel that tiling compositors tile.
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    elif sys.platform == "win32":
        # From source the process is python.exe, and the task bar shows Python's icon for it. An app ID of our own
        # makes it use the window icon. The installer's shortcuts carry the same ID, so pinned shortcuts group with it.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    from PySide6.QtWidgets import QApplication

    from . import settings as settings_module
    from .platform.instance import acquire_single_instance
    from .theme.qss import apply_theme

    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("homebound")
    application.setApplicationDisplayName("Homebound")
    application.setDesktopFileName("homebound")
    application.setQuitOnLastWindowClosed(False)
    apply_theme(application)

    guard = None
    if ALLOW_SECOND_INSTANCE not in arguments:
        guard = acquire_single_instance(settings_module.DATA_DIR)
        if guard is None:   # Homebound already runs on this folder; it was asked to show its window
            return 0
    try:
        return _run(application, guard)
    finally:
        if guard is not None:
            guard.release()


def _run(application, guard) -> int:
    """Open the universe and the map, show the window and run the event loop (the one instance's work)."""
    from PySide6.QtWidgets import QMessageBox

    from .settings import Settings
    from .universe.db import BUILD_COMMAND, UniverseDataError, open_universe, static_data_path

    try:
        universe = open_universe()
    except UniverseDataError:
        # first run (packages don't ship the data) or data from an older version: build it here
        from .ui.dialogs import BuildDataDialog
        if not BuildDataDialog(BUILD_COMMAND, outdated=static_data_path().exists()).exec():
            return 1
        try:
            universe = open_universe()
        except UniverseDataError as error:   # the build wrote a file the app still can't load
            QMessageBox.critical(None, "Homebound", f"The EVE data was built but can't be loaded: {error}")
            return 1

    from .ui.focus import FocusTracker
    from .ui.map_window import MapWindow
    from .ui.settings_dialog import PAGES
    from .ui.state import AppState, MapOpenError
    from .ui.sync_manager import SyncManager

    settings = Settings.load()
    try:
        state = AppState(universe, settings, auto_backup=True)
    except MapOpenError as error:   # not a damaged file (that is set aside): locked, read-only, a folder
        QMessageBox.critical(None, "Homebound", str(error))
        return 1
    focus = FocusTracker()
    sync = SyncManager(state)
    window = MapWindow(state, focus, sync)
    if guard is not None:
        guard.showRequested.connect(window.raise_window)
    state.start()
    focus.start()
    sync.start()
    window.show()
    if state.recovered_map:   # the open map was damaged and starts empty: offer the restore first
        window.open_settings(PAGES.index("Backups"))
    elif not settings.log_directory:
        window.open_settings(0)
    exit_code = application.exec()
    sync.stop()
    state.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

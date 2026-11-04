"""Homebound: offline wormhole mapper. Run with `python -m homebound`."""
import os
import sys

APP_USER_MODEL_ID = "Homebound.Homebound"   # also in packaging/windows/homebound.iss


def main() -> int:
    """Start the app and run the Qt event loop, or handle ``--version`` / ``--build-static [--refresh]``.

    Returns:
        The exit code: 1 if the static universe data is missing or outdated and the user quits instead of
        building it, else Qt's exit code.
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

    from .settings import Settings
    from .theme.qss import apply_theme
    from .universe.db import BUILD_COMMAND, UniverseDataError, open_universe, static_data_path

    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("homebound")
    application.setApplicationDisplayName("Homebound")
    application.setDesktopFileName("homebound")
    application.setQuitOnLastWindowClosed(False)
    apply_theme(application)

    try:
        universe = open_universe()
    except UniverseDataError:
        # first run (packages don't ship the data) or data from an older version: build it here
        from .ui.dialogs import BuildDataDialog
        if not BuildDataDialog(BUILD_COMMAND, outdated=static_data_path().exists()).exec():
            return 1
        universe = open_universe()

    from .ui.focus import FocusTracker
    from .ui.map_window import MapWindow
    from .ui.state import AppState

    settings = Settings.load()
    state = AppState(universe, settings, auto_backup=True)
    focus = FocusTracker()
    window = MapWindow(state, focus)
    state.start()
    focus.start()
    window.show()
    if not settings.log_directory:
        window.open_settings(0)
    exit_code = application.exec()
    state.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())

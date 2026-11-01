"""Homebound: offline wormhole mapper. Run with `python -m homebound`."""
import os
import sys


def main() -> int:
    """Start the app and run the Qt event loop.

    Returns:
        The exit code: 1 if the static universe data is missing or outdated, else Qt's exit code.
    """
    if sys.platform == "linux":
        # The overlay needs always-on-top, click-through and move(); those work reliably on KWin via XWayland.
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    from PySide6.QtWidgets import QApplication

    from .settings import Settings
    from .theme.qss import apply_theme
    from .universe.db import BUILD_COMMAND, DEFAULT_PATH, Universe, UniverseDataError

    application = QApplication.instance() or QApplication(sys.argv)
    application.setApplicationName("homebound")
    application.setApplicationDisplayName("Homebound")
    application.setDesktopFileName("homebound")
    application.setQuitOnLastWindowClosed(False)
    apply_theme(application)

    try:
        universe = Universe()
    except UniverseDataError:
        from .ui.dialogs import StartupErrorDialog
        StartupErrorDialog(DEFAULT_PATH, BUILD_COMMAND).exec()
        return 1

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

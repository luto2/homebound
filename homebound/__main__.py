"""Homebound: offline wormhole mapper. Run with `python -m homebound`."""
import os
import sys


def main() -> int:
    # The overlay needs always-on-top, click-through and move(); those work reliably on KWin via XWayland.
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .settings import Settings
    from .theme.qss import apply_theme
    from .universe.db import Universe

    app = QApplication(sys.argv)
    app.setApplicationName("homebound")
    app.setApplicationDisplayName("Homebound")
    app.setDesktopFileName("homebound")
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)

    try:
        uni = Universe()
    except FileNotFoundError as e:
        QMessageBox.critical(None, "Homebound", str(e))
        return 1

    from .ui.focus import FocusTracker
    from .ui.map_window import MapWindow
    from .ui.state import AppState

    settings = Settings.load()
    state = AppState(uni, settings)
    focus = FocusTracker()
    win = MapWindow(state, focus)
    state.start()
    focus.start()
    win.show()
    if not settings.log_dir:
        win.open_settings(0)
    rc = app.exec()
    state.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())

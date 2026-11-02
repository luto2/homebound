"""The bundled fonts (Noto Sans, Hack), loaded for Homebound only so every system draws the same widths.

Nothing is installed: the fonts live in the application's font database and disappear when it quits.
Licences: ``fonts/OFL-NotoSans.txt`` (SIL OFL 1.1) and ``fonts/LICENSE-Hack.txt`` (MIT + Bitstream Vera).
"""
from importlib.resources import files
from pathlib import Path

from PySide6.QtGui import QFontDatabase

FONT_FILES = ("NotoSans-Regular.ttf", "NotoSans-Medium.ttf", "NotoSans-Bold.ttf", "NotoSans-Black.ttf",
              "Hack-Regular.ttf", "Hack-Bold.ttf")
_loaded: dict[str, list[str]] = {}


def font_directory() -> Path:
    """Return the folder of the bundled font files."""
    return Path(str(files("homebound.theme").joinpath("fonts")))


def load_fonts() -> dict[str, list[str]]:
    """Add the bundled fonts to the application's font database (once).

    Needs a QGuiApplication. Fonts that fail to load are left out, so the system fonts are used instead.

    Returns:
        File name -> the font families it added.
    """
    if not _loaded:
        for name in FONT_FILES:
            font_id = QFontDatabase.addApplicationFont(str(font_directory() / name))
            _loaded[name] = QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []
    return _loaded

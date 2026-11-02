"""Render the app mark (``map_window.app_mark``) into the icon files the packages need.

Writes ``packaging/icons/homebound.png`` (256 px, for the AppImage and the AUR package), ``homebound.svg``
(the same drawing as a vector, for the hicolor theme) and ``homebound.ico`` (16-256 px, for the Windows
exe and installer). The results are committed; run this again after changing ``app_mark``:

    QT_QPA_PLATFORM=offscreen uv run python packaging/make_icons.py
"""
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

ICONS = Path(__file__).resolve().parent / "icons"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


def png_bytes(size: int) -> bytes:
    """Return the app mark at ``size`` pixels as PNG data."""
    from homebound.ui.map_window import app_mark
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    app_mark(size).save(buffer, "PNG")
    return bytes(buffer.data())


def ico_bytes(sizes: tuple[int, ...]) -> bytes:
    """Return an ICO file holding one PNG-compressed image per size (Windows Vista and later read these)."""
    images = [png_bytes(size) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)
    entries, data = b"", b""
    for size, image in zip(sizes, images):
        edge = 0 if size >= 256 else size          # 0 means 256 in an ICO directory entry
        entries += struct.pack("<BBBBHHII", edge, edge, 0, 0, 1, 32, len(image), offset + len(data))
        data += image
    return header + entries + data


def svg_text() -> str:
    """Return the app mark as SVG: the same shapes and theme colours ``app_mark`` paints on a 32 px grid."""
    from homebound.theme.tokens import color
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 32 32">\n'
        f'  <line x1="10" y1="21" x2="23" y2="10" stroke="{color("edge-fresh").name()}" stroke-width="2.5"/>\n'
        f'  <rect x="16" y="4" width="13" height="11" rx="3" fill="{color("bg-2").name()}" '
        f'stroke="{color("ink").name()}" stroke-width="2"/>\n'
        f'  <rect x="3" y="16" width="14" height="12" rx="3" fill="{color("accent").name()}"/>\n'
        '</svg>\n')


def main() -> int:
    """Write the three icon files and return 0."""
    application = QApplication.instance() or QApplication(sys.argv)
    ICONS.mkdir(exist_ok=True)
    (ICONS / "homebound.png").write_bytes(png_bytes(256))
    (ICONS / "homebound.ico").write_bytes(ico_bytes(ICO_SIZES))
    (ICONS / "homebound.svg").write_text(svg_text())
    del application
    return 0


if __name__ == "__main__":
    sys.exit(main())

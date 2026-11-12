# PyInstaller spec for Homebound, one-folder mode, Linux and Windows.
# Build from the project root:
#     uv run --group packaging pyinstaller packaging/homebound.spec --noconfirm
# Output: dist/homebound/ (Linux) or dist/Homebound/ (Windows). One-folder keeps the Qt libraries as
# separate files that can be replaced, as the LGPL requires.
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

ROOT = Path(SPECPATH).parent
WINDOWS = sys.platform == "win32"
NAME = "Homebound" if WINDOWS else "homebound"

# universe.sqlite is not shipped: it's EVE game data, so Homebound downloads the sources and builds it on the
# user's machine at first start. statics_resolved.json (the pinned answers to disputed statics) is shipped so
# that build doesn't need to ask zKillboard.
datas = [
    *collect_data_files("homebound"),      # theme tokens, icons, fonts + licences, schema.sql, the KWin script
    *copy_metadata("homebound"),           # app_version() reads the version from here
    (str(ROOT / "data/statics_resolved.json"), "data"),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "THIRD_PARTY_NOTICES.md"), "."),
]

# Qt modules Homebound never imports; QtDBus is only used by the Linux focus backend.
excludes = ["tkinter", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtOpenGL",
            "PySide6.QtPdf", "PySide6.QtWebEngineCore",
            # the sync server and its web stack stay out of the client (cloud-sync-plan §18)
            "homebound_server", "starlette", "uvicorn", "uvloop", "watchfiles"]
if WINDOWS:
    excludes.append("PySide6.QtDBus")

analysis = Analysis(
    [str(ROOT / "packaging/launch.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=["homebound.platform.focus_linux", "homebound.platform.focus_windows"],
    excludes=excludes,
    noarchive=False,
)
# Qt plugins Homebound doesn't need on Linux. The GTK theme plugin alone pulls in GTK, Cairo, Pango and
# ICU (about half the folder); Homebound draws its own style, so it doesn't need it.
UNUSED_PLUGINS = ("platformthemes/libqgtk3.so", "platforms/libqeglfs.so", "platforms/libqlinuxfb.so",
                  "platforms/libqminimalegl.so", "platforms/libqvnc.so", "platforms/libqvkkhrdisplay.so",
                  "egldeviceintegrations/")


def without_unused_plugins(binaries):
    """Drop the unused plugins, then every top-level library only they needed.

    The libraries PyInstaller collected as dependencies sit at the top of the folder; the ones still
    reachable from what's kept (DT_NEEDED, followed transitively) stay.
    """
    from PyInstaller.depend.bindepend import get_imports
    kept = [entry for entry in binaries if not any(part in entry[0].replace("\\", "/") for part in UNUSED_PLUGINS)]
    top_level = {entry[0]: entry for entry in kept if "/" not in entry[0].replace("\\", "/")}
    reachable, queue = set(), [entry for entry in kept if entry[0] not in top_level or entry[0].startswith("libpython")]
    while queue:
        name, source, _ = queue.pop()
        for needed, _ in get_imports(source):
            if needed in top_level and needed not in reachable:
                reachable.add(needed)
                queue.append(top_level[needed])
    return [entry for entry in kept if entry[0] not in top_level or entry[0] in reachable
            or entry[0].startswith("libpython")]


if not WINDOWS:
    analysis.binaries = without_unused_plugins(analysis.binaries)

pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,                         # no console window on Windows; Linux ignores it
    icon=str(ROOT / "packaging/icons/homebound.ico") if WINDOWS else None,
)
coll = COLLECT(exe, analysis.binaries, analysis.datas, name=NAME)

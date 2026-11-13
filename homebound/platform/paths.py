"""Where Homebound keeps its data, where the user's Documents folder is, which universe file to read, and SQLite URIs.

Linux keeps ``~/.local/share/homebound`` (existing data stays where it is); Windows uses
``%APPDATA%\\Homebound``. ``HOMEBOUND_HOME`` wins on both.
"""
import ctypes
import os
import sys
import uuid
from pathlib import Path, PurePath
from urllib.parse import quote

from ..validation import require_type

APP_FOLDER = "Homebound"
UNIVERSE_FILE = "universe.sqlite"
RESOLVED_FILE = "statics_resolved.json"
FOLDERID_DOCUMENTS = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"


def is_windows() -> bool:
    """Return whether Homebound runs on Windows."""
    return sys.platform == "win32"


def host_environment() -> dict[str, str]:
    """Return the environment for starting the system's own tools (kpackagetool6, xprop, gdbus).

    The PyInstaller bootloader points ``LD_LIBRARY_PATH`` at the bundle's ``_internal`` folder, so a host program
    started with our environment loads our older libstdc++ or liblzma and fails ("GLIBCXX_3.4.32 not found").
    The bootloader keeps the user's own value in ``LD_LIBRARY_PATH_ORIG``; this puts it back, or drops the
    variable when there was none. Outside a frozen build the environment is returned unchanged.
    """
    environment = dict(os.environ)
    if getattr(sys, "frozen", False):
        original = environment.pop("LD_LIBRARY_PATH_ORIG", None)
        if original:
            environment["LD_LIBRARY_PATH"] = original
        else:
            environment.pop("LD_LIBRARY_PATH", None)
    return environment


def data_directory() -> Path:
    """Return the folder for settings, maps and backups (not created here)."""
    if os.environ.get("HOMEBOUND_HOME"):
        return Path(os.environ["HOMEBOUND_HOME"])
    if is_windows():
        appdata = os.environ.get("APPDATA")
        return Path(appdata) / APP_FOLDER if appdata else Path.home() / "AppData/Roaming" / APP_FOLDER
    return Path.home() / ".local/share/homebound"


class _GUID(ctypes.Structure):
    """The Win32 GUID structure."""

    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _known_folder(folder_id: str) -> Path | None:
    """Ask Windows (SHGetKnownFolderPath) for a known folder; handles OneDrive and localised names.

    Returns:
        The folder, or None if Windows didn't give one.
    """
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    guid = _GUID.from_buffer_copy(uuid.UUID(folder_id).bytes_le)
    pointer = ctypes.c_wchar_p()
    if windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(pointer)) != 0:
        return None
    try:
        return Path(pointer.value) if pointer.value else None
    finally:
        windll.ole32.CoTaskMemFree(pointer)


def documents_directory() -> Path:
    """Return the user's Documents folder (on Windows wherever OneDrive or the user moved it)."""
    if is_windows():
        known = _known_folder(FOLDERID_DOCUMENTS)
        if known:
            return known
    return Path.home() / "Documents"


def source_tree() -> Path | None:
    """Return the project folder when Homebound runs from a source checkout, else None (an installed package)."""
    if getattr(sys, "frozen", False):
        return None
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").is_file() else None


def is_packaged() -> bool:
    """Return whether Homebound runs from a package (PyInstaller, AppImage, system install) instead of a checkout."""
    return source_tree() is None


def bundled_data_directory() -> Path:
    """Return the folder holding the shipped ``statics_resolved.json`` (and, in a checkout, ``universe.sqlite``).

    That is PyInstaller's bundle, the checkout's ``data/``, or ``<prefix>/share/homebound`` for a system
    install (the AUR package puts it in ``/usr/share/homebound``). Packages ship no ``universe.sqlite``: it is
    EVE game data, built into the data folder at first start.
    """
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        return Path(bundle) / "data"
    tree = source_tree()
    return tree / "data" if tree else Path(sys.prefix) / "share/homebound"


def static_build_directory() -> Path:
    """Return where ``build_static`` writes: the checkout's ``data/``, or the data folder in a package."""
    tree = source_tree()
    return tree / "data" if tree else data_directory()


def static_data_candidates() -> list[Path]:
    """Return the universe files to try, best first: ``HOMEBOUND_UNIVERSE`` alone, else a rebuilt copy in the
    data folder and then the one in a checkout's ``data/`` (only those that exist)."""
    if os.environ.get("HOMEBOUND_UNIVERSE"):
        return [Path(os.environ["HOMEBOUND_UNIVERSE"])]
    candidates = [data_directory() / UNIVERSE_FILE, bundled_data_directory() / UNIVERSE_FILE]
    return [path for index, path in enumerate(candidates) if path.is_file() and path not in candidates[:index]]


def file_uri(path: PurePath) -> str:
    """Return the ``file:`` URI of an absolute path, in the form SQLite accepts.

    Every character SQLite reads as URI syntax (``#``, ``?``, ``%``) is escaped. A drive letter gets a
    leading slash; a UNC path keeps its two, so ``server`` isn't read as a URI authority, which SQLite refuses.

    Raises:
        TypeError: If ``path`` is not a path.
    """
    require_type(path, PurePath, "path")
    name = path.as_posix()
    return f"file://{'' if name.startswith('/') else '/'}{quote(os.fsencode(name), safe='/:')}"


def read_only_uri(path: Path | str) -> str:
    """Return the URI that opens a SQLite file read-only, whatever characters its path has.

    Pass it to ``sqlite3.connect`` with ``uri=True``. A relative path is taken from the current folder.

    Raises:
        TypeError: If ``path`` is not a string or path.
    """
    require_type(path, (str, Path), "path")
    return f"{file_uri(Path(path).absolute())}?mode=ro"


def static_data_path() -> Path:
    """Return the universe file to read: ``HOMEBOUND_UNIVERSE``, a rebuilt copy in the data folder, else the bundled one.

    When none exists, the path ``build_static`` writes is returned, so the error names the file the build makes.
    """
    candidates = static_data_candidates()
    return candidates[0] if candidates else static_build_directory() / UNIVERSE_FILE

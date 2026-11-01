"""Where Homebound keeps its data, where the user's Documents folder is, and which universe file to read.

Linux keeps ``~/.local/share/homebound`` (existing data stays where it is); Windows uses
``%APPDATA%\\Homebound``. ``HOMEBOUND_HOME`` wins on both.
"""
import ctypes
import os
import sys
import uuid
from pathlib import Path

APP_FOLDER = "Homebound"
UNIVERSE_FILE = "universe.sqlite"
FOLDERID_DOCUMENTS = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"


def is_windows() -> bool:
    """Return whether Homebound runs on Windows."""
    return sys.platform == "win32"


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


def bundled_directory() -> Path:
    """Return the folder that holds ``data/`` next to the code: the source tree, or PyInstaller's bundle."""
    bundle = getattr(sys, "_MEIPASS", None)
    return Path(bundle) if bundle else Path(__file__).resolve().parents[2]


def static_data_path() -> Path:
    """Return the universe file to read: ``HOMEBOUND_UNIVERSE``, a rebuilt copy in the data folder, else the bundled one.

    When none exists, the bundled path is returned, so the error names the file the build writes.
    """
    if os.environ.get("HOMEBOUND_UNIVERSE"):
        return Path(os.environ["HOMEBOUND_UNIVERSE"])
    rebuilt = data_directory() / UNIVERSE_FILE
    if rebuilt.is_file():
        return rebuilt
    return bundled_directory() / "data" / UNIVERSE_FILE

"""Data folder, Documents folder and universe file per platform (sys.platform and the environment patched)."""
import ctypes
import sqlite3
import sys
from contextlib import closing
from pathlib import Path, PureWindowsPath
from types import SimpleNamespace

import pytest

import homebound.platform.paths as paths


@pytest.fixture
def clean_environment(monkeypatch, tmp_path) -> Path:
    """No Homebound overrides, a fake home folder."""
    for name in ("HOMEBOUND_HOME", "HOMEBOUND_UNIVERSE", "APPDATA"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return tmp_path


def test_data_directory_per_platform(clean_environment, monkeypatch) -> None:
    """Linux keeps ~/.local/share/homebound; Windows uses %APPDATA%\\Homebound; HOMEBOUND_HOME wins."""
    home = clean_environment
    monkeypatch.setattr(sys, "platform", "linux")
    assert paths.data_directory() == home / ".local/share/homebound"
    monkeypatch.setattr(sys, "platform", "win32")
    assert paths.data_directory() == home / "AppData/Roaming/Homebound"
    monkeypatch.setenv("APPDATA", str(home / "Roaming"))
    assert paths.data_directory() == home / "Roaming/Homebound"
    monkeypatch.setenv("HOMEBOUND_HOME", str(home / "custom"))
    assert paths.data_directory() == home / "custom"


class FakeWindll:
    """Stands in for ctypes.windll: SHGetKnownFolderPath answers with a scripted folder."""

    def __init__(self, folder: str | None, result: int = 0) -> None:
        """Script the folder and the HRESULT."""
        self.freed, self.requested = [], []
        self._folder, self._result = folder, result
        self.shell32 = SimpleNamespace(SHGetKnownFolderPath=self._get)
        self.ole32 = SimpleNamespace(CoTaskMemFree=self.freed.append)

    def _get(self, guid, flags, token, pointer) -> int:
        """Record the GUID and write the folder through the pointer."""
        self.requested.append(bytes(guid._obj))
        if self._result == 0:
            pointer._obj.value = self._folder
        return self._result


def test_documents_directory(clean_environment, monkeypatch) -> None:
    """Windows asks for FOLDERID_Documents and frees the string; failures and Linux use ~/Documents."""
    home = clean_environment
    monkeypatch.setattr(sys, "platform", "linux")
    assert paths.documents_directory() == home / "Documents"
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(ctypes, "windll", raising=False)
    assert paths.documents_directory() == home / "Documents"
    windll = FakeWindll(str(home / "OneDrive/Dokumente"))
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)
    assert paths.documents_directory() == home / "OneDrive/Dokumente"
    assert windll.requested == [paths.uuid.UUID(paths.FOLDERID_DOCUMENTS).bytes_le] and len(windll.freed) == 1
    monkeypatch.setattr(ctypes, "windll", FakeWindll(None, result=-2147024894), raising=False)
    assert paths.documents_directory() == home / "Documents"


def test_static_data_path(clean_environment, monkeypatch) -> None:
    """HOMEBOUND_UNIVERSE, then a rebuilt copy in the data folder, then the bundled file."""
    home = clean_environment
    monkeypatch.setenv("HOMEBOUND_HOME", str(home / "data"))
    source_tree = Path(paths.__file__).resolve().parents[2]
    assert paths.source_tree() == source_tree and not paths.is_packaged()
    assert paths.bundled_data_directory() == paths.static_build_directory() == source_tree / "data"
    monkeypatch.setattr(sys, "_MEIPASS", str(home / "bundle"), raising=False)
    (home / "bundle/data").mkdir(parents=True)
    (home / "bundle/data/universe.sqlite").write_bytes(b"")
    assert paths.static_data_path() == home / "bundle/data/universe.sqlite"
    (home / "data").mkdir()
    (home / "data/universe.sqlite").write_bytes(b"")
    assert paths.static_data_path() == home / "data/universe.sqlite"
    assert paths.static_data_candidates() == [home / "data/universe.sqlite", home / "bundle/data/universe.sqlite"]
    monkeypatch.setenv("HOMEBOUND_UNIVERSE", str(home / "elsewhere.sqlite"))
    assert paths.static_data_path() == home / "elsewhere.sqlite"


def test_packaged_paths(clean_environment, monkeypatch) -> None:
    """A frozen build reads its bundle and builds into the data folder; a system install reads <prefix>/share."""
    home = clean_environment
    monkeypatch.setenv("HOMEBOUND_HOME", str(home / "data"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(home / "bundle"), raising=False)
    assert paths.is_packaged() and paths.source_tree() is None
    assert paths.bundled_data_directory() == home / "bundle/data"
    assert paths.static_build_directory() == home / "data"
    assert paths.static_data_candidates() == []
    assert paths.static_data_path() == home / "data/universe.sqlite"     # what the build would write
    monkeypatch.delattr(sys, "_MEIPASS")
    monkeypatch.setattr(sys, "prefix", str(home / "usr"))
    assert paths.bundled_data_directory() == home / "usr/share/homebound"


# folder names with characters SQLite reads as URI syntax ("?" can't be in a Windows name)
AWKWARD_FOLDERS = ["data#1", pytest.param("what?x", marks=pytest.mark.skipif(sys.platform == "win32",
                                                                             reason="no ? in Windows names")),
                   "a%20b", "x%23y", "100%", "space here", "Grüße"]


def test_file_uri_handles_windows_paths() -> None:
    """A drive path gets a leading slash; a UNC path keeps its two, so ``server`` isn't read as an authority."""
    assert paths.file_uri(PureWindowsPath(r"C:\Users\Max#1\map.db")) == "file:///C:/Users/Max%231/map.db"
    assert paths.file_uri(PureWindowsPath(r"\\server\share\home#1\map.db")) == "file:////server/share/home%231/map.db"


def test_read_only_uri_escapes_uri_syntax(tmp_path) -> None:
    """``#``, ``?`` and ``%`` are escaped, so the only query is the ``mode=ro`` we add."""
    uri = paths.read_only_uri(Path("/x/a#b?c%20d/e%23f/100%/sp ace/ü.db"))
    # On Windows the path gets the current drive in front ("file:///D:/x/..."), which is right.
    assert uri.startswith("file:///")
    assert uri.endswith("/x/a%23b%3Fc%2520d/e%2523f/100%25/sp%20ace/%C3%BC.db?mode=ro")
    assert paths.read_only_uri("relative.db") == paths.read_only_uri(Path.cwd() / "relative.db")
    with pytest.raises(TypeError):
        paths.read_only_uri(None)


@pytest.mark.parametrize("folder", AWKWARD_FOLDERS)
def test_read_only_uri_opens_the_right_file_read_only(tmp_path, folder) -> None:
    """The URI opens the file at that path, read-only, and creates nothing else."""
    path = tmp_path / folder / "marker.db"
    path.parent.mkdir()
    with closing(sqlite3.connect(path)) as database:
        database.execute("CREATE TABLE marker (value TEXT)")
        database.execute("INSERT INTO marker VALUES ('here')")
        database.commit()
    with closing(sqlite3.connect(paths.read_only_uri(path), uri=True)) as database:
        assert database.execute("SELECT value FROM marker").fetchone() == ("here",)
        with pytest.raises(sqlite3.OperationalError):
            database.execute("INSERT INTO marker VALUES ('written')")
    assert sorted(entry.name for entry in tmp_path.iterdir()) == [folder]

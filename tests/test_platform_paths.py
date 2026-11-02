"""Data folder, Documents folder and universe file per platform (sys.platform and the environment patched)."""
import ctypes
import sys
from pathlib import Path
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

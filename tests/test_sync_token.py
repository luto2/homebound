"""Where the session token is kept: keyring first, else a 0600 file (plan §6)."""
import builtins
import stat
import sys
import types

from homebound.sync import token as token_module
from homebound.sync.token import SERVICE, TokenStore

SERVER = "https://homebound.example:8443"


class FakeKeyring:
    """A keyring in a dict; ``broken`` makes every call raise."""

    def __init__(self, broken: bool = False) -> None:
        """Start empty."""
        self.passwords: dict[tuple[str, str], str] = {}
        self.broken = broken

    def _check(self) -> None:
        """Raise when broken, as a locked or missing keyring daemon does."""
        if self.broken:
            raise RuntimeError("keyring locked")

    def get_password(self, service: str, user: str) -> str | None:
        """Return a stored password."""
        self._check()
        return self.passwords.get((service, user))

    def set_password(self, service: str, user: str, password: str) -> None:
        """Store a password."""
        self._check()
        self.passwords[(service, user)] = password

    def delete_password(self, service: str, user: str) -> None:
        """Forget a password."""
        self._check()
        del self.passwords[(service, user)]


def test_the_file_fallback_is_private_and_round_trips(tmp_path) -> None:
    """Without a keyring the token goes to a 0600 file; clear removes it; nothing stored loads as None."""
    store = TokenStore(tmp_path / "data", use_keyring=False)
    assert store.load(SERVER) is None                       # no directory yet
    store.save(SERVER, "secret-1")
    path = store._file(SERVER)
    assert path.parent == tmp_path / "data" and "/" not in path.name and ":" not in path.name
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert store.load(SERVER) == "secret-1"
    store.save(SERVER, "secret-2")                           # overwrite stays private
    assert store.load(SERVER) == "secret-2" and stat.S_IMODE(path.stat().st_mode) == 0o600
    store.clear(SERVER)
    assert not path.exists() and store.load(SERVER) is None
    store.clear(SERVER)                                      # clearing twice is fine
    path.write_text("  \n")
    assert store.load(SERVER) is None                        # an empty file counts as no token


def test_the_keyring_is_used_when_it_works(tmp_path) -> None:
    """A working keyring holds the token and no file is written."""
    store = TokenStore(tmp_path, use_keyring=False)
    store.keyring = FakeKeyring()
    store.save(SERVER, "secret")
    assert store.keyring.passwords == {(SERVICE, SERVER): "secret"}
    assert not store._file(SERVER).exists()
    assert store.load(SERVER) == "secret"
    store.clear(SERVER)
    assert store.keyring.passwords == {} and store.load(SERVER) is None


def test_a_broken_keyring_falls_back_to_the_file(tmp_path) -> None:
    """When the keyring raises, save, load and clear all use the file instead."""
    store = TokenStore(tmp_path, use_keyring=False)
    store.keyring = FakeKeyring(broken=True)
    store.save(SERVER, "secret")
    assert stat.S_IMODE(store._file(SERVER).stat().st_mode) == 0o600
    assert store.load(SERVER) == "secret"
    store.clear(SERVER)
    assert not store._file(SERVER).exists()


def test_an_empty_keyring_entry_falls_back_to_an_older_file(tmp_path) -> None:
    """A token saved to the file before the keyring worked is still found."""
    TokenStore(tmp_path, use_keyring=False).save(SERVER, "from-file")
    store = TokenStore(tmp_path, use_keyring=False)
    store.keyring = FakeKeyring()
    assert store.load(SERVER) == "from-file"


def test_no_usable_keyring_means_none(monkeypatch) -> None:
    """A missing module, the fail backend, or a broken setup all count as no keyring."""
    real_import = builtins.__import__

    def no_keyring(name, *args, **kwargs):
        if name == "keyring" or name.startswith("keyring."):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_keyring)
    assert token_module._keyring() is None
    monkeypatch.setattr(builtins, "__import__", real_import)

    class FailKeyring:
        pass

    fake = types.ModuleType("keyring")
    backends = types.ModuleType("keyring.backends")
    fail = types.ModuleType("keyring.backends.fail")
    fail.Keyring = FailKeyring
    backends.fail = fail
    fake.backends = backends
    monkeypatch.setitem(sys.modules, "keyring", fake)
    monkeypatch.setitem(sys.modules, "keyring.backends", backends)
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail)
    fake.get_keyring = FailKeyring
    assert token_module._keyring() is None
    fake.get_keyring = lambda: (_ for _ in ()).throw(RuntimeError("dbus down"))
    assert token_module._keyring() is None
    fake.get_keyring = object
    assert token_module._keyring() is fake
    assert TokenStore(".", use_keyring=True).keyring is fake
    assert TokenStore(".", use_keyring=False).keyring is None

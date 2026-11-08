"""Where the session token is kept: the system keyring if there is one, else a 0600 file (plan §6)."""
import os
from pathlib import Path

SERVICE = "homebound-sync"


def _keyring():
    """Return the keyring module if it is installed and has a usable backend, else None."""
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    try:
        if isinstance(keyring.get_keyring(), fail.Keyring):
            return None
    except Exception:   # noqa: BLE001 - a broken keyring setup counts as none
        return None
    return keyring


class TokenStore:
    """Keeps one session token per sync server."""

    def __init__(self, directory: Path, use_keyring: bool = True) -> None:
        """Use ``directory`` (DATA_DIR) for the file fallback."""
        self.directory = directory
        self.keyring = _keyring() if use_keyring else None

    def _file(self, server: str) -> Path:
        """Return the fallback file of a server."""
        name = "".join(character if character.isalnum() else "_" for character in server)[:80]
        return self.directory / f"sync_token_{name}"

    def load(self, server: str) -> str | None:
        """Return the stored token, or None."""
        if self.keyring is not None:
            try:
                token = self.keyring.get_password(SERVICE, server)
                if token:
                    return token
            except Exception:   # noqa: BLE001
                pass
        path = self._file(server)
        try:
            return path.read_text().strip() or None
        except OSError:
            return None

    def save(self, server: str, token: str) -> None:
        """Store a token."""
        if self.keyring is not None:
            try:
                self.keyring.set_password(SERVICE, server, token)
                return
            except Exception:   # noqa: BLE001 - fall back to the file
                pass
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._file(server)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            handle.write(token)
        os.chmod(path, 0o600)

    def clear(self, server: str) -> None:
        """Forget the token."""
        if self.keyring is not None:
            try:
                self.keyring.delete_password(SERVICE, server)
            except Exception:   # noqa: BLE001
                pass
        try:
            self._file(server).unlink()
        except OSError:
            pass

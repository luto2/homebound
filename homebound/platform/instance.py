"""One running Homebound per data folder: a lock file plus a local socket the second start talks to.

The first start takes ``homebound.lock`` (``QLockFile``) and listens on a ``QLocalServer`` named after the data
folder. A second start finds the lock taken, tells the running one to show its window and gives up. A lock left
by a process that is gone (crash, kill) is taken over: Qt treats a lock whose PID is dead as stale, whatever its age.
"""
import hashlib
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

LOCK_FILE = "homebound.lock"
SHOW_MESSAGE = b"show\n"
CONNECT_MS = 1000       # how long a second start waits for the running one to answer


def server_name(data_directory: Path) -> str:
    """Return the local socket name for a data folder (a hash of its resolved path, so any path length works)."""
    digest = hashlib.sha1(str(Path(data_directory).resolve()).encode("utf-8")).hexdigest()[:16]
    return f"homebound-{digest}"


class InstanceGuard(QObject):
    """The running instance's claim on a data folder; ``showRequested`` fires when a second start asks for the window."""

    showRequested = Signal()

    def __init__(self, lock: QLockFile, server: QLocalServer) -> None:
        """Hold the lock and serve ``show`` requests."""
        super().__init__()
        self.lock, self.server = lock, server
        self.server.newConnection.connect(self._accept)

    def _accept(self) -> None:
        """Take every waiting connection and read its message."""
        while (socket := self.server.nextPendingConnection()) is not None:
            socket.setParent(self)
            socket.readyRead.connect(lambda socket=socket: self._read(socket))
            socket.disconnected.connect(socket.deleteLater)
            if socket.bytesAvailable():
                self._read(socket)

    def _read(self, socket: QLocalSocket) -> None:
        """Raise the window for a ``show`` message; nothing else is understood."""
        if bytes(socket.readAll()).strip() == SHOW_MESSAGE.strip():
            self.showRequested.emit()
        socket.disconnectFromServer()

    def release(self) -> None:
        """Stop serving and remove the lock (safe to call twice)."""
        self.server.close()
        self.lock.unlock()


def _ask_to_show(name: str) -> bool:
    """Send ``show`` to the running instance; return whether it was reached."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(CONNECT_MS):
        return False
    socket.write(SHOW_MESSAGE)
    socket.flush()
    socket.waitForBytesWritten(CONNECT_MS)
    socket.disconnectFromServer()
    return True


def acquire_single_instance(data_directory: Path) -> InstanceGuard | None:
    """Claim the data folder for this process.

    Args:
        data_directory: The folder with settings and maps (created if missing).

    Returns:
        The guard (keep it until the app ends and call :meth:`InstanceGuard.release`), or None when another
        Homebound already runs on this folder; it has been asked to show its window. Needs a Qt application.
    """
    data_directory = Path(data_directory)
    data_directory.mkdir(parents=True, exist_ok=True)
    name = server_name(data_directory)
    lock = QLockFile(str(data_directory / LOCK_FILE))
    lock.setStaleLockTime(0)    # never stale by age; a dead PID still is
    if not lock.tryLock(0):     # tryLock itself removes a lock whose PID is gone and tries again
        _ask_to_show(name) or _ask_to_show(name)    # the holder may still be starting up and not listening yet
        return None
    QLocalServer.removeServer(name)     # a socket file left by a crashed run (we hold the lock, so it is dead)
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(name):
        lock.unlock()
        return None
    return InstanceGuard(lock, server)


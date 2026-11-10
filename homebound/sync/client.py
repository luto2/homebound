"""The sync socket in a background thread: connect, hello, receive, reconnect with backoff (protocol §1, §9).

The thread only moves messages. ``on_message`` and ``on_state`` are called from the thread; the Qt
side turns them into queued signals so all map work happens on the main thread.
"""
import json
import logging
import random
import threading
import time
from collections.abc import Callable

from websockets.exceptions import WebSocketException
from websockets.sync.client import ClientConnection, connect

from ..http import ssl_context
from .protocol import MAX_MESSAGE_BYTES

log = logging.getLogger("homebound.sync")
BACKOFF_START, BACKOFF_MAX, BACKOFF_RESET = 1.0, 60.0, 60.0


def socket_url(server: str) -> str:
    """Return the WebSocket URL of a sync server (``https://x`` -> ``wss://x/ws``)."""
    server = server.rstrip("/")
    if server.startswith("https://"):
        return "wss://" + server[len("https://"):] + "/ws"
    if server.startswith("http://"):
        return "ws://" + server[len("http://"):] + "/ws"
    return server


class SocketWorker:
    """Keeps one socket to the sync server open while started."""

    def __init__(self, server: str, hello: Callable[[], dict], on_message: Callable[[dict], object],
                 on_state: Callable[[str], object]) -> None:
        """Set up (not started).

        Args:
            server: The server's base URL.
            hello: Returns the ``hello`` message for each new connection.
            on_message: Gets every decoded message.
            on_state: Gets ``connecting``, ``connected``, ``offline`` or ``stopped``.
        """
        self.url = socket_url(server)
        self.hello, self.on_message, self.on_state = hello, on_message, on_state
        self.connection: ClientConnection | None = None
        self.lock = threading.Lock()
        self.running = False
        self.thread: threading.Thread | None = None
        self.wake = threading.Event()

    def start(self) -> None:
        """Start the thread (it reconnects on its own)."""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._run, name="homebound-sync", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        """Close the socket and end the thread."""
        self.running = False
        self.wake.set()
        with self.lock:
            connection = self.connection
        if connection is not None:
            try:
                connection.close()
            except Exception:   # noqa: BLE001
                pass
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=5)

    def reconnect_now(self) -> None:
        """Skip the rest of the backoff wait (e.g. right after signing in)."""
        self.wake.set()

    def send(self, message: dict) -> bool:
        """Send a message if connected (thread-safe). Returns whether it went out."""
        with self.lock:
            connection = self.connection
        if connection is None:
            return False
        try:
            connection.send(json.dumps(message))
            return True
        except (WebSocketException, OSError) as error:
            log.info("send failed: %s", error)
            return False

    def _run(self) -> None:
        """Connect, read until the socket closes, wait, repeat."""
        delay = BACKOFF_START
        while self.running:
            self.on_state("connecting")
            started = time.monotonic()
            try:
                options = {"ssl": ssl_context()} if self.url.startswith("wss://") else {}
                with connect(self.url, max_size=MAX_MESSAGE_BYTES * 4, open_timeout=15, close_timeout=2,
                             **options) as connection:
                    with self.lock:
                        self.connection = connection
                    connection.send(json.dumps(self.hello()))
                    self.on_state("connected")
                    for text in connection:
                        try:
                            message = json.loads(text)
                        except ValueError:
                            continue
                        if isinstance(message, dict):
                            self.on_message(message)
            except (WebSocketException, OSError, TimeoutError) as error:
                log.info("sync socket: %s", error)
            finally:
                with self.lock:
                    self.connection = None
            if not self.running:
                break
            self.on_state("offline")
            if time.monotonic() - started > BACKOFF_RESET:
                delay = BACKOFF_START
            self.wake.clear()
            self.wake.wait(delay * random.uniform(0.7, 1.3))
            delay = min(BACKOFF_MAX, delay * 2)
        self.on_state("stopped")

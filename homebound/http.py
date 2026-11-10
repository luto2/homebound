"""Shared HTTP plumbing: the User-Agent, the client, ISO time parsing and the background fetcher base class."""
import functools
import ssl
import threading
from collections.abc import Callable
from datetime import datetime, timezone

import certifi
import httpx

from . import app_version
from .validation import require_text


def user_agent(purpose: str = "personal offline wormhole mapper") -> str:
    """Return the User-Agent Homebound sends, e.g. ``homebound/0.3 (personal offline wormhole mapper)``."""
    require_text(purpose, "purpose")
    return f"homebound/{app_version()} ({purpose})"


@functools.cache
def ssl_context() -> ssl.SSLContext:
    """Return the one TLS context every Homebound connection uses: the system CAs plus certifi's bundle.

    A packaged app carries its own OpenSSL, whose default CA paths may not exist on the user's distribution
    (Arch), so certifi's ``cacert.pem`` is loaded on top of the system store. A company proxy CA in the
    system store keeps working. Built once and shared.
    """
    context = ssl.create_default_context()
    context.load_verify_locations(certifi.where())
    return context


def make_client(purpose: str = "personal offline wormhole mapper") -> httpx.Client:
    """Return an HTTP client with Homebound's User-Agent, gzip, a 30 second timeout and :func:`ssl_context`.

    Args:
        purpose: The text in parentheses in the User-Agent.
    """
    return httpx.Client(headers={"User-Agent": user_agent(purpose), "Accept-Encoding": "gzip"}, timeout=30,
                        verify=ssl_context())


def parse_utc(text: object) -> datetime | None:
    """Parse an ISO time (``Z`` allowed, naive means UTC) into an aware datetime, or None if it isn't one."""
    try:
        moment = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class BackgroundFetcher:
    """What the fetchers share: callbacks, a daemon worker thread running ``self._run`` and status reports.

    Subclasses define ``_run`` (the worker loop) and ``stop``.
    """

    def __init__(self, name: str, on_update: Callable[..., object],
                 on_status: Callable[[str, str], object] | None) -> None:
        """Create the fetcher; call :meth:`start` to run it.

        Args:
            name: The thread name.
            on_update: Called (from the worker thread) after new data was stored.
            on_status: Called with ``(level, text)`` after each request, or None.
        """
        if not callable(on_update) or (on_status is not None and not callable(on_status)):
            raise TypeError("on_update and on_status must be callable")
        self.on_update, self.on_status = on_update, on_status
        self._stopping = False
        self.thread = threading.Thread(target=self._run, name=name, daemon=True)

    def start(self) -> None:
        """Start the worker thread."""
        self.thread.start()

    def _run(self) -> None:
        """Worker loop; defined by the subclass."""
        raise NotImplementedError

    def _status(self, level: str, text: str) -> None:
        """Report a status if a status callback was given."""
        if self.on_status:
            self.on_status(level, text)

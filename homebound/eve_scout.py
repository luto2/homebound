"""EVE-Scout's public Thera and Turnur connections, fetched in the background for routes.

GET https://api.eve-scout.com/v2/public/signatures returns every wormhole EVE-Scout has scanned from
Thera and Turnur; each entry has both ends (``out_system_id`` is Thera/Turnur, ``in_system_id`` the
other side), the largest ship that fits and when the hole expires. The list is kept in memory only.
"""
import threading
from collections.abc import Callable
from datetime import datetime, timezone

import httpx

from .http import BackgroundFetcher, make_client, parse_utc
from .routing import ScoutLink
from .validation import is_int, is_number, require_type

API_URL = "https://api.eve-scout.com/v2/public/signatures"
REFRESH_INTERVAL = 5 * 60     # seconds between fetches
HUBS = ("thera", "turnur")      # out_system_name, lower case
SHIP_SIZES = {"small": "S", "medium": "M", "large": "L", "xlarge": "XL", "capital": "XL"}


def parse_signatures(data: object, now: datetime | None = None) -> list[ScoutLink]:
    """Turn the API response into links, skipping malformed, expired and non-wormhole entries.

    Args:
        data: The decoded JSON (a list of signature entries).
        now: The current time (UTC), or None for the clock.

    Returns:
        One link per usable entry.
    """
    require_type(now, (datetime, type(None)), "now")
    now = now or datetime.now(timezone.utc)
    links = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict) or entry.get("signature_type", "wormhole") != "wormhole":
            continue
        hub = str(entry.get("out_system_name", "")).lower()
        hub_id, system_id = entry.get("out_system_id"), entry.get("in_system_id")
        if hub not in HUBS or not all(is_int(value) and value > 0 for value in (hub_id, system_id)):
            continue
        expires = parse_utc(entry.get("expires_at"))
        if expires is not None and expires <= now:
            continue
        remaining = entry.get("remaining_hours")
        wormhole_type = entry.get("wh_type")
        links.append(ScoutLink(hub_id, system_id, hub,
                               wormhole_type if isinstance(wormhole_type, str) and wormhole_type else None,
                               SHIP_SIZES.get(str(entry.get("max_ship_size", "")).lower()),
                               expires.isoformat() if expires else None,
                               float(remaining) if is_number(remaining) else None))
    return links


class EveScoutFetcher(BackgroundFetcher):
    """Fetches EVE-Scout's connections every few minutes on a background thread.

    ``on_update()`` is called from the worker thread after new links were stored, so marshal it to Qt
    with a signal.
    """

    def __init__(self, on_update: Callable[[], object], on_status: Callable[[str, str], object] | None = None) -> None:
        """Create the fetcher; call :meth:`start` to run it.

        Args:
            on_update: Called after the links changed.
            on_status: Called with ``(level, text)`` after each request.
        """
        super().__init__("eve-scout", on_update, on_status)
        self._links: list[ScoutLink] = []
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._enabled = True

    @property
    def enabled(self) -> bool:
        """Whether the fetcher fetches; turning it on fetches right away, turning it off forgets the links."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        """Turn fetching on or off."""
        require_type(value, bool, "enabled")
        was = self._enabled
        self._enabled = value
        if value and not was:
            self._wake.set()
        elif not value and was:
            with self._lock:
                had_links = bool(self._links)
                self._links = []
            if had_links:
                self.on_update()

    def links(self) -> list[ScoutLink]:
        """Return the current links (empty while off), dropping those that expired since the fetch."""
        now = datetime.now(timezone.utc)
        with self._lock:
            return [link for link in self._links
                    if not link.expires_at or datetime.fromisoformat(link.expires_at) > now]

    def stop(self) -> None:
        """Ask the worker thread to finish."""
        self._stopping = True
        self._wake.set()

    def refresh(self) -> None:
        """Fetch now instead of waiting for the next interval."""
        self._wake.set()

    def _run(self) -> None:
        """Worker loop: fetch, then sleep until the interval ends or something wakes the thread."""
        client = make_client()
        try:
            while not self._stopping:
                if self._enabled:
                    self.fetch(client)
                self._wake.wait(REFRESH_INTERVAL)
                self._wake.clear()
        finally:
            client.close()

    def fetch(self, client: httpx.Client) -> bool:
        """Fetch and store the links once.

        Returns:
            True if new links were stored.
        """
        try:
            response = client.get(API_URL)
            response.raise_for_status()
            links = parse_signatures(response.json())
        except (httpx.HTTPError, ValueError) as error:
            self._status("warn", f"EVE-Scout: {type(error).__name__}")
            return False
        if not self._enabled:
            return False
        with self._lock:
            self._links = links
        self.on_update()
        thera = sum(1 for link in links if link.hub == "thera")
        self._status("ok", f"EVE-Scout {thera} Thera · {len(links) - thera} Turnur")
        return True

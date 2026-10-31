"""zKillboard activity for systems in visible chains (the only network call Homebound makes).

One request per system: /api/kills/solarSystemID/<id>/pastSeconds/86400/ returns killmails
with killmail_time, which gives kills 1h / 24h, pod kills 1h and last kill age.
Max one request per REQUEST_INTERVAL seconds, cached in the map DB for CACHE_LIFETIME.
"""
import queue
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone

import httpx

from .mapdb.store import Store, ZkillStats, utc_now_iso
from .validation import require_integer, require_text, require_type

API_URL = "https://zkillboard.com/api/kills/solarSystemID/{}/pastSeconds/86400/"
USER_AGENT = "homebound/0.1 (personal offline wormhole mapper)"
CACHE_LIFETIME = timedelta(minutes=10)
REQUEST_INTERVAL = 1.2        # seconds between requests
RATE_LIMIT_PAUSE = 10         # seconds to wait after HTTP 429
POD_TYPE_IDS = {670, 33328}   # capsule, Genolution 'Auroral' 197-variant capsule
_STOP = None                  # queue item that ends the worker


def summarize(system_id: int, kills: list[dict], now: datetime | None = None) -> ZkillStats:
    """Turn a zKill killmail list into activity numbers.

    Killmails without a readable ``killmail_time`` are skipped.

    Args:
        system_id: The system the kills are in.
        kills: The decoded API response.
        now: The current time (UTC), or None for the clock.

    Returns:
        Kills in the last hour and day, pod kills in the last hour, and the time of the last kill.
    """
    require_integer(system_id, "system_id", minimum=1)
    require_type(kills, list, "kills")
    require_type(now, (datetime, type(None)), "now")
    now = now or datetime.now(timezone.utc)
    times = []
    pod_kills_1h = 0
    for kill in kills:
        try:
            killed_at = datetime.fromisoformat(kill["killmail_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        times.append(killed_at)
        victim = kill.get("victim") or {}
        if now - killed_at <= timedelta(hours=1) and victim.get("ship_type_id") in POD_TYPE_IDS:
            pod_kills_1h += 1
    kills_1h = sum(1 for killed_at in times if now - killed_at <= timedelta(hours=1))
    last = max(times).isoformat() if times else None
    return ZkillStats(system_id, kills_1h, len(times), pod_kills_1h, last, utc_now_iso())


def is_fresh(stats: ZkillStats | None, now: datetime | None = None) -> bool:
    """Return whether cached stats are younger than CACHE_LIFETIME (None is never fresh)."""
    require_type(stats, (ZkillStats, type(None)), "stats")
    if not stats:
        return False
    now = now or datetime.now(timezone.utc)
    return now - datetime.fromisoformat(stats.fetched_at) < CACHE_LIFETIME


class ZkillFetcher:
    """Fetches zKill activity on a background thread.

    Call :meth:`request` whenever the visible set of systems changes. ``on_update(system_id)`` is
    called from the worker thread, so marshal it to Qt with a signal.
    """

    def __init__(self, database_path: str, on_update: Callable[[int], object],
                 on_status: Callable[[str, str], object] | None = None) -> None:
        """Create the fetcher; call :meth:`start` to run it.

        Args:
            database_path: The map file the cache lives in. Change it when the user switches maps.
            on_update: Called with a system ID after its stats were stored.
            on_status: Called with ``(level, text)`` after each request.
        """
        require_text(database_path, "database_path")
        if not callable(on_update) or (on_status is not None and not callable(on_status)):
            raise TypeError("on_update and on_status must be callable")
        self.database_path = database_path
        self.on_update, self.on_status = on_update, on_status
        self.queue: queue.Queue[tuple[int, bool] | None] = queue.Queue()
        self.pending: set[int] = set()
        self._pending_lock = threading.Lock()
        self.enabled = True
        self._stopping = False
        self.thread = threading.Thread(target=self._run, name="zkill", daemon=True)

    def start(self) -> None:
        """Start the worker thread."""
        self.thread.start()

    def stop(self) -> None:
        """Ask the worker thread to finish after its current request."""
        self._stopping = True
        self.queue.put(_STOP)

    def request(self, system_ids: Iterable[int], force: bool = False) -> int:
        """Queue systems for a fetch; systems already queued are skipped.

        Args:
            system_ids: The systems.
            force: Fetch even if the cache is fresh.

        Returns:
            The number of systems queued.
        """
        system_ids = list(system_ids)
        for system_id in system_ids:
            require_integer(system_id, "system_ids item", minimum=1)
        if not self.enabled:
            return 0
        queued = 0
        for system_id in system_ids:
            with self._pending_lock:
                if system_id in self.pending:
                    continue
                self.pending.add(system_id)
            self.queue.put((system_id, force))
            queued += 1
        return queued

    def _status(self, level: str, text: str) -> None:
        """Report a status if a status callback was given."""
        if self.on_status:
            self.on_status(level, text)

    def _run(self) -> None:
        """Worker loop: one request per queued system, at most one per REQUEST_INTERVAL."""
        path = self.database_path
        store = Store(path)  # own connection for this thread
        client = httpx.Client(headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}, timeout=30)
        while not self._stopping:
            item = self.queue.get()
            if item is _STOP:
                break
            system_id, force = item
            with self._pending_lock:
                self.pending.discard(system_id)
            if self.database_path != path:  # the user switched maps
                store.close()
                path = self.database_path
                store = Store(path)
            if not self.enabled or (not force and is_fresh(store.zkill_stats(system_id))):
                continue
            self._fetch(client, store, system_id)
            time.sleep(REQUEST_INTERVAL)
        client.close()
        store.close()

    def _fetch(self, client: httpx.Client, store: Store, system_id: int) -> bool:
        """Fetch and store the stats of one system.

        Returns:
            True if new stats were stored.
        """
        try:
            response = client.get(API_URL.format(system_id))
            if response.status_code == 429:
                time.sleep(RATE_LIMIT_PAUSE)
                self.request([system_id])
                return False
            response.raise_for_status()
            data = response.json()
            store.set_zkill_stats(summarize(system_id, data if isinstance(data, list) else []))
        except (httpx.HTTPError, ValueError) as error:
            self._status("warn", f"zKill: {type(error).__name__}")
            return False
        self.on_update(system_id)
        self._status("ok", "zKill ok")
        return True

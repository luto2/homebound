"""zKillboard activity for systems in visible chains (one of two network calls, with EVE-Scout).

One request per system: /api/kills/solarSystemID/<id>/pastSeconds/86400/ returns killmails
with killmail_time, which gives kills 1h / 24h, pod kills 1h and last kill age.
Max one request per REQUEST_INTERVAL seconds, cached in the map DB for CACHE_LIFETIME.
"""
import queue
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone

import httpx

from .http import BackgroundFetcher, make_client, parse_utc
from .mapdb.store import Store, ZkillStats, utc_now_iso
from .validation import require_integer, require_text, require_type

API_URL = "https://zkillboard.com/api/kills/solarSystemID/{}/pastSeconds/86400/"
CACHE_LIFETIME = timedelta(minutes=10)
RECENT_KILL_WINDOW = timedelta(minutes=10)   # a kill this young counts as "just now"
ZKILL_STALE_AFTER = timedelta(minutes=30)    # cached stats this old are shown as stale
REQUEST_INTERVAL = 1.2        # seconds between requests
RATE_LIMIT_PAUSE = 10         # seconds to wait after HTTP 429
POD_TYPE_IDS = {670, 33328}   # capsule, Genolution 'Auroral' 197-variant capsule
_STOP = None                  # queue item that ends the worker


def summarize(system_id: int, kills: list[dict], now: datetime | None = None) -> ZkillStats:
    """Turn a zKill killmail list into activity numbers.

    Killmails without a readable ``killmail_time`` are skipped; a ``victim`` that is not a dict
    only skips the pod check.

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
        killed_at = parse_utc(kill.get("killmail_time")) if isinstance(kill, dict) else None
        if killed_at is None:
            continue
        times.append(killed_at)
        victim = kill.get("victim")
        ship_type_id = victim.get("ship_type_id") if isinstance(victim, dict) else None
        if (now - killed_at <= timedelta(hours=1) and isinstance(ship_type_id, int)
                and ship_type_id in POD_TYPE_IDS):
            pod_kills_1h += 1
    kills_1h = sum(1 for killed_at in times if now - killed_at <= timedelta(hours=1))
    last = max(times).isoformat() if times else None
    return ZkillStats(system_id, kills_1h, len(times), pod_kills_1h, last, utc_now_iso())


def is_fresh(stats: ZkillStats | None, now: datetime | None = None) -> bool:
    """Return whether cached stats are younger than CACHE_LIFETIME (None is never fresh)."""
    require_type(stats, (ZkillStats, type(None)), "stats")
    return bool(stats) and age(stats, now) < CACHE_LIFETIME


def age(stats: ZkillStats | None, now: datetime | None = None) -> timedelta | None:
    """Return the time since the stats were fetched (None without stats)."""
    require_type(stats, (ZkillStats, type(None)), "stats")
    if not stats:
        return None
    return (now or datetime.now(timezone.utc)) - datetime.fromisoformat(stats.fetched_at)


def had_recent_kill(stats: ZkillStats | None, now: datetime | None = None) -> bool:
    """Return whether the last kill was within RECENT_KILL_WINDOW."""
    require_type(stats, (ZkillStats, type(None)), "stats")
    if not stats or not stats.last_kill_at:
        return False
    return (now or datetime.now(timezone.utc)) - datetime.fromisoformat(stats.last_kill_at) < RECENT_KILL_WINDOW


class ZkillFetcher(BackgroundFetcher):
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
        super().__init__("zkill", on_update, on_status)
        self.database_path = database_path
        self.queue: queue.Queue[tuple[int, bool] | None] = queue.Queue()
        self.pending: set[int] = set()
        self.forced: set[int] = set()      # queued systems that are to be fetched even if fresh
        self._pending_lock = threading.Lock()
        self.enabled = True

    def stop(self) -> None:
        """Ask the worker thread to finish after its current request."""
        self._stopping = True
        self.queue.put(_STOP)

    def request(self, system_ids: Iterable[int], force: bool = False) -> int:
        """Queue systems for a fetch; systems already queued are not queued twice.

        A forced request for a system that is already queued without force upgrades the queued
        entry, so it is fetched even if the cache is fresh.

        Args:
            system_ids: The systems.
            force: Fetch even if the cache is fresh.

        Returns:
            The number of systems queued or upgraded to forced.
        """
        system_ids = list(system_ids)
        for system_id in system_ids:
            require_integer(system_id, "system_ids item", minimum=1)
        if not self.enabled:
            return 0
        self._restart_if_dead()
        queued = 0
        for system_id in system_ids:
            with self._pending_lock:
                if system_id in self.pending:
                    if force and system_id not in self.forced:
                        self.forced.add(system_id)
                        queued += 1
                    continue
                self.pending.add(system_id)
                if force:
                    self.forced.add(system_id)
            self.queue.put((system_id, force))
            queued += 1
        return queued

    def _restart_if_dead(self) -> None:
        """Replace the worker thread if it died without being stopped, and say so on the status line."""
        if self._stopping or self.thread.ident is None or self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._run, name="zkill", daemon=True)
        self.thread.start()
        self._status("err", "zKill worker restarted")

    def _run(self) -> None:
        """Worker loop: one request per queued system, at most one per REQUEST_INTERVAL.

        An error on one system is reported on the status line and the loop goes on; the system is
        not queued again (the next request retries it). The client and the store are always closed.
        """
        client = store = None
        try:
            path = self.database_path
            store = Store(path)  # own connection for this thread
            client = make_client()
            while not self._stopping:
                item = self.queue.get()
                if item is _STOP:
                    break
                system_id, force = item
                try:
                    with self._pending_lock:
                        self.pending.discard(system_id)
                        force = force or system_id in self.forced
                        self.forced.discard(system_id)
                    if self.database_path != path:  # the user switched maps
                        store.close()
                        path = self.database_path
                        store = Store(path)
                    if not self.enabled or (not force and is_fresh(store.zkill_stats(system_id))):
                        continue
                    self._fetch(client, store, system_id, force=force)
                except Exception as error:  # keep the worker alive whatever one system does
                    self._status("err", f"zKill: {type(error).__name__}")
                time.sleep(REQUEST_INTERVAL)  # also after an error, so failures stay rate limited
        finally:
            if client:
                client.close()
            if store:
                store.close()

    def _fetch(self, client: httpx.Client, store: Store, system_id: int, force: bool = False) -> bool:
        """Fetch and store the stats of one system.

        Args:
            client: The HTTP client.
            store: The map store the stats go into.
            system_id: The system.
            force: The fetch was forced; a retry after a rate limit stays forced.

        Returns:
            True if new stats were stored.
        """
        try:
            response = client.get(API_URL.format(system_id))
            if response.status_code == 429:
                time.sleep(RATE_LIMIT_PAUSE)
                self.request([system_id], force=force)
                return False
            response.raise_for_status()
            data = response.json()
            store.set_zkill_stats(summarize(system_id, data if isinstance(data, list) else []))
        except (httpx.HTTPError, ValueError, sqlite3.Error) as error:
            self._status("warn", f"zKill: {type(error).__name__}")
            return False
        self.on_update(system_id)
        self._status("ok", "zKill ok")
        return True

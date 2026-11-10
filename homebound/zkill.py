"""zKillboard activity for systems in visible chains (one of two network calls, with EVE-Scout).

One request per system: /api/kills/solarSystemID/<id>/pastSeconds/86400/ returns full killmails:
killmail_time gives kills 1h / 24h, pod kills 1h and last kill age; victim, attackers and zkb give the
rows of the activity list (ships, value, NPC / solo).
Max one request per REQUEST_INTERVAL seconds, cached in the map DB for CACHE_LIFETIME.
"""
import queue
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone

import httpx

from .http import BackgroundFetcher, make_client, parse_utc
from .mapdb.store import KillSummary, Store, ZkillStats, utc_now_iso
from .validation import require_integer, require_text, require_type

API_URL = "https://zkillboard.com/api/kills/solarSystemID/{}/pastSeconds/86400/"
CACHE_LIFETIME = timedelta(minutes=10)
RECENT_KILL_WINDOW = timedelta(minutes=10)   # a kill this young counts as "just now"
ZKILL_STALE_AFTER = timedelta(minutes=30)    # cached stats this old are shown as stale
REQUEST_INTERVAL = 1.2        # seconds between requests
RATE_LIMIT_PAUSE = 10         # seconds to wait after HTTP 429
POD_TYPE_IDS = {670, 33328}   # capsule, Genolution 'Auroral' 197-variant capsule
MAX_KILLS_KEPT = 50           # the newest kills stored per system for the activity list
_STOP = None                  # queue item that ends the worker


def _is_npc(kill: dict) -> bool:
    """Return whether zKill flags a killmail as an NPC kill (``zkb.npc``)."""
    zkb = kill.get("zkb")
    return isinstance(zkb, dict) and zkb.get("npc") is True


def _ship_type_id(entry: object) -> int | None:
    """Return the ``ship_type_id`` of a victim or attacker entry, or None if it isn't a real ship type."""
    ship_type_id = entry.get("ship_type_id") if isinstance(entry, dict) else None
    return ship_type_id if isinstance(ship_type_id, int) and not isinstance(ship_type_id, bool) and ship_type_id > 0 else None


def summarize(system_id: int, kills: list[dict], now: datetime | None = None, count_npc: bool = False) -> ZkillStats:
    """Turn a zKill killmail list into activity numbers.

    Killmails without a readable ``killmail_time`` are skipped; a ``victim`` that is not a dict
    only skips the pod check. NPC kills (``zkb.npc``) are left out unless ``count_npc`` is set.

    Args:
        system_id: The system the kills are in.
        kills: The decoded API response.
        now: The current time (UTC), or None for the clock.
        count_npc: Count NPC kills as well (a rat killing a ratter makes the system look hot).

    Returns:
        Kills in the last hour and day, pod kills in the last hour, and the time of the last kill.
    """
    require_integer(system_id, "system_id", minimum=1)
    require_type(kills, list, "kills")
    require_type(now, (datetime, type(None)), "now")
    require_type(count_npc, bool, "count_npc")
    now = now or datetime.now(timezone.utc)
    times = []
    pod_kills_1h = 0
    for kill in kills:
        killed_at = parse_utc(kill.get("killmail_time")) if isinstance(kill, dict) else None
        if killed_at is None or (_is_npc(kill) and not count_npc):
            continue
        times.append(killed_at)
        if now - killed_at <= timedelta(hours=1) and _ship_type_id(kill.get("victim")) in POD_TYPE_IDS:
            pod_kills_1h += 1
    kills_1h = sum(1 for killed_at in times if now - killed_at <= timedelta(hours=1))
    last = max(times).isoformat() if times else None
    return ZkillStats(system_id, kills_1h, len(times), pod_kills_1h, last, utc_now_iso())


def summarize_kills(system_id: int, kills: list[dict]) -> list[KillSummary]:
    """Reduce killmails to the rows of the activity list, newest first.

    Entries without a readable time or a killmail ID, and repeated IDs, are skipped. A malformed
    ``victim``, ``attackers`` or ``zkb`` only leaves its fields empty. NPC kills are kept (the list
    shows them dimmed).

    Args:
        system_id: The system the kills are in.
        kills: The decoded API response.

    Returns:
        At most MAX_KILLS_KEPT rows.
    """
    require_integer(system_id, "system_id", minimum=1)
    require_type(kills, list, "kills")
    rows: dict[int, KillSummary] = {}
    for kill in kills:
        if not isinstance(kill, dict):
            continue
        killed_at, killmail_id = parse_utc(kill.get("killmail_time")), kill.get("killmail_id")
        if (killed_at is None or not isinstance(killmail_id, int) or isinstance(killmail_id, bool)
                or killmail_id in rows):
            continue
        attackers = kill.get("attackers")
        attackers = [entry for entry in attackers if isinstance(entry, dict)] if isinstance(attackers, list) else []
        ships = Counter(ship for ship in map(_ship_type_id, attackers) if ship)
        zkb = kill.get("zkb") if isinstance(kill.get("zkb"), dict) else {}
        value = zkb.get("totalValue")
        value = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0
        rows[killmail_id] = KillSummary(
            system_id, killmail_id, killed_at.isoformat(), _ship_type_id(kill.get("victim")), len(attackers),
            ships.most_common(1)[0][0] if ships else None, value, int(_is_npc(kill)), int(zkb.get("solo") is True))
    newest = sorted(rows.values(), key=lambda row: (row.killed_at, row.killmail_id), reverse=True)
    return newest[:MAX_KILLS_KEPT]


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
        self.count_npc = False             # count NPC kills in the meter (off by default)

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
            data = data if isinstance(data, list) else []
            store.set_zkill_stats(summarize(system_id, data, count_npc=self.count_npc))
            store.replace_zkill_kills(system_id, summarize_kills(system_id, data))
        except (httpx.HTTPError, ValueError, sqlite3.Error) as error:
            self._status("warn", f"zKill: {type(error).__name__}")
            return False
        self.on_update(system_id)
        self._status("ok", "zKill ok")
        return True

"""zKillboard activity for systems in visible chains (the only network call Homebound makes).

One request per system: /api/kills/solarSystemID/<id>/pastSeconds/86400/ returns killmails
with killmail_time, which gives kills 1h / 24h, pod kills 1h and last kill age.
Max one request per ZK_INTERVAL seconds, cached in the map DB for ZK_TTL.
"""
import queue
import threading
import time
from datetime import datetime, timedelta, timezone

import httpx

from .mapdb.store import Store, ZkillStats, now_iso

API = "https://zkillboard.com/api/kills/solarSystemID/{}/pastSeconds/86400/"
UA = "homebound/0.1 (personal offline wormhole mapper)"
ZK_TTL = timedelta(minutes=10)
ZK_INTERVAL = 1.2
PODS = {670, 33328}


def summarize(system_id: int, kills: list[dict], now: datetime | None = None) -> ZkillStats:
    now = now or datetime.now(timezone.utc)
    times = []
    pods_1h = 0
    for k in kills:
        try:
            t = datetime.fromisoformat(k["killmail_time"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        times.append(t)
        if now - t <= timedelta(hours=1) and k.get("victim", {}).get("ship_type_id") in PODS:
            pods_1h += 1
    k1 = sum(1 for t in times if now - t <= timedelta(hours=1))
    last = max(times).isoformat() if times else None
    return ZkillStats(system_id, k1, len(times), pods_1h, last, now_iso())


def is_fresh(z: ZkillStats | None) -> bool:
    if not z:
        return False
    return datetime.now(timezone.utc) - datetime.fromisoformat(z.fetched_at) < ZK_TTL


class ZkillFetcher:
    """Background thread; call request(ids) whenever the visible set changes.
    on_update(system_id) is called from the worker thread -> marshal to Qt via a signal."""

    def __init__(self, db_path: str, on_update, on_status=None):
        self.db_path = db_path
        self.on_update, self.on_status = on_update, on_status
        self.q: queue.Queue[int] = queue.Queue()
        self.pending: set[int] = set()
        self.enabled = True
        self._stop = False
        self.thread = threading.Thread(target=self._run, name="zkill", daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self._stop = True
        self.q.put(-1)

    def request(self, system_ids, force: bool = False):
        if not self.enabled:
            return
        for sid in system_ids:
            if sid not in self.pending:
                self.pending.add(sid)
                self.q.put(-sid - 2 if force else sid)

    def _run(self):
        path = self.db_path
        store = Store(path)  # own connection for this thread
        client = httpx.Client(headers={"User-Agent": UA, "Accept-Encoding": "gzip"}, timeout=30)
        while not self._stop:
            item = self.q.get()
            if item == -1:
                break
            force = item < -1
            sid = -item - 2 if force else item
            self.pending.discard(sid)
            if self.db_path != path:  # the user switched maps
                store.close()
                path = self.db_path
                store = Store(path)
            if not self.enabled or (not force and is_fresh(store.zkill(sid))):
                continue
            try:
                r = client.get(API.format(sid))
                if r.status_code == 429:
                    time.sleep(10)
                    self.request([sid])
                    continue
                r.raise_for_status()
                data = r.json()
                store.set_zkill(summarize(sid, data if isinstance(data, list) else []))
                self.on_update(sid)
                if self.on_status:
                    self.on_status("ok", "zKill ok")
            except (httpx.HTTPError, ValueError) as e:
                if self.on_status:
                    self.on_status("warn", f"zKill: {type(e).__name__}")
            time.sleep(ZK_INTERVAL)
        client.close()

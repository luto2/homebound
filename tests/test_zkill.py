"""zKill summaries, the cache check and the fetcher, with a fake HTTP transport."""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from conftest import HOME
from homebound import zkill
from homebound.mapdb.store import KillSummary, Store, ZkillStats, utc_now_iso

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
SAMPLE = json.loads((Path(__file__).parent / "data" / "zkill_sample.json").read_text())


def killmail(minutes_ago: int, ship_type_id: int = 587) -> dict:
    """A minimal killmail some minutes before NOW."""
    return {"killmail_time": (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z"),
            "victim": {"ship_type_id": ship_type_id}}


def test_summarize_counts_kills_pods_and_last_kill() -> None:
    """Kills in the last hour and day, pods in the last hour, and the newest kill time."""
    kills = [killmail(5), killmail(10, 670), killmail(90, 670), {"killmail_time": "bad"}, {}]
    stats = zkill.summarize(HOME.id, kills, now=NOW)
    assert (stats.kills_1h, stats.kills_24h, stats.pod_kills_1h) == (2, 3, 1)
    assert stats.last_kill_at == (NOW - timedelta(minutes=5)).isoformat()
    assert zkill.summarize(HOME.id, [], now=NOW).last_kill_at is None
    with pytest.raises(TypeError):
        zkill.summarize(HOME.id, {"kills": []})


def test_is_fresh() -> None:
    """Stats younger than the cache lifetime are fresh; None never is."""
    stats = ZkillStats(HOME.id, 0, 0, 0, None, NOW.isoformat())
    assert zkill.is_fresh(stats, now=NOW + timedelta(minutes=5))
    assert not zkill.is_fresh(stats, now=NOW + timedelta(minutes=15))
    assert not zkill.is_fresh(None)
    with pytest.raises(TypeError):
        zkill.is_fresh("fresh")


@pytest.fixture
def fetcher(tmp_path) -> zkill.ZkillFetcher:
    """A fetcher on a temporary map file that records its callbacks (the thread isn't started)."""
    updates, statuses = [], []
    fetcher = zkill.ZkillFetcher(str(tmp_path / "map.db"), updates.append, lambda *status: statuses.append(status))
    fetcher.updates, fetcher.statuses = updates, statuses
    return fetcher


def test_request_skips_queued_systems_and_disabled_fetcher(fetcher) -> None:
    """A system is queued once; nothing is queued while disabled."""
    assert fetcher.request([HOME.id, HOME.id]) == 1
    assert fetcher.request([HOME.id]) == 0
    fetcher.enabled = False
    assert fetcher.request([1234]) == 0
    with pytest.raises(TypeError):
        fetcher.request(["J161940"])


def test_forced_request_upgrades_a_queued_system(fetcher, monkeypatch) -> None:
    """A forced request for a system already queued without force makes the worker fetch it even when fresh."""
    monkeypatch.setattr(zkill, "REQUEST_INTERVAL", 0)
    fetched = []
    monkeypatch.setattr(zkill.ZkillFetcher, "_fetch",
                        lambda self, client, store, system_id, force=False: fetched.append(system_id) or True)
    Store(fetcher.database_path).set_zkill_stats(ZkillStats(HOME.id, 0, 0, 0, None, utc_now_iso()))
    assert fetcher.request([HOME.id]) == 1
    assert fetcher.request([HOME.id], force=True) == 1      # upgraded, not queued twice
    assert fetcher.request([HOME.id], force=True) == 0      # already forced
    assert fetcher.queue.qsize() == 1
    fetcher.queue.put(None)
    fetcher._run()
    assert fetched == [HOME.id] and not fetcher.pending and not fetcher.forced


def test_fetcher_needs_callables(tmp_path) -> None:
    """A fetcher whose update callback is None raises TypeError."""
    with pytest.raises(TypeError):
        zkill.ZkillFetcher(str(tmp_path / "map.db"), None)


def test_fetch_stores_stats_and_reports(fetcher, tmp_path) -> None:
    """A good response is stored; errors and rate limits are reported, not raised."""
    responses = iter([httpx.Response(200, json=[killmail(1)]), httpx.Response(500), httpx.Response(200, text="x")])
    client = httpx.Client(transport=httpx.MockTransport(lambda request: next(responses)))
    store = Store(tmp_path / "map.db")
    assert fetcher._fetch(client, store, HOME.id) is True
    assert store.zkill_stats(HOME.id).kills_24h == 1 and fetcher.updates == [HOME.id]
    assert fetcher._fetch(client, store, HOME.id) is False
    assert fetcher._fetch(client, store, HOME.id) is False
    assert fetcher.statuses[0] == ("ok", "zKill ok") and fetcher.statuses[1][0] == "warn"


def test_rate_limit_queues_the_system_again(fetcher, tmp_path, monkeypatch) -> None:
    """HTTP 429 waits and queues the system for another try."""
    monkeypatch.setattr(zkill.time, "sleep", lambda seconds: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429)))
    assert fetcher._fetch(client, Store(tmp_path / "map.db"), HOME.id) is False
    assert HOME.id in fetcher.pending


def test_rate_limit_keeps_a_forced_fetch_forced(fetcher, tmp_path, monkeypatch) -> None:
    """A forced fetch that hits HTTP 429 is queued again as forced, so the retry ignores a fresh cache."""
    monkeypatch.setattr(zkill.time, "sleep", lambda seconds: None)
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429)))
    assert fetcher._fetch(client, Store(tmp_path / "map.db"), HOME.id, force=True) is False
    assert HOME.id in fetcher.forced
    assert fetcher.queue.get_nowait() == (HOME.id, True)


def test_worker_thread_fetches_skips_fresh_and_follows_map_switches(fetcher, tmp_path, monkeypatch) -> None:
    """The worker skips fresh systems unless forced, reopens the store on a map switch and stops."""
    monkeypatch.setattr(zkill, "REQUEST_INTERVAL", 0)
    other = str(tmp_path / "other.db")
    fetched = []

    def fake_fetch(self, client, store, system_id, force: bool = False) -> bool:
        """Record the fetch and switch maps after the first one."""
        fetched.append((store.path, system_id))
        self.database_path = other
        return True

    monkeypatch.setattr(zkill.ZkillFetcher, "_fetch", fake_fetch)
    Store(tmp_path / "map.db").set_zkill_stats(ZkillStats(30000142, 0, 0, 0, None, utc_now_iso()))
    fetcher.request([30000142])                       # fresh: skipped
    fetcher.queue.put((30000142, True))               # forced: fetched
    fetcher.queue.put((HOME.id, False))               # after the switch: fetched from the other map
    fetcher.queue.put(None)                           # what stop() sends
    fetcher._run()
    assert fetched == [(fetcher_path(tmp_path), 30000142), (other, HOME.id)]
    fetcher.enabled = False
    fetcher.queue.put((HOME.id, True))
    fetcher.queue.put(None)
    fetcher._run()                                    # disabled: nothing fetched
    assert len(fetched) == 2


def fetcher_path(tmp_path) -> str:
    """The map file the fetcher fixture starts on."""
    return str(tmp_path / "map.db")


def test_age_and_had_recent_kill() -> None:
    """Age is time since fetch; a recent kill is one within the window."""
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    stats = ZkillStats(1, 0, 0, 0, (now - timedelta(minutes=3)).isoformat(), (now - timedelta(minutes=5)).isoformat())
    assert zkill.age(None, now) is None and zkill.age(stats, now) == timedelta(minutes=5)
    assert zkill.had_recent_kill(stats, now)
    assert not zkill.had_recent_kill(stats, now + timedelta(minutes=8))
    assert not zkill.had_recent_kill(None, now)


@pytest.mark.parametrize("victim", [[1], "x", {"ship_type_id": [1]}])
def test_summarize_ignores_a_victim_that_is_not_a_dict(victim) -> None:
    """A malformed victim still counts the kill, only the pod check is skipped."""
    kill = {"killmail_time": killmail(5)["killmail_time"], "victim": victim}
    stats = zkill.summarize(HOME.id, [kill], now=NOW)
    assert (stats.kills_1h, stats.kills_24h, stats.pod_kills_1h) == (1, 1, 0)


def test_fetch_reports_a_database_error(fetcher) -> None:
    """A database error while storing is reported as a warning, not raised."""
    class BrokenStore:
        """A store whose write fails."""

        def set_zkill_stats(self, stats) -> None:
            """Fail like a locked database."""
            raise sqlite3.OperationalError("database is locked")

    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[killmail(1)])))
    assert fetcher._fetch(client, BrokenStore(), HOME.id) is False
    assert fetcher.statuses[-1] == ("warn", "zKill: OperationalError")


def test_worker_survives_an_unexpected_error(fetcher, monkeypatch) -> None:
    """An unexpected error on one system is reported and the next system is still fetched."""
    monkeypatch.setattr(zkill, "REQUEST_INTERVAL", 0)
    fetched = []

    def fake_fetch(self, client, store, system_id, force: bool = False) -> bool:
        """Crash on the first system."""
        if not fetched:
            fetched.append(system_id)
            raise RuntimeError("boom")
        fetched.append(system_id)
        return True

    monkeypatch.setattr(zkill.ZkillFetcher, "_fetch", fake_fetch)
    fetcher.queue.put((HOME.id, True))
    fetcher.queue.put((30000142, True))
    fetcher.queue.put(None)
    fetcher._run()
    assert fetched == [HOME.id, 30000142]
    assert ("err", "zKill: RuntimeError") in fetcher.statuses


def test_worker_closes_client_and_store_when_it_crashes(fetcher, monkeypatch) -> None:
    """A crash outside the per-item guard still closes the client and the store."""
    closed = []

    class Closable:
        """Records close()."""

        def __init__(self, name) -> None:
            self.name = name

        def close(self) -> None:
            """Record the close."""
            closed.append(self.name)

    monkeypatch.setattr(zkill, "make_client", lambda: Closable("client"))
    monkeypatch.setattr(zkill, "Store", lambda path: Closable("store"))
    fetcher.queue.put("not a tuple")
    with pytest.raises(ValueError):
        fetcher._run()
    assert sorted(closed) == ["client", "store"]


def test_worker_waits_between_failing_items(fetcher, monkeypatch) -> None:
    """Failing items are still spaced REQUEST_INTERVAL apart, so a broken database isn't hammered."""
    sleeps = []
    monkeypatch.setattr(zkill.time, "sleep", sleeps.append)

    def fail(self, client, store, system_id, force: bool = False) -> bool:
        """Always crash."""
        raise RuntimeError("boom")

    monkeypatch.setattr(zkill.ZkillFetcher, "_fetch", fail)
    for system_id in (1, 2, 3):
        fetcher.queue.put((system_id, True))
    fetcher.queue.put(None)
    fetcher._run()
    assert sleeps == [zkill.REQUEST_INTERVAL] * 3


def test_request_restarts_a_dead_worker(fetcher, monkeypatch) -> None:
    """A worker thread that died is replaced on the next request and the failure is reported."""
    monkeypatch.setattr(zkill.ZkillFetcher, "_run", lambda self: None)   # for the replacement thread
    fetcher.thread = threading.Thread(target=lambda: None)               # ends at once, unasked
    fetcher.start()
    fetcher.thread.join()                      # the thread ended without being stopped
    dead = fetcher.thread
    assert fetcher.request([HOME.id]) == 1
    assert fetcher.thread is not dead
    fetcher.thread.join()
    assert ("err", "zKill worker restarted") in fetcher.statuses


def test_request_does_not_restart_a_stopped_or_unstarted_worker(fetcher) -> None:
    """Only a started worker that died unasked is restarted."""
    thread = fetcher.thread
    fetcher.request([HOME.id])
    assert fetcher.thread is thread            # never started
    fetcher.stop()
    fetcher.request([30000142])
    assert fetcher.thread is thread            # stopping on purpose


def test_summarize_counts_npc_kills() -> None:
    """On the sample, the rat kill counts like any other; pods follow the victims."""
    stats = zkill.summarize(HOME.id, SAMPLE, now=NOW)
    assert (stats.kills_1h, stats.kills_24h, stats.pod_kills_1h) == (3, 4, 1)
    npc_only = zkill.summarize(HOME.id, [SAMPLE[1]], now=NOW)
    assert npc_only.kills_24h == 1 and npc_only.last_kill_at is not None


def test_summarize_kills_lists_every_kill_newest_first() -> None:
    """The recorded sample gives one row per killmail with ships, attacker count, value and flags."""
    rows = zkill.summarize_kills(HOME.id, SAMPLE)
    assert [row.killmail_id for row in rows] == [1003, 1002, 1001, 1004]
    assert rows[2] == KillSummary(HOME.id, 1001, "2026-10-30T11:48:00+00:00", 29984, 4, 11978, 1234567890.5, 0, 0)
    assert (rows[1].top_attacker_ship_id, rows[1].attacker_count, rows[1].npc, rows[1].solo) == (None, 1, 1, 0)
    assert (rows[0].victim_ship_id, rows[0].solo, rows[0].value) == (670, 1, 10000.0)


def test_summarize_kills_skips_malformed_entries() -> None:
    """Bad times, ids, victims, attackers and zkb blocks never raise; what can't be read is left out or defaulted."""
    good = dict(SAMPLE[0])
    kills = [
        "text", {}, {"killmail_time": "bad", "killmail_id": 5},
        {"killmail_time": good["killmail_time"]},                          # no killmail_id
        {**good, "killmail_id": True},                                      # a bool is not an id
        {**good, "killmail_id": 7, "victim": [1], "attackers": "x", "zkb": [1]},
        {**good, "killmail_id": 8, "attackers": [1, {"ship_type_id": [3]}, {"ship_type_id": 5}],
         "zkb": {"totalValue": "lots", "npc": "yes"}},
        {**good, "killmail_id": 8},                                         # duplicate id
        {**good, "killmail_id": 9, "victim": {"ship_type_id": [1]}},
    ]
    rows = {row.killmail_id: row for row in zkill.summarize_kills(HOME.id, kills)}
    assert sorted(rows) == [7, 8, 9]
    assert (rows[7].victim_ship_id, rows[7].attacker_count, rows[7].top_attacker_ship_id, rows[7].value,
            rows[7].npc) == (None, 0, None, 0.0, 0)
    assert (rows[8].attacker_count, rows[8].top_attacker_ship_id, rows[8].value, rows[8].npc) == (2, 5, 0.0, 0)
    assert rows[9].victim_ship_id is None
    with pytest.raises(TypeError):
        zkill.summarize_kills(HOME.id, {"kills": []})


def test_summarize_kills_keeps_the_newest_few() -> None:
    """A busy system keeps at most MAX_KILLS_KEPT rows, the newest ones."""
    kills = [{**killmail(minute), "killmail_id": minute + 1} for minute in range(zkill.MAX_KILLS_KEPT + 5)]
    rows = zkill.summarize_kills(HOME.id, kills)
    assert len(rows) == zkill.MAX_KILLS_KEPT and rows[0].killmail_id == 1


def test_fetch_stores_the_kill_rows_too(fetcher, tmp_path) -> None:
    """A good response stores the stats (NPC kills included) and the kill rows."""
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=SAMPLE)))
    store = Store(tmp_path / "map.db")
    assert fetcher._fetch(client, store, HOME.id) is True
    assert [row.killmail_id for row in store.zkill_kills(HOME.id)] == [1003, 1002, 1001, 1004]
    assert store.zkill_stats(HOME.id).kills_24h == 4

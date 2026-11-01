"""zKill summaries, the cache check and the fetcher, with a fake HTTP transport."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import HOME
from homebound import zkill
from homebound.mapdb.store import Store, ZkillStats, utc_now_iso

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)


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
                        lambda self, client, store, system_id: fetched.append(system_id) or True)
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


def test_worker_thread_fetches_skips_fresh_and_follows_map_switches(fetcher, tmp_path, monkeypatch) -> None:
    """The worker skips fresh systems unless forced, reopens the store on a map switch and stops."""
    monkeypatch.setattr(zkill, "REQUEST_INTERVAL", 0)
    other = str(tmp_path / "other.db")
    fetched = []

    def fake_fetch(self, client, store, system_id) -> bool:
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

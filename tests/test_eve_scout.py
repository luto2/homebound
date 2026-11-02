"""EVE-Scout parsing and the fetcher, with a fake HTTP transport."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from conftest import HOME, UNIVERSE
from homebound import eve_scout

NOW = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
THERA, TURNUR = UNIVERSE.by_name("Thera").id, UNIVERSE.by_name("Turnur").id


def entry(hub: str = "Thera", hub_id: int = THERA, system_id: int = HOME.id, hours: float = 8, **extra) -> dict:
    """A minimal EVE-Scout signature entry expiring some hours after NOW."""
    expires = (NOW + timedelta(hours=hours)).isoformat().replace("+00:00", ".000Z")
    return {"out_system_name": hub, "out_system_id": hub_id, "in_system_id": system_id, "wh_type": "Q063",
            "max_ship_size": "large", "expires_at": expires, "remaining_hours": hours,
            "signature_type": "wormhole", **extra}


def test_parse_keeps_usable_wormholes_only() -> None:
    """Thera and Turnur entries become links; expired, foreign, malformed and non-wormhole ones are skipped."""
    data = [entry(), entry("Turnur", TURNUR, max_ship_size="capital", wh_type=""), entry(hours=-1),
            entry("Jita"), entry(system_id="J161940"), entry(hub_id=True), entry(signature_type="combat"),
            entry(expires_at="soon", remaining_hours="8"), entry(max_ship_size="huge"), "junk"]
    links = eve_scout.parse_signatures(data, now=NOW)
    assert [(link.hub, link.size) for link in links] == [("thera", "L"), ("turnur", "XL"), ("thera", "L"),
                                                         ("thera", None)]
    assert links[0].hub_id == THERA and links[0].system_id == HOME.id and links[0].remaining_hours == 8
    assert links[1].wormhole_type is None and links[2].expires_at is None and links[2].remaining_hours is None
    assert eve_scout.parse_signatures({"not": "a list"}) == []
    assert eve_scout._expiry("2026-11-02T12:00:00").tzinfo is timezone.utc
    with pytest.raises(TypeError):
        eve_scout.parse_signatures([], now="now")


@pytest.fixture
def fetcher() -> eve_scout.EveScoutFetcher:
    """A fetcher that records its callbacks (the thread isn't started)."""
    fetcher = eve_scout.EveScoutFetcher(lambda: fetcher.updates.append(1),
                                        lambda level, text: fetcher.statuses.append((level, text)))
    fetcher.updates, fetcher.statuses = [], []
    return fetcher


def test_fetcher_needs_callables() -> None:
    """Callbacks must be callable."""
    with pytest.raises(TypeError):
        eve_scout.EveScoutFetcher(None)
    with pytest.raises(TypeError):
        eve_scout.EveScoutFetcher(lambda: None, "status")


def test_fetch_stores_links_and_reports(fetcher) -> None:
    """A good response replaces the links; an error keeps them and warns."""
    now = datetime.now(timezone.utc)
    later = (now + timedelta(hours=5)).isoformat()
    payload = [entry(expires_at=later), entry("Turnur", TURNUR, expires_at=later)]
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)))
    assert fetcher.fetch(client) and len(fetcher.links()) == 2
    assert fetcher.statuses[-1] == ("ok", "EVE-Scout 1 Thera · 1 Turnur") and fetcher.updates == [1]
    broken = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    assert not fetcher.fetch(broken) and len(fetcher.links()) == 2
    assert fetcher.statuses[-1] == ("warn", "EVE-Scout: HTTPStatusError")
    fetcher._links = [eve_scout.ScoutLink(THERA, HOME.id, "thera", expires_at=(now - timedelta(minutes=1)).isoformat())]
    assert fetcher.links() == []                 # expired since the fetch


def test_turning_off_forgets_links_and_turning_on_wakes(fetcher) -> None:
    """Off drops the links (and tells the views); on wakes the thread; a fetch while off is dropped."""
    fetcher._links = [eve_scout.ScoutLink(THERA, HOME.id, "thera")]
    fetcher.enabled = False
    assert fetcher.links() == [] and fetcher.updates == [1] and not fetcher.enabled
    fetcher.enabled = False                      # no change, no update
    assert fetcher.updates == [1]
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[entry()])))
    assert not fetcher.fetch(client)
    fetcher.enabled = True
    assert fetcher._wake.is_set()
    with pytest.raises(TypeError):
        fetcher.enabled = "yes"


def test_worker_loop_fetches_until_stopped(fetcher, monkeypatch) -> None:
    """The loop fetches while on, waits, and ends on stop()."""
    calls = []

    def fake_fetch(client: httpx.Client) -> bool:
        """Record the fetch and stop the loop."""
        calls.append(client)
        fetcher.stop()
        return True

    monkeypatch.setattr(fetcher, "fetch", fake_fetch)
    fetcher.refresh()
    fetcher._run()
    assert len(calls) == 1
    fetcher._stopping = False
    fetcher._enabled = False
    fetcher._wake.set()
    monkeypatch.setattr(fetcher._wake, "clear", lambda: setattr(fetcher, "_stopping", True))
    fetcher._run()                               # off: waits without fetching
    assert len(calls) == 1


def test_status_without_callback() -> None:
    """A fetcher without a status callback still works; a thread stopped before it starts never fetches."""
    fetcher = eve_scout.EveScoutFetcher(lambda: None)
    fetcher._status("ok", "fine")
    fetcher.stop()
    fetcher.start()
    fetcher.thread.join(timeout=5)
    assert not fetcher.thread.is_alive()

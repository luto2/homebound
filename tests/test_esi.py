"""ESI lookups that tell a Tranquility character from its test server copy, with a fake HTTP transport."""
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from homebound import esi

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
REAL, COPY = 2112000001, 2112000002


def fake_client(answers: dict[int, httpx.Response | Exception]) -> httpx.Client:
    """A client whose ESI answers each character ID with the given response (or raises the given error)."""
    def handle(request: httpx.Request) -> httpx.Response:
        answer = answers[int(request.url.path.rstrip("/").rsplit("/", 1)[1])]
        if isinstance(answer, Exception):
            raise answer
        return answer
    return httpx.Client(transport=httpx.MockTransport(handle))


def character(name: str) -> httpx.Response:
    """ESI's answer for a live Tranquility character."""
    return httpx.Response(200, json={"name": name, "corporation_id": 1000001})


DELETED = httpx.Response(404, json={"error": "Character has been deleted!"})


def test_tranquility_name_reads_the_answer() -> None:
    """A live character gives its name; a 404 (unknown or deleted) gives None; anything else is unanswered."""
    client = fake_client({REAL: character("Yaentei Tacia Ocaras"), COPY: DELETED,
                          3: httpx.Response(502), 4: httpx.Response(200, text="not json"),
                          5: httpx.Response(200, json=["a list"]), 6: httpx.Response(200, json={"name": " "}),
                          7: httpx.ConnectError("offline")})
    assert esi.tranquility_name(client, REAL) == "Yaentei Tacia Ocaras"
    assert esi.tranquility_name(client, COPY) is None
    for character_id, reason in ((3, "HTTP 502"), (4, "not a character"), (5, "not a character"),
                                 (6, "not a character"), (7, "ConnectError")):
        with pytest.raises(esi.Unanswered, match=reason):
            esi.tranquility_name(client, character_id)
    with pytest.raises(ValueError):
        esi.tranquility_name(client, 0)


def test_split_by_server_needs_exactly_one_tranquility_character() -> None:
    """The one ID ESI knows under the shared name stays; a 404 or another name is a copy; ties decide nothing."""
    name = "Yaentei Tacia Ocaras"
    assert esi.split_by_server(name, {COPY: None, REAL: "yaentei  tacia ocaras"}) == (REAL, [COPY])
    assert esi.split_by_server(name, {REAL: name, COPY: "Someone Else", 9: None}) == (REAL, [9, COPY])
    assert esi.split_by_server(name, {REAL: None, COPY: None}) is None
    assert esi.split_by_server(name, {REAL: name, COPY: name}) is None
    with pytest.raises(TypeError):
        esi.split_by_server(name, [REAL])


def test_same_name_ignores_case_and_spacing() -> None:
    """EVE names are unique regardless of case, so the comparison is too."""
    assert esi.same_name("Vyntha Mer Ocaras", " vyntha  mer OCARAS ")
    assert not esi.same_name("Vyntha Mer Ocaras", "Vyntha Mer")
    with pytest.raises(TypeError):
        esi.same_name(None, "x")


@pytest.fixture
def checker(monkeypatch) -> esi.DuplicateChecker:
    """A checker that records its callbacks and doesn't wait between requests (the thread isn't started)."""
    monkeypatch.setattr(esi, "REQUEST_INTERVAL", 0)
    updates, statuses = [], []
    checker = esi.DuplicateChecker(lambda *update: updates.append(update), lambda *status: statuses.append(status))
    checker.updates, checker.statuses = updates, statuses
    return checker


def test_request_queues_a_group_once_until_the_retry_time(checker) -> None:
    """The same group isn't queued again within RETRY_AFTER, whatever the order of its IDs; a new group is."""
    assert checker.request("Yaentei", [REAL, COPY], now=NOW)
    assert not checker.request("Yaentei", [COPY, REAL], now=NOW + timedelta(minutes=1))
    assert checker.request("Yaentei", [REAL, COPY, 9], now=NOW + timedelta(minutes=1))
    assert checker.request("Yaentei", [REAL, COPY], now=NOW + esi.RETRY_AFTER)
    assert checker.queue.qsize() == 3
    with pytest.raises(ValueError):
        checker.request("Yaentei", [REAL, REAL])
    with pytest.raises(TypeError):
        checker.request("Yaentei", ["2112000001", COPY])


def test_check_reports_the_copy_or_why_it_could_not(checker) -> None:
    """A decided group goes to on_update; a failed lookup or an undecided group only gives a status."""
    name = "Yaentei Tacia Ocaras"
    assert checker.check(fake_client({REAL: character(name), COPY: DELETED}), name, {REAL, COPY}) == (REAL, [COPY])
    assert checker.updates == [(name, REAL, [COPY])]
    assert checker.check(fake_client({REAL: httpx.ReadTimeout("slow"), COPY: DELETED}), name, {REAL, COPY}) is None
    assert checker.check(fake_client({REAL: DELETED, COPY: DELETED}), name, {REAL, COPY}) is None
    assert len(checker.updates) == 1
    assert [level for level, _ in checker.statuses] == ["warn", "warn"]
    assert "checked again later" in checker.statuses[0][1] and "Tranquility" in checker.statuses[1][1]


def test_worker_runs_queued_groups_and_survives_an_error(checker, monkeypatch) -> None:
    """The thread checks each queued group, reports an unexpected error and stops when asked."""
    seen = []

    def check(client, name, group):
        seen.append(name)
        if name == "boom":
            raise RuntimeError("bad")
    monkeypatch.setattr(checker, "check", check)
    checker.request("boom", [1, 2], now=NOW)
    checker.request("fine", [3, 4], now=NOW)
    checker.queue.put(None)                 # the end item stop() sends, without the stop flag set first
    checker._run()
    assert seen == ["boom", "fine"]
    assert checker.statuses == [("err", "ESI: RuntimeError")]
    checker.stop()
    assert checker._stopping and checker.queue.get_nowait() is None

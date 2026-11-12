"""The job loop around EVE's daily downtime: 504s held from 10:55, a pull at 11:15, one retry at 11:30."""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from homebound_server import jobs as jobs_module
from homebound_server.jobs import Jobs, is_downtime_error


def status_error(code: int) -> httpx.HTTPStatusError:
    """An ESI answer with this status code."""
    request = httpx.Request("GET", "https://esi.evetech.net/latest/characters/1/wallet/journal/")
    return httpx.HTTPStatusError(f"{code}", request=request, response=httpx.Response(code, request=request))


class FakeService:
    """Every job is a no-op that counts its runs; ``errors`` makes ESI jobs raise."""

    def __init__(self) -> None:
        """No failures and no reports yet."""
        self.runs: dict[str, int] = {}
        self.errors: dict[str, Exception] = {}
        self.problems: list[str] = []
        self.events = SimpleNamespace(server_problem=self.problems.append, flush=lambda: None)
        self.hub = SimpleNamespace(expire_presence=lambda: None, trim_tail=lambda: None)

    def __getattr__(self, name: str):
        """Any other job: count it, raise its configured error."""
        def job():
            self.runs[name] = self.runs.get(name, 0) + 1
            if name in self.errors:
                raise self.errors[name]
        return job


def at(hour: int, minute: int) -> datetime:
    """A UTC time on one fixed day."""
    return datetime(2026, 11, 12, hour, minute, tzinfo=timezone.utc)


def tick(jobs: Jobs, moment: datetime) -> None:
    """Run one tick, with every interval elapsed so each job is due again."""
    jobs.last.clear()
    asyncio.run(jobs.tick(moment))


def test_only_gateway_errors_count_as_downtime() -> None:
    assert all(is_downtime_error(status_error(code)) for code in (502, 503, 504))
    assert not is_downtime_error(status_error(500))
    assert not is_downtime_error(status_error(401))
    assert not is_downtime_error(RuntimeError("boom"))


def test_a_504_outside_downtime_is_reported() -> None:
    service = FakeService()
    service.errors["poll_wallet"] = status_error(504)
    tick(Jobs(service), at(9, 0))
    assert service.problems == ["poll_wallet failed: 504"]


def test_downtime_504s_are_held_and_a_good_pull_at_1115_reports_nothing() -> None:
    service = FakeService()
    jobs = Jobs(service)
    service.errors = {"poll_wallet": status_error(504), "poll_mail": status_error(503)}
    tick(jobs, at(10, 58))
    tick(jobs, at(11, 5))
    assert service.problems == []
    assert service.runs.get("send_mails", 0) == 0          # mails wait instead of using up attempts
    service.errors = {}
    tick(jobs, at(11, 15))
    assert service.problems == []
    assert service.runs["send_mails"] == 1                  # the pull covers every ESI job


def test_other_errors_during_downtime_are_still_reported() -> None:
    service = FakeService()
    service.errors["poll_wallet"] = RuntimeError("HB_TOKEN_ENC_KEY doesn't match")
    tick(Jobs(service), at(11, 5))
    assert service.problems == ["poll_wallet failed: HB_TOKEN_ENC_KEY doesn't match"]


def test_a_non_downtime_error_in_the_1115_pull_is_reported() -> None:
    service = FakeService()
    jobs = Jobs(service)
    service.errors["poll_affiliations"] = status_error(500)
    asyncio.run(jobs.tick(at(11, 15)))
    assert service.problems == ["poll_affiliations failed in the 11:15 UTC pull after downtime: 500"]


def test_still_down_at_1115_waits_until_1130_and_recovers_quietly() -> None:
    service = FakeService()
    jobs = Jobs(service)
    service.errors["poll_wallet"] = status_error(504)
    asyncio.run(jobs.tick(at(11, 15)))
    tick(jobs, at(11, 20))                                  # hourly runs in between stay quiet too
    assert service.problems == []
    service.errors = {}
    asyncio.run(jobs.tick(at(11, 30)))
    assert service.problems == []
    assert service.runs["poll_wallet"] == 3


def test_still_down_at_1130_is_reported_once() -> None:
    service = FakeService()
    jobs = Jobs(service)
    service.errors["poll_wallet"] = status_error(504)
    asyncio.run(jobs.tick(at(11, 15)))
    asyncio.run(jobs.tick(at(11, 30)))
    assert service.problems == ["poll_wallet failed at 11:30 UTC, still after downtime: 504"]
    jobs.last_success -= jobs_module.POLLER_SILENT_SECONDS + 1
    asyncio.run(jobs.tick(at(12, 0)))                       # no second "silent for 2 h" on top
    assert service.problems == ["poll_wallet failed at 11:30 UTC, still after downtime: 504"]


def test_the_pull_runs_once_a_day_and_not_after_a_late_start() -> None:
    service = FakeService()
    jobs = Jobs(service)
    asyncio.run(jobs.tick(at(11, 16)))
    asyncio.run(jobs.tick(at(11, 17)))
    assert service.runs["poll_wallet"] == 1                 # the pull reset the hourly timer
    late = Jobs(FakeService())
    assert late.downtime.due(at(14, 0)) is None


def test_the_silent_poller_warning_waits_out_downtime() -> None:
    service = FakeService()
    jobs = Jobs(service)
    jobs.last_success -= jobs_module.POLLER_SILENT_SECONDS + 1
    jobs.last = {name: float("inf") for name, _, _ in jobs_module.SCHEDULE}   # nothing due
    asyncio.run(jobs.tick(at(11, 5)))
    assert service.problems == []
    asyncio.run(jobs.tick(at(13, 0)))
    assert service.problems == ["the wallet poller has been silent for more than 2 h"]

"""The server's timers: prune, billing, wallet and mail polls, corp checks, tail trim, presence, webhook.

EVE's daily downtime starts at 11:00 UTC and ESI answers 502/503/504 while it lasts. Those errors are
only logged from 10:55 to 11:15 UTC; at 11:15 every ESI job runs once. If ESI is still down then, the
jobs run again at 11:30, and only a failure at 11:30 goes to the webhook.
"""
import asyncio
import logging
import time
from datetime import date, datetime, time as clock, timezone

import httpx

from .service import Service

log = logging.getLogger("homebound.server")

# (job, seconds between runs, runs in a thread because it calls ESI or Discord)
SCHEDULE = [
    ("expire_presence", 60, False),
    ("prune", 600, False),
    ("sweep_billing", 3600, False),
    ("trim_tail", 3600, False),
    ("poll_wallet", 3600, True),
    ("poll_mail", 3600, True),
    ("poll_affiliations", 3600, True),
    ("send_mails", 120, True),
    ("check_disk", 600, False),
    ("purge_old_records", 86400, False),
    ("flush_events", 60, True),
]
THREADED = {name: threaded for name, _, threaded in SCHEDULE}
POLLER_SILENT_SECONDS = 2 * 3600
ESI_JOBS = ("poll_wallet", "poll_mail", "poll_affiliations", "send_mails")
DOWNTIME_STATUSES = frozenset({502, 503, 504})    # what ESI and SSO answer while EVE is down
DOWNTIME_FROM = clock(10, 55)                     # UTC; downtime itself starts at 11:00
DOWNTIME_PULL = clock(11, 15)                     # UTC; the full ESI pull after downtime
DOWNTIME_RETRY = clock(11, 30)                    # UTC; one more pull if ESI was still down at 11:15


def run_job(service: Service, name: str):
    """Run one job by name (also used by the admin CLI)."""
    return {
        "expire_presence": service.hub.expire_presence,
        "prune": service.prune,
        "sweep_billing": service.sweep_billing,
        "trim_tail": service.hub.trim_tail,
        "poll_wallet": service.poll_wallet,
        "poll_mail": service.poll_mail,
        "poll_affiliations": service.poll_affiliations,
        "send_mails": service.send_mails,
        "check_disk": service.check_disk,
        "purge_old_records": service.purge_old_records,
        "flush_events": service.events.flush,
    }[name]()


def is_downtime_error(error: BaseException) -> bool:
    """Return whether an error looks like EVE's downtime (a 502, 503 or 504 from ESI or SSO)."""
    return isinstance(error, httpx.HTTPStatusError) and error.response.status_code in DOWNTIME_STATUSES


class Downtime:
    """When ESI downtime errors are expected, and when the pulls after downtime are due."""

    def __init__(self) -> None:
        """Start with no pull done and no retry waiting."""
        self.pulled: date | None = None      # the day whose 11:15 pull ran
        self.retry = False                   # ESI was still down at 11:15; pull again at 11:30

    def holding(self, now: datetime) -> bool:
        """Return whether downtime errors are only logged now (10:55–11:15 UTC, or until the 11:30 retry)."""
        return self.retry or DOWNTIME_FROM <= now.time() < DOWNTIME_PULL

    def due(self, now: datetime) -> str | None:
        """Return ``"pull"`` once a day from 11:15 UTC, ``"retry"`` from 11:30 if one is waiting, else None."""
        if self.retry and now.time() >= DOWNTIME_RETRY:
            self.retry = False
            return "retry"
        if self.pulled != now.date() and DOWNTIME_PULL <= now.time() < DOWNTIME_RETRY:
            self.pulled = now.date()
            return "pull"
        return None


class Jobs:
    """Every job on its interval, the pulls after EVE's downtime, and the silent-poller warning."""

    def __init__(self, service: Service) -> None:
        """Run nothing yet; the first tick runs every job."""
        self.service = service
        self.last: dict[str, float] = {}
        self.last_success = time.monotonic()
        self.warned = False
        self.downtime = Downtime()

    async def run(self, name: str) -> Exception | None:
        """Run one job; return its error instead of raising."""
        try:
            if THREADED[name]:
                await asyncio.to_thread(run_job, self.service, name)
            else:
                run_job(self.service, name)
        except Exception as error:   # noqa: BLE001 - keep the server running
            log.exception("job %s failed", name)
            return error
        if name == "poll_wallet":
            self.last_success, self.warned = time.monotonic(), False
        return None

    def report(self, name: str, error: Exception, now: datetime) -> None:
        """Send a failed job to the webhook, unless it's a downtime error while those are expected."""
        if name == "flush_events":
            return
        if is_downtime_error(error) and self.downtime.holding(now):
            log.info("%s hit EVE downtime (%s); the ESI pull after downtime checks again", name, error)
            return
        self.service.events.server_problem(f"{name} failed: {error}")

    async def pull(self, stage: str) -> None:
        """Run every ESI job once after downtime; report what fails (a downtime error at 11:15 waits until 11:30)."""
        errors = {}
        for name in ESI_JOBS:
            self.last[name] = time.monotonic()
            if (error := await self.run(name)) is not None:
                errors[name] = error
        if stage == "pull" and any(is_downtime_error(error) for error in errors.values()):
            self.downtime.retry = True
            log.info("ESI still down at 11:15 UTC; pulling again at 11:30")
            errors = {name: error for name, error in errors.items() if not is_downtime_error(error)}
        when = "at 11:30 UTC, still after downtime" if stage == "retry" else "in the 11:15 UTC pull after downtime"
        for name, error in errors.items():
            self.service.events.server_problem(f"{name} failed {when}: {error}")
            if name == "poll_wallet":
                self.warned = True           # already reported; the silent-poller warning would repeat it

    async def tick(self, now: datetime) -> None:
        """Run what is due at ``now`` (UTC)."""
        if stage := self.downtime.due(now):
            await self.pull(stage)
        holding = self.downtime.holding(now)
        for name, interval, _ in SCHEDULE:
            if time.monotonic() - self.last.get(name, -interval) < interval:
                continue
            if name == "send_mails" and holding:
                continue                     # a failed send uses up one of the mail's few attempts
            self.last[name] = time.monotonic()
            if (error := await self.run(name)) is not None:
                self.report(name, error, now)
        if (not self.downtime.holding(now) and not self.warned
                and time.monotonic() - self.last_success > POLLER_SILENT_SECONDS):
            self.service.events.server_problem("the wallet poller has been silent for more than 2 h")
            self.warned = True


async def run_jobs(service: Service) -> None:
    """Run every job on its interval forever; a failing job is logged and reported, then retried."""
    jobs = Jobs(service)
    while True:
        await jobs.tick(datetime.now(timezone.utc))
        await asyncio.sleep(5)

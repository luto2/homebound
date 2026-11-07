"""The server's timers: prune, billing, wallet and mail polls, corp checks, tail trim, presence, webhook."""
import asyncio
import logging
import time

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
    ("flush_events", 60, True),
]
POLLER_SILENT_SECONDS = 2 * 3600


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
        "flush_events": service.events.flush,
    }[name]()


async def run_jobs(service: Service) -> None:
    """Run every job on its interval forever; a failing job is logged and reported, then retried."""
    last: dict[str, float] = {}
    last_success = time.monotonic()
    warned = False
    while True:
        now = time.monotonic()
        for name, interval, threaded in SCHEDULE:
            if now - last.get(name, -interval) < interval:
                continue
            last[name] = now
            try:
                if threaded:
                    await asyncio.to_thread(run_job, service, name)
                else:
                    run_job(service, name)
                if name == "poll_wallet":
                    last_success, warned = time.monotonic(), False
            except Exception as error:   # noqa: BLE001 - keep the server running
                log.exception("job %s failed", name)
                if name != "flush_events":
                    service.events.server_problem(f"{name} failed: {error}")
        if time.monotonic() - last_success > POLLER_SILENT_SECONDS and not warned:
            service.events.server_problem("the wallet poller has been silent for more than 2 h")
            warned = True
        await asyncio.sleep(5)

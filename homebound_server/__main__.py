"""``homebound-server``: run the sync server, or ``homebound-server admin …`` for the admin CLI (plan §7, §19)."""
import argparse
import asyncio
import contextlib
import json
import logging
import sys

from .config import Config
from .db import iso, utc_now
from .service import Service


def serve(options: argparse.Namespace) -> int:
    """Run the server with its background jobs."""
    import uvicorn

    from .app import create_app
    from .jobs import run_jobs

    config = Config.from_environment(dev=options.dev)
    if options.db:
        config.database = options.db
    service = Service(config)
    app = create_app(service)

    @contextlib.asynccontextmanager
    async def lifespan(_app):
        service.started()
        task = asyncio.create_task(run_jobs(service))
        yield
        task.cancel()

    app.router.lifespan_context = lifespan
    if config.dev:
        logging.getLogger("homebound.server").info("dev mode: fake SSO at %s/dev/sso", config.base_url)
    uvicorn.run(app, host=options.host, port=options.port, proxy_headers=True, forwarded_allow_ips="127.0.0.1")
    return 0


def admin(options: argparse.Namespace) -> int:
    """Run one admin command and print the result as JSON."""
    from .jobs import run_job

    config = Config.from_environment(dev=options.dev)
    if options.db:
        config.database = options.db
    service = Service(config)
    db = service.db
    command = options.command
    args = options.args

    def account_of(value: str) -> int:
        """Accept an account ID or a character name / ID."""
        if value.startswith("a"):
            return int(value[1:])
        row = db.one("SELECT account_id FROM characters WHERE name=? OR char_id=?", (value, value))
        if not row:
            raise SystemExit(f"no account for {value!r}")
        return row["account_id"]

    if command == "lookup":
        account_id = account_of(args[0])
        result = service.account(account_id)
    elif command == "credit":          # credit <account|char> <isk> [note]
        payer = service.maps.payer_for_account(account_of(args[0]))
        service.billing.credit(payer, float(args[1]), ref_type="manual", note=" ".join(args[2:]))
        result = service.billing.row(payer)
    elif command == "credit-corp":     # credit-corp <corp_id> <isk> [note]
        payer = service.maps.payer_for_corp(int(args[0]))
        service.billing.credit(payer, float(args[1]), ref_type="manual", note=" ".join(args[2:]))
        result = service.billing.row(payer)
    elif command == "comp":            # comp <account|char> on|off
        payer = service.maps.payer_for_account(account_of(args[0]))
        db.execute("UPDATE billing SET comp=? WHERE payer_id=?", (int(args[1] == "on"), payer))
        result = service.billing.row(payer)
    elif command == "map-limit":       # map-limit <account|char> <n>
        db.execute("UPDATE accounts SET map_limit=? WHERE id=?", (int(args[1]), account_of(args[0])))
        result = {"ok": True}
    elif command == "reset-strikes":   # reset-strikes char|corp <id>
        result = {"removed": db.execute("DELETE FROM payment_strikes WHERE subject_kind=? AND subject_id=?",
                                        (args[0], int(args[1]))).rowcount}
    elif command == "maps":
        result = [dict(row) for row in db.all("SELECT id, name, payer_id, created_at, seq, deleted_at FROM maps")]
    elif command == "delete-map":
        result = {"members": service.maps.delete(args[0])}
    elif command == "unmatched":
        result = [dict(row) for row in db.all("SELECT * FROM unmatched WHERE refunded_at IS NULL ORDER BY seen_at")]
    elif command == "refunded":        # refunded <journal_id>
        result = {"ok": db.execute("UPDATE unmatched SET refunded_at=? WHERE journal_id=?",
                                   (iso(utc_now()), int(args[0]))).rowcount > 0}
    elif command == "suspend":         # suspend <account|char> on|off
        db.execute("UPDATE accounts SET suspended_at=? WHERE id=?",
                   (iso(utc_now()) if args[1] == "on" else None, account_of(args[0])))
        result = {"ok": True}
    elif command == "export":
        result = service.export(account_of(args[0]))
    elif command == "delete-account":
        service.delete_account(account_of(args[0]))
        result = {"ok": True}
    elif command == "new-key":         # a key for HB_TOKEN_ENC_KEY
        from cryptography.fernet import Fernet
        result = {"HB_TOKEN_ENC_KEY": Fernet.generate_key().decode()}
    elif command == "receiver-link":   # one-time link that signs the receiving character in
        result = {"link": service.create_receiver_link(), "valid_minutes": 30}
    elif command == "receiver":
        char_id, refresh_token = service.receiver()
        result = {"char_id": char_id, "name": db.value("SELECT value FROM meta WHERE key='receiver_name'"),
                  "linked_at": db.value("SELECT value FROM meta WHERE key='receiver_linked_at'"),
                  "has_token": bool(refresh_token)}
    elif command == "mails":           # receipt / problem mails that failed too often
        result = service.mailer.given_up()
    elif command == "run":             # run <job>
        result = run_job(service, args[0])
    else:
        print(f"unknown command {command!r}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse the command line and run."""
    parser = argparse.ArgumentParser(prog="homebound-server", description="Homebound cloud sync server")
    parser.add_argument("--dev", action="store_true", help="fake SSO and ESI, for development")
    parser.add_argument("--db", help="the server database (default: $HB_DB)")
    sub = parser.add_subparsers(dest="mode")
    run = sub.add_parser("serve", help="run the server (the default)")
    run.add_argument("--host", default="127.0.0.1")
    run.add_argument("--port", type=int, default=8080)
    tool = sub.add_parser("admin", help="admin commands: lookup, credit, credit-corp, comp, map-limit, "
                                        "reset-strikes, maps, delete-map, unmatched, refunded, suspend, export, "
                                        "delete-account, new-key, receiver-link, receiver, mails, run <job>")
    tool.add_argument("command")
    tool.add_argument("args", nargs="*")
    options = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if options.mode == "admin":
        return admin(options)
    if options.mode is None:
        options.host, options.port = "127.0.0.1", 8080
    return serve(options)


if __name__ == "__main__":
    sys.exit(main())

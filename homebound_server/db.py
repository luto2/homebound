"""The server's SQLite database (plan §8). One file, WAL mode, one connection guarded by a lock."""
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from homebound.http import parse_utc
from homebound.mapdb.store import utc_now_iso

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;      -- wait for Litestream's short locks instead of failing
PRAGMA synchronous = NORMAL;     -- the usual setting with WAL (and what Litestream recommends)

CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at    TEXT NOT NULL,
    map_limit     INTEGER NOT NULL DEFAULT 6,
    trial_used_at TEXT,
    suspended_at  TEXT,
    deleted_at    TEXT
);
CREATE TABLE IF NOT EXISTS characters (
    char_id         INTEGER PRIMARY KEY,
    account_id      INTEGER NOT NULL REFERENCES accounts(id),
    name            TEXT NOT NULL,
    corp_id         INTEGER,
    corp_checked_at TEXT,
    owner_hash      TEXT,
    transfer_flag   INTEGER NOT NULL DEFAULT 0,      -- in an NPC corp: next login must re-verify (§6)
    linked_at       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash   TEXT PRIMARY KEY,
    account_id   INTEGER NOT NULL REFERENCES accounts(id),
    device       TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL,
    deferred_at  TEXT,                                -- the one "Do it later" (+36 h) was used
    relogin_due  INTEGER NOT NULL DEFAULT 0,          -- a transfer check needs a fresh SSO login
    revoked_at   TEXT
);
CREATE TABLE IF NOT EXISTS pending_logins (           -- an SSO round trip in progress
    state        TEXT PRIMARY KEY,                    -- the state we sent to EVE
    port         INTEGER,                             -- the app's loopback port, NULL: show a code
    client_state TEXT NOT NULL,
    link_account INTEGER,                             -- adding an alt to this account
    created_at   TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_codes (
    code_hash  TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    state      TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS link_codes (               -- lets a logged-in app start "add an alt"
    code_hash  TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corporations (
    corp_id    INTEGER PRIMARY KEY,
    name       TEXT,
    ceo_id     INTEGER,
    checked_at TEXT,
    alliance_id INTEGER                               -- from the affiliation poll and the corp lookup
);
CREATE TABLE IF NOT EXISTS alliances (              -- only those named in HB_FREE_MAPS
    alliance_id      INTEGER PRIMARY KEY,
    name             TEXT,
    executor_corp_id INTEGER,                         -- its corp maps are the alliance's free maps
    checked_at       TEXT
);
CREATE TABLE IF NOT EXISTS payers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind       TEXT NOT NULL CHECK (kind IN ('account', 'corp')),
    subject_id INTEGER NOT NULL,
    UNIQUE (kind, subject_id)
);
CREATE TABLE IF NOT EXISTS billing (
    payer_id     INTEGER PRIMARY KEY REFERENCES payers(id),
    code         TEXT NOT NULL UNIQUE,                -- HB-7Q2K / HBC-4M8T, for the donation reason
    balance_isk  REAL NOT NULL DEFAULT 0,             -- what is left after paying up to billed_until
    billed_until TEXT,                                -- costs are charged up to here
    paid_until   TEXT,
    trial_until  TEXT,
    state        TEXT NOT NULL DEFAULT 'active'
                 CHECK (state IN ('trial', 'active', 'warning', 'read_only', 'deleted')),
    lapsed_at    TEXT,                                -- when paid_until passed without a payment
    lapse_char   INTEGER,                             -- the paying character at that moment
    lapse_corp   INTEGER,
    lapse_repeat INTEGER NOT NULL DEFAULT 0,          -- a repeat miss: read-only straight away
    comp         INTEGER NOT NULL DEFAULT 0           -- free of charge (admin)
);
CREATE TABLE IF NOT EXISTS maps (
    id             TEXT PRIMARY KEY,                  -- m_xxxxxxxx
    name           TEXT NOT NULL,
    payer_id       INTEGER NOT NULL REFERENCES payers(id),
    home_system_id INTEGER,
    created_at     TEXT NOT NULL,
    seq            INTEGER NOT NULL DEFAULT 0,
    corp_join      INTEGER NOT NULL DEFAULT 0,        -- corp maps: corp members may join as viewer
    seat_limit     INTEGER,                           -- corp maps: seats the corp pays for
    deleted_at     TEXT
);
CREATE TABLE IF NOT EXISTS members (
    map_id     TEXT NOT NULL REFERENCES maps(id),
    account_id INTEGER NOT NULL REFERENCES accounts(id),
    role       TEXT NOT NULL CHECK (role IN ('owner', 'manager', 'editor', 'viewer')),
    joined_at  TEXT NOT NULL,
    via_corp   INTEGER NOT NULL DEFAULT 0,            -- joined through the corp switch, not an invite
    PRIMARY KEY (map_id, account_id)
);
CREATE TABLE IF NOT EXISTS map_access (             -- who may join; empty on a personal map: anyone invited
    map_id     TEXT NOT NULL REFERENCES maps(id),
    kind       TEXT NOT NULL CHECK (kind IN ('alliance', 'corp', 'char')),
    subject_id INTEGER NOT NULL,
    name       TEXT,
    added_by   INTEGER,
    added_at   TEXT NOT NULL,
    PRIMARY KEY (map_id, kind, subject_id)
);
CREATE TABLE IF NOT EXISTS invites (
    code       TEXT PRIMARY KEY,
    map_id     TEXT NOT NULL REFERENCES maps(id),
    role       TEXT NOT NULL CHECK (role IN ('manager', 'editor', 'viewer')),
    expires_at TEXT NOT NULL,
    uses_left  INTEGER NOT NULL,
    created_by INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS entities (                 -- current state + tombstones, kept forever
    map_id    TEXT NOT NULL,
    kind      TEXT NOT NULL,
    key       TEXT NOT NULL,                          -- JSON of the natural key
    state     TEXT NOT NULL,                          -- JSON of the merged EntityState
    PRIMARY KEY (map_id, kind, key)
);
CREATE TABLE IF NOT EXISTS ops (                      -- the tail, trimmed on a timer
    map_id      TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    batch_id    TEXT NOT NULL,
    account_id  INTEGER,                              -- NULL: the server (auto-prune)
    payload     TEXT NOT NULL,                        -- JSON list of ops
    stamped     TEXT NOT NULL DEFAULT '{}',
    received_at TEXT NOT NULL,
    PRIMARY KEY (map_id, seq)
);
CREATE TABLE IF NOT EXISTS seen_batches (
    map_id   TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    seq      INTEGER NOT NULL,
    PRIMARY KEY (map_id, batch_id)
);
CREATE TABLE IF NOT EXISTS payments (
    journal_id INTEGER PRIMARY KEY,
    char_id    INTEGER,
    corp_id    INTEGER,
    ref_type   TEXT NOT NULL,
    amount     REAL NOT NULL,
    payer_id   INTEGER REFERENCES payers(id),
    applied_at TEXT NOT NULL,
    note       TEXT
);
CREATE TABLE IF NOT EXISTS unmatched (
    journal_id  INTEGER PRIMARY KEY,
    char_id     INTEGER,
    amount      REAL NOT NULL,
    reason      TEXT,
    seen_at     TEXT NOT NULL,
    refunded_at TEXT
);
CREATE TABLE IF NOT EXISTS trials (
    char_id    INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL,
    started_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS payment_strikes (
    subject_kind TEXT NOT NULL CHECK (subject_kind IN ('char', 'corp')),
    subject_id   INTEGER NOT NULL,
    count        INTEGER NOT NULL DEFAULT 0,
    last_at      TEXT NOT NULL,
    PRIMARY KEY (subject_kind, subject_id)
);
CREATE TABLE IF NOT EXISTS admin_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    sent_at      TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mails (                    -- EVE mails to the receiver already handled
    mail_id INTEGER PRIMARY KEY,
    seen_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outgoing_mails (           -- EVE mails the receiver sends (payment receipts, problems)
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT NOT NULL UNIQUE,                -- e.g. receipt:<journal id>, so one is never sent twice
    kind         TEXT NOT NULL,
    recipient_id INTEGER NOT NULL,
    subject      TEXT NOT NULL,
    body         TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    sent_at      TEXT,
    attempts     INTEGER NOT NULL DEFAULT 0,
    last_error   TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def utc_now() -> datetime:
    """Return the current UTC time with whole seconds (the app's :func:`utc_now_iso`, as a datetime)."""
    return stored_time(utc_now_iso())


def iso(moment: datetime) -> str:
    """Return a time as UTC ISO 8601 text with whole seconds, the form :func:`utc_now_iso` writes."""
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_iso(text: str | None) -> datetime | None:
    """Parse ISO 8601 text with the app's :func:`parse_utc` (None stays None)."""
    return parse_utc(text) if text else None


def stored_time(text: str | None) -> datetime:
    """Parse a time the server wrote itself (a NOT NULL column); ValueError if it isn't one."""
    moment = parse_utc(text)
    if moment is None:
        raise ValueError(f"not a stored time: {text!r}")
    return moment


class Database:
    """The server database. Calls are serialised with a lock, so request threads can share it."""

    def __init__(self, path: Path | str) -> None:
        """Open (and create) the database file; ``":memory:"`` for tests."""
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.connection.executescript(SCHEMA)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Add columns that were added to the schema after a database file was made."""
        for table, column, kind in (("corporations", "alliance_id", "INTEGER"),):
            if column not in {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}:
                self.connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")

    def close(self) -> None:
        """Close the file."""
        self.connection.close()

    def execute(self, sql: str, parameters=()) -> sqlite3.Cursor:
        """Run one statement (autocommit unless inside :meth:`transaction`)."""
        with self.lock:
            return self.connection.execute(sql, parameters)

    def one(self, sql: str, parameters=()) -> sqlite3.Row | None:
        """Return the first row of a query, or None."""
        with self.lock:
            return self.connection.execute(sql, parameters).fetchone()

    def required(self, sql: str, parameters=()) -> sqlite3.Row:
        """Return the first row of a query that must have one (LookupError if it hasn't)."""
        row = self.one(sql, parameters)
        if row is None:
            raise LookupError(f"no row for {sql!r} {tuple(parameters)!r}")
        return row

    def all(self, sql: str, parameters=()) -> list[sqlite3.Row]:
        """Return every row of a query."""
        with self.lock:
            return list(self.connection.execute(sql, parameters))

    def value(self, sql: str, parameters=(), default=None):
        """Return the first column of the first row, or ``default``."""
        row = self.one(sql, parameters)
        return row[0] if row is not None and row[0] is not None else default

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run several statements atomically (nesting is fine: the outer one commits)."""
        with self.lock:
            if self.connection.in_transaction:
                yield self.connection
                return
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                yield self.connection
            except BaseException:
                self.connection.execute("ROLLBACK")
                raise
            self.connection.execute("COMMIT")

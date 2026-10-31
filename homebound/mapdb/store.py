"""SQLite persistence for the map (systems, connections, signatures, characters, zKill cache)."""
import sqlite3
from dataclasses import dataclass, fields
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path


# Wormhole lifetime state, stored in the `eol` column of connections and signatures.
# eol_at is when the current state was set; the hole is gone LIFE_LEFT after that.
LIFE_STABLE, LIFE_4H, LIFE_1H, LIFE_EXPIRED = 0, 1, 2, 3
LIFE_LABEL = {LIFE_STABLE: "Stable", LIFE_4H: "< 4h", LIFE_1H: "< 1h", LIFE_EXPIRED: "Expired"}
LIFE_CHIP = {LIFE_4H: "◷ <4h", LIFE_1H: "◷ <1h", LIFE_EXPIRED: "✕ EXP"}
LIFE_LEFT = {LIFE_4H: timedelta(hours=4), LIFE_1H: timedelta(hours=1),
             LIFE_EXPIRED: timedelta(minutes=10)}  # expired: keep it visible for a few minutes


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class MapSystem:
    id: int
    parent_id: int | None = None
    hole_index: int | None = None
    level_index: int | None = None
    custom_name: str | None = None
    x: float = 0.0
    y: float = 0.0
    owner_ticker: str | None = None
    owner_slot: int | None = None
    owner_note: str | None = None
    notes: str = ""
    added_at: str = ""
    scanned_at: str | None = None
    pinned: int = 0
    confirmed: int = 1           # 0: added by hand, no character has been here yet


@dataclass
class Connection:
    id: int
    a: int
    b: int
    sig_a: str | None = None
    sig_b: str | None = None
    wh_type: str | None = None
    mass: str = "fresh"
    eol: int = 0                 # LIFE_* state
    eol_at: str | None = None
    frig: int = 0
    created_at: str = ""
    updated_at: str = ""
    size: str | None = None      # S/M/L/XL, set by hand only when the type doesn't say (K162/unknown)
    confirmed: int = 1           # 0: drawn by hand, nobody has jumped it yet

    def other(self, sid: int) -> int:
        return self.b if sid == self.a else self.a

    def sig_on(self, sid: int) -> str | None:
        return self.sig_a if sid == self.a else self.sig_b


@dataclass
class Signature:
    system_id: int
    sig_id: str
    grp: str | None = None
    type: str | None = None
    name: str | None = None
    scan_pct: float | None = None
    hole_index: int | None = None
    state: str = ""
    created_at: str = ""
    updated_at: str = ""
    kind: str = "sig"
    dest: str | None = None
    mass: str | None = None
    eol: int = 0                 # LIFE_* state
    eol_at: str | None = None
    frig: int = 0
    size: str | None = None


SIG_COLS = [f.name for f in fields(Signature)]

# columns added after the first release: (table, column, declaration)
MIGRATIONS = [
    ("signatures", "kind", "TEXT DEFAULT 'sig'"),
    ("signatures", "dest", "TEXT"),
    ("signatures", "mass", "TEXT"),
    ("signatures", "eol", "INTEGER DEFAULT 0"),
    ("signatures", "eol_at", "TEXT"),
    ("signatures", "frig", "INTEGER DEFAULT 0"),
    ("signatures", "size", "TEXT"),
    ("connections", "size", "TEXT"),
    ("connections", "confirmed", "INTEGER DEFAULT 1"),
    ("systems", "confirmed", "INTEGER DEFAULT 1"),
]


@dataclass
class ZkillStats:
    system_id: int
    kills_1h: int | None
    kills_24h: int | None
    pods_1h: int | None
    last_kill_at: str | None
    fetched_at: str


def _row(cls, r):
    return cls(**{f.name: r[f.name] for f in fields(cls)}) if r else None


class Store:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self._migrate()
        self.db.executescript(files("homebound.mapdb").joinpath("schema.sql").read_text())

    def _migrate(self) -> None:
        """Add columns that older map files lack (CREATE TABLE IF NOT EXISTS won't)."""
        for table, col, decl in MIGRATIONS:
            have = {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}
            if have and col not in have:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ undo
    SNAPSHOT_KEYS = {"systems": ("id",), "connections": ("id",), "signatures": ("system_id", "sig_id")}

    def snapshot(self) -> dict[str, list[dict]]:
        """Map rows before a destructive action, for one level of Undo (spec: ConfirmDialog)."""
        return {t: [dict(r) for r in self.db.execute(f"SELECT * FROM {t}")] for t in self.SNAPSHOT_KEYS}

    def restore(self, snap: dict[str, list[dict]]) -> None:
        """Put back every snapshot row (systems first, so foreign keys hold). Rows added since stay;
        rows that still exist get their old values back. Updates in place, so no cascades fire."""
        self.db.execute("PRAGMA defer_foreign_keys = ON")   # parents may come back after children
        with self.db:
            for table, keys in self.SNAPSHOT_KEYS.items():
                for row in snap.get(table, []):
                    cols = [c for c in row if c not in keys]
                    cur = self.db.execute(
                        f"UPDATE {table} SET {', '.join(f'{c}=?' for c in cols)} WHERE "
                        + " AND ".join(f"{k}=?" for k in keys), [row[c] for c in cols] + [row[k] for k in keys])
                    if cur.rowcount == 0:
                        self.db.execute(f"INSERT OR IGNORE INTO {table} ({', '.join(row)}) "
                                        f"VALUES ({', '.join('?' * len(row))})", list(row.values()))

    # ------------------------------------------------------------ systems
    def system(self, sid: int) -> MapSystem | None:
        return _row(MapSystem, self.db.execute("SELECT * FROM systems WHERE id=?", (sid,)).fetchone())

    def systems(self) -> list[MapSystem]:
        return [_row(MapSystem, r) for r in self.db.execute("SELECT * FROM systems")]

    def has_system(self, sid: int) -> bool:
        return self.db.execute("SELECT 1 FROM systems WHERE id=?", (sid,)).fetchone() is not None

    def add_system(self, sid: int, **kw) -> MapSystem:
        s = MapSystem(sid, added_at=now_iso(), **kw)
        cols = [f.name for f in fields(MapSystem)]
        self.db.execute(f"INSERT OR IGNORE INTO systems ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [getattr(s, c) for c in cols])
        self.db.commit()
        return self.system(sid)

    def update_system(self, sid: int, **kw) -> None:
        if not kw:
            return
        self.db.execute(f"UPDATE systems SET {', '.join(f'{k}=?' for k in kw)} WHERE id=?", [*kw.values(), sid])
        self.db.commit()

    def remove_system(self, sid: int) -> None:
        self.db.execute("UPDATE systems SET parent_id=NULL, hole_index=NULL WHERE parent_id=?", (sid,))
        self.db.execute("DELETE FROM systems WHERE id=?", (sid,))
        self.db.commit()

    def children(self, sid: int) -> list[MapSystem]:
        return [_row(MapSystem, r) for r in
                self.db.execute("SELECT * FROM systems WHERE parent_id=? ORDER BY hole_index", (sid,))]

    # ------------------------------------------------------------ connections
    def connections(self) -> list[Connection]:
        return [_row(Connection, r) for r in self.db.execute("SELECT * FROM connections")]

    def connection(self, a: int, b: int) -> Connection | None:
        a, b = min(a, b), max(a, b)
        return _row(Connection, self.db.execute("SELECT * FROM connections WHERE a=? AND b=?", (a, b)).fetchone())

    def connection_by_id(self, cid: int) -> Connection | None:
        return _row(Connection, self.db.execute("SELECT * FROM connections WHERE id=?", (cid,)).fetchone())

    def connections_of(self, sid: int) -> list[Connection]:
        return [_row(Connection, r) for r in self.db.execute("SELECT * FROM connections WHERE a=? OR b=?", (sid, sid))]

    def add_connection(self, a: int, b: int, **kw) -> Connection:
        existing = self.connection(a, b)
        if existing:
            return existing
        lo, hi = min(a, b), max(a, b)
        # keep sig_a/sig_b aligned with the sorted ids
        if a > b:
            kw["sig_a"], kw["sig_b"] = kw.pop("sig_b", None), kw.pop("sig_a", None)
        t = now_iso()
        cols = ["a", "b", "created_at", "updated_at", *kw]
        self.db.execute(f"INSERT INTO connections ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                        [lo, hi, t, t, *kw.values()])
        self.db.commit()
        return self.connection(a, b)

    def update_connection(self, cid: int, **kw) -> None:
        kw["updated_at"] = now_iso()
        if "eol_at" not in kw and "eol" in kw:
            # restart the clock only when the state changes; saving the same state again keeps it
            c = self.connection_by_id(cid)
            if not kw["eol"]:
                kw["eol_at"] = None
            elif c and c.eol != kw["eol"]:
                kw["eol_at"] = now_iso()
        self.db.execute(f"UPDATE connections SET {', '.join(f'{k}=?' for k in kw)} WHERE id=?", [*kw.values(), cid])
        self.db.commit()

    def set_connection_sig(self, cid: int, system_id: int, sig_id: str | None) -> None:
        c = self.connection_by_id(cid)
        if c:
            self.update_connection(cid, **{"sig_a" if system_id == c.a else "sig_b": sig_id})

    def remove_connection(self, cid: int) -> None:
        self.db.execute("DELETE FROM connections WHERE id=?", (cid,))
        self.db.commit()

    # ------------------------------------------------------------ signatures
    def signatures(self, sid: int) -> list[Signature]:
        return [_row(Signature, r) for r in
                self.db.execute("SELECT * FROM signatures WHERE system_id=? ORDER BY sig_id", (sid,))]

    def signature(self, sid: int, sig_id: str) -> Signature | None:
        return _row(Signature, self.db.execute("SELECT * FROM signatures WHERE system_id=? AND sig_id=?",
                                               (sid, sig_id)).fetchone())

    def upsert_signature(self, s: Signature) -> None:
        t = now_iso()
        vals = {c: getattr(s, c) for c in SIG_COLS} | {"created_at": s.created_at or t, "updated_at": t}
        keep = ("system_id", "sig_id", "created_at")
        self.db.execute(f"""
            INSERT INTO signatures ({','.join(SIG_COLS)}) VALUES ({','.join('?' * len(SIG_COLS))})
            ON CONFLICT (system_id, sig_id) DO UPDATE SET
                {', '.join(f'{c}=excluded.{c}' for c in SIG_COLS if c not in keep)}
        """, list(vals.values()))
        self.db.commit()

    def update_signature(self, sid: int, sig_id: str, **kw) -> None:
        kw["updated_at"] = now_iso()
        if "eol_at" not in kw and "eol" in kw:
            old = self.signature(sid, sig_id)
            if not kw["eol"]:
                kw["eol_at"] = None
            elif old and old.eol != kw["eol"]:
                kw["eol_at"] = now_iso()
        self.db.execute(f"UPDATE signatures SET {', '.join(f'{k}=?' for k in kw)} WHERE system_id=? AND sig_id=?",
                        [*kw.values(), sid, sig_id])
        self.db.commit()

    def delete_signatures(self, sid: int, sig_ids: list[str]) -> None:
        self.db.executemany("DELETE FROM signatures WHERE system_id=? AND sig_id=?", [(sid, s) for s in sig_ids])
        self.db.commit()

    # ------------------------------------------------------------ characters
    def set_character(self, cid: int, name: str, system_id: int | None, seen_at: str) -> int | None:
        """Store the new location; returns the previous system id."""
        r = self.db.execute("SELECT system_id FROM characters WHERE id=?", (cid,)).fetchone()
        prev = r["system_id"] if r else None
        self.db.execute("""INSERT INTO characters (id, name, system_id, prev_system_id, seen_at) VALUES (?,?,?,?,?)
                           ON CONFLICT (id) DO UPDATE SET name=excluded.name, system_id=excluded.system_id,
                           prev_system_id=excluded.prev_system_id, seen_at=excluded.seen_at""",
                        (cid, name, system_id, prev, seen_at))
        self.db.commit()
        return prev

    def characters(self) -> list[sqlite3.Row]:
        return list(self.db.execute("SELECT * FROM characters ORDER BY name"))

    # ------------------------------------------------------------ zkill
    def zkill(self, sid: int) -> ZkillStats | None:
        return _row(ZkillStats, self.db.execute("SELECT * FROM zkill WHERE system_id=?", (sid,)).fetchone())

    def set_zkill(self, z: ZkillStats) -> None:
        self.db.execute("INSERT OR REPLACE INTO zkill VALUES (?,?,?,?,?,?)",
                        (z.system_id, z.kills_1h, z.kills_24h, z.pods_1h, z.last_kill_at, z.fetched_at))
        self.db.commit()

    # ------------------------------------------------------------ kv
    def get(self, key: str, default: str | None = None) -> str | None:
        r = self.db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def put(self, key: str, value: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (key, value))
        self.db.commit()


def map_stats(path) -> dict:
    """Systems, signatures and last activity of a map file, without opening it as a Store."""
    out = {"systems": 0, "signatures": 0, "last": None}
    if not Path(path).exists():
        return out
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        out["systems"] = db.execute("SELECT count(*) FROM systems").fetchone()[0]
        out["signatures"] = db.execute("SELECT count(*) FROM signatures").fetchone()[0]
        out["last"] = max(filter(None, (
            db.execute("SELECT max(added_at) FROM systems").fetchone()[0],
            db.execute("SELECT max(updated_at) FROM connections").fetchone()[0],
            db.execute("SELECT max(updated_at) FROM signatures").fetchone()[0])), default=None)
    except sqlite3.Error:
        pass
    finally:
        db.close()
    return out


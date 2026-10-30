"""SQLite persistence for the map (systems, connections, signatures, characters, zKill cache)."""
import sqlite3
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path


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


@dataclass
class Connection:
    id: int
    a: int
    b: int
    sig_a: str | None = None
    sig_b: str | None = None
    wh_type: str | None = None
    mass: str = "fresh"
    eol: int = 0
    eol_at: str | None = None
    frig: int = 0
    created_at: str = ""
    updated_at: str = ""

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
        self.db.executescript(files("homebound.mapdb").joinpath("schema.sql").read_text())

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
        if kw.get("eol") and "eol_at" not in kw:
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
        self.db.execute("""
            INSERT INTO signatures (system_id, sig_id, grp, type, name, scan_pct, hole_index, state, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (system_id, sig_id) DO UPDATE SET grp=excluded.grp, type=excluded.type, name=excluded.name,
                scan_pct=excluded.scan_pct, hole_index=excluded.hole_index, state=excluded.state, updated_at=excluded.updated_at
        """, (s.system_id, s.sig_id, s.grp, s.type, s.name, s.scan_pct, s.hole_index, s.state, s.created_at or t, t))
        self.db.commit()

    def update_signature(self, sid: int, sig_id: str, **kw) -> None:
        kw["updated_at"] = now_iso()
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

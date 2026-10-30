"""Read-only lookups over data/universe.sqlite (built by build_static.py)."""
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "universe.sqlite"
KSPACE = ("HS", "LS", "NS")


@dataclass(frozen=True)
class System:
    id: int
    name: str
    cls: str            # C1..C6, TH, C13, DR, PV, HS, LS, NS
    security: float
    region: str
    constellation: str
    effect: str | None  # pulsar, blackhole, ... (theme effect keys) or None
    wspace: bool
    statics: tuple[str, ...] = field(default=())

    @property
    def kspace(self) -> bool:
        return self.cls in KSPACE


@dataclass(frozen=True)
class WhType:
    name: str
    dest: tuple[str, ...]
    lifetime_h: float | None
    total_mass: int | None
    max_jump_mass: int | None
    is_static: bool


class Universe:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(
                f"{path} missing. Run: uv run python -m homebound.universe.build_static")
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        statics: dict[int, list[str]] = {}
        for sid, t in self.db.execute("SELECT system_id, wh_type FROM statics"):
            statics.setdefault(sid, []).append(t)
        self._by_id: dict[int, System] = {}
        self._by_name: dict[str, System] = {}
        for sid, name, cls, sec, region, const, eff, ws in self.db.execute(
                "SELECT id, name, class, security, region, constellation, effect, wspace FROM systems"):
            s = System(sid, name, cls, sec, region, const, eff, bool(ws), tuple(sorted(statics.get(sid, ()))))
            self._by_id[sid] = s
            self._by_name[name.lower()] = s
        self._adj: dict[int, set[int]] = {}
        for a, b in self.db.execute("SELECT a, b FROM gates"):
            self._adj.setdefault(a, set()).add(b)
            self._adj.setdefault(b, set()).add(a)
        self._types: dict[str, WhType] = {}
        for name, dest, life, tm, mj, st in self.db.execute(
                "SELECT name, dest, lifetime_h, total_mass, max_jump_mass, is_static FROM wh_types"):
            self._types[name] = WhType(name, tuple(dest.split(",")) if dest else (), life, tm, mj, bool(st))

    # ---- systems
    def by_name(self, name: str) -> System | None:
        return self._by_name.get(name.strip().lower())

    def by_id(self, sid: int) -> System | None:
        return self._by_id.get(sid)

    def search(self, prefix: str, limit: int = 20) -> list[System]:
        p = prefix.strip().lower()
        if not p:
            return []
        hits = [s for k, s in self._by_name.items() if k.startswith(p)]
        return sorted(hits, key=lambda s: s.name)[:limit]

    def all_names(self) -> list[str]:
        return [s.name for s in self._by_id.values()]

    # ---- gates
    def gate_neighbours(self, sid: int) -> set[int]:
        return self._adj.get(sid, set())

    def gate_adjacent(self, a: int, b: int) -> bool:
        return b in self._adj.get(a, ())

    def gate_distances(self, start: int, max_jumps: int) -> dict[int, int]:
        """BFS over stargates; {system_id: jumps} for everything within max_jumps."""
        dist = {start: 0}
        q = deque([start])
        while q:
            cur = q.popleft()
            if dist[cur] >= max_jumps:
                continue
            for n in self._adj.get(cur, ()):
                if n not in dist:
                    dist[n] = dist[cur] + 1
                    q.append(n)
        return dist

    # ---- wormhole types
    def wh_type(self, name: str) -> WhType | None:
        return self._types.get(name.upper())

    def static_types(self, sid: int) -> list[WhType]:
        s = self._by_id.get(sid)
        return [t for t in (self._types.get(n) for n in (s.statics if s else ())) if t]

    def static_dest_classes(self, sid: int) -> list[str]:
        return [d for t in self.static_types(sid) for d in t.dest]


@lru_cache(maxsize=1)
def universe() -> Universe:
    return Universe()

"""Read-only lookups over data/universe.sqlite (built by build_static.py)."""
import sqlite3
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from ..validation import require_integer, require_text, require_type

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "data" / "universe.sqlite"
BUILD_COMMAND = "uv run python -m homebound.universe.build_static"
SCHEMA_VERSION = "2"         # bump together with build_static.SCHEMA_VERSION
KNOWN_SPACE_CLASSES = ("HS", "LS", "NS")

# max mass per jump (kg) -> size class; the sizes CCP uses are 5, 62, 375 and ≥ 1000 kt
SIZES = ["S", "M", "L", "XL"]
SIZE_NAMES = {"S": "Frigate", "M": "Medium", "L": "Large", "XL": "Capital"}


class UniverseDataError(FileNotFoundError):
    """The static universe file is missing or was built by an older version; it needs a rebuild."""


@dataclass(frozen=True)
class System:
    """A solar system from the SDE."""

    id: int
    name: str
    system_class: str            # C1..C6, TH, C13, DR, PV, HS, LS, NS
    security: float
    region: str
    constellation: str
    effect: str | None           # pulsar, blackhole, ... (theme effect keys) or None
    wormhole_space: bool
    statics: tuple[str, ...] = field(default=())

    @property
    def known_space(self) -> bool:
        """Whether the system is high-, low- or null-sec."""
        return self.system_class in KNOWN_SPACE_CLASSES


@dataclass(frozen=True)
class WormholeType:
    """A wormhole type such as H296 or K162."""

    name: str
    destinations: tuple[str, ...]
    lifetime_hours: float | None
    total_mass: int | None
    max_jump_mass: int | None
    is_static: bool
    spawns_in: tuple[str, ...] = ()               # classes this type can spawn in (Wanderer data)
    wanders_into: tuple[str, ...] | None = None   # classes it can wander into (whtype.info); None = unknown

    @property
    def size(self) -> str | None:
        """Largest ship class that fits: S (frigate), M (cruiser/BC), L (battleship), XL (capital)."""
        return size_for_jump_mass(self.max_jump_mass)


def size_for_jump_mass(max_jump_mass: int | float | None) -> str | None:
    """Return the size class of a hole from its maximum mass per jump.

    Args:
        max_jump_mass: Mass in kg, or None/0 if unknown.

    Returns:
        ``"S"``, ``"M"``, ``"L"`` or ``"XL"``, or None if the mass is unknown.

    Raises:
        TypeError: If the mass is not a number.
        ValueError: If the mass is negative.
    """
    if max_jump_mass is None:
        return None
    if isinstance(max_jump_mass, bool) or not isinstance(max_jump_mass, (int, float)):
        raise TypeError(f"max_jump_mass must be a number, not {type(max_jump_mass).__name__}")
    if max_jump_mass < 0:
        raise ValueError(f"max_jump_mass must not be negative, got {max_jump_mass}")
    if not max_jump_mass:
        return None
    if max_jump_mass <= 5e6:
        return "S"
    if max_jump_mass <= 62e6:
        return "M"
    return "L" if max_jump_mass <= 375e6 else "XL"


def _split(text: str | None) -> tuple[str, ...]:
    """Split a comma-separated column into a tuple, dropping empty parts."""
    return tuple(part for part in (text or "").split(",") if part)


class Universe:
    """All systems, gates, statics and wormhole types, loaded into memory once."""

    def __init__(self, path: Path | str = DEFAULT_PATH) -> None:
        """Load the universe file.

        Args:
            path: The SQLite file built by ``build_static``.

        Raises:
            UniverseDataError: If the file is missing or has an older schema.
        """
        require_type(path, (str, Path), "path")
        path = Path(path)
        if not path.exists():
            raise UniverseDataError(f"{path} missing. Run: {BUILD_COMMAND}")
        self.database = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        version = self._schema_version()
        if version != SCHEMA_VERSION:
            self.database.close()
            raise UniverseDataError(f"{path} was built by an older version (schema {version or '1'}). "
                                    f"Run: {BUILD_COMMAND}")
        statics: dict[int, list[str]] = {}
        for system_id, wormhole_type in self.database.execute("SELECT system_id, wormhole_type FROM statics"):
            statics.setdefault(system_id, []).append(wormhole_type)
        self._by_id: dict[int, System] = {}
        self._by_name: dict[str, System] = {}
        for row in self.database.execute(
                "SELECT id, name, system_class, security, region, constellation, effect, wormhole_space "
                "FROM systems"):
            system = System(*row[:7], bool(row[7]), tuple(sorted(statics.get(row[0], ()))))
            self._by_id[system.id] = system
            self._by_name[system.name.lower()] = system
        self._gates: dict[int, set[int]] = {}
        for system_a, system_b in self.database.execute("SELECT system_a, system_b FROM gates"):
            self._gates.setdefault(system_a, set()).add(system_b)
            self._gates.setdefault(system_b, set()).add(system_a)
        self._types: dict[str, WormholeType] = {}
        for (name, destinations, lifetime_hours, total_mass, max_jump_mass, is_static, spawns_in,
             wanders_into) in self.database.execute(
                "SELECT name, destinations, lifetime_hours, total_mass, max_jump_mass, is_static, spawns_in, "
                "wanders_into FROM wormhole_types"):
            self._types[name] = WormholeType(name, _split(destinations), lifetime_hours, total_mass, max_jump_mass,
                                             bool(is_static), _split(spawns_in),
                                             None if wanders_into is None else _split(wanders_into))

    def close(self) -> None:
        """Close the universe file."""
        self.database.close()

    def _schema_version(self) -> str | None:
        """Return the schema version stored in the file, or None for files from before versions."""
        try:
            row = self.database.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row else None

    # ---- systems
    def by_name(self, name: str) -> System | None:
        """Return a system by name, ignoring case and surrounding spaces, or None."""
        require_type(name, str, "name")
        return self._by_name.get(name.strip().lower())

    def by_id(self, system_id: int) -> System | None:
        """Return a system by its EVE solar system ID, or None."""
        require_integer(system_id, "system_id")
        return self._by_id.get(system_id)

    def search(self, prefix: str, limit: int = 20) -> list[System]:
        """Return systems whose name starts with ``prefix``, sorted by name.

        Args:
            prefix: The start of the name (case-insensitive). Blank gives no results.
            limit: The most results to return.

        Returns:
            Up to ``limit`` systems.
        """
        require_type(prefix, str, "prefix")
        require_integer(limit, "limit", minimum=0)
        start = prefix.strip().lower()
        if not start:
            return []
        hits = [system for key, system in self._by_name.items() if key.startswith(start)]
        return sorted(hits, key=lambda system: system.name)[:limit]

    def all_names(self) -> list[str]:
        """Return the name of every system."""
        return [system.name for system in self._by_id.values()]

    def all_systems(self) -> list[System]:
        """Return every system, ordered by ID."""
        return sorted(self._by_id.values(), key=lambda system: system.id)

    # ---- gates
    def gate_neighbours(self, system_id: int) -> set[int]:
        """Return the systems one stargate jump away."""
        require_integer(system_id, "system_id")
        return set(self._gates.get(system_id, ()))

    def gate_adjacent(self, system_a: int, system_b: int) -> bool:
        """Return whether a stargate links two systems."""
        require_integer(system_a, "system_a")
        require_integer(system_b, "system_b")
        return system_b in self._gates.get(system_a, ())

    def gate_distances(self, start: int, max_jumps: int) -> dict[int, int]:
        """Breadth-first search over stargates.

        Args:
            start: The system to start from.
            max_jumps: How far to search.

        Returns:
            ``{system_id: jumps}`` for every system within ``max_jumps``, including ``start`` at 0.
        """
        require_integer(start, "start")
        require_integer(max_jumps, "max_jumps", minimum=0)
        distances = {start: 0}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            if distances[current] >= max_jumps:
                continue
            for neighbour in self._gates.get(current, ()):
                if neighbour not in distances:
                    distances[neighbour] = distances[current] + 1
                    queue.append(neighbour)
        return distances

    # ---- wormhole types
    def wormhole_type(self, name: str) -> WormholeType | None:
        """Return a wormhole type by code (case-insensitive), or None."""
        require_text(name, "name")
        return self._types.get(name.strip().upper())

    def wormhole_type_names(self) -> list[str]:
        """Return every wormhole type code, sorted."""
        return sorted(self._types)

    def wormhole_types_in(self, system_id: int) -> list[WormholeType]:
        """Return the types that can be found in a system, statics first.

        These are the system's statics plus the types that wander into its class (whtype.info;
        Wanderer's spawn data for types whtype.info doesn't know). K162 is not included.

        Returns:
            The types, statics first, then by code. Empty for an unknown system.
        """
        system = self.by_id(system_id)
        if not system:
            return []
        found = {wormhole_type.name: wormhole_type for wormhole_type in self.static_types(system_id)}
        for wormhole_type in self._types.values():
            if wormhole_type.wanders_into is not None:
                possible = system.system_class in wormhole_type.wanders_into
            else:
                possible = system.system_class in wormhole_type.spawns_in and not wormhole_type.is_static
            if wormhole_type.name != "K162" and possible:
                found.setdefault(wormhole_type.name, wormhole_type)
        return sorted(found.values(), key=lambda wormhole_type: (wormhole_type.name not in system.statics, wormhole_type.name))

    def static_types(self, system_id: int) -> list[WormholeType]:
        """Return the static wormhole types of a system (empty for k-space and unknown systems)."""
        system = self.by_id(system_id)
        names = system.statics if system else ()
        return [wormhole_type for wormhole_type in (self._types.get(name) for name in names) if wormhole_type]

    def static_destination_classes(self, system_id: int) -> list[str]:
        """Return every class the statics of a system lead to."""
        return [destination for wormhole_type in self.static_types(system_id) for destination in wormhole_type.destinations]


@lru_cache(maxsize=1)
def load_universe() -> Universe:
    """Return the universe from the default path, loaded once per process.

    Raises:
        UniverseDataError: If the file is missing or outdated.
    """
    return Universe()

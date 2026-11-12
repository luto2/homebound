"""Routes over stargates, the map's wormhole connections and EVE-Scout's Thera/Turnur connections.

One Dijkstra search from the origin gives the route to every destination. Each jump costs 1; the
"safer" preference adds a large cost for entering low-/null-sec (and w-space), "less secure" one for
entering high-sec, so penalised space is only used when there is no other way, like the in-game
autopilot.
"""
import heapq
from collections.abc import Iterable
from dataclasses import dataclass, field

from .mapdb.store import LIFETIME_1H, LIFETIME_4H, LIFETIME_EXPIRED, Connection
from .mapping.tracker import hole_size
from .universe.db import SIZES, Universe
from .validation import require_choice, require_integer, require_type

PREFERENCES = ("shortest", "safer", "less_secure")
PREFERENCE_LABELS = {"shortest": "Shortest", "safer": "Safer", "less_secure": "Less secure"}
HIGH_SEC = 0.45           # true security from which the autopilot counts a system as high-sec
PENALTY = 1000            # cost of entering a system the preference avoids
VIA_GATE, VIA_WORMHOLE, VIA_THERA, VIA_TURNUR = "gate", "wormhole", "thera", "turnur"


@dataclass(frozen=True)
class ScoutLink:
    """A wormhole EVE-Scout reports between Thera or Turnur and another system."""

    hub_id: int                  # Thera or Turnur
    system_id: int               # the other end
    hub: str                     # "thera" or "turnur"
    wormhole_type: str | None = None
    size: str | None = None      # S/M/L/XL, None if unknown
    expires_at: str | None = None
    remaining_hours: float | None = None


@dataclass(frozen=True)
class RouteOptions:
    """What a route may use."""

    preference: str = "shortest"
    use_eve_scout: bool = True
    use_eol: bool = True              # holes at < 4h or < 1h
    use_mass_critical: bool = False
    ship_size: str = "M"              # holes smaller than this are skipped; holes of unknown size are kept

    def __post_init__(self) -> None:
        """Check the choices.

        Raises:
            ValueError: If the preference or ship size is unknown.
        """
        require_choice(self.preference, PREFERENCES, "preference")
        require_choice(self.ship_size, SIZES, "ship_size")
        for name in ("use_eve_scout", "use_eol", "use_mass_critical"):
            require_type(getattr(self, name), bool, name)

    def fits(self, size: str | None) -> bool:
        """Return whether the ship fits through a hole of this size (unknown sizes fit)."""
        return size is None or SIZES.index(size) >= SIZES.index(self.ship_size)


@dataclass(frozen=True)
class Hop:
    """One system on a route and how it is entered."""

    system_id: int
    via: str                     # VIA_*; the origin is entered "via" nothing and has via ""
    link: ScoutLink | None = None


@dataclass
class Route:
    """The systems from the origin to a destination, origin first."""

    destination_id: int
    hops: list[Hop] = field(default_factory=list)

    @property
    def jumps(self) -> int:
        """The number of jumps (systems after the origin)."""
        return max(0, len(self.hops) - 1)


Graph = dict[int, list[tuple[int, str, ScoutLink | None]]]


def connection_usable(universe: Universe, connection: Connection, options: RouteOptions) -> bool:
    """Return whether a map connection may be used under the options (never once expired)."""
    require_type(connection, Connection, "connection")
    if connection.lifetime == LIFETIME_EXPIRED:
        return False
    if connection.lifetime in (LIFETIME_4H, LIFETIME_1H) and not options.use_eol:
        return False
    if connection.mass == "critical" and not options.use_mass_critical:
        return False
    size = "S" if connection.frigate_only else hole_size(universe, connection.wormhole_type, connection.size)
    return options.fits(size)


def build_graph(universe: Universe, connections: Iterable[Connection], scout_links: Iterable[ScoutLink],
                options: RouteOptions) -> Graph:
    """Return ``{system: [(neighbour, via, scout link or None), ...]}`` for a search.

    Args:
        universe: Static data, for the stargates and hole sizes.
        connections: The map's wormhole connections.
        scout_links: EVE-Scout's connections (used only if the options allow).
        options: What the route may use.
    """
    require_type(universe, Universe, "universe")
    require_type(options, RouteOptions, "options")
    graph: Graph = {system: [(neighbour, VIA_GATE, None) for neighbour in neighbours]
                    for system, neighbours in universe.gate_graph().items()}

    def link(first: int, second: int, via: str, scout: ScoutLink | None = None) -> None:
        """Add an edge both ways."""
        graph.setdefault(first, []).append((second, via, scout))
        graph.setdefault(second, []).append((first, via, scout))

    for connection in connections:
        if connection_usable(universe, connection, options):
            link(connection.system_a, connection.system_b, VIA_WORMHOLE)
    if options.use_eve_scout:
        for scout in scout_links:
            if options.fits(scout.size):
                link(scout.hub_id, scout.system_id, VIA_THERA if scout.hub == "thera" else VIA_TURNUR, scout)
    return graph


def entry_cost(universe: Universe, system_id: int, preference: str) -> int:
    """Return the cost of jumping into a system: 1, plus the penalty if the preference avoids it."""
    if preference == "shortest":
        return 1
    system = universe.by_id(system_id)
    high_sec = bool(system and system.known_space and system.security >= HIGH_SEC)
    avoided = not high_sec if preference == "safer" else high_sec
    return 1 + (PENALTY if avoided else 0)


def search(universe: Universe, graph: Graph, origin: int, preference: str = "shortest"
           ) -> dict[int, tuple[int, str, ScoutLink | None]]:
    """Dijkstra from ``origin``.

    Returns:
        ``{system: (previous system, via, scout link)}`` for every reachable system; the origin maps to
        ``(0, "", None)``.
    """
    require_integer(origin, "origin")
    require_choice(preference, PREFERENCES, "preference")
    # The cost of a jump depends only on the system entered, so the first way a system is reached
    # (from the cheapest system popped so far) is already its cheapest: no entry is ever improved.
    previous: dict[int, tuple[int, str, ScoutLink | None]] = {origin: (0, "", None)}
    heap = [(0, origin)]
    while heap:
        current_cost, current = heapq.heappop(heap)
        for neighbour, via, scout in graph.get(current, ()):
            if neighbour not in previous:
                previous[neighbour] = (current, via, scout)
                heapq.heappush(heap, (current_cost + entry_cost(universe, neighbour, preference), neighbour))
    return previous


def path_to(previous: dict[int, tuple[int, str, ScoutLink | None]], destination: int) -> Route | None:
    """Read the route to a destination back from a search, or None if it wasn't reached."""
    if destination not in previous:
        return None
    hops = []
    current = destination
    while current:
        before, via, scout = previous[current]
        hops.append(Hop(current, via, scout))
        current = before
    return Route(destination, hops[::-1])


def routes(universe: Universe, origin: int, destinations: Iterable[int], connections: Iterable[Connection],
           scout_links: Iterable[ScoutLink], options: RouteOptions) -> list[Route | None]:
    """Return the route from ``origin`` to each destination (None where there is no known way).

    Args:
        universe: Static data.
        origin: The system to start from.
        destinations: The systems to reach, in the order the routes are returned.
        connections: The map's wormhole connections.
        scout_links: EVE-Scout's connections.
        options: What the routes may use.
    """
    graph = build_graph(universe, connections, scout_links, options)
    previous = search(universe, graph, origin, options.preference)
    return [path_to(previous, destination) for destination in destinations]

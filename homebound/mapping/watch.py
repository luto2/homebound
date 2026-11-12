"""Watched systems: systems that are not on the map, and an alert when a chain reaches them.

There are two watch lists (plan-watched-systems.md, owner decisions 2026-11-12):

* the **map's** list, the map setting ``watched_systems`` (a JSON list of system IDs). It syncs on a
  cloud map, so a group watches the same systems;
* a **private** list per user (``Settings.watched_private``, system names), never synced.

A watched w-space, Thera or Pochven system is *reached* when it is on the map. A watched k-space
system is reached when a mapped system with a usable wormhole connection is at most ``max_jumps``
stargate jumps away (the setting ``watch_jumps``: 1, 2, 5 or 10). Wormhole edges only join mapped
systems, so the nearest mapped exit over gates is also the nearest over gates plus the map's holes.

A watched system that is not on the map is not mapped from either (see ``Tracker.mapping_blocked``).
"""
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ..mapdb.store import LIFETIME_EXPIRED, Store
from ..universe.db import Universe
from ..validation import require_integer, require_type

WATCH_SETTING = "watched_systems"
WATCH_JUMPS = (1, 2, 5, 10)            # how far to look from a watched k-space system; 1 is "adjacent only"
DEFAULT_WATCH_JUMPS = 5
QUIET_TIME = timedelta(hours=1)       # a hit fires again only after it was gone this long


MAX_WATCHED = 200                      # systems on a map's watch list (the server refuses more)


def watched_ids(value: Any) -> list[int] | None:
    """Return the system IDs of a ``watched_systems`` setting value (a JSON list), or None if it isn't one.

    An empty or missing value is the empty list. The sync protocol checks values with this too.
    """
    if value in (None, ""):
        return []
    try:
        ids = json.loads(value) if isinstance(value, str) else None
    except ValueError:
        return None
    if not isinstance(ids, list) or len(ids) > MAX_WATCHED or not all(
            isinstance(item, int) and not isinstance(item, bool) and 1 <= item < 2**31 for item in ids):
        return None
    return ids


def map_watch_list(store: Store) -> list[int]:
    """Return the system IDs of the map's watch list, in the order they were added (empty if the value is bad)."""
    require_type(store, Store, "store")
    return watched_ids(store.map_setting(WATCH_SETTING)) or []


def set_map_watch_list(store: Store, system_ids: list[int]) -> bool:
    """Store the map's watch list (one action, synced on a cloud map).

    Returns:
        True if it changed.

    Raises:
        ValueError: If the list is longer than :data:`MAX_WATCHED`.
    """
    require_type(system_ids, list, "system_ids")
    if len(set(system_ids)) > MAX_WATCHED:
        raise ValueError(f"a map can watch at most {MAX_WATCHED} systems")
    unique = list(dict.fromkeys(require_integer(system_id, "system_id", minimum=1) for system_id in system_ids))
    return store.set_map_setting(WATCH_SETTING, json.dumps(unique, separators=(",", ":")) if unique else "")


def is_reached_on_map(universe: Universe, system_id: int) -> bool:
    """Return whether a watched system counts as reached only when it is on the map (w-space, Thera, Pochven)."""
    system = universe.by_id(system_id)
    return bool(system and (system.wormhole_space or system.system_class in ("PV", "TH")))


@dataclass(frozen=True)
class WatchHit:
    """How close the chain is to a watched system."""

    system_id: int
    jumps: int                         # 0: on the map
    via_id: int                        # the mapped system it is nearest to (itself when on the map)


def chain_exits(store: Store) -> set[int]:
    """Return the mapped systems that have a wormhole connection that isn't expired."""
    require_type(store, Store, "store")
    return {end for connection in store.connections() if connection.lifetime != LIFETIME_EXPIRED
            for end in (connection.system_a, connection.system_b)}


def distance(store: Store, universe: Universe, system_id: int, max_jumps: int,
             exits: set[int] | None = None) -> WatchHit | None:
    """Return how close the chain is to a watched system, or None if it isn't within reach.

    Args:
        store: The map.
        universe: Static data, for the stargates.
        system_id: The watched system.
        max_jumps: How many stargate jumps from a k-space system still count.
        exits: :func:`chain_exits`, if the caller has it already.

    Returns:
        The hit: ``jumps`` 0 when the system is on the map, else the gate jumps to the nearest
        mapped system with a live connection.
    """
    require_integer(system_id, "system_id", minimum=1)
    require_integer(max_jumps, "max_jumps", minimum=0)
    if store.has_system(system_id):
        return WatchHit(system_id, 0, system_id)
    if is_reached_on_map(universe, system_id):
        return None
    exits = chain_exits(store) if exits is None else exits
    gates = universe.gate_graph()
    seen, queue = {system_id: 0}, deque([system_id])
    while queue:
        current = queue.popleft()
        if seen[current] >= max_jumps:
            continue
        for neighbour in sorted(gates.get(current, ())):
            if neighbour in seen:
                continue
            seen[neighbour] = seen[current] + 1
            if neighbour in exits:
                return WatchHit(system_id, seen[neighbour], neighbour)
            queue.append(neighbour)
    return None


def check(store: Store, universe: Universe, watched: list[int], max_jumps: int) -> dict[int, WatchHit]:
    """Return the watched systems the chain reaches now.

    Args:
        store: The map.
        universe: Static data.
        watched: The watched system IDs (both lists).
        max_jumps: The ``watch_jumps`` setting.

    Returns:
        ``{system id: hit}`` for every watched system that is reached.
    """
    exits = chain_exits(store)
    hits = {}
    for system_id in dict.fromkeys(watched):
        if universe.by_id(system_id) and (hit := distance(store, universe, system_id, max_jumps, exits)):
            hits[system_id] = hit
    return hits


@dataclass
class WatchAlerts:
    """Decides when a hit is new enough to alert: once when it becomes true, again only after a quiet time.

    A hit that goes away and comes back within :data:`QUIET_TIME` doesn't alert again, so a stable
    connection doesn't nag.
    """

    quiet: timedelta = QUIET_TIME
    active: set[int] = field(default_factory=set)          # reached at the last update
    gone_since: dict[int, datetime] = field(default_factory=dict)   # not reached any more, since when

    def update(self, hits: dict[int, WatchHit], now: datetime, silent: bool = False) -> list[WatchHit]:
        """Take the hits of a check and return those that alert now.

        Args:
            hits: :func:`check`.
            now: The current time.
            silent: Take the hits without alerting (a map was just opened, or the system was just added).

        Returns:
            The new hits, in the order of ``hits``.
        """
        require_type(hits, dict, "hits")
        require_type(now, datetime, "now")
        fired = []
        for system_id, hit in hits.items():
            if system_id in self.active:
                continue
            gone = self.gone_since.pop(system_id, None)
            if not silent and (gone is None or now - gone >= self.quiet):
                fired.append(hit)
            self.active.add(system_id)
        for system_id in self.active - set(hits):
            self.active.discard(system_id)
            self.gone_since[system_id] = now
        return fired

    def accept(self, hit: WatchHit) -> None:
        """Count one hit as seen without alerting (the system was just added to a list)."""
        require_type(hit, WatchHit, "hit")
        self.active.add(hit.system_id)
        self.gone_since.pop(hit.system_id, None)

    def forget(self, system_id: int | None = None) -> None:
        """Forget what was seen of one system (it is no longer watched), or of all (another map)."""
        if system_id is None:
            self.active.clear()
            self.gone_since.clear()
            return
        self.active.discard(system_id)
        self.gone_since.pop(system_id, None)

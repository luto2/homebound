"""Which chain is a character in, and how does it look as a tree?

* Character in a mapped system that has wormhole connections -> that connected component.
* Character in k-space off the map -> BFS stargates up to `radius` jumps; the nearest mapped
  system of a component is the entrance and the character counts as in that chain.
"""
from collections import deque
from dataclasses import dataclass, field

from ..mapdb.store import Store
from ..universe.db import Universe
from ..validation import require_integer, require_type


@dataclass
class ChainRow:
    """One row of the chain tree, as the overlay draws it."""

    system_id: int
    depth: int
    prefix: str = ""             # box drawing tree prefix (mono)
    current: bool = False
    jumps_from: int | None = None  # set for the virtual k-space row: jumps from the entrance
    entrance_id: int | None = None
    virtual: bool = False        # character's k-space system, not on the map


@dataclass
class Chain:
    """The chain a character is in."""

    root_id: int
    members: set[int]
    rows: list[ChainRow] = field(default_factory=list)
    current_id: int | None = None
    entrance_id: int | None = None  # set when the character is out in k-space
    jumps: int = 0


def wormhole_adjacency(store: Store) -> dict[int, set[int]]:
    """Return ``{system: neighbours}`` over the wormhole connections of the map."""
    require_type(store, Store, "store")
    adjacency: dict[int, set[int]] = {}
    for connection in store.connections():
        adjacency.setdefault(connection.system_a, set()).add(connection.system_b)
        adjacency.setdefault(connection.system_b, set()).add(connection.system_a)
    return adjacency


def connected_component(adjacency: dict[int, set[int]], start: int) -> set[int]:
    """Return every system reachable from ``start``, including ``start``.

    Args:
        adjacency: ``{system: neighbours}``.
        start: The system to start from.

    Returns:
        The set of reachable systems.
    """
    require_type(adjacency, dict, "adjacency")
    require_integer(start, "start")
    seen, queue = {start}, deque([start])
    while queue:
        for neighbour in adjacency.get(queue.popleft(), ()):
            if neighbour not in seen:
                seen.add(neighbour)
                queue.append(neighbour)
    return seen


def resolve_chain(store: Store, universe: Universe, system_id: int | None, home_id: int | None = None,
                  radius: int = 5, max_depth: int = 4) -> Chain | None:
    """Find the chain a character in ``system_id`` is in, as a tree.

    Args:
        store: The map.
        universe: Static data, for the stargate graph.
        system_id: Where the character is, or None if unknown.
        home_id: Home; it is the tree root when it is part of the chain.
        radius: How many gate jumps from an entrance still count as in the chain.
        max_depth: Deeper rows are folded into ``…``.

    Returns:
        The chain, or None if the character is in no chain.
    """
    require_integer(system_id, "system_id", optional=True)
    require_integer(home_id, "home_id", optional=True)
    require_integer(radius, "radius", minimum=0)
    require_integer(max_depth, "max_depth", minimum=0)
    if system_id is None:
        return None
    adjacency = wormhole_adjacency(store)
    entrance, jumps = None, 0
    if system_id in adjacency:
        members = connected_component(adjacency, system_id)
    else:
        system = universe.by_id(system_id)
        if not system or not system.known_space:
            return None
        distances = universe.gate_distances(system_id, radius)
        nearest = sorted((jumps_away, other) for other, jumps_away in distances.items() if other in adjacency)
        if not nearest:
            return None
        jumps, entrance = nearest[0]
        members = connected_component(adjacency, entrance)

    root = home_id if home_id in members else _tree_root(store, members, entrance or system_id)
    chain = Chain(root, members, current_id=system_id, entrance_id=entrance, jumps=jumps)
    chain.rows = _chain_rows(store, adjacency, members, root, system_id, entrance, jumps, max_depth)
    return chain


def _tree_root(store: Store, members: set[int], fallback: int) -> int:
    """Return the topmost ancestor of ``fallback`` inside the chain."""
    current, seen = store.system(fallback), set()
    while current and current.parent_id in members and current.id not in seen:
        seen.add(current.id)
        current = store.system(current.parent_id)
    return current.id if current else fallback


def _chain_rows(store: Store, adjacency: dict[int, set[int]], members: set[int], root: int, current: int,
                entrance: int | None, jumps: int, max_depth: int) -> list[ChainRow]:
    """Lay the chain out as tree rows with box-drawing prefixes.

    Children follow the naming tree (``parent_id``) first, then the remaining connections by BFS.
    A character out in k-space gets a virtual row under the entrance.
    """
    children: dict[int, list[int]] = {}
    placed = {root}
    by_parent: dict[int, list] = {}
    for system in store.systems():
        if system.id in members and system.parent_id in members:
            by_parent.setdefault(system.parent_id, []).append(system)
    queue = deque([root])
    while queue:
        parent = queue.popleft()
        ordered = [system.id for system in sorted(by_parent.get(parent, []), key=lambda system: (system.hole_index or 99, system.id))]
        ordered += sorted(neighbour for neighbour in adjacency.get(parent, ()) if neighbour not in ordered)
        for child in ordered:
            if child not in placed:
                placed.add(child)
                children.setdefault(parent, []).append(child)
                queue.append(child)
    if entrance is not None:
        children.setdefault(entrance, []).append(-current)  # negative id marks the virtual row

    rows: list[ChainRow] = []

    def walk(node: int, depth: int, lead: str, last: bool) -> None:
        """Append the row of ``node`` and then of its children."""
        virtual = node < 0
        system_id = -node if virtual else node
        branch = "" if depth == 0 else lead + ("└ " if last else "├ ")
        if depth > max_depth:
            branch = lead + "… "
        rows.append(ChainRow(system_id, depth, branch, current=(system_id == current), virtual=virtual,
                             jumps_from=jumps if virtual else None, entrance_id=entrance if virtual else None))
        below = children.get(node, [])
        next_lead = "" if depth == 0 else lead + ("  " if last else "│ ")
        if depth >= max_depth:
            next_lead = lead
        for index, child in enumerate(below):
            walk(child, depth + 1, next_lead, index == len(below) - 1)

    walk(root, 0, "", True)
    return rows

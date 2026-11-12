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
    # Overlay focus (see focus_rows). kind "" is a real row; the others stand for hidden systems:
    #   "branch": one hidden branch (system_id is its first system), summary_count systems
    #   "more":   several hidden branches of system_id merged under the row cap, summary_count systems
    #   "path":   systems folded out of the way to home, summary_count of them
    kind: str = ""
    summary_count: int = 0
    exits: frozenset = frozenset()  # "high" | "low" | "null": holes into k-space on this row or in what it hides
    leads_home: bool = False        # a summary row whose hidden part contains home


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


EXIT_CLASSES = {"HS": "high", "LS": "low", "NS": "null"}
MIN_FOCUS_ROWS = 5   # home, the folded way, the character and one merged summary beside each


def focus_rows(rows: list[ChainRow], store: Store, universe: Universe, current: int | None = None, radius: int = 2,
               max_rows: int = 15, home_id: int | None = None) -> list[ChainRow]:
    """Fold the full tree rows into the part around the character (the overlay's view of a big chain).

    Shown: systems within ``radius`` jumps of the character (0 = no limit), the root (home) and the
    path to it, and everything below the character's row (the way on, shown beyond the radius). Systems
    folded out of the path to home become one ``"path"`` row; every other hidden branch becomes one
    ``"branch"`` summary row. If more than ``max_rows`` would show, the farthest rows are folded first,
    and then the hidden branches of a system merge into one ``"more"`` row.

    ``exits`` holds the k-space classes (``"high"``, ``"low"``, ``"null"``) of the holes into k-space on
    a wormhole row, or anywhere in what a summary row hides. Holes into w-space never count.

    Args:
        rows: The full rows of :func:`resolve_chain` (depth first; give it a large ``max_depth``).
        store: The map (for the connections).
        universe: Static data (for the security class).
        current: The character's system; None takes the row flagged ``current``.
        radius: Jumps from the character that are shown; 0 shows the whole chain (up to ``max_rows``).
        max_rows: The most rows to return, at least :data:`MIN_FOCUS_ROWS`.
        home_id: Home, for ``leads_home``.

    Returns:
        New rows with fresh tree prefixes and depths.
    """
    require_integer(current, "current", optional=True)
    require_integer(radius, "radius", minimum=0)
    require_integer(max_rows, "max_rows", minimum=MIN_FOCUS_ROWS)
    require_integer(home_id, "home_id", optional=True)
    here = next((index for index, row in enumerate(rows)
                 if (row.current if current is None else row.system_id == current)), None)
    if here is None:
        return list(rows)
    adjacency = wormhole_adjacency(store)
    parent: dict[int, int | None] = {}
    children: dict[int, list[int]] = {index: [] for index in range(len(rows))}
    stack: list[int] = []
    for index, row in enumerate(rows):
        del stack[row.depth:]
        parent[index] = stack[-1] if stack else None
        if stack:
            children[stack[-1]].append(index)
        stack.append(index)

    def exit_class(system_id: int) -> str | None:
        system = universe.by_id(system_id)
        return EXIT_CLASSES.get(system.system_class) if system else None

    def own_exits(index: int) -> frozenset:
        """The k-space class a row is, or (for a wormhole row) the classes it is connected to."""
        system_id = rows[index].system_id
        own = exit_class(system_id)
        if own:
            return frozenset({own})
        return frozenset({exit_class(other) for other in adjacency.get(system_id, ())} - {None})

    def subtree(index: int) -> list[int]:
        found, todo = [], [index]
        while todo:
            node = todo.pop()
            found.append(node)
            todo.extend(children[node])
        return found

    def exits_of(indexes: list[int]) -> frozenset:
        return frozenset().union(*(own_exits(index) for index in indexes))

    def home_in(indexes: list[int]) -> bool:
        return home_id is not None and any(rows[index].system_id == home_id for index in indexes)

    ancestors = []  # from the parent of the character's row up to the root
    node = parent[here]
    while node is not None:
        ancestors.append(node)
        node = parent[node]
    top = len(ancestors)  # position of the root on the path (the character is position 0)
    position = {here: 0, **{node: place + 1 for place, node in enumerate(ancestors)}}
    root = ancestors[-1] if ancestors else here
    way_on = set(subtree(here))

    def reach(index: int) -> tuple[int, int]:
        """The position where the way from ``index`` joins the path, and the jumps from the character."""
        steps, node = 0, index
        while node not in position:
            up = parent[node]
            assert up is not None   # the root is on the path to here, so the climb stops there
            node, steps = up, steps + 1
        return position[node], steps + position[node]

    reached = {index: reach(index) for index in range(len(rows))}

    def build(window: int, show_single: bool) -> list[ChainRow] | None:
        """Rows when ``window`` ancestors are shown, or None if the cap can't be met that way."""
        window = min(window, top)
        if show_single and top - 1 - window == 1:
            window += 1  # folding a single system saves nothing
        folded = [node for node in ancestors if window < position[node] < top]
        keep = {index for index in range(len(rows))
                if (reached[index][0] <= window or reached[index][0] == top)
                and (radius == 0 or reached[index][1] <= radius or index in way_on)}
        protected = {here, root} | {node for node in ancestors if position[node] <= window}
        keep |= protected
        merged: set[int] = set()

        def hidden_of(node: int) -> list[int]:
            return [child for child in children[node] if child not in keep and child not in folded]

        def cost() -> int:
            total = len(keep) + (1 if folded else 0)
            for node in keep:
                hidden = hidden_of(node)
                total += 1 if node in merged and hidden else len(hidden)
            return total

        while cost() > max_rows:
            leaves = [node for node in keep - protected if not any(child in keep for child in children[node])]
            if not leaves:
                return None
            farthest = max(leaves, key=lambda node: (reached[node][1], node))
            keep.discard(farthest)
            up = parent[farthest]
            assert up is not None   # only the root has no parent, and the root is protected
            merged.add(up)
        resume = ancestors[window - 1] if window else here
        return render(keep, folded, merged, hidden_of, resume)

    def render(keep: set[int], folded: list[int], merged: set[int], hidden_of, resume: int) -> list[ChainRow]:
        """Lay the kept rows and the summary rows out with fresh prefixes."""
        out: list[ChainRow] = []
        resumed = set(subtree(resume))

        def entries(node: int) -> list[tuple]:
            """The output children of ``node``: kept rows, the path row, summaries."""
            hidden = hidden_of(node)
            collapse = node in merged and len(hidden) > 1
            found: list[tuple] = []
            for child in children[node]:
                if child in keep:
                    found.append(("row", child, []))
                elif child in folded:
                    found.append(("path", child, [each for each in subtree(child) if each not in resumed]))
                elif not collapse:
                    found.append(("branch", child, subtree(child)))
            if collapse:
                found.append(("more", node, [each for child in hidden for each in subtree(child)]))
            return found

        def walk(item: tuple, depth: int, lead: str, last: bool) -> None:
            kind, node, members = item
            branch = "" if depth == 0 else lead + ("└ " if last else "├ ")
            below: list[tuple] = []
            if kind == "row":
                source = rows[node]
                out.append(ChainRow(source.system_id, depth, branch, current=source.current,
                                    jumps_from=source.jumps_from, entrance_id=source.entrance_id, virtual=source.virtual,
                                    exits=frozenset() if exit_class(source.system_id) else own_exits(node)))
                below = entries(node)
            else:
                count = len(folded) if kind == "path" else len(members)
                out.append(ChainRow(rows[node].system_id, depth, branch, kind=kind, summary_count=count,
                                    exits=exits_of(members), leads_home=home_in(members)))
                if kind == "path":
                    below = [("row", resume, [])]
            next_lead = "" if depth == 0 else lead + ("  " if last else "│ ")
            for place, child in enumerate(below):
                walk(child, depth + 1, next_lead, place == len(below) - 1)

        walk(("row", root, []), 0, "", True)
        return out

    window = top if radius == 0 else radius
    while window >= 0:
        result = build(window, True) or build(window, False)
        if result is not None:
            return result
        window -= 1
    return list(rows)[:max_rows]

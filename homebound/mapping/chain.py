"""Which chain is a character in, and how does it look as a tree?

* Character in a mapped system that has wormhole connections -> that connected component.
* Character in k-space off the map -> BFS stargates up to `radius` jumps; the nearest mapped
  system of a component is the entrance and the character counts as in that chain.
"""
from collections import deque
from dataclasses import dataclass, field

from ..mapdb.store import Store
from ..universe.db import Universe


@dataclass
class ChainRow:
    system_id: int
    depth: int
    prefix: str = ""             # box drawing tree prefix (mono)
    current: bool = False
    jumps_from: int | None = None  # set for the virtual k-space row: jumps from the entrance
    entrance_id: int | None = None
    virtual: bool = False        # character's k-space system, not on the map


@dataclass
class Chain:
    root_id: int
    members: set[int]
    rows: list[ChainRow] = field(default_factory=list)
    current_id: int | None = None
    entrance_id: int | None = None  # set when the character is out in k-space
    jumps: int = 0


def wh_adjacency(store: Store) -> dict[int, set[int]]:
    adj: dict[int, set[int]] = {}
    for c in store.connections():
        adj.setdefault(c.a, set()).add(c.b)
        adj.setdefault(c.b, set()).add(c.a)
    return adj


def component(adj: dict[int, set[int]], start: int) -> set[int]:
    seen, q = {start}, deque([start])
    while q:
        for n in adj.get(q.popleft(), ()):
            if n not in seen:
                seen.add(n)
                q.append(n)
    return seen


def resolve(store: Store, uni: Universe, system_id: int | None, home_id: int | None = None,
            radius: int = 5, max_depth: int = 4) -> Chain | None:
    if system_id is None:
        return None
    adj = wh_adjacency(store)
    entrance, jumps = None, 0
    if system_id in adj:
        members = component(adj, system_id)
    else:
        s = uni.by_id(system_id)
        if not s or not s.kspace:
            return None
        dist = uni.gate_distances(system_id, radius)
        near = sorted((d, sid) for sid, d in dist.items() if sid in adj)
        if not near:
            return None
        jumps, entrance = near[0]
        members = component(adj, entrance)

    root = home_id if home_id in members else _tree_root(store, members, entrance or system_id)
    chain = Chain(root, members, current_id=system_id, entrance_id=entrance, jumps=jumps)
    chain.rows = _rows(store, adj, members, root, system_id, entrance, jumps, max_depth)
    return chain


def _tree_root(store: Store, members: set[int], fallback: int) -> int:
    cur, seen = store.system(fallback), set()
    while cur and cur.parent_id in members and cur.id not in seen:
        seen.add(cur.id)
        cur = store.system(cur.parent_id)
    return cur.id if cur else fallback


def _rows(store, adj, members, root, current, entrance, jumps, max_depth) -> list[ChainRow]:
    # children: prefer the naming tree (parent_id), fall back to BFS over connections
    kids: dict[int, list[int]] = {}
    placed = {root}
    by_parent: dict[int, list] = {}
    for s in store.systems():
        if s.id in members and s.parent_id in members:
            by_parent.setdefault(s.parent_id, []).append(s)
    q = deque([root])
    while q:
        p = q.popleft()
        ordered = [s.id for s in sorted(by_parent.get(p, []), key=lambda s: (s.hole_index or 99, s.id))]
        ordered += sorted(n for n in adj.get(p, ()) if n not in ordered)
        for n in ordered:
            if n not in placed:
                placed.add(n)
                kids.setdefault(p, []).append(n)
                q.append(n)
    if entrance is not None:
        kids.setdefault(entrance, []).append(-current)  # virtual row marker

    rows: list[ChainRow] = []

    def walk(sid: int, depth: int, lead: str, last: bool):
        virtual = sid < 0
        real = -sid if virtual else sid
        branch = "" if depth == 0 else lead + ("└ " if last else "├ ")
        if depth > max_depth:
            branch = lead + "… "
        rows.append(ChainRow(real, depth, branch, current=(real == current), virtual=virtual,
                             jumps_from=jumps if virtual else None, entrance_id=entrance if virtual else None))
        ch = kids.get(sid, [])
        nxt = "" if depth == 0 else lead + ("  " if last else "│ ")
        if depth >= max_depth:
            nxt = lead
        for i, c in enumerate(ch):
            walk(c, depth + 1, nxt, i == len(ch) - 1)

    walk(root, 0, "", True)
    return rows

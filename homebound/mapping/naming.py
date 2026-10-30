"""Naming schemes.

Every mapped system has a parent (towards home or the chain root) and a hole index:
the index of the hole in the parent that leads to it. The static is always 1 (multi-static
systems use 1..n for their statics), other holes count up from there.

Names are derived from the path of hole indices from home, so restructuring the chain
renames the subtree automatically; a custom name set by the user always wins.

Bookmark labels: the hole with index h in system S is "S-h"; the hole leading back
towards home is "S-0".
"""
from dataclasses import dataclass

from ..mapdb.store import MapSystem, Store
from ..universe.db import Universe

HOME_NAME = "Home"


def _digit(h: int) -> str:
    return str(h) if h < 10 else chr(ord("a") + h - 10)


def _letter(i: int, upper: bool = True) -> str:
    """1 -> A, 26 -> Z, 27 -> AA"""
    s = ""
    while i > 0:
        i, r = divmod(i - 1, 26)
        s = chr(ord("A") + r) + s
    return s if upper else s.lower()


@dataclass(frozen=True)
class Scheme:
    key: str
    title: str
    description: str

    def name(self, path: list[int], level_index: int | None) -> str:
        raise NotImplementedError


class NumericPath(Scheme):
    def name(self, path, level_index):
        return "".join(_digit(h) for h in path)


class A2a(Scheme):
    def name(self, path, level_index):
        return f"{_letter(path[0])}{len(path)}{_letter((level_index or 0) + 1, upper=False)}"


class LetterNumeric(Scheme):
    def name(self, path, level_index):
        return _letter(path[0]) + "".join(_digit(h) for h in path[1:])


SCHEMES: dict[str, Scheme] = {
    "numeric": NumericPath("numeric", "Numeric path",
                           "Each hop adds the hole number; the static is always 1. Home → 1 → 12 → 123."),
    "a2a": A2a("a2a", "Chain · depth · position",
               "Chain letter from home (A = static), jumps deep, then the system's letter at that depth. A1a, A2a, A2b, B1a."),
    "letter_numeric": LetterNumeric("letter_numeric", "Letter-led numeric",
                                    "Like numeric, but the first hop from home is a capital letter. A → A2 → A23."),
}


def scheme(key: str) -> Scheme:
    return SCHEMES.get(key, SCHEMES["numeric"])


# ---------------------------------------------------------------- tree helpers

def path_of(store: Store, sid: int) -> tuple[int, list[int]]:
    """(root id, hole indices from root to sid)."""
    path, seen, cur = [], set(), store.system(sid)
    while cur and cur.parent_id is not None and cur.id not in seen:
        seen.add(cur.id)
        path.append(cur.hole_index or 0)
        cur = store.system(cur.parent_id)
    return (cur.id if cur else sid), path[::-1]


def static_slots(uni: Universe, sid: int) -> int:
    s = uni.by_id(sid)
    if not s or not s.wspace:
        return 0
    return max(1, len(s.statics))


def used_hole_indices(store: Store, parent_id: int) -> set[int]:
    used = {c.hole_index for c in store.children(parent_id) if c.hole_index}
    used |= {s.hole_index for s in store.signatures(parent_id) if s.hole_index}
    return used


def static_slot_for(uni: Universe, parent_id: int, child_cls: str | None) -> int:
    """Static slot (1-based) whose destination matches the child class; 1 if unknown."""
    for i, t in enumerate(uni.static_types(parent_id), start=1):
        if child_cls and child_cls in t.dest:
            return i
    return 1


def next_hole_index(store: Store, uni: Universe, parent_id: int, is_static: bool,
                    child_cls: str | None = None) -> int:
    used = used_hole_indices(store, parent_id)
    n = static_slots(uni, parent_id)
    if is_static and n:
        slot = static_slot_for(uni, parent_id, child_cls)
        return slot
    i = n + 1
    while i in used:
        i += 1
    return i


def next_level_index(store: Store, root: int, path_prefix: list[int]) -> int:
    """Number of systems already at this depth in this chain (same root, same first hop)."""
    depth, first = len(path_prefix), path_prefix[0] if path_prefix else None
    n = 0
    for s in store.systems():
        r, p = path_of(store, s.id)
        if r == root and len(p) == depth and p and p[0] == first:
            n += 1
    return n


# ---------------------------------------------------------------- display

@dataclass
class Namer:
    store: Store
    uni: Universe
    scheme_key: str = "numeric"
    home_id: int | None = None

    @property
    def scheme(self) -> Scheme:
        return scheme(self.scheme_key)

    def label(self, sid: int) -> str:
        """Scheme label without the k-space real-name suffix (used for bookmarks)."""
        ms = self.store.system(sid)
        if ms and ms.custom_name:
            return ms.custom_name
        root, path = path_of(self.store, sid)
        if not path:
            if root == self.home_id:
                return HOME_NAME
            s = self.uni.by_id(sid)
            return s.name if s else str(sid)
        label = self.scheme.name(path, ms.level_index if ms else None)
        return label if root == self.home_id or self.home_id is None else "~" + label

    def display(self, sid: int) -> str:
        """Custom name / scheme label, with the real name for k-space: '13 (Jita)'."""
        lbl = self.label(sid)
        s = self.uni.by_id(sid)
        if s and s.kspace and lbl != s.name and not (self.store.system(sid) or MapSystem(sid)).custom_name:
            return f"{lbl} ({s.name})"
        return lbl

    def preview(self, parent_id: int, hole_index: int, level_index: int | None = None) -> str:
        """Name a new child of parent_id would get with this hole index."""
        root, path = path_of(self.store, parent_id)
        path = path + [hole_index]
        if level_index is None:
            level_index = next_level_index(self.store, root, path)
        label = self.scheme.name(path, level_index)
        return label if root == self.home_id or self.home_id is None else "~" + label

    def bookmark(self, sid: int, hole_index: int) -> str:
        return f"{self.label(sid)}-{hole_index}"

    def bookmark_home(self, sid: int) -> str:
        return f"{self.label(sid)}-0"


# ---------------------------------------------------------------- static question

@dataclass
class StaticGuess:
    applies: bool    # False when the parent has no static (k-space, Thera) -> no question
    guess: bool
    certain: bool
    reason: str


def guess_static(store: Store, uni: Universe, parent_id: int, child_id: int,
                 sig_type: str | None = None) -> StaticGuess:
    parent, child = uni.by_id(parent_id), uni.by_id(child_id)
    if not parent or not child or not static_slots(uni, parent_id):
        return StaticGuess(False, False, True, "No static in the parent system")
    types = uni.static_types(parent_id)
    if sig_type and sig_type.upper() != "K162":
        is_static = any(t.name == sig_type.upper() for t in types)
        return StaticGuess(True, is_static, True,
                           f"Sig type {sig_type.upper()} {'is' if is_static else 'is not'} "
                           f"{parent.name}'s static")
    dests = [d for t in types for d in t.dest]
    names = ", ".join(f"{t.name} → {'/'.join(t.dest)}" for t in types) or "unknown"
    if child.cls not in dests:
        return StaticGuess(True, False, True, f"Static {names}; this is {child.cls}")
    slot = static_slot_for(uni, parent_id, child.cls)
    taken = [c for c in store.children(parent_id) if c.hole_index == slot and c.id != child_id]
    if taken:
        return StaticGuess(True, False, False, f"Static {names} is already mapped")
    # Certain only if the scan backs it up: exactly one unlinked WH sig left and it is the static type.
    static_names = {t.name for t in types}
    unlinked = [s for s in store.signatures(parent_id) if s.grp == "wormhole" and not s.hole_index]
    certain = len(unlinked) == 1 and (unlinked[0].type or "").upper() in static_names
    return StaticGuess(True, True, certain, f"Static {names} matches this {child.cls}")

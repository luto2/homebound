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
from ..validation import require_integer, require_type

HOME_NAME = "Home"

# Chain root letters, after the EVE University WHC System Naming Scheme
# (wiki.eveuniversity.org/WHC_Mapping_Scheme): A and B are kept for the static chains, all other
# chains take Z Y W U S R Q O M K I E D in that order. Never used: C (wormhole class), F (frigate
# hole), G (sounds like B/E), H/L/N (high/low/null-sec suffix), J (J-names), P and V (sound like B),
# T (Pochven), X (sounds like "Eggs").
STATIC_LETTERS = "AB"
ROOT_LETTERS = "ZYWUSRQOMKIED"


def hole_digit(hole_index: int) -> str:
    """Return the name part for a hole index: 0–9 as digits, 10–35 as a–z, 36 and up as ``(36)``.

    Past ``z`` the index is written as its decimal number in parentheses. Capital letters would
    clash with the chain letters of the letter-led scheme (Z followed by hole Z reads as chain ZZ),
    and more small letters would make paths ambiguous (``aa`` could be 10, 10). A bracketed number
    can't be mistaken for either and has no upper limit.

    Args:
        hole_index: The hole index, 0 or more.

    Returns:
        One character up to 35, otherwise the bracketed number.
    """
    require_integer(hole_index, "hole_index", minimum=0)
    if hole_index < 10:
        return str(hole_index)
    if hole_index < 36:
        return chr(ord("a") + hole_index - 10)
    return f"({hole_index})"


def chain_letter(hole_index: int, statics: int = 1) -> str:
    """Return the letter of the chain behind home's hole ``hole_index``.

    Args:
        hole_index: The hole in home; 1..statics are the statics.
        statics: How many statics home has.

    Returns:
        A or B for a static chain, otherwise Z … D, then ZZ, YY, …; ``"?"`` for an index below 1.
    """
    require_integer(hole_index, "hole_index")
    require_integer(statics, "statics", minimum=0)
    if hole_index <= 0:
        return "?"
    static_chains = min(statics, len(STATIC_LETTERS))
    if hole_index <= static_chains:
        return STATIC_LETTERS[hole_index - 1]
    position = hole_index - static_chains - 1  # a 3rd static (none exist today) would get Z
    return ROOT_LETTERS[position % len(ROOT_LETTERS)] * (position // len(ROOT_LETTERS) + 1)


def letters_for_number(number: int, upper: bool = True) -> str:
    """Return spreadsheet-style letters for a number: 1 → A, 26 → Z, 27 → AA.

    Args:
        number: 1 or more; 0 gives an empty string.
        upper: Capital letters if True, small letters otherwise.

    Returns:
        The letters.
    """
    require_integer(number, "number", minimum=0)
    letters = ""
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters if upper else letters.lower()


@dataclass(frozen=True)
class Scheme:
    """A naming scheme: turns a path of hole indices into a system name."""

    key: str
    title: str
    description: str

    def name(self, path: list[int], level_index: int | None, statics: int = 1) -> str:
        """Return the name of the system at the end of ``path``.

        Args:
            path: Hole indices from the chain root, at least one.
            level_index: Position among the systems at this depth in this chain (0-based).
            statics: Number of static slots in the root.

        Returns:
            The name.
        """
        raise NotImplementedError

    @staticmethod
    def _check(path: list[int], level_index: int | None, statics: int) -> None:
        """Validate the arguments of :meth:`name`.

        Raises:
            TypeError: If an argument has the wrong type.
            ValueError: If ``path`` is empty or has a negative index.
        """
        require_type(path, list, "path")
        if not path:
            raise ValueError("path must have at least one hole index")
        for hole_index in path:
            require_integer(hole_index, "path item", minimum=0)
        require_integer(level_index, "level_index", minimum=0, optional=True)
        require_integer(statics, "statics", minimum=0)


class NumericPathScheme(Scheme):
    """Home → 1 → 12 → 123: each hop adds its hole digit."""

    def name(self, path: list[int], level_index: int | None, statics: int = 1) -> str:
        """Return the hole digits of the path joined together."""
        self._check(path, level_index, statics)
        return "".join(hole_digit(hole_index) for hole_index in path)


class ChainDepthPositionScheme(Scheme):
    """A1a, A2a, A2b, Z1a: chain letter, depth, then the position at that depth."""

    def name(self, path: list[int], level_index: int | None, statics: int = 1) -> str:
        """Return chain letter + depth + position letter."""
        self._check(path, level_index, statics)
        return f"{chain_letter(path[0], statics)}{len(path)}{letters_for_number((level_index or 0) + 1, upper=False)}"


class LetterNumericScheme(Scheme):
    """A → A2 → A23, Z: like numeric, but the first hop is the chain letter."""

    def name(self, path: list[int], level_index: int | None, statics: int = 1) -> str:
        """Return the chain letter followed by the remaining hole digits."""
        self._check(path, level_index, statics)
        return chain_letter(path[0], statics) + "".join(hole_digit(hole_index) for hole_index in path[1:])


SCHEMES: dict[str, Scheme] = {
    "numeric": NumericPathScheme(
        "numeric", "Numeric path",
        "Each hop adds the hole number; the static is always 1. Home → 1 → 12 → 123."),
    "a2a": ChainDepthPositionScheme(
        "a2a", "Chain · depth · position",
        "Chain letter from home (A = static, B = 2nd static, then Z, Y, W … as on EVE Uni; no C), "
        "jumps deep, then the system's letter at that depth. A1a, A2a, A2b, Z1a."),
    "letter_numeric": LetterNumericScheme(
        "letter_numeric", "Letter-led numeric",
        "Like numeric, but the first hop from home is a chain letter (same letters as above). A → A2 → A23, Z."),
}


def scheme_for_key(key: str) -> Scheme:
    """Return the scheme for a settings key; unknown keys fall back to numeric.

    Args:
        key: ``"numeric"``, ``"a2a"`` or ``"letter_numeric"``.

    Returns:
        The scheme.
    """
    require_type(key, str, "key")
    return SCHEMES.get(key, SCHEMES["numeric"])


# ---------------------------------------------------------------- tree helpers

def hole_path(store: Store, system_id: int) -> tuple[int, list[int]]:
    """Follow the parents of a system up to its chain root.

    Args:
        store: The map.
        system_id: The system to start from.

    Returns:
        ``(root ID, hole indices from the root down to system_id)``. A system without a parent is its
        own root with an empty path. A parent loop stops at the first repeat.
    """
    path, seen, current = [], set(), store.system(system_id)
    while current and current.parent_id is not None and current.id not in seen:
        seen.add(current.id)
        path.append(current.hole_index or 0)
        current = store.system(current.parent_id)
    return (current.id if current else system_id), path[::-1]


def static_slot_count(universe: Universe, system_id: int) -> int:
    """Return how many hole indices a system reserves for statics: 0 in k-space, else at least 1."""
    system = universe.by_id(system_id)
    if not system or not system.wormhole_space:
        return 0
    return max(1, len(system.statics))


def used_hole_indices(store: Store, parent_id: int) -> set[int]:
    """Return the hole indices already taken in a system, by children or by signatures."""
    used = {child.hole_index for child in store.children(parent_id) if child.hole_index}
    used |= {signature.hole_index for signature in store.signatures(parent_id) if signature.hole_index}
    return used


def static_slot_for_class(universe: Universe, parent_id: int, child_class: str | None) -> int:
    """Return the static slot (1-based) whose destination matches the child's class; 1 if none does."""
    require_type(child_class, (str, type(None)), "child_class")
    for slot, static_type in enumerate(universe.static_types(parent_id), start=1):
        if child_class and child_class in static_type.destinations:
            return slot
    return 1


def next_hole_index(store: Store, universe: Universe, parent_id: int, is_static: bool,
                    child_class: str | None = None) -> int:
    """Return the hole index a new child of ``parent_id`` gets.

    Args:
        store: The map.
        universe: Static data.
        parent_id: The system the hole is in.
        is_static: Whether the hole is a static.
        child_class: The class of the system behind it, to pick the right static slot.

    Returns:
        The static slot for a static, else the lowest free index after the static slots.
    """
    require_type(is_static, bool, "is_static")
    statics = static_slot_count(universe, parent_id)
    if is_static and statics:
        return static_slot_for_class(universe, parent_id, child_class)
    used = used_hole_indices(store, parent_id)
    index = statics + 1
    while index in used:
        index += 1
    return index


def next_level_index(store: Store, root_id: int, path_prefix: list[int]) -> int:
    """Count the systems already at this depth in this chain (same root, same first hop).

    Args:
        store: The map.
        root_id: The chain root.
        path_prefix: The hole path of the new system.

    Returns:
        The 0-based position the new system takes at its depth.
    """
    require_type(path_prefix, list, "path_prefix")
    if not path_prefix:
        return 0
    # Walk down from the root instead of walking up from every system: only the subtree behind
    # the first hop is visited, one level at a time.
    level = [child for child in store.children(root_id) if (child.hole_index or 0) == path_prefix[0]]
    seen = {root_id} | {system.id for system in level}
    for _ in range(len(path_prefix) - 1):
        next_level = []
        for system in level:
            for child in store.children(system.id):
                if child.id not in seen:
                    seen.add(child.id)
                    next_level.append(child)
        level = next_level
    return len(level)


# ---------------------------------------------------------------- display

@dataclass
class Namer:
    """Names systems of one map under the active scheme."""

    store: Store
    universe: Universe
    scheme_key: str = "numeric"
    home_id: int | None = None

    @property
    def scheme(self) -> Scheme:
        """The active naming scheme."""
        return scheme_for_key(self.scheme_key)

    def label(self, system_id: int) -> str:
        """Return the custom name or scheme label, without the k-space real name (used for bookmarks)."""
        mapped = self.store.system(system_id)
        if mapped and mapped.custom_name:
            return mapped.custom_name
        return self.scheme_label(system_id)

    def scheme_label(self, system_id: int) -> str:
        """Return the name the naming scheme gives, ignoring a custom name.

        Home is "Home"; a root other than home keeps its real name; systems in a chain that doesn't
        start at home get a ``~`` prefix.
        """
        mapped = self.store.system(system_id)
        root, path = hole_path(self.store, system_id)
        if not path:
            if root == self.home_id:
                return HOME_NAME
            system = self.universe.by_id(system_id)
            return system.name if system else str(system_id)
        label = self.scheme.name(path, mapped.level_index if mapped else None,
                                 static_slot_count(self.universe, root))
        return label if root == self.home_id or self.home_id is None else "~" + label

    def display(self, system_id: int) -> str:
        """Return the custom name or scheme label, with the real name for k-space: '13 (Jita)'."""
        label = self.label(system_id)
        system = self.universe.by_id(system_id)
        mapped = self.store.system(system_id) or MapSystem(system_id)
        if system and system.known_space and label != system.name and not mapped.custom_name:
            return f"{label} ({system.name})"
        return label

    def preview(self, parent_id: int, hole_index: int, level_index: int | None = None) -> str:
        """Return the name a new child of ``parent_id`` would get behind this hole.

        Args:
            parent_id: The parent system.
            hole_index: The hole in the parent.
            level_index: The position at that depth, or None to count it.

        Returns:
            The name under the active scheme.
        """
        require_integer(hole_index, "hole_index", minimum=0)
        root, path = hole_path(self.store, parent_id)
        path = path + [hole_index]
        if level_index is None:
            level_index = next_level_index(self.store, root, path)
        label = self.scheme.name(path, level_index, static_slot_count(self.universe, root))
        return label if root == self.home_id or self.home_id is None else "~" + label

    def bookmark(self, system_id: int, hole_index: int) -> str:
        """Return the bookmark label of a hole: ``<label>-<hole index>``."""
        require_integer(hole_index, "hole_index", minimum=0)
        return f"{self.label(system_id)}-{hole_index}"

    def bookmark_home(self, system_id: int) -> str:
        """Return the bookmark label of the hole leading back home: ``<label>-0``."""
        return f"{self.label(system_id)}-0"


# ---------------------------------------------------------------- static question

@dataclass
class StaticGuess:
    """The best guess whether a new system is behind its parent's static."""

    applies: bool    # False when no static can lead here (k-space parent, or wrong class) -> no question
    guess: bool
    certain: bool
    reason: str


def guess_static(store: Store, universe: Universe, parent_id: int, child_id: int,
                 signature_type: str | None = None, strict: bool = True) -> StaticGuess:
    """Guess whether ``child_id`` was reached through the static of ``parent_id``.

    Args:
        store: The map.
        universe: Static data.
        parent_id: The system the hole is in.
        child_id: The system behind it.
        signature_type: The type of the signature taken, if known; it decides the answer.
        strict: A child whose class no static leads to can't be the static, so don't ask.
            False keeps the question (the user picked "Change static / hole" by hand).

    Returns:
        The guess, how sure it is, and why.
    """
    require_type(signature_type, (str, type(None)), "signature_type")
    parent, child = universe.by_id(parent_id), universe.by_id(child_id)
    if not parent or not child or not static_slot_count(universe, parent_id):
        return StaticGuess(False, False, True, "No static in the parent system")
    types = universe.static_types(parent_id)
    if signature_type and signature_type.upper() != "K162":
        is_static = any(wormhole_type.name == signature_type.upper() for wormhole_type in types)
        return StaticGuess(True, is_static, True,
                           f"Sig type {signature_type.upper()} {'is' if is_static else 'is not'} "
                           f"{parent.name}'s static")
    destinations = [destination for wormhole_type in types for destination in wormhole_type.destinations]
    names = ", ".join(f"{wormhole_type.name} → {'/'.join(wormhole_type.destinations)}" for wormhole_type in types) or "unknown"
    if child.system_class not in destinations:
        return StaticGuess(not strict, False, True, f"Static {names}; this is {child.system_class}, so not the static")
    slot = static_slot_for_class(universe, parent_id, child.system_class)
    taken = [sibling for sibling in store.children(parent_id) if sibling.hole_index == slot and sibling.id != child_id]
    if taken:
        return StaticGuess(True, False, False, f"Static {names} is already mapped")
    # Certain only if the scan backs it up: exactly one unlinked WH sig left and it is the static type.
    static_names = {wormhole_type.name for wormhole_type in types}
    unlinked = [signature for signature in store.signatures(parent_id) if signature.group == "wormhole" and not signature.hole_index]
    certain = len(unlinked) == 1 and (unlinked[0].type or "").upper() in static_names
    return StaticGuess(True, True, certain, f"Static {names} matches this {child.system_class}")

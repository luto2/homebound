"""Naming schemes, hole numbers and the static question.

Every wormhole signature gets a bookmark number the moment it is pasted: the next one of its
system, counting up from 1 in the order the holes are found and never reused. No number is kept
for the static; whether a hole is the static is a flag on the system behind it (``static``).

Every mapped system has a parent (towards home or the chain root) and a hole index: the number
of the hole in the parent that leads to it, which is the number of the sig it came through. Its
name comes from the path of hole numbers from home under the map's scheme and is stored when the
system is attached (``label``), so it never changes on its own; a custom name set by the user
always wins.

Bookmark labels: the hole with number h in system S is "S-h"; the hole leading back towards
home is "S-0".
"""
import re
from dataclasses import dataclass

from ..mapdb.store import LEGACY_NAMES_KEY, NAMES_BACKFILL_KEY, Change, MapSystem, Signature, Store
from ..universe.db import Universe
from ..validation import require_choice, require_integer, require_text, require_type

HOME_NAME = "Home"

# Chain root letters, after the EVE University WHC System Naming Scheme
# (wiki.eveuniversity.org/WHC_Mapping_Scheme): home's holes take A and B, then Z Y W U S R Q O M K I
# E D, in the order they were found (EVE Uni keeps A and B for the statics; here the static is a
# badge and keeps the letter of its number). Never used: C (wormhole class), F (frigate
# hole), G (sounds like B/E), H/L/N (high/low/null-sec suffix), J (J-names), P and V (sound like B),
# T (Pochven), X (sounds like "Eggs").
STATIC_LETTERS = "AB"
ROOT_LETTERS = "ZYWUSRQOMKIED"
# A stored chain·depth·position label (``A2c``, ``~Z1b``): its trailing letters are the position.
POSITION_LABEL = re.compile(r"^~?[A-Z?]+\d+([a-z]+)$")


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


def chain_letter(hole_index: int, statics: int = len(STATIC_LETTERS)) -> str:
    """Return the letter of the chain behind home's hole ``hole_index``.

    Args:
        hole_index: The hole in home: 1 → A, 2 → B, 3 → Z …
        statics: Only for names from before stored names (the migration): how many of home's
            first holes were static slots and took A and B; the default gives the found order.

    Returns:
        A, B, then Z … D, then ZZ, YY, …; ``"?"`` for an index below 1.
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


def number_for_letters(letters: str) -> int:
    """Return the number of spreadsheet-style letters, the inverse of :func:`letters_for_number`: a → 1, aa → 27."""
    require_type(letters, str, "letters")
    number = 0
    for letter in letters.upper():
        if not "A" <= letter <= "Z":
            raise ValueError(f"not a letter: {letter!r}")
        number = number * 26 + ord(letter) - ord("A") + 1
    return number


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

    def name(self, path: list[int], level_index: int | None, statics: int = len(STATIC_LETTERS)) -> str:
        """Return the name of the system at the end of ``path``.

        Args:
            path: Hole indices from the chain root, at least one.
            level_index: Position among the systems at this depth in this chain (0-based).
            statics: See :func:`chain_letter`; only the migration of old maps passes it.

        Returns:
            The name.
        """
        raise NotImplementedError

    def place(self, label: str) -> str:
        """Return the part of a label that says where the system is, without what only orders it at its depth.

        Two labels with the same place name the same spot in the chain; re-attaching a system
        there keeps its old label. Only :class:`ChainDepthPositionScheme` has more than its place.
        """
        require_type(label, str, "label")
        return label

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

    def name(self, path: list[int], level_index: int | None, statics: int = len(STATIC_LETTERS)) -> str:
        """Return the hole digits of the path joined together."""
        self._check(path, level_index, statics)
        return "".join(hole_digit(hole_index) for hole_index in path)


class ChainDepthPositionScheme(Scheme):
    """A1a, A2a, A2b, Z1a: chain letter, depth, then the position at that depth."""

    def name(self, path: list[int], level_index: int | None, statics: int = len(STATIC_LETTERS)) -> str:
        """Return chain letter + depth + position letter."""
        self._check(path, level_index, statics)
        return f"{chain_letter(path[0], statics)}{len(path)}{letters_for_number((level_index or 0) + 1, upper=False)}"

    def place(self, label: str) -> str:
        """Return the label without its position letters: ``A2c`` → ``A2``."""
        require_type(label, str, "label")
        match = POSITION_LABEL.match(label)
        return label[:match.start(1)] if match else label


class LetterNumericScheme(Scheme):
    """A → A2 → A23, Z: like numeric, but the first hop is the chain letter."""

    def name(self, path: list[int], level_index: int | None, statics: int = len(STATIC_LETTERS)) -> str:
        """Return the chain letter followed by the remaining hole digits."""
        self._check(path, level_index, statics)
        return chain_letter(path[0], statics) + "".join(hole_digit(hole_index) for hole_index in path[1:])


SCHEMES: dict[str, Scheme] = {
    "numeric": NumericPathScheme(
        "numeric", "Numeric path",
        "Each hop adds the hole's number, counted in the order the holes were found. Home → 1 → 12 → 123."),
    "a2a": ChainDepthPositionScheme(
        "a2a", "Chain · depth · position",
        "Chain letter of home's hole in the order found (A, B, then Z, Y, W … as on EVE Uni; no C), "
        "jumps deep, then the system's letter at that depth. A1a, A2a, A2b, B1a."),
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


def signature_destination(universe: Universe, signature: Signature) -> str | None:
    """Return where a signature leads: the destination set on it, else its known type's first one."""
    require_type(signature, Signature, "signature")
    if signature.destination:
        return signature.destination
    known = universe.known_type(signature.type)
    return known.destinations[0] if known and known.destinations else None


def is_static_signature(universe: Universe, system_id: int, signature: Signature) -> bool:
    """Return whether a signature's type is one of the system's statics (case-insensitive)."""
    require_type(signature, Signature, "signature")
    system = universe.by_id(system_id)
    code = (signature.type or "").strip().upper()
    return bool(system and code and code in {static.upper() for static in system.statics})


def take_hole_number(store: Store, system_id: int) -> int:
    """Return the next hole number of a system: one past the highest it has given out or holds now.

    Numbers count up per system and are never reused, so a bookmark made in-game for a deleted
    sig or hole can't end up on another one. The caller stores the number (on a sig or as a child's
    ``hole_index``); the system's ``next_hole`` follows when the batch ends.

    Args:
        store: The map.
        system_id: The system the hole is in.

    Returns:
        The number, 1 or more.
    """
    require_integer(system_id, "system_id", minimum=1)
    system = store.system(system_id)
    return max([system.next_hole if system else 0, *used_hole_indices(store, system_id)]) + 1


def is_numbered_kind(signature: Signature) -> bool:
    """Return whether a signature gets a bookmark number: a wormhole signature, not an anomaly."""
    require_type(signature, Signature, "signature")
    return signature.group == "wormhole" and signature.kind != "anom"


def number_wormhole_signatures(store: Store, system_id: int) -> list[str]:
    """Give every wormhole signature of a system that has no bookmark number yet the next one.

    Called by every paste and by an edit that makes a sig a wormhole, so a number exists from the
    moment the sig is known to be a hole, in any system, mapped or not. New sigs are numbered in
    signature ID order, so two clients pasting the same scan agree. A sig a connection already
    uses is left alone (its bookmark comes from the system behind it).

    Args:
        store: The map.
        system_id: The system.

    Returns:
        The IDs of the signatures that got a number.
    """
    require_integer(system_id, "system_id", minimum=1)
    with store.batch():
        return [signature.signature_id for signature in store.signatures(system_id)   # in signature ID order
                if number_signature(store, system_id, signature.signature_id)]


def number_signature(store: Store, system_id: int, signature_id: str) -> int | None:
    """Give one signature the next bookmark number if it is a wormhole sig without one.

    Args:
        store: The map.
        system_id: The system the signature is in.
        signature_id: The signature.

    Returns:
        The new number, or None if the sig is gone, not a wormhole, already numbered or used by a
        connection.
    """
    require_integer(system_id, "system_id", minimum=1)
    signature = store.signature(system_id, signature_id)
    if signature is None or not is_numbered_kind(signature) or signature.hole_index:
        return None
    if any(connection.signature_on(system_id) == signature_id for connection in store.connections_of_system(system_id)):
        return None
    number = take_hole_number(store, system_id)
    store.update_signature(system_id, signature_id, hole_index=number)
    return number


def next_level_index(store: Store, root_id: int, path_prefix: list[int], exclude: int | None = None) -> int:
    """Return the position a new system takes at its depth in this chain (same root, same first hop).

    That is one past the systems already there, and past the highest position a stored
    chain·depth·position label there holds: a position is never given twice, so after A2b goes the
    next one is A2d, not a second A2c.

    Args:
        store: The map.
        root_id: The chain root.
        path_prefix: The hole path of the new system.
        exclude: The new system itself, if it is placed already.

    Returns:
        The 0-based position.
    """
    require_type(path_prefix, list, "path_prefix")
    require_integer(exclude, "exclude", optional=True)
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
    members = [system for system in level if system.id != exclude]
    held = [number_for_letters(match.group(1)) for system in members
            if system.label and (match := POSITION_LABEL.match(system.label))]
    return max([len(members), *held])


def level_positions(store: Store) -> dict[int, int]:
    """Return every placed system's position at its depth in its chain, derived from the map.

    Systems with the same root, the same first hop and the same depth are ordered by when they were
    added to the map, then by ID, so every replica of a cloud map names them the same way (nothing
    is stored). The root itself isn't in the result.

    Returns:
        System ID -> 0-based position.
    """
    systems = {system.id: system for system in store.systems()}
    groups: dict[tuple[int, int, int], list] = {}
    for system in systems.values():
        path, seen, current = [], set(), system
        while current is not None and current.parent_id is not None and current.id not in seen:
            seen.add(current.id)
            path.append(current.hole_index or 0)
            current = systems.get(current.parent_id)
        if not path or current is None:
            continue
        groups.setdefault((current.id, path[-1], len(path)), []).append(system)
    positions = {}
    for members in groups.values():
        for position, system in enumerate(sorted(members, key=lambda member: (member.added_at or "", member.id))):
            positions[system.id] = position
    return positions


# ---------------------------------------------------------------- display

@dataclass
class Namer:
    """Names systems of one map under the active scheme."""

    store: Store
    universe: Universe
    scheme_key: str = "numeric"
    home_id: int | None = None
    _levels: tuple[int, int, dict] | None = None   # (id(store), store revision, positions)

    def level_index(self, system_id: int) -> int | None:
        """Return the derived position of a system at its depth (see :func:`level_positions`)."""
        cache = self._levels
        if cache is None or cache[0] != id(self.store) or cache[1] != self.store.revision:
            cache = self._levels = (id(self.store), self.store.revision, level_positions(self.store))
        return cache[2].get(system_id)

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
        """Return the scheme name of a system, ignoring a custom name.

        Home is "Home". Otherwise the name stored when the system was attached (``label``) wins, so a
        name doesn't change when the map around it does; a system without one gets
        :meth:`computed_label`.
        """
        mapped = self.store.system(system_id)
        root, path = hole_path(self.store, system_id)
        if not path and root == self.home_id:
            return HOME_NAME
        if mapped and mapped.label:
            return mapped.label
        return self.computed_label(system_id)

    def computed_label(self, system_id: int) -> str:
        """Return the name the scheme gives a system where it is now, ignoring any stored name.

        A root other than home keeps its real name; systems in a chain that doesn't start at home get
        a ``~`` prefix.
        """
        mapped = self.store.system(system_id)
        root, path = hole_path(self.store, system_id)
        if not path:
            if root == self.home_id:
                return HOME_NAME
            system = self.universe.by_id(system_id)
            return system.name if system else str(system_id)
        label = self.scheme.name(path, self.level_index(system_id) if mapped else None)
        return label if root == self.home_id or self.home_id is None else "~" + label

    def display(self, system_id: int) -> str:
        """Return the custom name or scheme label, with the real name for k-space: '13 Jita'."""
        label = self.label(system_id)
        system = self.universe.by_id(system_id)
        mapped = self.store.system(system_id) or MapSystem(system_id)
        if system and system.known_space and label != system.name and not mapped.custom_name:
            return f"{label} {system.name}"
        return label

    def with_real_name(self, system_id: int) -> str:
        """Return :meth:`display` followed by the real name when it doesn't show it already: 'Home J161940', '13 Jita'."""
        display = self.display(system_id)
        system = self.universe.by_id(system_id)
        if not system or display == system.name or display.endswith(f" {system.name}"):
            return display
        return f"{display} {system.name}"

    def names(self, system_id: int) -> set[str]:
        """Return the names a system answers to: its display name, label and real name (empty ones left out)."""
        system = self.universe.by_id(system_id)
        return {name for name in (self.display(system_id), self.label(system_id), system.name if system else "")
                if name}

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
        label = self.scheme.name(path, level_index)
        return label if root == self.home_id or self.home_id is None else "~" + label

    def attach_label(self, system_id: int) -> str | None:
        """Return the label a system gets now that it is attached where it is, to be stored.

        The position at its depth (``a2a``) is the next one there, so it is computed once here and
        then kept in the label.

        Returns:
            The label, or None for a system without a parent.
        """
        root, path = hole_path(self.store, system_id)
        if not path:
            return None
        label = self.scheme.name(path, next_level_index(self.store, root, path, exclude=system_id))
        return label if root == self.home_id or self.home_id is None else "~" + label

    def bookmark(self, system_id: int, hole_index: int) -> str:
        """Return the bookmark label of a hole: ``<label>-<hole index>``."""
        require_integer(hole_index, "hole_index", minimum=0)
        return f"{self.label(system_id)}-{hole_index}"

    def bookmark_home(self, system_id: int) -> str:
        """Return the bookmark label of the hole leading back home: ``<label>-0``."""
        return f"{self.label(system_id)}-0"


def needs_label(change: Change) -> bool:
    """Return whether a store change left a placed system without a stored label."""
    require_type(change, Change, "change")
    after = change.after or {}
    return change.entity == "system" and after.get("parent_id") is not None and not after.get("label")


def label_unnamed_systems(namer: "Namer", origin: str = "local") -> int:
    """Store a label for every placed system that has none, as if it had just been attached.

    The tracker names what it attaches itself; this catches systems placed some other way: by a
    cloud client from before protocol 2 (it sends no label), or hung under a new home by "Make home".
    Parents go before their children and older systems before newer ones, so ``a2a`` positions
    count on in the order the systems came. One batch.

    Args:
        namer: The namer of the map, with its scheme and home.
        origin: The batch's origin; ``"remote"`` keeps the labels on this computer (not synced, not undone).

    Returns:
        The number of systems labelled.
    """
    require_type(namer, Namer, "namer")
    require_choice(origin, ("local", "remote"), "origin")
    store = namer.store
    unnamed = [system for system in store.systems() if system.parent_id is not None and not system.label]
    unnamed.sort(key=lambda system: (len(hole_path(store, system.id)[1]), system.added_at or "", system.id))
    with store.batch(origin):
        for system in unnamed:
            store.update_system(system.id, label=namer.attach_label(system.id))
    return len(unnamed)


def renames(namer: "Namer", system_ids: list[int] | None = None) -> dict[int, tuple[str, str]]:
    """Return the systems whose stored name differs from what the scheme gives them where they are now.

    Positions at a depth (``a2a``) are counted afresh, in the order the systems were added.

    Args:
        namer: The namer of the map, with the scheme to rename to.
        system_ids: Only these systems; None for every placed one.

    Returns:
        System ID -> (name now, new name), nearest to the root first, then by hole numbers.
    """
    require_type(namer, Namer, "namer")
    require_type(system_ids, (list, type(None)), "system_ids")
    store = namer.store
    wanted = None if system_ids is None else set(system_ids)
    placed = [system for system in store.systems()
              if system.parent_id is not None and (wanted is None or system.id in wanted)]
    placed.sort(key=lambda system: (len(path := hole_path(store, system.id)[1]), path, system.id))
    changes = {}
    for system in placed:
        new = namer.computed_label(system.id)
        old = namer.scheme_label(system.id)
        if new != old:
            changes[system.id] = (old, new)
    return changes


def rename_systems(namer: "Namer", system_ids: list[int] | None = None) -> int:
    """Give systems the names the scheme gives them where they are now: the explicit rename.

    Names never change on their own; this is what a scheme change or "Make home" runs when the user
    says yes. Custom names and hole numbers stay. One batch, so one Undo puts the old names back.

    Args:
        namer: The namer of the map, with the scheme to rename to.
        system_ids: Only these systems; None for every placed one.

    Returns:
        The number of systems renamed.
    """
    changes = renames(namer, system_ids)
    with namer.store.batch():
        for system_id, (_, new) in changes.items():
            namer.store.update_system(system_id, label=new)
    return len(changes)


def rename_system(store: Store, system_id: int, name: str, remember: bool) -> None:
    """Give a system a custom name by hand; one action, so one Undo puts name and alias back.

    The alias is the remembered custom name of the solar system (it outlives the system's place on
    the map and fills ``custom_name`` when it is added again). It never touches ``label``, the stored
    scheme name. Checked, the name becomes the alias; unchecked, the alias is forgotten; an empty
    name goes back to the scheme name and forgets the alias.

    Args:
        store: The map.
        system_id: The system.
        name: The new custom name; empty or blank for the scheme name.
        remember: Whether to keep the name as the system's alias.
    """
    require_integer(system_id, "system_id", minimum=1)
    require_text(name, "name", allow_empty=True)
    require_type(remember, bool, "remember")
    name = name.strip()
    with store.batch():
        store.update_system(system_id, custom_name=name or None)
        if name and remember:
            store.set_alias(system_id, name)
        else:
            store.delete_alias(system_id)


# ---------------------------------------------------------------- maps from before stored names

def legacy_label(store: Store, universe: Universe, scheme_key: str, home_id: int | None, system_id: int,
                 positions: dict[int, int]) -> str | None:
    """Return the name a placed system showed before names were stored, or None for a root.

    Back then the name was worked out from the map every time: the chain letter counted the root's
    static slots (A = static, then Z for a one-static root) and the ``a2a`` position came from the
    order the systems at a depth were added in.

    Args:
        store: The map.
        universe: Static data.
        scheme_key: The map's naming scheme.
        home_id: The map's home.
        system_id: The system.
        positions: :func:`level_positions` of the map.
    """
    root, path = hole_path(store, system_id)
    if not path:
        return None
    label = scheme_for_key(scheme_key).name(path, positions.get(system_id), static_slot_count(universe, root))
    return label if root == home_id or home_id is None else "~" + label


def legacy_signature_numbers(store: Store, universe: Universe, system_id: int) -> dict[str, int]:
    """Return the bookmark numbers unnumbered WH sigs of a system showed before numbers were stored.

    They were a preview: a sig typed as one of the system's statics took that static's slot while
    it was free, the others counted on after the static slots, in signature ID order. Sigs a
    connection uses are left out (their bookmark came from the system behind them).

    Returns:
        Signature ID -> number.
    """
    leads = {connection.signature_on(system_id) for connection in store.connections_of_system(system_id)}
    used = used_hole_indices(store, system_id)
    unnumbered = [signature for signature in store.signatures(system_id)
                  if signature.group == "wormhole" and signature.kind != "anom" and not signature.hole_index
                  and signature.signature_id not in leads]
    numbers: dict[str, int] = {}
    remaining = []
    for signature in unnumbered:
        known = universe.known_type(signature.type)
        slot = None
        if known and is_static_signature(universe, system_id, signature) and static_slot_count(universe, system_id):
            slot = static_slot_for_class(universe, system_id, known.destinations[0] if known.destinations else None)
        if slot is not None and slot not in used:
            numbers[signature.signature_id] = slot
            used.add(slot)
        else:
            remaining.append(signature)
    number = static_slot_count(universe, system_id) + 1
    for signature in remaining:
        while number in used:
            number += 1
        numbers[signature.signature_id] = number
        used.add(number)
    return numbers


def freeze_legacy_names(store: Store, universe: Universe, scheme_key: str, home_id: int | None) -> int:
    """Store the names and numbers an older map file showed, so nothing visible changes on upgrade.

    Placed systems get their shown name as ``label`` and the static flag when they sat in a static
    slot; unnumbered WH sigs get the bookmark number they showed. One batch; the file is marked as
    done (:data:`LEGACY_NAMES_KEY`), so this runs once per file. On a cloud map what it filled in
    goes to the server with the next backfill (:data:`NAMES_BACKFILL_KEY`, ``LocalReplica.backfill``).

    Args:
        store: The map, just opened.
        universe: Static data.
        scheme_key: The map's naming scheme.
        home_id: The map's home.

    Returns:
        The number of systems that got a label.
    """
    require_type(store, Store, "store")
    require_type(scheme_key, str, "scheme_key")
    require_integer(home_id, "home_id", minimum=1, optional=True)
    positions = level_positions(store)
    labelled = 0
    with store.batch():
        systems = store.systems()
        labels = {system.id: legacy_label(store, universe, scheme_key, home_id, system.id, positions)
                  for system in systems if system.parent_id is not None and not system.label}
        for system in systems:
            changes: dict = {}
            if labels.get(system.id):
                changes["label"] = labels[system.id]
                labelled += 1
            if (system.parent_id and system.hole_index
                    and system.hole_index <= static_slot_count(universe, system.parent_id)):
                changes["static"] = 1
            if changes:
                store.update_system(system.id, **changes)
        for system in systems:
            for signature_id, number in legacy_signature_numbers(store, universe, system.id).items():
                store.update_signature(system.id, signature_id, hole_index=number)
        store.put_value(LEGACY_NAMES_KEY, "0")
        store.put_value(NAMES_BACKFILL_KEY, "pending")
    return labelled


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
    taken = [sibling for sibling in store.children(parent_id)
             if sibling.static and sibling.id != child_id and (known := universe.by_id(sibling.id))
             and static_slot_for_class(universe, parent_id, known.system_class) == slot]
    if taken:
        return StaticGuess(True, False, False, f"Static {names} is already mapped")
    # Certain only if the scan backs it up: exactly one unlinked WH sig left and it is the static type.
    static_names = {wormhole_type.name for wormhole_type in types}
    linked = {connection.signature_on(parent_id) for connection in store.connections_of_system(parent_id)}
    unlinked = [signature for signature in store.signatures(parent_id)
                if is_numbered_kind(signature) and signature.signature_id not in linked and signature.state != "missing"]
    certain = len(unlinked) == 1 and (unlinked[0].type or "").upper() in static_names
    return StaticGuess(True, True, certain, f"Static {names} matches this {child.system_class}")


# ---------------------------------------------------------------- return hole

@dataclass(frozen=True)
class ReturnType:
    """The wormhole type of a return hole, worked out from the other side of the connection."""

    code: str | None = None             # the type to set, or None when the other side doesn't settle it
    candidates: tuple[str, ...] = ()    # the types that fit (the type picker lists them first)


def return_type(universe: Universe, near_type: str | None, connection_type: str | None, origin_id: int,
                system_id: int) -> ReturnType:
    """Return the type of the hole in ``system_id`` that leads back to ``origin_id``.

    A hole has a real type on one end and K162 on the other. So a real type on the origin's side makes
    this side K162; a K162 there makes this side the real type: the connection's type if it has one, else
    the types found in this system that lead to the origin's class, which settles it only when exactly
    one does.

    Args:
        universe: Static data.
        near_type: The type of the signature on the origin's side, if there is one.
        connection_type: The type stored on the connection, if any.
        origin_id: The system the hole leads to.
        system_id: The system the hole is in.

    Returns:
        The type to set (None if unknown) and the candidates (empty when the origin's side is unknown or real).
    """
    require_type(near_type, (str, type(None)), "near_type")
    require_type(connection_type, (str, type(None)), "connection_type")
    near = (near_type or "").strip().upper()
    stored = (connection_type or "").strip().upper()
    if near and near != "K162":
        return ReturnType("K162")
    if not near and stored and stored != "K162":
        return ReturnType("K162")
    if not near:
        return ReturnType()
    if stored and stored != "K162":
        return ReturnType(stored, (stored,))
    origin = universe.by_id(origin_id)
    candidates = tuple(wormhole_type.name for wormhole_type in universe.wormhole_types_in(system_id)
                       if origin and origin.system_class in wormhole_type.destinations)
    return ReturnType(candidates[0] if len(candidates) == 1 else None, candidates)

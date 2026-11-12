"""What the map shows about a system's structures and scanned ships: badges, timers and the ⚔ chip.

Plain functions over :class:`~homebound.mapdb.store.Structure` and :class:`~homebound.mapdb.store.Ship`
rows, so the panel, the node and the timer toast agree (plan-structure-list.md, plan-combat-scan.md).
"""
from datetime import datetime, timedelta

from ..http import parse_utc
from ..mapdb.store import Ship, Structure
from ..validation import require_type
from .scan_status import format_duration

TIMER_TAB_WITHIN = timedelta(hours=24)       # a node shows the ⏱ tab when a timer ends within this
TIMER_WARNING = timedelta(minutes=15)        # the toast comes this long before a timer ends
SHIPS_FRESH = timedelta(minutes=5)           # the ⚔ chip shows ships seen within this
TIMER_LABELS = {"armor": "Armor", "hull": "Hull", "anchoring": "Anchoring", "unanchoring": "Unanchoring"}
# short badge text by group, for the groups whose type name is long or says little
GROUP_BADGES = {"Control Tower": "POS", "Orbital Infrastructure": "POCO", "Skyhook": "Skyhook",
                "Mercenary Den": "Merc Den"}


def structure_badge(structure: Structure) -> str:
    """Return the type badge of a structure row: ``Fortizar``, ``POCO``, ``POS``, ``Skyhook``."""
    require_type(structure, Structure, "structure")
    return GROUP_BADGES.get(structure.group_name or "", structure.type_name)


def timer_left(structure: Structure, now: datetime) -> timedelta | None:
    """Return how long until a structure's timer ends (negative once it has), or None without a timer."""
    require_type(structure, Structure, "structure")
    require_type(now, datetime, "now")
    when = parse_utc(structure.timer_at)
    return None if when is None else when - now


def timer_text(structure: Structure, now: datetime) -> str:
    """Return the timer of a structure row, e.g. ``Armor in 4h 10m``, ``Hull 12m ago``, or "" without one."""
    left = timer_left(structure, now)
    if left is None:
        return ""
    label = TIMER_LABELS.get(structure.timer_kind or "", "Timer")
    if left >= timedelta(0):
        return f"{label} in {format_duration(left)}"
    return f"{label} {format_duration(-left)} ago"


def next_timer(structures: list[Structure], now: datetime) -> Structure | None:
    """Return the structure whose timer ends next and hasn't ended yet, or None."""
    require_type(structures, list, "structures")
    upcoming = [(left, structure) for structure in structures
                if (left := timer_left(structure, now)) is not None and left >= timedelta(0)]
    return min(upcoming, key=lambda pair: pair[0])[1] if upcoming else None


def timer_tab_label(structures: list[Structure], now: datetime) -> str:
    """Return the node's timer tab, ``⏱ 4h``, while a timer ends within 24 h; else ""."""
    structure = next_timer(structures, now)
    if structure is None:
        return ""
    left = timer_left(structure, now)
    if left is None:
        return ""
    return f"⏱ {format_duration(left, 'short')}" if left <= TIMER_TAB_WITHIN else ""


def timers_to_warn(structures: list[Structure], now: datetime) -> list[Structure]:
    """Return the structures whose timer ends within the next 15 minutes (and hasn't ended)."""
    require_type(structures, list, "structures")
    return [structure for structure in structures
            if (left := timer_left(structure, now)) is not None and timedelta(0) <= left <= TIMER_WARNING]


def fresh_ships(ships: list[Ship], now: datetime) -> int:
    """Return how many ships (not deployables) were seen in the last 5 minutes, for the node's ⚔ chip."""
    require_type(ships, list, "ships")
    require_type(now, datetime, "now")
    return sum(1 for ship in ships if ship.category == "ship" and (seen := parse_utc(ship.seen_at)) is not None
               and now - seen <= SHIPS_FRESH)


def ship_text(ship: Ship) -> str:
    """Return one row of the ships list: ``Frigate · Rifter · Bob's Rifter · 1.2 AU``.

    The name is left out when it only repeats the type.
    """
    require_type(ship, Ship, "ship")
    parts = [ship.group_name if ship.group_name != ship.type_name else None, ship.type_name,
             ship.name if ship.name and ship.name != ship.type_name else None, ship.distance]
    return " · ".join(part for part in parts if part)

"""Replay Local logs through the tracker without the game or the UI.

    python -m homebound.logs.replay LOGFILE [LOGFILE ...] [--home J161940] [--scheme numeric] [--db map.db]

Naming questions are answered with the best guess. Prints jumps, naming decisions and the final tree.
"""
import argparse
import sys
from pathlib import Path

from ..mapdb.store import Store
from ..mapping.naming import SCHEMES, Namer
from ..mapping.tracker import NamingRequest, Tracker, UnknownJump
from ..universe.db import Universe
from .parser import parse_log_file


def print_tree(store: Store, universe: Universe, namer: Namer, system_id: int, depth: int = 0) -> int:
    """Print a system and its children as an indented tree.

    Args:
        store: The map.
        universe: Static data.
        namer: Gives the names.
        system_id: The system to start at.
        depth: Indent level of ``system_id``.

    Returns:
        The number of lines printed.
    """
    system = universe.by_id(system_id)
    name, system_class = (system.name, system.system_class) if system else (str(system_id), "?")
    print("  " * depth + f"{namer.display(system_id):<14} {name:<10} {system_class}")
    return 1 + sum(print_tree(store, universe, namer, child.id, depth + 1) for child in store.children(system_id))


def _name(universe: Universe, system_id: int) -> str:
    """Return a system's name, or its ID when the universe data doesn't have it."""
    system = universe.by_id(system_id)
    return system.name if system else str(system_id)


def main(arguments: list[str] | None = None, universe: Universe | None = None) -> int:
    """Replay log files and print what the tracker made of them.

    Args:
        arguments: Command-line arguments, or None for ``sys.argv``.
        universe: Static data, or None to load the default file.

    Returns:
        The exit code: 0, or 2 for an unknown home system.
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--home")
    parser.add_argument("--scheme", default="numeric", choices=list(SCHEMES))
    parser.add_argument("--db", default=":memory:")
    parser.add_argument("--quiet", action="store_true")
    options = parser.parse_args(arguments)

    universe = universe or Universe()
    store = Store(options.db)
    home = universe.by_name(options.home) if options.home else None
    if options.home and not home:
        print(f"unknown home system: {options.home}", file=sys.stderr)
        return 2
    namer = Namer(store, universe, options.scheme, home.id if home else None)
    tracker = Tracker(store, universe, namer)
    if home:
        store.add_system(home.id)

    changes = sorted((change for path in options.logs for change in parse_log_file(path)), key=lambda change: change.at)
    for change in changes:
        prefix = f"{change.at:%F %T} {change.character_name}:"
        for event in tracker.on_system_change(change):
            if isinstance(event, NamingRequest):
                tracker.apply_naming(event, event.guess.guess)
                if not options.quiet:
                    print(f"{prefix} new {_name(universe, event.child_id)} -> {namer.display(event.child_id)} "
                          f"(static={event.guess.guess}; {event.guess.reason})")
            elif isinstance(event, UnknownJump) and not options.quiet:
                print(f"{prefix} unknown jump {_name(universe, event.from_id)} -> {_name(universe, event.to_id)}")

    print(f"\n{len(changes)} system changes, {len(store.systems())} systems, {len(store.connections())} connections\n")
    for root in (system for system in store.systems() if system.parent_id is None):
        print_tree(store, universe, namer, root.id)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Replay Local logs through the tracker without the game or the UI.

    python -m homebound.logs.replay LOGFILE [LOGFILE ...] [--home J161940] [--scheme numeric] [--db map.db]

Naming questions are answered with the best guess. Prints jumps, naming decisions and the final tree.
"""
import argparse
from pathlib import Path

from ..mapdb.store import Store
from ..mapping.naming import Namer
from ..mapping.tracker import AutoNamed, NamingRequest, Tracker, UnknownJump
from ..universe.db import Universe
from .parser import parse_file


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", nargs="+", type=Path)
    ap.add_argument("--home")
    ap.add_argument("--scheme", default="numeric", choices=["numeric", "a2a", "letter_numeric"])
    ap.add_argument("--db", default=":memory:")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args(argv)

    uni = Universe()
    store = Store(a.db)
    home = uni.by_name(a.home) if a.home else None
    namer = Namer(store, uni, a.scheme, home.id if home else None)
    tr = Tracker(store, uni, namer)
    if home:
        store.add_system(home.id)

    changes = sorted((c for p in a.logs for c in parse_file(p)), key=lambda c: c.at)
    for ch in changes:
        for ev in tr.on_change(ch):
            if isinstance(ev, NamingRequest):
                tr.apply_naming(ev, ev.guess.guess)
                if not a.quiet:
                    print(f"{ch.at:%F %T} {ch.char_name}: new {uni.by_id(ev.child_id).name} -> "
                          f"{namer.display(ev.child_id)} (static={ev.guess.guess}; {ev.guess.reason})")
            elif isinstance(ev, AutoNamed) and not a.quiet:
                print(f"{ch.at:%F %T} {ch.char_name}: auto {ev.name}")
            elif isinstance(ev, UnknownJump) and not a.quiet:
                print(f"{ch.at:%F %T} {ch.char_name}: unknown jump {uni.by_id(ev.from_id).name} -> {uni.by_id(ev.to_id).name}")

    print(f"\n{len(changes)} system changes, {len(store.systems())} systems, {len(store.connections())} connections\n")
    roots = [s for s in store.systems() if s.parent_id is None]

    def tree(sid, depth=0):
        s = uni.by_id(sid)
        print("  " * depth + f"{namer.display(sid):<14} {s.name:<10} {s.cls}")
        for c in store.children(sid):
            tree(c.id, depth + 1)

    for r in roots:
        tree(r.id)


if __name__ == "__main__":
    main()

"""Turn character system changes into map updates.

A->B classification:
  * stargate between A and B (SDE)            -> gate jump: location only
  * either side w-space / Thera / Pochven     -> wormhole: add B + connection A<->B
  * k-space to non-adjacent k-space           -> unknown (bridge, filament, clone jump): ask the UI

New systems get a provisional name straight away (best guess), and a NamingRequest
is emitted so the UI can confirm "was this the static?" / "which sig?".
"""
from dataclasses import dataclass, field

from ..logs.parser import SystemChange
from ..mapdb.store import Store
from ..universe.db import Universe
from .naming import Namer, StaticGuess, guess_static, next_hole_index, next_level_index, path_of, static_slots

CHILD_DX, CHILD_DY = 252, 96


@dataclass
class Moved:
    char_id: int
    char_name: str
    system_id: int | None
    prev_id: int | None


@dataclass
class UnknownJump:
    char_id: int
    char_name: str
    from_id: int
    to_id: int


@dataclass
class NamingRequest:
    parent_id: int
    child_id: int
    connection_id: int
    guess: StaticGuess
    sig_candidates: list[str] = field(default_factory=list)  # unlinked WH sigs in the parent
    char_name: str = ""


@dataclass
class AutoNamed:
    child_id: int
    name: str
    previous: dict  # values to restore on undo


@dataclass
class MapChanged:
    pass


def is_wh_side(uni: Universe, sid: int) -> bool:
    s = uni.by_id(sid)
    return bool(s and (s.wspace or s.cls in ("PV", "TH")))


class Tracker:
    def __init__(self, store: Store, uni: Universe, namer: Namer, auto_when_certain: bool = False):
        self.store, self.uni, self.namer = store, uni, namer
        self.auto_when_certain = auto_when_certain

    # ------------------------------------------------------------ log events
    def on_change(self, ch: SystemChange) -> list:
        sys_ = self.uni.by_name(ch.system)
        sid = sys_.id if sys_ else None
        prev = self.store.set_character(ch.char_id, ch.char_name, sid, ch.at.isoformat())
        events: list = [Moved(ch.char_id, ch.char_name, sid, prev)]
        if ch.first or sid is None or prev is None or prev == sid:
            return events
        if self.uni.gate_adjacent(prev, sid):
            return events
        if is_wh_side(self.uni, prev) or is_wh_side(self.uni, sid):
            events += self.add_wormhole(prev, sid, ch.char_name)
        else:
            events.append(UnknownJump(ch.char_id, ch.char_name, prev, sid))
        return events

    # ------------------------------------------------------------ map building
    def ensure_system(self, sid: int, near: int | None = None) -> bool:
        if self.store.has_system(sid):
            return False
        x = y = 0.0
        if near and (n := self.store.system(near)):
            x, y = n.x + CHILD_DX, n.y + CHILD_DY * len(self.store.children(near))
        elif self.namer.home_id == sid:
            x = y = 0.0
        else:
            others = self.store.systems()
            y = max((s.y for s in others), default=-CHILD_DY * 3) + CHILD_DY * 3
        self.store.add_system(sid, x=x, y=y)
        return True

    def unlinked_wh_sigs(self, sid: int) -> list[str]:
        linked = {c.sig_on(sid) for c in self.store.connections_of(sid)}
        return [s.sig_id for s in self.store.signatures(sid)
                if s.grp == "wormhole" and s.sig_id not in linked and s.state != "missing"]

    def add_wormhole(self, a: int, b: int, char_name: str = "") -> list:
        events: list = [MapChanged()]
        a_new = self.ensure_system(a)
        b_existed = self.store.has_system(b)
        if b_existed and a_new:
            # Came from outside into a known system: the new side hangs under the known one.
            self.store.update_system(a, x=self.store.system(b).x - CHILD_DX, y=self.store.system(b).y)
            self.store.add_connection(a, b)
            return events + self._attach(b, a, char_name)
        if not b_existed:
            self.ensure_system(b, near=a)
        self.store.add_connection(a, b)

        bs = self.store.system(b)
        root_a, _ = path_of(self.store, a)
        becomes_child = (bs.parent_id is None and b != self.namer.home_id and root_a != b
                         and (not b_existed or root_a == self.namer.home_id))
        if not becomes_child:
            return events
        return events + self._attach(a, b, char_name)

    def _attach(self, a: int, b: int, char_name: str) -> list:
        """Make b a child of a, provisionally named by the best guess; ask if unsure."""
        events: list = []
        bs = self.store.system(b)
        conn = self.store.connection(a, b)
        guess = guess_static(self.store, self.uni, a, b)
        hole = next_hole_index(self.store, self.uni, a, guess.applies and guess.guess, self.uni.by_id(b).cls)
        root, path = path_of(self.store, a)
        level = next_level_index(self.store, root, path + [hole])
        previous = {"parent_id": bs.parent_id, "hole_index": bs.hole_index, "level_index": bs.level_index}
        self.store.update_system(b, parent_id=a, hole_index=hole, level_index=level)

        cands = self.unlinked_wh_sigs(a)
        if len(cands) == 1:
            self.link_sig(conn.id, a, cands[0], hole)
            cands = []
        if not guess.applies or (guess.certain and not cands and self.auto_when_certain):
            if guess.applies:
                events.append(AutoNamed(b, self.namer.display(b), previous))
            return events
        events.append(NamingRequest(a, b, conn.id, guess, cands, char_name))
        return events

    def link_sig(self, conn_id: int, system_id: int, sig_id: str, hole_index: int | None) -> None:
        self.store.set_connection_sig(conn_id, system_id, sig_id)
        if hole_index is not None:
            self.store.update_signature(system_id, sig_id, hole_index=hole_index)
        sig = self.store.signature(system_id, sig_id)
        c = self.store.connection_by_id(conn_id)
        if sig and sig.type and sig.type != "K162" and c and not c.wh_type:
            self.store.update_connection(conn_id, wh_type=sig.type)

    # ------------------------------------------------------------ expiry
    def expire(self, now=None) -> int:
        """Drop connections past their lifetime (type lifetime, else 48h; EOL + 4h) and orphaned systems.
        Returns the number of connections removed."""
        from datetime import datetime, timedelta, timezone
        now = now or datetime.now(timezone.utc)
        removed = 0
        for c in self.store.connections():
            t = self.uni.wh_type(c.wh_type) if c.wh_type else None
            life = timedelta(hours=t.lifetime_h if t and t.lifetime_h else 48)
            dead = now - datetime.fromisoformat(c.created_at) > life
            if c.eol and c.eol_at:
                dead |= now - datetime.fromisoformat(c.eol_at) > timedelta(hours=4)
            if dead:
                self.store.remove_connection(c.id)
                removed += 1
        if removed:
            home = self.namer.home_id
            for s in self.store.systems():
                if s.id != home and not s.pinned and not self.store.connections_of(s.id):
                    self.store.remove_system(s.id)
            for s in self.store.systems():
                if s.parent_id and not self.store.connection(s.parent_id, s.id):
                    self.store.update_system(s.id, parent_id=None, hole_index=None)
        return removed

    # ------------------------------------------------------------ answers from the UI
    def apply_naming(self, req: NamingRequest, is_static: bool, sig_id: str | None = None,
                     custom_name: str | None = None) -> None:
        a, b = req.parent_id, req.child_id
        child_cls = self.uni.by_id(b).cls
        cur = self.store.system(b)
        cur_is_static = cur.hole_index is not None and cur.hole_index <= static_slots(self.uni, a)
        if is_static != cur_is_static:
            # free our current index before picking a new one
            self.store.update_system(b, hole_index=None)
            hole = next_hole_index(self.store, self.uni, a, is_static, child_cls)
            self.store.update_system(b, hole_index=hole)
            for s in self.store.signatures(a):
                if s.hole_index == cur.hole_index:
                    self.store.update_signature(a, s.sig_id, hole_index=hole)
        if sig_id:
            self.link_sig(req.connection_id, a, sig_id, self.store.system(b).hole_index)
        self.store.update_system(b, custom_name=custom_name or None)

    def undo_auto(self, ev: AutoNamed) -> None:
        self.store.update_system(ev.child_id, **ev.previous)

    def preview_names(self, req: NamingRequest) -> tuple[str, str]:
        """(name if static, name if not static) under the active scheme."""
        a, b = req.parent_id, req.child_id
        cls = self.uni.by_id(b).cls
        cur = self.store.system(b)
        n = static_slots(self.uni, a)
        from .naming import static_slot_for, used_hole_indices
        used = used_hole_indices(self.store, a) - {cur.hole_index}
        stat = static_slot_for(self.uni, a, cls) if n else None
        i = n + 1
        while i in used:
            i += 1
        lvl = cur.level_index
        yes = self.namer.preview(a, stat, lvl) if stat else ""
        no = self.namer.preview(a, i if not (cur.hole_index and cur.hole_index > n) else cur.hole_index, lvl)
        return yes, no

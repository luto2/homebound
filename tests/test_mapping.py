from datetime import datetime, timedelta, timezone

import pytest

from homebound.logs.parser import SystemChange
from homebound.mapdb.store import Store
from homebound.mapping import sigparse
from homebound.mapping.naming import Namer, guess_static
from homebound.mapping.tracker import NamingRequest, Tracker, UnknownJump
from homebound.universe.db import universe

U = universe()
HOME = U.by_name("J161940")  # C5, static H296 -> C5


def pick(cls, statics_to=None, exclude=()):
    for s in sorted(U._by_id.values(), key=lambda s: s.id):
        if s.cls != cls or s.id in exclude:
            continue
        if statics_to and statics_to not in U.static_dest_classes(s.id):
            continue
        if statics_to and len(s.statics) != 1:
            continue
        return s
    raise LookupError(cls)


class Sim:
    def __init__(self, scheme="numeric"):
        self.store = Store(":memory:")
        self.namer = Namer(self.store, U, scheme, HOME.id)
        self.tr = Tracker(self.store, U, self.namer)
        self.t = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        self.store.add_system(HOME.id)
        self.jump(HOME.name, first=True)

    def jump(self, name, first=False, char=1):
        self.t += timedelta(minutes=1)
        return self.tr.on_change(SystemChange(char, "Pilot", name, self.t, first=first))

    def req(self, events):
        return next(e for e in events if isinstance(e, NamingRequest))


def test_gate_jump_no_connection():
    s = Sim()
    s.jump("Jita", first=True)
    s.jump("Perimeter")
    assert s.store.connections() == []
    assert not s.store.has_system(U.by_name("Perimeter").id)


def test_kspace_teleport_is_unknown():
    s = Sim()
    s.jump("Jita", first=True)
    ev = s.jump("Amarr")
    assert any(isinstance(e, UnknownJump) for e in ev)


def test_numeric_chain_and_bookmarks():
    s = Sim("numeric")
    c5 = pick("C5", exclude={HOME.id})
    ev = s.jump(c5.name)                         # matches home's C5 static -> guessed static
    r = s.req(ev)
    assert r.guess.guess is True
    s.tr.apply_naming(r, is_static=True)
    assert s.namer.display(c5.id) == "1"
    assert s.namer.bookmark(HOME.id, 1) == "Home-1"

    s.jump(HOME.name)
    c3 = pick("C3")
    r = s.req(s.jump(c3.name))                   # C3 can't be home's static
    assert r.guess.guess is False and r.guess.certain
    s.tr.apply_naming(r, is_static=False)
    assert s.namer.display(c3.id) == "2"

    s.jump(HOME.name)
    s.jump(c5.name)
    stat_dest = U.static_dest_classes(c5.id)[0]
    nxt = pick(stat_dest, exclude={HOME.id, c5.id, c3.id})
    r = s.req(s.jump(nxt.name))
    s.tr.apply_naming(r, is_static=True)
    assert s.namer.display(nxt.id).split(" ")[0] == "11"
    assert s.namer.bookmark(c5.id, 1) == "1-1"
    assert s.namer.bookmark_home(nxt.id).startswith("11")


def test_answer_no_moves_to_wanderer_index():
    s = Sim("numeric")
    c5 = pick("C5", exclude={HOME.id})
    r = s.req(s.jump(c5.name))
    s.tr.apply_naming(r, is_static=False)
    assert s.namer.display(c5.id) == "2"


def test_a2a_and_letter_schemes():
    for scheme, first, second in (("a2a", "A1a", "B1a"), ("letter_numeric", "A", "B")):
        s = Sim(scheme)
        c5 = pick("C5", exclude={HOME.id})
        s.tr.apply_naming(s.req(s.jump(c5.name)), is_static=True)
        s.jump(HOME.name)
        c3 = pick("C3")
        s.tr.apply_naming(s.req(s.jump(c3.name)), is_static=False)
        assert s.namer.display(c5.id) == first
        assert s.namer.display(c3.id) == second


def test_a2a_level_index():
    s = Sim("a2a")
    c5 = pick("C5", exclude={HOME.id})
    s.tr.apply_naming(s.req(s.jump(c5.name)), is_static=True)
    kids = []
    for cls in ("C1", "C2"):
        s.jump(c5.name)
        k = pick(cls, exclude={c.id for c in kids})
        s.tr.apply_naming(s.req(s.jump(k.name)), is_static=False)
        kids.append(k)
    assert [s.namer.display(k.id) for k in kids] == ["A2a", "A2b"]


def test_kspace_display_has_real_name():
    s = Sim()
    hs = U.by_name("Jita")
    s.jump(hs.name)  # home -> Jita via WH
    assert s.namer.display(hs.id) == "2 (Jita)"


def test_custom_name_wins():
    s = Sim()
    c5 = pick("C5", exclude={HOME.id})
    r = s.req(s.jump(c5.name))
    s.tr.apply_naming(r, is_static=True, custom_name="Farm")
    assert s.namer.display(c5.id) == "Farm"


SCAN = ("ABC-123\tCosmic Signature\tWormhole\tUnstable Wormhole\t100,0%\t4,32 AU\n"
        "DEF-456\tCosmic Signature\t\t\t12,5%\t10 AU\n"
        "GHI-789\tCosmic Signature\tGas Site\tBarren Perimeter Reservoir\t100.0%\t2 AU\n"
        "XYZ-000\tCosmic Anomaly\tCombat Site\tSansha Hideaway\t100%\t1 AU\n")


def test_sigparse_and_merge():
    p = sigparse.parse(SCAN)
    assert [x.sig_id for x in p] == ["ABC-123", "DEF-456", "GHI-789"]
    assert p[0].grp == "wormhole" and p[0].scan_pct == 100.0 and p[1].grp is None
    st = Store(":memory:")
    st.add_system(HOME.id)
    r = sigparse.merge(st, HOME.id, p)
    assert sorted(r.added) == ["ABC-123", "DEF-456", "GHI-789"]
    # second paste: DEF upgraded, GHI unknown now (doesn't downgrade), ABC missing
    r = sigparse.merge(st, HOME.id, sigparse.parse("DEF-456\tCosmic Signature\tData Site\tUnsecured Frontier Server\t100%\t1 AU\n"
                                                    "GHI-789\tCosmic Signature\t\t\t5%\t2 AU\n"))
    assert r.missing == ["ABC-123"]
    sigs = {x.sig_id: x for x in st.signatures(HOME.id)}
    assert sigs["DEF-456"].grp == "data"
    assert sigs["GHI-789"].grp == "gas" and sigs["GHI-789"].name == "Barren Perimeter Reservoir"
    assert sigs["ABC-123"].state == "missing"


def test_single_unlinked_sig_is_linked():
    s = Sim()
    sigparse.merge(s.store, HOME.id, sigparse.parse(SCAN))
    c5 = pick("C5", exclude={HOME.id})
    s.jump(c5.name)
    c = s.store.connection(HOME.id, c5.id)
    assert c.sig_on(HOME.id) == "ABC-123"
    assert s.store.signature(HOME.id, "ABC-123").hole_index == 1


def test_chain_kspace_radius():
    from homebound.mapping.chain import resolve
    s = Sim()
    jita = U.by_name("Jita")
    s.jump(jita.name)  # home -> Jita by WH; Jita is the entrance
    dist = U.gate_distances(jita.id, 6)
    for d in range(0, 7):
        sid = next(k for k, v in sorted(dist.items()) if v == d)
        ch = resolve(s.store, U, sid, HOME.id, radius=5)
        if d <= 5:
            assert ch is not None and HOME.id in ch.members, d
            if d:
                assert ch.entrance_id == jita.id and ch.jumps == d
                assert any(r.virtual and r.current and r.jumps_from == d for r in ch.rows)
        else:
            assert ch is None


def test_chain_tree_rows():
    from homebound.mapping.chain import resolve
    s = Sim()
    c5 = pick("C5", exclude={HOME.id})
    s.tr.apply_naming(s.req(s.jump(c5.name)), is_static=True)
    s.jump(HOME.name)
    c3 = pick("C3")
    s.tr.apply_naming(s.req(s.jump(c3.name)), is_static=False)
    ch = resolve(s.store, U, c3.id, HOME.id)
    assert [r.system_id for r in ch.rows] == [HOME.id, c5.id, c3.id]
    assert [r.prefix for r in ch.rows] == ["", "├ ", "└ "]
    assert ch.rows[2].current


def test_expire():
    s = Sim()
    c5 = pick("C5", exclude={HOME.id})
    s.tr.apply_naming(s.req(s.jump(c5.name)), is_static=True)
    c = s.store.connection(HOME.id, c5.id)
    created = datetime.fromisoformat(c.created_at)
    assert s.tr.expire(created + timedelta(hours=47)) == 0
    s.store.update_connection(c.id, eol=1, eol_at=(created + timedelta(hours=1)).isoformat())
    assert s.tr.expire(created + timedelta(hours=6)) == 1
    assert not s.store.has_system(c5.id) and s.store.has_system(HOME.id)

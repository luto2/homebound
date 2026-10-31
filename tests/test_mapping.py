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
    ev = s.jump(c3.name)                         # C3 can't be home's C5 static: no question at all
    assert not any(isinstance(e, NamingRequest) for e in ev)
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
    for scheme, first, second in (("a2a", "A1a", "Z1a"), ("letter_numeric", "A", "Z")):
        s = Sim(scheme)
        c5 = pick("C5", exclude={HOME.id})
        s.tr.apply_naming(s.req(s.jump(c5.name)), is_static=True)
        s.jump(HOME.name)
        c3 = pick("C3")
        s.jump(c3.name)  # not the static's class: named as a wanderer without asking
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
        s.jump(k.name)
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
    p = sigparse.parse(SCAN, include_anomalies=False)
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
    s.jump(c3.name)
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


def test_anomalies_kept_and_missing_per_kind():
    p = sigparse.parse(SCAN + "QRS-111\tCosmic Anomaly\tOre Site\tAverage Frontier Deposit\t100%\t3 AU\n")
    anoms = {x.sig_id: x for x in p if x.kind == "anom"}
    assert {k: v.grp for k, v in anoms.items()} == {"XYZ-000": "combat", "QRS-111": "ore"}
    st = Store(":memory:")
    st.add_system(HOME.id)
    sigparse.merge(st, HOME.id, p)
    # a paste with only signatures must not flag the anomalies as missing, and vice versa
    r = sigparse.merge(st, HOME.id, sigparse.parse("ABC-123\tCosmic Signature\tWormhole\t\t100%\t1 AU\n"))
    assert sorted(r.missing) == ["DEF-456", "GHI-789"]
    r = sigparse.merge(st, HOME.id, sigparse.parse("XYZ-000\tCosmic Anomaly\tCombat Site\tSansha Hideaway\t100%\t1 AU\n"))
    assert r.missing == ["QRS-111"]
    assert st.signature(HOME.id, "ABC-123").state == ""


def test_sig_wh_details_carry_to_connection():
    s = Sim()
    sigparse.merge(s.store, HOME.id, sigparse.parse(SCAN))
    s.tr.save_sig(HOME.id, "ABC-123", {"grp": "wormhole", "type": "K162", "dest": "C5",
                                         "mass": "critical", "eol": 1, "frig": 0})
    sig = s.store.signature(HOME.id, "ABC-123")
    assert (sig.type, sig.dest, sig.mass, sig.eol) == ("K162", "C5", "critical", 1) and sig.eol_at
    # pasting again keeps what was typed in (the scanner never shows these)
    sigparse.merge(s.store, HOME.id, sigparse.parse(SCAN))
    assert s.store.signature(HOME.id, "ABC-123").mass == "critical"
    c5 = pick("C5", exclude={HOME.id})
    s.jump(c5.name)
    c = s.store.connection(HOME.id, c5.id)
    assert c.sig_on(HOME.id) == "ABC-123"
    assert (c.mass, c.eol, c.wh_type) == ("critical", 1, None)
    assert c.eol_at == sig.eol_at
    # editing the linked sig updates the connection; saving EOL again keeps the EOL clock
    s.tr.save_sig(HOME.id, "ABC-123", {"grp": "wormhole", "type": "H296", "mass": "reduced", "eol": 1, "frig": 0})
    c2 = s.store.connection(HOME.id, c5.id)
    assert (c2.mass, c2.wh_type, c2.eol_at) == ("reduced", "H296", c.eol_at)


def test_chain_letters_follow_eve_uni():
    from homebound.mapping.naming import chain_letter
    assert [chain_letter(h, 1) for h in range(1, 6)] == ["A", "Z", "Y", "W", "U"]
    assert [chain_letter(h, 2) for h in range(1, 5)] == ["A", "B", "Z", "Y"]
    assert chain_letter(1, 0) == "Z"  # k-space root: no static
    letters = {chain_letter(h, 2) for h in range(1, 16)}
    assert not letters & set("CFGHJLNPTVX")
    assert chain_letter(14, 1) == "D" and chain_letter(15, 1) == "ZZ"  # A + 13 root letters, then doubled


def test_store_migrates_old_signatures_table(tmp_path):
    import sqlite3
    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE signatures (system_id INTEGER NOT NULL, sig_id TEXT NOT NULL, grp TEXT, type TEXT,"
                " name TEXT, scan_pct REAL, hole_index INTEGER, state TEXT DEFAULT '', created_at TEXT NOT NULL,"
                " updated_at TEXT NOT NULL, PRIMARY KEY (system_id, sig_id))")
    con.execute("INSERT INTO signatures VALUES (1, 'ABC-123', 'wormhole', NULL, NULL, 100, NULL, '', 't', 't')")
    con.commit()
    con.close()
    st = Store(db)
    sig = st.signature(1, "ABC-123")
    assert sig.kind == "sig" and sig.mass is None and sig.eol == 0


def test_grid_matches_node_height():
    from homebound.mapping.tracker import GRID
    from homebound.theme.tokens import PX
    assert GRID == PX["size-node-h"] / 3


def test_maps_in_settings(tmp_path, monkeypatch):
    import homebound.settings as S
    monkeypatch.setattr(S, "DATA_DIR", tmp_path)
    monkeypatch.setattr(S, "SETTINGS_FILE", tmp_path / "settings.json")
    (tmp_path / "settings.json").write_text('{"home_system": "J161940", "naming_scheme": "a2a"}')
    s = S.Settings.load()  # settings from before maps existed: the old map.db becomes "Main"
    assert s.maps == {"Main": {"file": "map.db", "home_system": "J161940", "naming_scheme": "a2a"}}
    s.add_map("Corp chain", "J100551", "numeric")
    assert s.maps["Corp chain"]["file"] == "maps/corp-chain.db"
    with pytest.raises(ValueError):
        s.add_map("Corp chain")
    s.switch_map("Corp chain")
    assert (s.home_system, s.naming_scheme) == ("J100551", "numeric")
    s.naming_scheme = "letter_numeric"
    s.save()
    s = S.Settings.load()
    assert s.active_map == "Corp chain" and s.maps["Corp chain"]["naming_scheme"] == "letter_numeric"
    s.switch_map("Main")
    assert (s.home_system, s.naming_scheme) == ("J161940", "a2a")
    s.rename_map("Corp chain", "Corp")
    with pytest.raises(ValueError):
        s.remove_map("Main")  # active
    assert s.remove_map("Corp") == tmp_path / "maps/corp-chain.db"


def test_lifetime_levels_expire_and_k162_size():
    from homebound.mapdb.store import LIFE_1H, LIFE_4H
    s = Sim()
    sigparse.merge(s.store, HOME.id, sigparse.parse(SCAN))
    # K162: size and destination are entered by hand; lifetime < 1h
    s.tr.save_sig(HOME.id, "ABC-123", {"grp": "wormhole", "type": "K162", "dest": "C5", "size": "L",
                                         "eol": LIFE_1H, "mass": "fresh", "frig": 0})
    c5 = pick("C5", exclude={HOME.id})
    s.jump(c5.name)
    c = s.store.connection(HOME.id, c5.id)
    assert (c.size, c.eol, c.frig) == ("L", LIFE_1H, 0)
    # the clock restarts when the state changes, not when the same state is saved again
    s.store.update_connection(c.id, eol=LIFE_1H)
    assert s.store.connection_by_id(c.id).eol_at == c.eol_at
    s.store.update_connection(c.id, eol=LIFE_4H)
    c4 = s.store.connection_by_id(c.id)
    set_at = datetime.fromisoformat(c4.eol_at)
    assert s.tr.expire(now=set_at + timedelta(hours=2)) == 0        # < 4h: still there after 2h
    s.store.update_connection(c.id, eol=LIFE_1H, eol_at=set_at.isoformat())
    assert s.tr.expire(now=set_at + timedelta(hours=2)) == 1        # < 1h: gone after 2h


def test_hole_size_and_possible_types():
    from homebound.mapping.tracker import hole_size
    assert hole_size(U, "E004", None) == "S" and hole_size(U, "H296", "S") == "XL"  # the type wins
    assert hole_size(U, "K162", "M") == "M" and hole_size(U, None, None) is None
    types = [t.name for t in U.wh_types_from(HOME.id)]
    assert types[0] == "H296" and "K162" not in types                 # static first
    assert "C008" in types and "B274" not in types                    # B274 is a HS static, never in a C5
    jita = [t.name for t in U.wh_types_from(U.by_name("Jita").id)]
    assert jita and all("HS" in U.wh_type(n).src for n in jita)


def test_sig_question_only_lists_holes_that_can_lead_here():
    s = Sim()
    sigparse.merge(s.store, HOME.id, sigparse.parse(
        "AAA-111\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n"
        "BBB-222\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n"
        "CCC-333\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n"
        "DDD-444\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n"))
    s.store.update_signature(HOME.id, "AAA-111", type="H296")              # -> C5 only
    s.store.update_signature(HOME.id, "BBB-222", type="M001")              # -> C4
    s.store.update_signature(HOME.id, "CCC-333", type="K162", dest="C2")   # K162: always listed
    c4 = pick("C4")
    ev = s.jump(c4.name)
    r = s.req(ev)
    assert r.guess.applies is False                       # a C4 can never be home's C5 static
    assert sorted(r.sig_candidates) == ["BBB-222", "CCC-333", "DDD-444"]   # not the H296


def test_single_possible_sig_is_linked_without_asking():
    s = Sim()
    sigparse.merge(s.store, HOME.id, sigparse.parse(
        "AAA-111\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n"
        "BBB-222\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n"))
    s.store.update_signature(HOME.id, "AAA-111", type="H296")
    s.store.update_signature(HOME.id, "BBB-222", type="M001")
    c4 = pick("C4")
    ev = s.jump(c4.name)
    assert not any(isinstance(e, NamingRequest) for e in ev)
    assert s.store.connection(HOME.id, c4.id).sig_on(HOME.id) == "BBB-222"


def test_hand_drawn_connection_is_unconfirmed_until_jumped():
    s = Sim()
    c5 = pick("C5", exclude={HOME.id})
    s.tr.add_wormhole(HOME.id, c5.id, manual=True)
    c = s.store.connection(HOME.id, c5.id)
    assert not c.confirmed and not s.store.system(c5.id).confirmed
    assert s.store.system(HOME.id).confirmed            # existing systems stay confirmed
    s.jump(c5.name)                                      # someone jumps it
    assert s.store.connection(HOME.id, c5.id).confirmed and s.store.system(c5.id).confirmed


def test_visiting_a_hand_added_system_confirms_it_but_not_its_connection():
    s = Sim()
    c5 = pick("C5", exclude={HOME.id})
    s.tr.add_wormhole(HOME.id, c5.id, manual=True)
    s.jump(c5.name, first=True, char=2)                  # logged in there: the system is real
    assert s.store.system(c5.id).confirmed
    assert not s.store.connection(HOME.id, c5.id).confirmed
    assert s.tr.confirm(system_id=c5.id, with_connections=True)
    assert s.store.connection(HOME.id, c5.id).confirmed


def test_snapshot_restore_undoes_removal():
    s = Sim()
    c5 = pick("C5", exclude=(HOME.id,))
    c3 = pick("C3")
    a = s.store.add_system(c5.id, parent_id=HOME.id, hole_index=1)
    s.store.add_system(c3.id, parent_id=a.id, hole_index=2)
    s.store.add_connection(HOME.id, c5.id)
    s.store.add_connection(c5.id, c3.id)
    snap = s.store.snapshot()
    s.store.remove_system(c5.id)           # cascades both connections, orphans c3
    assert s.store.connections() == [] and s.store.system(c3.id).parent_id is None
    jita = U.by_name("Jita").id
    s.store.add_system(jita)               # something that happened in between stays
    s.store.restore(snap)
    assert s.store.has_system(c5.id) and s.store.has_system(jita)
    assert s.store.system(c3.id).parent_id == c5.id
    assert len(s.store.connections()) == 2

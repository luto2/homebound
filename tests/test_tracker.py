"""Tracker: jump classification, map building, naming answers, signatures and expiry."""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import HOME, SCAN, UNIVERSE, MapSimulation, pick_system
from homebound.logs.parser import SystemChange
from homebound.mapdb.store import LIFETIME_1H, LIFETIME_4H, LIFETIME_REMAINING, Store
from homebound.mapping import signature_parser
from homebound.mapping.naming import Namer
from homebound.mapping.tracker import (AutoNamed, MapChanged, Moved, NamingRequest, Tracker, UnknownJump,
                                       expires_at, hole_size, is_wormhole_side)
from homebound.ui.map_scene import removed_in


def paste(simulation: MapSimulation, text: str = SCAN) -> None:
    """Paste scanner text into home."""
    signature_parser.merge_signatures(simulation.store, HOME.id, signature_parser.parse_scanner_text(text))


def test_tracker_rejects_wrong_argument_types() -> None:
    """The tracker needs a store, a universe and a namer."""
    store = Store(":memory:")
    with pytest.raises(TypeError):
        Tracker("store", UNIVERSE, Namer(store, UNIVERSE))
    with pytest.raises(TypeError):
        Tracker(store, UNIVERSE, None)
    with pytest.raises(TypeError):
        Tracker(store, UNIVERSE, Namer(store, UNIVERSE)).on_system_change("J161940")


def test_gate_jump_creates_no_connection(simulation) -> None:
    """A gate jump only moves the character."""
    simulation.jump("Jita", first=True)
    events = simulation.jump("Perimeter")
    assert simulation.store.connections() == []
    assert not simulation.store.has_system(UNIVERSE.by_name("Perimeter").id)
    assert [type(event) for event in events] == [Moved]


def test_wormhole_jump_marks_the_move(simulation) -> None:
    """A jump into w-space flags its move as a wormhole jump; a gate jump doesn't."""
    simulation.jump(HOME.name, first=True)
    target = pick_system("C5", exclude={HOME.id})
    moved = simulation.jump(target.name)[0]
    assert isinstance(moved, Moved) and moved.wormhole and moved.system_id == target.id
    simulation.jump("Jita", first=True)
    assert simulation.jump("Perimeter")[0].wormhole is False


def test_known_space_teleport_is_an_unknown_jump(simulation) -> None:
    """K-space to k-space without a gate is reported for the UI to ask."""
    simulation.jump("Jita", first=True)
    events = simulation.jump("Amarr")
    unknown = next(event for event in events if isinstance(event, UnknownJump))
    assert (unknown.from_id, unknown.to_id) == (UNIVERSE.by_name("Jita").id, UNIVERSE.by_name("Amarr").id)


def test_unknown_system_name_only_moves(simulation) -> None:
    """A system name the universe doesn't know gives a move to nowhere."""
    events = simulation.jump("Nowhere")
    assert events == [Moved(1, "Pilot", None, HOME.id)]


def test_same_system_again_is_not_a_jump(simulation) -> None:
    """Seeing the same system twice adds nothing."""
    assert [type(event) for event in simulation.jump(HOME.name)] == [Moved]


def test_wormhole_side_check() -> None:
    """W-space, Thera and Pochven count as wormhole sides; k-space and unknown IDs don't."""
    assert is_wormhole_side(UNIVERSE, HOME.id)
    assert not is_wormhole_side(UNIVERSE, UNIVERSE.by_name("Jita").id)
    assert is_wormhole_side(UNIVERSE, UNIVERSE.by_name("Thera").id)
    assert not is_wormhole_side(UNIVERSE, 1)


def test_numeric_chain_and_bookmarks(simulation) -> None:
    """The static is hole 1, other holes count on, and bookmarks follow the names."""
    c5 = pick_system("C5", exclude={HOME.id})
    request = simulation.naming_request(simulation.jump(c5.name))  # matches home's C5 static
    assert request.guess.guess is True
    simulation.tracker.apply_naming(request, is_static=True)
    assert simulation.namer.display(c5.id) == "1"
    assert simulation.namer.bookmark(HOME.id, 1) == "Home-1"

    simulation.jump(HOME.name)
    c3 = pick_system("C3")
    events = simulation.jump(c3.name)  # C3 can't be home's C5 static: no question at all
    assert not any(isinstance(event, NamingRequest) for event in events)
    assert simulation.namer.display(c3.id) == "2"

    simulation.jump(HOME.name)
    simulation.jump(c5.name)
    static_destination = UNIVERSE.static_destination_classes(c5.id)[0]
    next_system = pick_system(static_destination, exclude={HOME.id, c5.id, c3.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(next_system.name)), is_static=True)
    assert simulation.namer.display(next_system.id).split(" ")[0] == "11"
    assert simulation.namer.bookmark(c5.id, 1) == "1-1"
    assert simulation.namer.bookmark_home(next_system.id).startswith("11")


def test_answer_no_moves_to_the_next_free_index(simulation) -> None:
    """Answering "not the static" gives the first index after the static slots."""
    c5 = pick_system("C5", exclude={HOME.id})
    request = simulation.naming_request(simulation.jump(c5.name))
    assert simulation.tracker.apply_naming(request, is_static=False) == 2
    assert simulation.namer.display(c5.id) == "2"


def test_preview_names_for_both_answers(simulation) -> None:
    """The naming question shows both possible names."""
    c5 = pick_system("C5", exclude={HOME.id})
    request = simulation.naming_request(simulation.jump(c5.name))
    assert simulation.tracker.preview_names(request) == ("1", "2")
    with pytest.raises(TypeError):
        simulation.tracker.preview_names(None)


def test_chain_depth_position_and_letter_schemes() -> None:
    """Static chain is A, the first other chain Z."""
    for scheme, first, second in (("a2a", "A1a", "Z1a"), ("letter_numeric", "A", "Z")):
        simulation = MapSimulation(scheme)
        c5 = pick_system("C5", exclude={HOME.id})
        simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
        simulation.jump(HOME.name)
        c3 = pick_system("C3")
        simulation.jump(c3.name)
        assert simulation.namer.display(c5.id) == first
        assert simulation.namer.display(c3.id) == second


def test_chain_depth_position_counts_systems_at_the_same_depth() -> None:
    """Two systems behind A1a are A2a and A2b."""
    simulation = MapSimulation("a2a")
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
    children = []
    for system_class in ("C1", "C2"):
        simulation.jump(c5.name)
        child = pick_system(system_class, exclude={sibling.id for sibling in children})
        simulation.jump(child.name)
        children.append(child)
    assert [simulation.namer.display(sibling.id) for sibling in children] == ["A2a", "A2b"]


def test_known_space_display_has_the_real_name(simulation) -> None:
    """K-space shows the scheme label and the real name."""
    jita = UNIVERSE.by_name("Jita")
    simulation.jump(jita.name)
    assert simulation.namer.display(jita.id) == "2 Jita"


def test_custom_name_wins(simulation) -> None:
    """A name typed into the naming question replaces the scheme name."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True,
                                    custom_name="Farm")
    assert simulation.namer.display(c5.id) == "Farm"


def test_changing_the_answer_moves_the_reserved_signature_index(simulation) -> None:
    """Switching static → not static also moves a signature that held the old index."""
    c5 = pick_system("C5", exclude={HOME.id})
    request = simulation.naming_request(simulation.jump(c5.name))
    simulation.tracker.apply_naming(request, is_static=True)
    paste(simulation, "AAA-111\tCosmic Signature\tWormhole\t\t100%\t1 AU\n")
    simulation.store.update_signature(HOME.id, "AAA-111", hole_index=1)
    simulation.tracker.apply_naming(request, is_static=False, signature_id="AAA-111")
    assert simulation.store.system(c5.id).hole_index == 2
    assert simulation.store.signature(HOME.id, "AAA-111").hole_index == 2
    assert simulation.store.connection(HOME.id, c5.id).signature_on(HOME.id) == "AAA-111"


def test_auto_naming_when_the_guess_is_certain() -> None:
    """One unlinked static-type sig and auto naming on: no question, and undo puts the system back."""
    simulation = MapSimulation()
    simulation.tracker.auto_when_certain = True
    paste(simulation, "AAA-111\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n")
    simulation.store.update_signature(HOME.id, "AAA-111", type="H296")
    c5 = pick_system("C5", exclude={HOME.id})
    events = simulation.jump(c5.name)
    auto = next(event for event in events if isinstance(event, AutoNamed))
    assert auto.child_id == c5.id and auto.name == "1"
    assert not any(isinstance(event, NamingRequest) for event in events)
    assert simulation.tracker.undo_auto_naming(auto)
    assert simulation.store.system(c5.id).parent_id is None
    with pytest.raises(TypeError):
        simulation.tracker.undo_auto_naming("1")


def test_single_unlinked_signature_is_linked(simulation) -> None:
    """With one wormhole sig in the parent, the jump uses it."""
    paste(simulation)
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.jump(c5.name)
    assert simulation.store.connection(HOME.id, c5.id).signature_on(HOME.id) == "ABC-123"
    assert simulation.store.signature(HOME.id, "ABC-123").hole_index == 1


def test_signature_details_carry_to_the_connection(simulation) -> None:
    """Type, mass and lifetime typed into a sig carry over once it is jumped, and later edits mirror."""
    paste(simulation)
    simulation.tracker.save_signature(HOME.id, "ABC-123", {"group": "wormhole", "type": "K162", "destination": "C5",
                                                           "mass": "critical", "lifetime": 1, "frigate_only": 0})
    signature = simulation.store.signature(HOME.id, "ABC-123")
    assert (signature.type, signature.destination, signature.mass, signature.lifetime) == ("K162", "C5", "critical", 1)
    assert signature.lifetime_set_at
    paste(simulation)  # pasting again keeps what was typed in (the scanner never shows these)
    assert simulation.store.signature(HOME.id, "ABC-123").mass == "critical"
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.jump(c5.name)
    connection = simulation.store.connection(HOME.id, c5.id)
    assert connection.signature_on(HOME.id) == "ABC-123"
    assert (connection.mass, connection.lifetime, connection.wormhole_type) == ("critical", 1, None)
    assert connection.lifetime_set_at == signature.lifetime_set_at
    assert simulation.tracker.save_signature(HOME.id, "ABC-123", {"group": "wormhole", "type": "H296",
                                                                  "mass": "reduced", "lifetime": 1}) == connection.id
    updated = simulation.store.connection(HOME.id, c5.id)
    assert (updated.mass, updated.wormhole_type, updated.lifetime_set_at) == ("reduced", "H296",
                                                                             connection.lifetime_set_at)


def test_saving_a_signature_as_a_site_unlinks_it(simulation) -> None:
    """A linked sig that turns out to be a data site no longer belongs to the connection."""
    paste(simulation)
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.jump(c5.name)
    assert simulation.tracker.save_signature(HOME.id, "ABC-123", {"group": "data"}) is None
    assert simulation.store.connection(HOME.id, c5.id).signature_on(HOME.id) is None


def test_save_signature_without_a_connection_and_bad_keys(simulation) -> None:
    """An unlinked sig just stores the values; unknown keys are rejected."""
    paste(simulation)
    assert simulation.tracker.save_signature(HOME.id, "GHI-789", {"name": "Gas"}) is None
    assert simulation.store.signature(HOME.id, "GHI-789").name == "Gas"
    with pytest.raises(ValueError):
        simulation.tracker.save_signature(HOME.id, "GHI-789", {"colour": "red"})
    with pytest.raises(TypeError):
        simulation.tracker.save_signature(HOME.id, "GHI-789", [("name", "x")])


def test_link_signature_for_missing_rows(simulation) -> None:
    """Linking a sig that doesn't exist reports False."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.jump(c5.name)
    connection = simulation.store.connection(HOME.id, c5.id)
    assert simulation.tracker.link_signature(connection.id, HOME.id, "NOP-000", None) is False


def test_frigate_hole_marks_the_connection(simulation) -> None:
    """A sig of a frigate-size type makes the connection frigate-only."""
    paste(simulation)
    simulation.tracker.save_signature(HOME.id, "ABC-123", {"type": "E004"})
    c1 = pick_system("C1")
    simulation.jump(c1.name)
    connection = simulation.store.connection(HOME.id, c1.id)
    assert connection.frigate_only == 1 and connection.wormhole_type == "E004"


def test_expire_drops_old_connections_and_orphans(simulation) -> None:
    """Connections go after their lifetime; systems left alone go with them."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
    simulation.jump(HOME.name)
    connection = simulation.store.connection(HOME.id, c5.id)
    created = datetime.fromisoformat(connection.created_at)
    assert simulation.tracker.expire(created + timedelta(hours=47)) == 0
    simulation.store.update_connection(connection.id, lifetime=1,
                                       lifetime_set_at=(created + timedelta(hours=1)).isoformat())
    assert simulation.tracker.expire(created + timedelta(hours=6)) == 1
    assert not simulation.store.has_system(c5.id) and simulation.store.has_system(HOME.id)
    with pytest.raises(TypeError):
        simulation.tracker.expire("now")


def test_expire_clears_the_parent_of_systems_that_stay(simulation) -> None:
    """A pinned child whose connection expired stays, without a parent."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
    simulation.jump(HOME.name)
    simulation.store.update_system(c5.id, pinned=1)
    connection = simulation.store.connection(HOME.id, c5.id)
    assert simulation.tracker.expire(datetime.fromisoformat(connection.created_at) + timedelta(days=3)) == 1
    assert simulation.store.has_system(c5.id) and simulation.store.system(c5.id).parent_id is None


def test_expire_removes_an_unpinned_system_nobody_is_in(simulation) -> None:
    """Without the pin the same expired child goes, so the pin is what keeps it."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
    simulation.jump(HOME.name)
    connection = simulation.store.connection(HOME.id, c5.id)
    assert simulation.tracker.expire(datetime.fromisoformat(connection.created_at) + timedelta(days=3)) == 1
    assert not simulation.store.has_system(c5.id)


def test_expire_keeps_systems_no_expired_connection_touched(simulation) -> None:
    """A hand-added system without a link survives an unrelated expiry; the expired end goes."""
    c5 = pick_system("C5", exclude={HOME.id})
    c3 = pick_system("C3", exclude={HOME.id, c5.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
    simulation.jump(HOME.name)
    assert simulation.tracker.ensure_system(c3.id, confirmed=False)
    connection = simulation.store.connection(HOME.id, c5.id)
    assert simulation.tracker.expire(datetime.fromisoformat(connection.created_at) + timedelta(days=3)) == 1
    assert not simulation.store.has_system(c5.id)
    assert simulation.store.has_system(c3.id)


def test_expire_keeps_the_system_a_character_is_in(simulation) -> None:
    """The far end of an expired connection stays while a pilot sits in it, without a parent."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
    connection = simulation.store.connection(HOME.id, c5.id)
    assert simulation.tracker.expire(datetime.fromisoformat(connection.created_at) + timedelta(days=3)) == 1
    assert simulation.store.has_system(c5.id) and simulation.store.system(c5.id).parent_id is None


def test_expire_keeps_the_chain_behind_an_expired_connection(simulation) -> None:
    """Only the expired connection's ends are checked: a system that still links on down the chain stays."""
    c5 = pick_system("C5", exclude={HOME.id})
    c3 = pick_system("C3", exclude={HOME.id, c5.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
    simulation.jump(c3.name)
    simulation.jump(HOME.name)
    first_link = simulation.store.connection(HOME.id, c5.id)
    created = datetime.fromisoformat(first_link.created_at)
    simulation.store.update_connection(first_link.id, lifetime=LIFETIME_1H, lifetime_set_at=created.isoformat())
    assert simulation.tracker.expire(created + timedelta(hours=2)) == 1
    assert simulation.store.connection(c5.id, c3.id)
    assert simulation.store.has_system(c5.id) and simulation.store.has_system(c3.id)
    assert simulation.store.system(c5.id).parent_id is None
    assert simulation.store.system(c3.id).parent_id == c5.id


def test_lifetime_levels_expire_and_k162_size(simulation) -> None:
    """K162 size comes from the sig; < 4h and < 1h count from when they were set."""
    paste(simulation)
    simulation.tracker.save_signature(HOME.id, "ABC-123", {"group": "wormhole", "type": "K162", "destination": "C5",
                                                           "size": "L", "lifetime": LIFETIME_1H, "mass": "fresh",
                                                           "frigate_only": 0})
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.jump(c5.name)
    connection = simulation.store.connection(HOME.id, c5.id)
    assert (connection.size, connection.lifetime, connection.frigate_only) == ("L", LIFETIME_1H, 0)
    simulation.store.update_connection(connection.id, lifetime=LIFETIME_1H)
    assert simulation.store.connection_by_id(connection.id).lifetime_set_at == connection.lifetime_set_at
    simulation.store.update_connection(connection.id, lifetime=LIFETIME_4H)
    set_at = datetime.fromisoformat(simulation.store.connection_by_id(connection.id).lifetime_set_at)
    assert simulation.tracker.expire(now=set_at + timedelta(hours=2)) == 0
    simulation.store.update_connection(connection.id, lifetime=LIFETIME_1H, lifetime_set_at=set_at.isoformat())
    assert simulation.tracker.expire(now=set_at + timedelta(hours=2)) == 1


def test_hole_size_prefers_the_type() -> None:
    """The type's size wins over the one set by hand; K162 uses the hand-set size."""
    assert hole_size(UNIVERSE, "E004", None) == "S" and hole_size(UNIVERSE, "H296", "S") == "XL"
    assert hole_size(UNIVERSE, "K162", "M") == "M" and hole_size(UNIVERSE, None, None) is None
    with pytest.raises(TypeError):
        hole_size(UNIVERSE, 296, None)


def test_signature_question_only_lists_holes_that_can_lead_here(simulation) -> None:
    """Typed sigs that lead elsewhere are not offered; unknown and K162 always are."""
    paste(simulation, "AAA-111\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n"
                      "BBB-222\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n"
                      "CCC-333\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n"
                      "DDD-444\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n")
    simulation.store.update_signature(HOME.id, "AAA-111", type="H296")
    simulation.store.update_signature(HOME.id, "BBB-222", type="M001")
    simulation.store.update_signature(HOME.id, "CCC-333", type="K162", destination="C2")
    c4 = pick_system("C4")
    request = simulation.naming_request(simulation.jump(c4.name))
    assert request.guess.applies is False
    assert sorted(request.signature_candidates) == ["BBB-222", "CCC-333", "DDD-444"]


def test_single_possible_signature_is_linked_without_asking(simulation) -> None:
    """When only one typed sig can lead here, it is linked and nothing is asked."""
    paste(simulation, "AAA-111\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n"
                      "BBB-222\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t1 AU\n")
    simulation.store.update_signature(HOME.id, "AAA-111", type="H296")
    simulation.store.update_signature(HOME.id, "BBB-222", type="M001")
    c4 = pick_system("C4")
    events = simulation.jump(c4.name)
    assert not any(isinstance(event, NamingRequest) for event in events)
    assert simulation.store.connection(HOME.id, c4.id).signature_on(HOME.id) == "BBB-222"


def test_hand_drawn_connection_is_unconfirmed_until_jumped(simulation) -> None:
    """A hand-drawn connection and its new system are confirmed by a jump."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.add_wormhole(HOME.id, c5.id, manual=True)
    connection = simulation.store.connection(HOME.id, c5.id)
    assert not connection.confirmed and not simulation.store.system(c5.id).confirmed
    assert simulation.store.system(HOME.id).confirmed
    events = simulation.jump(c5.name)
    assert any(isinstance(event, MapChanged) for event in events)
    assert simulation.store.connection(HOME.id, c5.id).confirmed and simulation.store.system(c5.id).confirmed


def test_visiting_a_hand_added_system_confirms_only_the_system(simulation) -> None:
    """Logging in somewhere proves the system, not the connection."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.add_wormhole(HOME.id, c5.id, manual=True)
    simulation.jump(c5.name, first=True, character=2)
    assert simulation.store.system(c5.id).confirmed
    assert not simulation.store.connection(HOME.id, c5.id).confirmed
    assert simulation.tracker.confirm(system_id=c5.id, with_connections=True)
    assert simulation.store.connection(HOME.id, c5.id).confirmed
    assert simulation.tracker.confirm(system_id=c5.id, with_connections=True) is False


def test_add_wormhole_rejects_a_loop_and_bad_ids(simulation) -> None:
    """A wormhole needs two different, valid systems."""
    with pytest.raises(ValueError):
        simulation.tracker.add_wormhole(HOME.id, HOME.id)
    with pytest.raises(TypeError):
        simulation.tracker.add_wormhole("J161940", HOME.id)


def test_jump_from_outside_into_a_known_system_hangs_the_new_side_under_it(simulation) -> None:
    """Entering home from an unmapped system puts that system under home."""
    simulation.jump("Jita", first=True)
    jita = UNIVERSE.by_name("Jita")
    simulation.jump(HOME.name)
    assert simulation.store.system(jita.id).parent_id == HOME.id
    assert simulation.store.system(jita.id).x < simulation.store.system(HOME.id).x


def test_systems_off_home_stack_below_each_other() -> None:
    """Systems without a neighbour are placed below the lowest one."""
    store = Store(":memory:")
    tracker = Tracker(store, UNIVERSE, Namer(store, UNIVERSE, home_id=HOME.id))
    assert tracker.ensure_system(HOME.id)
    assert not tracker.ensure_system(HOME.id)
    jita = UNIVERSE.by_name("Jita").id
    tracker.ensure_system(jita)
    assert store.system(jita).y > store.system(HOME.id).y


def test_first_change_of_a_session_is_not_a_jump() -> None:
    """A login in a w-space system doesn't connect it to where the character was before."""
    store = Store(":memory:")
    tracker = Tracker(store, UNIVERSE, Namer(store, UNIVERSE, home_id=HOME.id))
    now = datetime(2026, 10, 30, tzinfo=timezone.utc)
    tracker.on_system_change(SystemChange(5, "Alt", "Jita", now, first=True))
    tracker.on_system_change(SystemChange(5, "Alt", HOME.name, now, first=True))
    assert store.connections() == []


def test_expires_at_is_the_earlier_limit_and_agrees_with_expire(simulation) -> None:
    """A < 4h hole of a short-lived type goes at the type's limit, not 4h after the state was set."""
    c1 = pick_system("C1")
    simulation.jump(c1.name)
    store = simulation.store
    connection = store.connection(HOME.id, c1.id)
    store.update_connection(connection.id, wormhole_type="E004")
    connection = store.connection_by_id(connection.id)
    created = datetime.fromisoformat(connection.created_at)
    type_limit = created + timedelta(hours=UNIVERSE.wormhole_type("E004").lifetime_hours)
    assert expires_at(connection, UNIVERSE) == type_limit
    store.update_connection(connection.id, lifetime=LIFETIME_4H,
                            lifetime_set_at=(created + timedelta(hours=3)).isoformat())
    connection = store.connection_by_id(connection.id)
    assert expires_at(connection, UNIVERSE) == type_limit < created + timedelta(hours=7)
    store.update_connection(connection.id, lifetime=LIFETIME_1H,
                            lifetime_set_at=(created + timedelta(hours=1)).isoformat())
    connection = store.connection_by_id(connection.id)
    assert expires_at(connection, UNIVERSE) == created + timedelta(hours=1) + LIFETIME_REMAINING[LIFETIME_1H]
    with pytest.raises(TypeError):
        expires_at("x", UNIVERSE)


def test_removed_in_matches_when_expire_drops_the_connection(simulation) -> None:
    """The tooltip countdown ends exactly where Tracker.expire drops a short-lived < 4h hole."""
    c1 = pick_system("C1")
    simulation.jump(c1.name)
    store = simulation.store
    connection = store.connection(HOME.id, c1.id)
    now = datetime.now(timezone.utc)
    store.update_connection(connection.id, lifetime=LIFETIME_4H, lifetime_set_at=now.isoformat(),
                            wormhole_type="E004", created_at=(now - timedelta(hours=4)).isoformat())
    connection = store.connection_by_id(connection.id)
    limit = expires_at(connection, UNIVERSE)
    assert removed_in(connection, UNIVERSE) in ("29 min", "30 min")
    assert simulation.tracker.expire(limit - timedelta(minutes=1)) == 0
    assert simulation.tracker.expire(limit + timedelta(minutes=1)) == 1

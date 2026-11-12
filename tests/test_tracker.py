"""Tracker: jump classification, map building, naming answers, signatures and expiry."""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import HOME, SCAN, UNIVERSE, MapSimulation, pick_system
from homebound.logs.parser import SystemChange
from homebound.mapdb.store import LIFETIME_1H, LIFETIME_4H, LIFETIME_REMAINING, Store
from homebound.mapping import signature_parser
from homebound.mapping.naming import Namer, ReturnType
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
    """Holes are numbered in the order they are found, and bookmarks follow the names."""
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


def test_answer_no_keeps_the_number(simulation) -> None:
    """Answering "not the static" only clears the flag: the first hole found stays 1."""
    c5 = pick_system("C5", exclude={HOME.id})
    request = simulation.naming_request(simulation.jump(c5.name))
    assert simulation.tracker.apply_naming(request, is_static=False) == 1
    assert simulation.namer.display(c5.id) == "1" and simulation.store.system(c5.id).static == 0


def test_apply_naming_after_the_child_expired(simulation) -> None:
    """A question whose system left the map while it was open is dropped: nothing changes, nothing raises."""
    c5 = pick_system("C5", exclude={HOME.id})
    request = simulation.naming_request(simulation.jump(c5.name))
    simulation.store.remove_system(request.child_id)
    assert simulation.tracker.apply_naming(request, is_static=True) is None
    assert simulation.store.system(request.child_id) is None


def test_apply_naming_after_the_parent_left(simulation) -> None:
    """The same when the parent system is no longer on the map."""
    c5 = pick_system("C5", exclude={HOME.id})
    request = simulation.naming_request(simulation.jump(c5.name))
    gone = NamingRequest(99999999, request.child_id, request.connection_id, request.guess)
    assert simulation.tracker.apply_naming(gone, is_static=True) is None


def test_preview_names_for_both_answers(simulation) -> None:
    """The naming question shows the name for both answers: the same, as the answer doesn't move the number."""
    c5 = pick_system("C5", exclude={HOME.id})
    request = simulation.naming_request(simulation.jump(c5.name))
    assert simulation.tracker.preview_names(request) == ("1", "1")
    assert simulation.tracker.preview_name(request, "NOP-000") == "1"   # a sig that isn't there
    with pytest.raises(TypeError):
        simulation.tracker.preview_names(None)


def test_chain_depth_position_and_letter_schemes() -> None:
    """Chain letters go in found order: the first hole is A (here the static), the second B."""
    for scheme, first, second in (("a2a", "A1a", "B1a"), ("letter_numeric", "A", "B")):
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


def test_a2a_letters_stay_when_a_sibling_goes() -> None:
    """Path 4: A2a, A2b, A2c; remove the second: the third is still A2c, and the next one is A2d."""
    simulation = MapSimulation("a2a")
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
    children = []
    for system_class in ("C1", "C2", "C4", "C6"):
        simulation.jump(c5.name)
        child = pick_system(system_class, exclude={HOME.id, c5.id})
        simulation.jump(child.name)
        children.append(child)
        if len(children) == 3:
            assert [simulation.namer.display(sibling.id) for sibling in children] == ["A2a", "A2b", "A2c"]
            simulation.store.remove_system(children[1].id)
    assert simulation.namer.display(children[2].id) == "A2c"
    assert simulation.namer.display(children[3].id) == "A2d"
    assert simulation.store.system(children[2].id).label == "A2c"


def test_a_removed_connection_added_back_keeps_the_number_and_name(simulation) -> None:
    """Path 5: detaching keeps hole number and label; linking the same systems again gives them back."""
    paste(simulation, "AAA-111\tCosmic Signature\tWormhole\t\t100%\t1 AU\n"
                      "BBB-222\tCosmic Signature\tWormhole\t\t100%\t1 AU\n")
    c3 = pick_system("C3")
    request = simulation.naming_request(simulation.jump(c3.name))
    simulation.tracker.apply_naming(request, is_static=False, signature_id="BBB-222")
    assert simulation.namer.display(c3.id) == "2"
    store = simulation.store
    store.remove_connection(store.connection(HOME.id, c3.id).id)
    store.clear_broken_parents()
    detached = store.system(c3.id)
    assert (detached.parent_id, detached.hole_index, detached.label) == (None, 2, "2")
    assert simulation.namer.display(c3.id) == "2"
    simulation.tracker.add_wormhole(HOME.id, c3.id, manual=True)
    attached = store.system(c3.id)
    assert (attached.parent_id, attached.hole_index, attached.label) == (HOME.id, 2, "2")
    assert store.connection(HOME.id, c3.id).signature_on(HOME.id) == "BBB-222"    # the sig with its number


def test_a_system_relinked_under_a_new_parent_gets_a_new_number_and_name(simulation) -> None:
    """Owner, 2026-11-12: its bookmark must fit the system it hangs off now; its children keep their names."""
    store = simulation.store
    c5, other, c3, c2 = (pick_system("C5", exclude={HOME.id}), pick_system("C4"), pick_system("C3"), pick_system("C2"))
    with store.batch():
        store.add_system(c5.id, parent_id=HOME.id, hole_index=1, label="1")
        store.add_system(other.id, parent_id=HOME.id, hole_index=2, label="2")
        store.add_system(c3.id, parent_id=c5.id, hole_index=1, label="11")
        store.add_system(c2.id, parent_id=c3.id, hole_index=1, label="111")
        for low, high in ((HOME.id, c5.id), (HOME.id, other.id), (c5.id, c3.id), (c3.id, c2.id)):
            store.add_connection(low, high)
    signature_parser.merge_signatures(store, other.id, signature_parser.parse_scanner_text(
        "AAA-111\tCosmic Signature\tWormhole\t\t100%\t1 AU\n"))
    store.delete_signatures(other.id, ["AAA-111"])          # number 1 of the other system was given out
    store.remove_connection(store.connection(c5.id, c3.id).id)
    store.clear_broken_parents()
    assert (store.system(c3.id).parent_id, store.system(c3.id).detached_from) == (None, c5.id)
    simulation.tracker.add_wormhole(other.id, c3.id, manual=True)
    moved = store.system(c3.id)
    assert (moved.parent_id, moved.hole_index, moved.label, moved.detached_from) == (other.id, 2, "22", None)
    assert store.system(c2.id).label == "111" and simulation.namer.display(c2.id) == "111"   # children keep theirs


def test_a_removed_parent_leaves_its_children_their_numbers(simulation) -> None:
    """Path 5 for remove_system and its undo: the children keep hole number and name."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
    child = pick_system("C2")
    simulation.jump(child.name)
    assert simulation.namer.display(child.id) == "11"
    store = simulation.store
    with store.batch() as action:
        store.remove_system(c5.id)
    assert (store.system(child.id).parent_id, store.system(child.id).hole_index) == (None, 1)
    assert simulation.namer.display(child.id) == "11"
    store.remove_system(child.id)
    store.undo(action)                       # c5 comes back; the child is gone, nothing to put back under it
    assert store.system(c5.id).hole_index == 1 and simulation.namer.display(c5.id) == "1"


def test_known_space_display_has_the_real_name(simulation) -> None:
    """K-space shows the scheme label and the real name."""
    jita = UNIVERSE.by_name("Jita")
    simulation.jump(jita.name)
    assert simulation.namer.display(jita.id) == "1 Jita"


def test_custom_name_wins(simulation) -> None:
    """A name typed into the naming question replaces the scheme name."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True,
                                    custom_name="Farm")
    assert simulation.namer.display(c5.id) == "Farm"


def test_changing_the_answer_to_a_sig_takes_its_number(simulation) -> None:
    """Re-asked later ("Change static / hole"), picking a sig moves the system to that sig's number."""
    c5 = pick_system("C5", exclude={HOME.id})
    request = simulation.naming_request(simulation.jump(c5.name))
    simulation.tracker.apply_naming(request, is_static=True)
    paste(simulation, "AAA-111\tCosmic Signature\tWormhole\t\t100%\t1 AU\n")
    assert simulation.store.signature(HOME.id, "AAA-111").hole_index == 2
    simulation.tracker.apply_naming(request, is_static=False, signature_id="AAA-111")
    assert simulation.store.system(c5.id).hole_index == 2 and simulation.store.system(c5.id).static == 0
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


def test_a_system_takes_the_number_of_the_sig_it_came_through(simulation) -> None:
    """Path 2: a sig whose bookmark was copied as -3 names the system behind it 3, static or not."""
    paste(simulation, "AAA-111\tCosmic Signature\tWormhole\t\t100%\t1 AU\n"
                      "BBB-222\tCosmic Signature\tWormhole\t\t100%\t1 AU\n"
                      "CCC-333\tCosmic Signature\tWormhole\t\t100%\t1 AU\n")
    simulation.store.delete_signatures(HOME.id, ["AAA-111", "BBB-222"])
    c5 = pick_system("C5", exclude={HOME.id})       # home's static class: the guess says static
    events = simulation.jump(c5.name)
    assert simulation.store.connection(HOME.id, c5.id).signature_on(HOME.id) == "CCC-333"
    assert simulation.store.system(c5.id).hole_index == 3 and simulation.namer.display(c5.id) == "3"
    assert simulation.store.signature(HOME.id, "CCC-333").hole_index == 3
    request = simulation.naming_request(events)
    assert simulation.tracker.apply_naming(request, is_static=True) == 3


def test_the_sig_picked_in_the_naming_question_gives_the_number(simulation) -> None:
    """Path 2 through the question: with two candidates the system waits for the answer, then takes the sig's number."""
    paste(simulation, "AAA-111\tCosmic Signature\tWormhole\t\t100%\t1 AU\n"
                      "BBB-222\tCosmic Signature\tWormhole\t\t100%\t1 AU\n")
    c3 = pick_system("C3")
    request = simulation.naming_request(simulation.jump(c3.name))
    assert request.signature_candidates == ["AAA-111", "BBB-222"]
    assert simulation.store.system(c3.id).hole_index == 3          # no sig yet: the counter's next
    assert simulation.tracker.preview_name(request, "BBB-222") == "2"
    assert simulation.tracker.apply_naming(request, is_static=False, signature_id="BBB-222") == 2
    assert simulation.namer.display(c3.id) == "2"
    assert simulation.store.signature(HOME.id, "BBB-222").hole_index == 2


def test_answering_static_flags_the_hole_and_keeps_its_number(simulation) -> None:
    """Path 3: "static" for a hole numbered 2 leaves it 2 and sets the flag; "not static" clears only the flag."""
    paste(simulation, "AAA-111\tCosmic Signature\tWormhole\t\t100%\t1 AU\n"
                      "BBB-222\tCosmic Signature\tWormhole\t\t100%\t1 AU\n")
    simulation.store.delete_signatures(HOME.id, ["AAA-111"])
    c5 = pick_system("C5", exclude={HOME.id})
    request = simulation.naming_request(simulation.jump(c5.name))
    assert simulation.store.system(c5.id).hole_index == 2 and simulation.store.system(c5.id).static == 1
    assert simulation.tracker.apply_naming(request, is_static=False) == 2
    assert simulation.store.system(c5.id).static == 0
    assert simulation.tracker.apply_naming(request, is_static=True) == 2
    assert simulation.store.system(c5.id).static == 1 and simulation.namer.display(c5.id) == "2"


def test_a_sig_set_to_wormhole_by_hand_gets_its_number(simulation) -> None:
    """DEF-456 has no group in the paste: it is numbered when the dialog makes it a wormhole, not before."""
    paste(simulation)
    assert simulation.store.signature(HOME.id, "DEF-456").hole_index is None
    simulation.tracker.save_signature(HOME.id, "DEF-456", {"group": "wormhole"})
    assert simulation.store.signature(HOME.id, "DEF-456").hole_index == 2
    simulation.tracker.save_signature(HOME.id, "DEF-456", {"name": "x"})
    assert simulation.store.signature(HOME.id, "DEF-456").hole_index == 2


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
    assert simulation.tracker.link_signature(connection.id, HOME.id, "NOP-000") is False


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


def record_actions(store: Store) -> list:
    """Collect every finished store action from now on."""
    actions: list = []
    store.listeners.append(actions.append)
    return actions


def test_a_wormhole_jump_is_one_undoable_action(simulation) -> None:
    """The jump's system and connection arrive as one action, and one undo takes both back."""
    simulation.jump(HOME.name, first=True)
    actions = record_actions(simulation.store)
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.jump(c5.name)
    assert len(actions) == 1
    assert {change.entity for change in actions[0].changes} >= {"system", "connection"}
    simulation.store.undo(actions[0])
    assert not simulation.store.has_system(c5.id) and simulation.store.connections() == []


def test_add_wormhole_is_one_undoable_action(simulation) -> None:
    """Adding a wormhole by hand is a single action."""
    c3 = pick_system("C3")
    actions = record_actions(simulation.store)
    simulation.tracker.add_wormhole(HOME.id, c3.id, manual=True)
    assert len(actions) == 1
    simulation.store.undo(actions[0])
    assert not simulation.store.has_system(c3.id) and simulation.store.connections() == []


def test_expiry_is_one_undoable_action(simulation) -> None:
    """Expiring a connection and its orphaned system is one action; undo brings both back."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
    simulation.jump(HOME.name)
    connection = simulation.store.connection(HOME.id, c5.id)
    actions = record_actions(simulation.store)
    assert simulation.tracker.expire(datetime.fromisoformat(connection.created_at) + timedelta(days=3)) == 1
    assert len(actions) == 1
    simulation.store.undo(actions[0])
    assert simulation.store.has_system(c5.id) and simulation.store.connection(HOME.id, c5.id) is not None


def test_alias_survives_expiry_and_names_the_system_when_tracked_again(simulation) -> None:
    """A remembered name outlives the system's place on the map and comes back when it is mapped again."""
    c5 = pick_system("C5", exclude={HOME.id})
    simulation.tracker.apply_naming(simulation.naming_request(simulation.jump(c5.name)), is_static=True)
    simulation.jump(HOME.name)
    simulation.store.set_alias(c5.id, "Staging")
    connection = simulation.store.connection(HOME.id, c5.id)
    assert simulation.tracker.expire(datetime.fromisoformat(connection.created_at) + timedelta(days=3)) == 1
    assert not simulation.store.has_system(c5.id) and simulation.store.alias(c5.id) == "Staging"
    request = simulation.naming_request(simulation.jump(c5.name))
    assert simulation.store.system(c5.id).custom_name == "Staging"
    simulation.tracker.apply_naming(request, is_static=True)       # an answer without a typed name keeps it
    assert simulation.store.system(c5.id).custom_name == "Staging"


# ---------- lost connection (disconnect) ----------

def build_chain(simulation: MapSimulation) -> tuple:
    """Home -> C5 -> C3 by two jumps, the pilot ending in C3; return both systems."""
    c5 = pick_system("C5", exclude={HOME.id})
    c3 = pick_system("C3", exclude={HOME.id, c5.id})
    simulation.jump(c5.name)
    simulation.jump(c3.name)
    return c5, c3


def test_disconnect_keeps_both_systems_and_marks_the_far_piece(simulation) -> None:
    """The far side and what is behind it stay, the hole's sigs go, and only the piece away from home is marked."""
    c5, c3 = build_chain(simulation)
    paste(simulation)
    link = simulation.store.connection(HOME.id, c5.id)
    simulation.store.set_connection_signature(link.id, HOME.id, "ABC-123")
    number, label = simulation.store.system(c5.id).hole_index, simulation.store.system(c5.id).label
    now = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
    assert simulation.tracker.disconnect(link.id, now) == sorted([c5.id, c3.id])
    store = simulation.store
    assert store.connection(HOME.id, c5.id) is None and store.connection(c5.id, c3.id)
    assert all(store.has_system(system_id) for system_id in (HOME.id, c5.id, c3.id))
    assert store.signature(HOME.id, "ABC-123") is None and store.signature(HOME.id, "DEF-456")
    assert simulation.tracker.cut_off_since(c5.id) == now
    assert simulation.tracker.cut_off_since(c3.id) is None and simulation.tracker.cut_off_since(HOME.id) is None
    assert store.system(c5.id).parent_id is None and store.system(c3.id).parent_id == c5.id
    assert (store.system(c5.id).hole_index, store.system(c5.id).label) == (number, label)   # numbers and names stay


def test_disconnect_inside_a_cut_off_piece_marks_only_the_part_without_a_mark(simulation) -> None:
    """Cutting a piece again keeps its countdown; the half that has none gets one, so it can go too."""
    c5, c3 = build_chain(simulation)
    first = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
    simulation.tracker.disconnect(simulation.store.connection(HOME.id, c5.id).id, first)
    later = first + timedelta(minutes=20)
    assert simulation.tracker.disconnect(simulation.store.connection(c5.id, c3.id).id, later) == [c3.id]
    assert simulation.tracker.cut_off_since(c5.id) == first and simulation.tracker.cut_off_since(c3.id) == later
    assert simulation.tracker.disconnect(31337) == []


def test_disconnect_a_loop_marks_nothing(simulation) -> None:
    """A link whose ends still reach home another way cuts nobody off."""
    c5, c3 = build_chain(simulation)
    simulation.tracker.add_wormhole(HOME.id, c3.id)
    assert simulation.tracker.disconnect(simulation.store.connection(c5.id, c3.id).id) == []
    assert simulation.tracker.cut_off_since(c5.id) is None and simulation.store.has_system(c5.id)


def test_disconnect_checks_its_arguments(simulation) -> None:
    """The ID must be an integer and the time a datetime."""
    with pytest.raises(TypeError):
        simulation.tracker.disconnect("one")
    with pytest.raises(TypeError):
        simulation.tracker.disconnect(1, "now")


def test_cut_off_piece_stays_while_a_pilot_is_in_it_and_goes_an_hour_after(simulation) -> None:
    """A character inside keeps the piece; it goes one hour after they leave."""
    c5, c3 = build_chain(simulation)
    now = simulation.time
    simulation.tracker.disconnect(simulation.store.connection(HOME.id, c5.id).id, now)
    assert simulation.tracker.expire(now + timedelta(hours=5)) == 0
    assert simulation.store.has_system(c5.id) and simulation.store.has_system(c3.id)   # the pilot is in c3
    simulation.store.set_character(1, "Pilot", HOME.id, (now + timedelta(hours=5)).isoformat())
    assert simulation.tracker.expire(now + timedelta(hours=5, minutes=59)) == 0
    assert simulation.store.has_system(c5.id)
    assert simulation.tracker.expire(now + timedelta(hours=6, minutes=1)) == 1
    assert not simulation.store.has_system(c5.id) and not simulation.store.has_system(c3.id)
    assert simulation.store.has_system(HOME.id) and simulation.tracker.cut_off_since(c5.id) is None


def test_cut_off_piece_with_nobody_goes_after_the_grace_and_pinned_stays(simulation) -> None:
    """With nobody inside it goes one hour after the cut; a pinned system in it stays, without its links."""
    c5, c3 = build_chain(simulation)
    now = simulation.time
    simulation.store.set_character(1, "Pilot", HOME.id, now.isoformat())   # the pilot got out before the hole closed
    simulation.tracker.disconnect(simulation.store.connection(HOME.id, c5.id).id, now)
    simulation.store.update_system(c3.id, pinned=1)
    assert simulation.tracker.expire(now + timedelta(minutes=59)) == 0
    assert simulation.tracker.expire(now + timedelta(hours=1)) == 1
    assert not simulation.store.has_system(c5.id) and simulation.store.has_system(c3.id)
    assert simulation.store.system(c3.id).parent_id is None


def test_a_jump_that_joins_the_piece_back_clears_the_mark(simulation) -> None:
    """When a new connection reaches home again the piece isn't cut off any more, and it isn't removed."""
    c5, c3 = build_chain(simulation)
    now = simulation.time
    simulation.tracker.disconnect(simulation.store.connection(HOME.id, c5.id).id, now)
    simulation.jump(HOME.name)
    simulation.jump(c3.name)   # found a new way from home into the piece
    assert simulation.store.connection(HOME.id, c3.id)
    assert simulation.tracker.cut_off_since(c5.id) is None
    simulation.tracker.expire(now + timedelta(hours=3))
    assert simulation.store.has_system(c5.id) and simulation.store.has_system(c3.id)


def test_disconnect_is_one_undo_step(simulation) -> None:
    """Undo brings back the connection and both sigs, and the piece is no longer cut off."""
    c5, c3 = build_chain(simulation)
    paste(simulation)
    link = simulation.store.connection(HOME.id, c5.id)
    simulation.store.set_connection_signature(link.id, HOME.id, "ABC-123")
    with simulation.store.batch() as action:
        simulation.tracker.disconnect(link.id)
    assert action
    simulation.store.undo(action)
    assert simulation.store.connection(HOME.id, c5.id).signature_on(HOME.id) == "ABC-123"
    assert simulation.store.signature(HOME.id, "ABC-123") and simulation.store.system(c5.id).parent_id == HOME.id
    assert simulation.tracker.cut_off_since(c5.id) is None


# ---------- return hole ----------

WH_SCAN = ("KLM-111\tCosmic Signature\tWormhole\tUnstable Wormhole\t100,0%\t1 AU\n"
           "NOP-222\tCosmic Signature\tWormhole\tUnstable Wormhole\t100,0%\t2 AU\n")


def jump_with_scans(simulation: MapSimulation, name: str, far_type: str | None = None):
    """Paste one wormhole into home (typed ``far_type``), jump home -> ``name``, paste two wormholes there.

    Returns:
        The new system and the connection to home.
    """
    paste(simulation)
    if far_type:
        simulation.store.update_signature(HOME.id, "ABC-123", type=far_type)
    system = UNIVERSE.by_name(name)
    simulation.jump(name)
    signature_parser.merge_signatures(simulation.store, system.id, signature_parser.parse_scanner_text(WH_SCAN))
    return system, simulation.store.connection(HOME.id, system.id)


def test_return_hole_behind_a_real_type_is_a_k162_to_the_origin(simulation) -> None:
    """Home's sig is H296: the sig in B becomes K162 towards home's class, linked on B's side, and keeps its number."""
    b, connection = jump_with_scans(simulation, pick_system("C5", exclude={HOME.id}).name, "H296")
    assert connection.signature_on(HOME.id) == "ABC-123" and connection.signature_on(b.id) is None
    number = simulation.store.signature(b.id, "NOP-222").hole_index
    assert simulation.tracker.mark_return(b.id, "NOP-222", connection.id) == ReturnType("K162")
    sig = simulation.store.signature(b.id, "NOP-222")
    assert (sig.type, sig.destination, sig.hole_index) == ("K162", "C5", number)
    assert simulation.store.connection(HOME.id, b.id).signature_on(b.id) == "NOP-222"
    assert simulation.store.connection(HOME.id, b.id).wormhole_type == "H296"


def test_return_hole_behind_a_k162_takes_the_only_type_that_fits(simulation) -> None:
    """Origin's sig is K162 and exactly one type leads from B to the origin's class: it is set."""
    c1 = pick_system("C1")
    simulation.jump(c1.name)
    signature_parser.merge_signatures(simulation.store, c1.id, signature_parser.parse_scanner_text(WH_SCAN[:WH_SCAN.index("\n") + 1]))
    simulation.store.update_signature(c1.id, "KLM-111", type="K162")
    b = pick_system("C5", exclude={HOME.id})
    simulation.jump(b.name)
    signature_parser.merge_signatures(simulation.store, b.id, signature_parser.parse_scanner_text(WH_SCAN))
    connection = simulation.store.connection(c1.id, b.id)
    assert simulation.tracker.mark_return(b.id, "NOP-222", connection.id) == ReturnType("E004", ("E004",))
    sig = simulation.store.signature(b.id, "NOP-222")
    assert (sig.type, sig.destination) == ("E004", "C1")
    assert simulation.store.connection(c1.id, b.id).wormhole_type == "E004"


def test_return_hole_with_several_candidates_leaves_the_type_empty(simulation) -> None:
    """Two types lead from B to the origin's class: nothing is guessed, the candidates come back."""
    b, connection = jump_with_scans(simulation, pick_system("C3").name, "K162")
    assert simulation.tracker.mark_return(b.id, "NOP-222", connection.id) == ReturnType(None, ("C008", "N770"))
    sig = simulation.store.signature(b.id, "NOP-222")
    assert (sig.type, sig.destination) == (None, "C5")
    assert simulation.store.connection(HOME.id, b.id).signature_on(b.id) == "NOP-222"


def test_return_hole_with_an_unknown_origin_side_sets_only_the_destination(simulation) -> None:
    """Without a type on the origin's side (or the connection) only where it leads is set."""
    b, connection = jump_with_scans(simulation, pick_system("C3").name)
    assert simulation.tracker.mark_return(b.id, "NOP-222", connection.id) == ReturnType()
    sig = simulation.store.signature(b.id, "NOP-222")
    assert (sig.type, sig.destination) == (None, "C5")


def test_return_hole_copies_the_state_of_the_connection(simulation) -> None:
    """Mass, lifetime, size and frigate-only of the connection show on the sig, so both ends agree."""
    b, connection = jump_with_scans(simulation, pick_system("C5", exclude={HOME.id}).name, "H296")
    simulation.store.update_connection(connection.id, mass="reduced", lifetime=LIFETIME_1H, size="L", frigate_only=1)
    simulation.tracker.mark_return(b.id, "NOP-222", connection.id)
    sig = simulation.store.signature(b.id, "NOP-222")
    stored = simulation.store.connection(HOME.id, b.id)
    assert (sig.mass, sig.lifetime, sig.size, sig.frigate_only) == ("reduced", LIFETIME_1H, "L", 1)
    assert sig.lifetime_set_at == stored.lifetime_set_at and stored.lifetime == LIFETIME_1H


def test_return_hole_takes_what_the_sig_already_knew(simulation) -> None:
    """Details set on the sig before it was marked carry over to the connection, as link_signature does."""
    b, connection = jump_with_scans(simulation, pick_system("C5", exclude={HOME.id}).name, "H296")
    simulation.store.update_signature(b.id, "NOP-222", mass="critical")
    simulation.tracker.mark_return(b.id, "NOP-222", connection.id)
    assert simulation.store.connection(HOME.id, b.id).mass == "critical"
    assert simulation.store.signature(b.id, "NOP-222").mass == "critical"


def test_marking_another_sig_unlinks_the_first(simulation) -> None:
    """A connection has one sig per side: the newly marked one replaces the old one, which stays a sig."""
    b, connection = jump_with_scans(simulation, pick_system("C5", exclude={HOME.id}).name, "H296")
    simulation.tracker.mark_return(b.id, "KLM-111", connection.id)
    number = simulation.store.signature(b.id, "KLM-111").hole_index
    simulation.tracker.mark_return(b.id, "NOP-222", connection.id)
    assert simulation.store.connection(HOME.id, b.id).signature_on(b.id) == "NOP-222"
    assert simulation.store.signature(b.id, "KLM-111").hole_index == number


def test_return_hole_from_a_child_takes_the_childs_number(simulation) -> None:
    """Coming back from a child: the sig is that connection's, and shares the child's hole number, not -0."""
    c5 = pick_system("C5", exclude={HOME.id})
    paste(simulation)
    simulation.jump(c5.name)                       # ABC-123 is linked to it; the child has its number
    child_number = simulation.store.system(c5.id).hole_index
    simulation.jump(HOME.name)                     # back through the same hole
    assert simulation.tracker.return_origin(HOME.id, 1) == c5.id
    connection = simulation.store.connection(HOME.id, c5.id)
    simulation.store.update_signature(HOME.id, "DEF-456", group="wormhole")
    own = simulation.store.signature(HOME.id, "DEF-456").hole_index
    simulation.tracker.mark_return(HOME.id, "DEF-456", connection.id)
    assert simulation.store.connection(HOME.id, c5.id).signature_on(HOME.id) == "DEF-456"
    assert simulation.store.signature(HOME.id, "DEF-456").hole_index == child_number
    assert simulation.store.signature(HOME.id, "ABC-123").hole_index == own      # swapped, so no number is twice


def test_return_origin_prefers_the_jump_then_the_parent(simulation) -> None:
    """The origin is where the character came from into the system; else the parent; else nothing."""
    c5 = pick_system("C5", exclude={HOME.id})
    c3 = pick_system("C3")
    simulation.jump(c5.name)
    simulation.jump(c3.name)
    assert simulation.tracker.return_origin(c3.id, 1) == c5.id
    assert simulation.tracker.return_origin(c3.id, 99) == c5.id       # another pilot: the parent
    assert simulation.tracker.return_origin(c3.id, None) == c5.id
    assert simulation.tracker.return_origin(HOME.id, 1) is None       # home has no parent and nobody jumped in
    simulation.store.remove_connection(simulation.store.connection(c5.id, c3.id).id)
    assert simulation.tracker.return_origin(c3.id, 1) is None


def test_mark_return_is_one_undoable_action(simulation) -> None:
    """The link, the type and the copied state come back with one undo."""
    b, connection = jump_with_scans(simulation, pick_system("C5", exclude={HOME.id}).name, "H296")
    before = simulation.store.signature(b.id, "NOP-222")
    actions = record_actions(simulation.store)
    simulation.tracker.mark_return(b.id, "NOP-222", connection.id)
    assert len(actions) == 1
    simulation.store.undo(actions[0])
    assert simulation.store.signature(b.id, "NOP-222") == before
    assert simulation.store.connection(HOME.id, b.id).signature_on(b.id) is None


def test_mark_return_checks_its_arguments(simulation) -> None:
    """Only a wormhole sig of an end of the connection can be marked; unknown things do nothing."""
    b, connection = jump_with_scans(simulation, pick_system("C5", exclude={HOME.id}).name, "H296")
    signature_parser.merge_signatures(simulation.store, b.id, signature_parser.parse_scanner_text(
        WH_SCAN + "GHI-789\tCosmic Signature\tGas Site\tBarren Perimeter Reservoir\t100.0%\t2 AU\n"))
    assert simulation.tracker.mark_return(b.id, "NOP-222", 31337) is None
    assert simulation.tracker.mark_return(b.id, "ZZZ-999", connection.id) is None
    with pytest.raises(ValueError):
        simulation.tracker.mark_return(b.id, "GHI-789", connection.id)
    with pytest.raises(ValueError):
        simulation.tracker.mark_return(pick_system("C1").id, "NOP-222", connection.id)
    with pytest.raises(TypeError):
        simulation.tracker.mark_return(b.id, 5, connection.id)


def test_return_candidates_name_the_sigs_that_fit(simulation) -> None:
    """Only unlinked wormhole sigs of the right type fit; none once the connection has its side."""
    b, connection = jump_with_scans(simulation, pick_system("C5", exclude={HOME.id}).name, "H296")
    assert simulation.tracker.return_candidates(b.id, HOME.id) == []           # two unlinked sigs, neither a K162
    simulation.store.update_signature(b.id, "NOP-222", type="K162")
    assert simulation.tracker.return_candidates(b.id, HOME.id) == ["NOP-222"]
    simulation.store.update_signature(b.id, "KLM-111", type="K162")
    assert simulation.tracker.return_candidates(b.id, HOME.id) == ["KLM-111", "NOP-222"]
    simulation.tracker.mark_return(b.id, "KLM-111", connection.id)
    assert simulation.tracker.return_candidates(b.id, HOME.id) == []           # the connection has its B side now

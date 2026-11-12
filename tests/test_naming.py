"""Naming schemes, hole indices and the static guess."""
import sqlite3

import pytest

from conftest import HOME, UNIVERSE, MapSimulation, drop_columns, pick_system
from homebound.mapdb.store import Signature, Store
from homebound.mapping.naming import (SCHEMES, rename_system, ChainDepthPositionScheme, Namer, Scheme, chain_letter,
                                      freeze_legacy_names, guess_static, label_unnamed_systems, number_for_letters,
                                      hole_digit, hole_path, is_static_signature, letters_for_number, level_positions,
                                      next_level_index, ReturnType, return_type, scheme_for_key, signature_destination,
                                      static_slot_count, static_slot_for_class, take_hole_number)


def test_hole_digits() -> None:
    """Indices below 10 are digits, then letters."""
    assert [hole_digit(index) for index in (0, 1, 9, 10, 11)] == ["0", "1", "9", "a", "b"]
    with pytest.raises(ValueError):
        hole_digit(-1)


def test_hole_digits_past_z_are_bracketed_numbers() -> None:
    """Index 35 is still ``z``; 36 and up are the number in brackets, never punctuation like ``{``."""
    assert [hole_digit(hole_index) for hole_index in (35, 36, 37, 100)] == ["z", "(36)", "(37)", "(100)"]
    assert SCHEMES["numeric"].name([1, 36], None) == "1(36)"
    assert SCHEMES["letter_numeric"].name([3, 61], None) == "Z(61)"


def test_next_level_index_counts_the_same_depth_in_the_same_chain() -> None:
    """The count matches the systems whose hole path has the same root, depth and first hop."""
    store = Store(":memory:")
    store.add_system(1)
    store.add_system(2, parent_id=1, hole_index=1)
    store.add_system(3, parent_id=1, hole_index=2)
    store.add_system(4, parent_id=2, hole_index=2)
    store.add_system(5, parent_id=2, hole_index=3)
    store.add_system(6, parent_id=3, hole_index=2)
    store.add_system(7, parent_id=4, hole_index=2)
    store.add_system(8)
    store.add_system(9, parent_id=8, hole_index=1)
    assert next_level_index(store, 1, [1]) == 1
    assert next_level_index(store, 1, [1, 5]) == 2
    assert next_level_index(store, 1, [2, 5]) == 1
    assert next_level_index(store, 1, [1, 2, 5]) == 1
    assert next_level_index(store, 1, [1, 2, 2, 5]) == 0
    assert next_level_index(store, 1, [3]) == 0
    assert next_level_index(store, 8, [1, 1]) == 0
    assert next_level_index(store, 1, []) == 0


def test_level_positions_are_derived_from_when_systems_were_added() -> None:
    """Order at a depth comes from (added_at, id), not from a stored counter, so every copy agrees."""
    store = Store(":memory:")
    store.add_system(1)
    store.add_system(4, parent_id=1, hole_index=1, added_at="2026-01-01T00:00:03+00:00")
    store.add_system(2, parent_id=4, hole_index=2, added_at="2026-01-01T00:00:02+00:00")
    store.add_system(3, parent_id=4, hole_index=3, added_at="2026-01-01T00:00:02+00:00")
    store.add_system(5, parent_id=4, hole_index=4, added_at="2026-01-01T00:00:01+00:00")
    assert level_positions(store) == {4: 0, 5: 0, 2: 1, 3: 2}
    store.remove_system(5)
    assert level_positions(store) == {4: 0, 2: 0, 3: 1}
    namer = Namer(store, UNIVERSE)
    assert namer.level_index(3) == 1 and namer.level_index(1) is None


def test_next_level_index_reads_only_the_chain_behind_the_first_hop() -> None:
    """The count walks down from the root, not up from every system on the map."""
    store = Store(":memory:")
    store.add_system(1)
    for system_id in range(2, 202):
        store.add_system(system_id, parent_id=1, hole_index=system_id)
    store.add_system(500, parent_id=1, hole_index=1)
    store.add_system(501, parent_id=500, hole_index=2)
    visited: list[int] = []
    original_children = store.children

    def counting_children(system_id: int) -> list:
        """Record which systems' children are read."""
        visited.append(system_id)
        return original_children(system_id)

    store.children = counting_children
    store.systems = lambda: pytest.fail("next_level_index must not list the whole map")
    assert next_level_index(store, 1, [1, 3]) == 1
    assert visited == [1, 500]


def test_chain_letters_follow_eve_uni() -> None:
    """A, B, then Z, Y, W …, never letters that are hard to tell apart; with static slots (old maps) as before."""
    assert [chain_letter(hole, 1) for hole in range(1, 6)] == ["A", "Z", "Y", "W", "U"]
    assert [chain_letter(hole, 2) for hole in range(1, 5)] == ["A", "B", "Z", "Y"]
    assert chain_letter(1, 0) == "Z" and chain_letter(0, 1) == "?"
    assert not {chain_letter(hole, 2) for hole in range(1, 16)} & set("CFGHJLNPTVX")
    assert chain_letter(14, 1) == "D" and chain_letter(15, 1) == "ZZ"
    with pytest.raises(TypeError):
        chain_letter("1")


def test_chain_letters_go_in_found_order() -> None:
    """Home's holes 1, 2, 3 are chains A, B, Z whatever its statics; a k-space home starts at A too."""
    store = Store(":memory:")
    namer = Namer(store, UNIVERSE, "a2a", HOME.id)
    store.add_system(HOME.id)
    children = [system for system in UNIVERSE.all_systems() if system.system_class == "C2"][:3]
    for hole, child in enumerate(children, start=1):
        store.add_system(child.id, parent_id=HOME.id, hole_index=hole)
    assert [namer.display(child.id) for child in children] == ["A1a", "B1a", "Z1a"]
    assert [chain_letter(hole) for hole in range(1, 5)] == ["A", "B", "Z", "Y"]
    assert "static" not in SCHEMES["numeric"].description and "A = static" not in SCHEMES["a2a"].description


def test_level_positions_are_never_given_twice() -> None:
    """A stored a2a label holds its position: with A2a and A2c left, the next one at that depth is A2d."""
    store = Store(":memory:")
    store.add_system(1)
    store.add_system(2, parent_id=1, hole_index=1)
    store.add_system(3, parent_id=2, hole_index=1, label="A2a")
    store.add_system(4, parent_id=2, hole_index=3, label="A2c")
    store.add_system(5, parent_id=2, hole_index=4)
    assert next_level_index(store, 1, [1, 9]) == 3
    assert next_level_index(store, 1, [1, 4], exclude=5) == 3
    assert Namer(store, UNIVERSE, "a2a").attach_label(5) == "A2d" and Namer(store, UNIVERSE).attach_label(1) is None
    assert number_for_letters("a") == 1 and number_for_letters("aa") == 27
    with pytest.raises(ValueError):
        number_for_letters("a1")
    assert SCHEMES["a2a"].place("~A2c") == "~A2" and SCHEMES["a2a"].place("Jita") == "Jita"
    assert SCHEMES["numeric"].place("12a") == "12a"


def test_label_unnamed_systems_names_parents_first() -> None:
    """Placed systems without a label get one in depth order; roots and named ones are left alone."""
    store = Store(":memory:")
    store.add_system(HOME.id)
    children = [system.id for system in UNIVERSE.all_systems() if system.system_class == "C2"][:3]
    store.add_system(children[1], parent_id=HOME.id, hole_index=2, added_at="2")
    store.add_system(children[0], parent_id=children[1], hole_index=1, added_at="1")
    store.add_system(children[2], parent_id=HOME.id, hole_index=5, label="Kept", added_at="3")
    namer = Namer(store, UNIVERSE, "a2a", HOME.id)
    assert label_unnamed_systems(namer) == 2
    assert [store.system(system_id).label for system_id in children] == ["B2a", "B1a", "Kept"]
    assert store.system(HOME.id).label is None and label_unnamed_systems(namer) == 0
    with pytest.raises(TypeError):
        label_unnamed_systems(store)


def test_letters_for_number() -> None:
    """1 → A, 26 → Z, 27 → AA, small letters on request."""
    assert [letters_for_number(number) for number in (0, 1, 26, 27, 52)] == ["", "A", "Z", "AA", "AZ"]
    assert letters_for_number(2, upper=False) == "b"


def test_schemes_name_paths() -> None:
    """Each scheme turns the same path into its own name."""
    assert SCHEMES["numeric"].name([1, 2, 3], None) == "123"
    assert SCHEMES["a2a"].name([1, 2], 1) == "A2b"
    assert SCHEMES["letter_numeric"].name([2, 3], 0) == "B3"
    assert SCHEMES["letter_numeric"].name([2, 3], 0, 1) == "Z3"      # a one-static home before stored names
    for bad in ([], [1, -1]):
        with pytest.raises(ValueError):
            SCHEMES["numeric"].name(bad, None)
    with pytest.raises(TypeError):
        SCHEMES["a2a"].name((1, 2), 0)
    with pytest.raises(NotImplementedError):
        Scheme("x", "X", "").name([1], None)
    assert isinstance(scheme_for_key("a2a"), ChainDepthPositionScheme)
    assert scheme_for_key("nonsense") is SCHEMES["numeric"]


def test_static_slots() -> None:
    """K-space has no static slots; a J-space system has at least one."""
    assert static_slot_count(UNIVERSE, UNIVERSE.by_name("Jita").id) == 0
    assert static_slot_count(UNIVERSE, HOME.id) == 1
    assert static_slot_count(UNIVERSE, 1) == 0
    assert static_slot_for_class(UNIVERSE, HOME.id, "C5") == 1
    assert static_slot_for_class(UNIVERSE, HOME.id, None) == 1
    with pytest.raises(TypeError):
        static_slot_for_class(UNIVERSE, HOME.id, 5)


def test_take_hole_number_counts_up_from_one() -> None:
    """No slot for the static: the first hole is 1, then one past the highest number given out or in use."""
    store = Store(":memory:")
    store.add_system(HOME.id)
    assert take_hole_number(store, HOME.id) == 1
    store.upsert_signature(Signature(HOME.id, "ABC-123", hole_index=2))
    assert take_hole_number(store, HOME.id) == 3
    store.delete_signatures(HOME.id, ["ABC-123"])
    assert take_hole_number(store, HOME.id) == 3          # 2 was given out
    assert take_hole_number(store, 30000142) == 1         # not on the map
    with pytest.raises(ValueError):
        take_hole_number(store, 0)


def test_hole_path_stops_at_a_loop() -> None:
    """A parent loop doesn't hang the path walk."""
    store = Store(":memory:")
    store.add_system(1, parent_id=None)
    store.add_system(2, parent_id=1, hole_index=1)
    store.update_system(1, parent_id=2, hole_index=1)
    root, path = hole_path(store, 2)
    assert root in (1, 2) and len(path) <= 2
    assert hole_path(store, 3) == (3, [])


def test_namer_labels_for_roots_and_foreign_chains() -> None:
    """Home is "Home"; other roots keep their name; chains off home get a ~ prefix."""
    store = Store(":memory:")
    namer = Namer(store, UNIVERSE, "numeric", HOME.id)
    store.add_system(HOME.id)
    jita = UNIVERSE.by_name("Jita").id
    store.add_system(jita)
    assert namer.display(HOME.id) == "Home" and namer.display(jita) == "Jita"
    amarr = UNIVERSE.by_name("Amarr").id
    store.add_system(amarr, parent_id=jita, hole_index=2)
    assert namer.display(amarr) == "~2 Amarr"
    assert namer.preview(jita, 3) == "~3"
    assert namer.scheme_label(99999999) == "99999999"
    store.update_system(amarr, custom_name="Market")
    assert namer.display(amarr) == "Market" and namer.bookmark(amarr, 1) == "Market-1"
    assert Namer(store, UNIVERSE).preview(jita, 3) == "3"
    with pytest.raises(ValueError):
        namer.bookmark(amarr, -1)


def test_guess_static_reasons() -> None:
    """The guess says why: no static, sig type, wrong class, already mapped, or matching class."""
    simulation = MapSimulation()
    store = simulation.store
    jita = UNIVERSE.by_name("Jita").id
    c5, c3 = pick_system("C5", exclude={HOME.id}), pick_system("C3")
    assert not guess_static(store, UNIVERSE, jita, HOME.id).applies
    by_type = guess_static(store, UNIVERSE, HOME.id, c5.id, signature_type="h296")
    assert by_type.guess and by_type.certain and "is" in by_type.reason
    assert not guess_static(store, UNIVERSE, HOME.id, c5.id, signature_type="D382").guess
    assert not guess_static(store, UNIVERSE, HOME.id, c3.id).applies
    assert guess_static(store, UNIVERSE, HOME.id, c3.id, strict=False).applies
    matching = guess_static(store, UNIVERSE, HOME.id, c5.id)
    assert matching.applies and matching.guess and not matching.certain
    other = pick_system("C5", exclude={HOME.id, c5.id})
    store.add_system(other.id, parent_id=HOME.id, hole_index=4)
    assert guess_static(store, UNIVERSE, HOME.id, c5.id).guess    # a hole, but not flagged as the static
    store.update_system(other.id, static=1)
    assert "already mapped" in guess_static(store, UNIVERSE, HOME.id, c5.id).reason
    with pytest.raises(TypeError):
        guess_static(store, UNIVERSE, HOME.id, c5.id, signature_type=296)


def test_with_real_name_adds_it_once() -> None:
    """K-space display names already end in the real name; w-space and home get it appended; plain names stay."""
    store = Store(":memory:")
    namer = Namer(store, UNIVERSE, "numeric", HOME.id)
    store.add_system(HOME.id)
    jita = UNIVERSE.by_name("Jita").id
    store.add_system(jita, parent_id=HOME.id, hole_index=2)
    assert namer.with_real_name(HOME.id) == f"Home {HOME.name}"
    assert namer.with_real_name(jita) == "2 Jita" == namer.display(jita)
    root = UNIVERSE.by_name("Amarr").id
    store.add_system(root)
    assert namer.with_real_name(root) == "Amarr"
    assert namer.with_real_name(1) == namer.display(1)


def test_signature_destination_and_static_signature_ignore_case() -> None:
    """The set destination wins, else the known type's first; lower-case h296 is HOME's static, k162 knows nothing."""
    assert signature_destination(UNIVERSE, Signature(HOME.id, "A", type="h296")) == "C5"
    assert signature_destination(UNIVERSE, Signature(HOME.id, "A", type="h296", destination="C3")) == "C3"
    assert signature_destination(UNIVERSE, Signature(HOME.id, "A", type="k162")) is None
    assert signature_destination(UNIVERSE, Signature(HOME.id, "A", type="k162", destination="HS")) == "HS"
    assert signature_destination(UNIVERSE, Signature(HOME.id, "A")) is None
    assert is_static_signature(UNIVERSE, HOME.id, Signature(HOME.id, "A", type="h296"))
    assert is_static_signature(UNIVERSE, HOME.id, Signature(HOME.id, "A", type="H296"))
    assert not is_static_signature(UNIVERSE, HOME.id, Signature(HOME.id, "A", type="B274"))
    assert not is_static_signature(UNIVERSE, HOME.id, Signature(HOME.id, "A", type="k162"))
    assert not is_static_signature(UNIVERSE, HOME.id, Signature(HOME.id, "A"))
    with pytest.raises(TypeError):
        signature_destination(UNIVERSE, "A")


def test_names_collects_display_label_and_real_name() -> None:
    """names has every non-empty way a system is called; a k-space system adds its real name, an unknown ID none."""
    store = Store(":memory:")
    namer = Namer(store, UNIVERSE, "numeric", HOME.id)
    store.add_system(HOME.id)
    jita = UNIVERSE.by_name("Jita").id
    store.add_system(jita, parent_id=HOME.id, hole_index=2)
    assert namer.names(HOME.id) == {"Home", HOME.name}
    assert namer.names(jita) == {"2 Jita", "2", "Jita"}
    assert namer.names(1) == {"1"}


def age_map_file(path) -> None:
    """Turn a map file into one from before stored names: no label, static or next_hole column."""
    database = sqlite3.connect(path)
    drop_columns(database, "systems", ("label", "static", "next_hole"))
    database.execute("DELETE FROM key_value")
    database.commit()
    database.close()


def build_legacy_map(path) -> dict[str, int]:
    """Write a map with derived names into ``path`` and age it; return the systems by role.

    Home (one static) → A (hole 1, the static) → C, D, E (holes 2, 3, 4); home → B (hole 2). Home has two
    unnumbered WH sigs and the sig of B's connection; B has an unnumbered sig of its static type and an
    untyped one.
    """
    c5 = pick_system("C5", exclude={HOME.id})
    b = pick_system("C3", statics_to="HS")
    others = [system for system in UNIVERSE.all_systems() if system.system_class == "C2"][:3]
    ids = {"A": c5.id, "B": b.id, "C": others[0].id, "D": others[1].id, "E": others[2].id}
    store = Store(path)
    store.add_system(HOME.id)
    for number, (role, parent, hole) in enumerate([("A", HOME.id, 1), ("B", HOME.id, 2), ("C", c5.id, 2),
                                                   ("D", c5.id, 3), ("E", c5.id, 4)]):
        store.add_system(ids[role], parent_id=parent, hole_index=hole, added_at=f"2026-01-01T00:00:0{number}+00:00")
    store.add_connection(HOME.id, b.id, signature_a="CCC-333")
    for system_id, signature_id, code in [(HOME.id, "AAA-111", "H296"), (HOME.id, "BBB-222", None),
                                          (HOME.id, "CCC-333", None), (b.id, "DDD-444", UNIVERSE.by_id(b.id).statics[0]),
                                          (b.id, "EEE-555", None)]:
        store.upsert_signature(Signature(system_id, signature_id, "wormhole", code))
    store.close()
    age_map_file(path)
    return ids


def test_an_old_map_keeps_the_names_it_showed(tmp_path) -> None:
    """Migration: the names, static slots and sig numbers an old file showed are stored as they were."""
    path = tmp_path / "old.db"
    ids = build_legacy_map(path)
    store = Store(path)
    assert store.get_value("legacy_names") == "1"
    assert store.system(HOME.id).next_hole == 2          # from the highest number in use
    assert freeze_legacy_names(store, UNIVERSE, "a2a", HOME.id) == 5
    namer = Namer(store, UNIVERSE, "a2a", HOME.id)
    assert {role: namer.label(system_id) for role, system_id in ids.items()} == {
        "A": "A1a", "B": "Z1a", "C": "A2a", "D": "A2b", "E": "A2c"}
    assert [role for role, system_id in ids.items() if store.system(system_id).static] == ["A"]
    numbers = {signature.signature_id: signature.hole_index
               for system_id in (HOME.id, ids["B"]) for signature in store.signatures(system_id)}
    assert numbers == {"AAA-111": 3, "BBB-222": 4, "CCC-333": None, "DDD-444": 1, "EEE-555": 2}
    assert store.system(HOME.id).next_hole == 4 and store.system(ids["B"]).next_hole == 2
    assert store.get_value("legacy_names") == "0"
    store.close()
    assert Store(path).get_value("legacy_names") == "0"   # once per file


def test_an_old_numeric_map_keeps_its_names_and_a_new_file_needs_nothing(tmp_path) -> None:
    """The numeric names come back the same; a file made now is not marked for the migration."""
    path = tmp_path / "old.db"
    ids = build_legacy_map(path)
    store = Store(path)
    freeze_legacy_names(store, UNIVERSE, "numeric", HOME.id)
    namer = Namer(store, UNIVERSE, "numeric", HOME.id)
    assert [namer.label(ids[role]) for role in "ABCDE"] == ["1", "2", "12", "13", "14"]
    assert namer.label(HOME.id) == "Home"
    assert Store(tmp_path / "new.db").get_value("legacy_names") is None
    with pytest.raises(TypeError):
        freeze_legacy_names(store, UNIVERSE, 1, HOME.id)


def test_namer_shows_a_remembered_name_like_a_custom_name() -> None:
    """A system added again with an alias shows it as its name, and the bookmarks use it."""
    store = Store(":memory:")
    store.add_system(HOME.id)
    child = pick_system("C5", exclude={HOME.id})
    store.set_alias(child.id, "Staging")
    store.add_system(child.id, parent_id=HOME.id, hole_index=1)
    namer = Namer(store, UNIVERSE, "numeric", HOME.id)
    assert namer.display(child.id) == "Staging" and namer.bookmark(child.id, 2) == "Staging-2"
    assert namer.scheme_label(child.id) != "Staging"


def test_rename_system_remembers_or_forgets_in_one_action() -> None:
    """Rename sets the custom name; "remember" also keeps it as the alias; an empty name forgets both."""
    store = Store(":memory:")
    store.add_system(HOME.id)
    child = pick_system("C5", exclude={HOME.id})
    store.add_system(child.id, parent_id=HOME.id, hole_index=1)
    actions = []
    store.listeners.append(actions.append)
    rename_system(store, child.id, "Staging", remember=True)
    assert (store.system(child.id).custom_name, store.alias(child.id)) == ("Staging", "Staging") and len(actions) == 1
    rename_system(store, child.id, "Farm", remember=False)        # unchecked: the alias goes
    assert (store.system(child.id).custom_name, store.alias(child.id)) == ("Farm", None)
    rename_system(store, child.id, "Farm", remember=True)
    rename_system(store, child.id, "", remember=True)             # clearing the name forgets both
    assert (store.system(child.id).custom_name, store.alias(child.id)) == (None, None)
    rename_system(store, child.id, "Staging", remember=True)
    store.undo(actions[-1])                                       # one undo step undoes name and alias
    assert (store.system(child.id).custom_name, store.alias(child.id)) == (None, None)
    with pytest.raises(TypeError):
        rename_system(store, child.id, "x", remember="yes")


def test_return_type_from_the_other_side() -> None:
    """A real type on the near side makes the return hole a K162; a K162 there asks for the real type."""
    c1, c5, c3 = pick_system("C1"), pick_system("C5"), pick_system("C3")
    assert return_type(UNIVERSE, "H296", None, c5.id, c1.id) == ReturnType("K162")
    assert return_type(UNIVERSE, None, "H296", c5.id, c1.id) == ReturnType("K162")     # only the connection knows
    assert return_type(UNIVERSE, "K162", None, c1.id, c5.id) == ReturnType("E004", ("E004",))   # one type leads from C5 to C1
    assert return_type(UNIVERSE, "K162", None, c5.id, c3.id) == ReturnType(None, ("C008", "N770"))   # two lead from C3 to C5
    assert return_type(UNIVERSE, "K162", "N770", c5.id, c3.id) == ReturnType("N770", ("N770",))   # the connection knows
    assert return_type(UNIVERSE, None, None, c5.id, c3.id) == ReturnType()
    assert return_type(UNIVERSE, "k162", None, c1.id, c5.id).code == "E004"
    with pytest.raises(TypeError):
        return_type(UNIVERSE, 5, None, c5.id, c3.id)

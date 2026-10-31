"""Naming schemes, hole indices and the static guess."""
import pytest

from conftest import HOME, UNIVERSE, MapSimulation, pick_system
from homebound.mapdb.store import Signature, Store
from homebound.mapping.naming import (SCHEMES, ChainDepthPositionScheme, Namer, Scheme, chain_letter, guess_static,
                                      hole_digit, hole_path, letters_for_number, next_hole_index, scheme_for_key,
                                      static_slot_count, static_slot_for_class)


def test_hole_digits() -> None:
    """Indices below 10 are digits, then letters."""
    assert [hole_digit(i) for i in (0, 1, 9, 10, 11)] == ["0", "1", "9", "a", "b"]
    with pytest.raises(ValueError):
        hole_digit(-1)


def test_chain_letters_follow_eve_uni() -> None:
    """A/B for statics, then Z, Y, W …, never letters that are hard to tell apart."""
    assert [chain_letter(h, 1) for h in range(1, 6)] == ["A", "Z", "Y", "W", "U"]
    assert [chain_letter(h, 2) for h in range(1, 5)] == ["A", "B", "Z", "Y"]
    assert chain_letter(1, 0) == "Z" and chain_letter(0, 1) == "?"
    assert not {chain_letter(h, 2) for h in range(1, 16)} & set("CFGHJLNPTVX")
    assert chain_letter(14, 1) == "D" and chain_letter(15, 1) == "ZZ"
    with pytest.raises(TypeError):
        chain_letter("1")


def test_letters_for_number() -> None:
    """1 → A, 26 → Z, 27 → AA, small letters on request."""
    assert [letters_for_number(n) for n in (0, 1, 26, 27, 52)] == ["", "A", "Z", "AA", "AZ"]
    assert letters_for_number(2, upper=False) == "b"


def test_schemes_name_paths() -> None:
    """Each scheme turns the same path into its own name."""
    assert SCHEMES["numeric"].name([1, 2, 3], None) == "123"
    assert SCHEMES["a2a"].name([1, 2], 1) == "A2b"
    assert SCHEMES["letter_numeric"].name([2, 3], 0) == "Z3"
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


def test_next_hole_index_skips_used_indices() -> None:
    """Non-static holes take the lowest index that no child or sig uses."""
    store = Store(":memory:")
    store.add_system(HOME.id)
    assert next_hole_index(store, UNIVERSE, HOME.id, True, "C5") == 1
    assert next_hole_index(store, UNIVERSE, HOME.id, False) == 2
    store.upsert_signature(Signature(HOME.id, "ABC-123", hole_index=2))
    assert next_hole_index(store, UNIVERSE, HOME.id, False) == 3
    with pytest.raises(TypeError):
        next_hole_index(store, UNIVERSE, HOME.id, "yes")


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
    assert namer.display(amarr) == "~2 (Amarr)"
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
    store.add_system(other.id, parent_id=HOME.id, hole_index=1)
    assert "already mapped" in guess_static(store, UNIVERSE, HOME.id, c5.id).reason
    with pytest.raises(TypeError):
        guess_static(store, UNIVERSE, HOME.id, c5.id, signature_type=296)

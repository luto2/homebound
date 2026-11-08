"""Probe scanner paste parsing and merging."""
import pytest

from conftest import HOME, SCAN
from homebound.mapdb.store import Store
from homebound.mapping.signature_parser import (ParsedSignature, group_for_text, merge_signatures, parse_percent,
                                                parse_scanner_text)


@pytest.fixture
def store() -> Store:
    """An empty map with home on it."""
    store = Store(":memory:")
    store.add_system(HOME.id)
    return store


def test_group_and_percent_parsing() -> None:
    """Groups match English and German words; percent accepts comma and dot."""
    assert group_for_text("Gas Site") == "gas" and group_for_text("Datenstandort") == "data"
    assert group_for_text("  ") is None and group_for_text("Something") == "unknown"
    assert parse_percent("12,5%") == 12.5 and parse_percent("100 %") == 100.0 and parse_percent("") is None
    with pytest.raises(TypeError):
        group_for_text(None)
    with pytest.raises(TypeError):
        parse_percent(12)


def test_parse_skips_other_lines_and_reads_codes() -> None:
    """Only lines with a sig ID count; WH codes come from the name; the last duplicate wins."""
    text = "junk line\n" + SCAN + "ABC-123\tCosmic Signature\tWormhole\tWormhole K162\t100%\t1 AU\n"
    parsed = parse_scanner_text(text)
    assert [signature.signature_id for signature in parsed] == ["ABC-123", "DEF-456", "GHI-789", "XYZ-000"]
    assert parsed[0].wormhole_code == "K162"
    assert parsed[1].group is None and parsed[3].kind == "anom"
    assert [signature.signature_id for signature in parse_scanner_text(SCAN, include_anomalies=False)] == [
        "ABC-123", "DEF-456", "GHI-789"]
    assert parse_scanner_text("ABC-123") == [ParsedSignature("ABC-123", None, None, None, None, "sig")]
    with pytest.raises(TypeError):
        parse_scanner_text(None)


def test_merge_never_downgrades_and_flags_missing(store) -> None:
    """A second paste keeps known info, upgrades what it can and flags what's gone."""
    result = merge_signatures(store, HOME.id, parse_scanner_text(SCAN, include_anomalies=False))
    assert sorted(result.added) == ["ABC-123", "DEF-456", "GHI-789"]
    result = merge_signatures(store, HOME.id, parse_scanner_text(
        "DEF-456\tCosmic Signature\tData Site\tUnsecured Frontier Server\t100%\t1 AU\n"
        "GHI-789\tCosmic Signature\t\t\t5%\t2 AU\n"))
    assert result.missing == ["ABC-123"] and sorted(result.updated) == ["DEF-456", "GHI-789"]
    signatures = {signature.signature_id: signature for signature in store.signatures(HOME.id)}
    assert signatures["DEF-456"].group == "data"
    assert signatures["GHI-789"].group == "gas" and signatures["GHI-789"].name == "Barren Perimeter Reservoir"
    assert signatures["ABC-123"].state == "missing"
    assert store.system(HOME.id).scanned_at


def test_missing_is_only_checked_per_kind(store) -> None:
    """A paste of only signatures leaves anomalies alone, and the other way round."""
    merge_signatures(store, HOME.id, parse_scanner_text(
        SCAN + "QRS-111\tCosmic Anomaly\tOre Site\tAverage Frontier Deposit\t100%\t3 AU\n"))
    result = merge_signatures(store, HOME.id, parse_scanner_text("ABC-123\tCosmic Signature\tWormhole\t\t100%\t1 AU\n"))
    assert sorted(result.missing) == ["DEF-456", "GHI-789"]
    result = merge_signatures(store, HOME.id, parse_scanner_text(
        "XYZ-000\tCosmic Anomaly\tCombat Site\tSansha Hideaway\t100%\t1 AU\n"))
    assert result.missing == ["QRS-111"]
    assert store.signature(HOME.id, "ABC-123").state == ""


OLD = "2000-01-01T00:00:00+00:00"


def test_merge_sets_seen_at_on_every_paste(store) -> None:
    """Every sig in a paste is seen now, even with unchanged values; a missing one keeps its last sighting."""
    merge_signatures(store, HOME.id, parse_scanner_text(SCAN, include_anomalies=False))
    for signature in store.signatures(HOME.id):
        store.update_signature(HOME.id, signature.signature_id, seen_at=OLD)
    merge_signatures(store, HOME.id, parse_scanner_text(
        "ABC-123\tCosmic Signature\tWormhole\tUnstable Wormhole\t100%\t4 AU\n"
        "DEF-456\tCosmic Signature\t\t\t12,5%\t10 AU\n"))
    seen = {signature.signature_id: signature.seen_at != OLD for signature in store.signatures(HOME.id)}
    assert seen == {"ABC-123": True, "DEF-456": True, "GHI-789": False}


def test_merge_validation(store) -> None:
    """Merge needs a store, a system ID and parsed rows."""
    with pytest.raises(TypeError):
        merge_signatures(store, HOME.id, SCAN)
    with pytest.raises(TypeError):
        merge_signatures(store, HOME.id, [("ABC-123",)])
    with pytest.raises(ValueError):
        merge_signatures(store, 0, [])


def test_a_paste_is_one_undoable_action(store) -> None:
    """Every signature of one paste arrives as one action; one undo removes them all."""
    actions: list = []
    store.listeners.append(actions.append)
    merge_signatures(store, HOME.id, parse_scanner_text(SCAN))
    assert len(actions) == 1 and len(store.signatures(HOME.id)) > 1
    store.undo(actions[0])
    assert store.signatures(HOME.id) == []

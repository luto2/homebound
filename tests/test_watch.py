"""Watched systems (plan-watched-systems.md): the check, the alerts, where not to map from, the lists and the UI.

Also the lost-connection mark as a system column (task 18): it is what "not mapped from an hour
after a cut" and the synced countdown read.
"""
import json
from collections import deque
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtCore import QPoint

from conftest import HOME, UNIVERSE, MapSimulation, drop_columns, pick_system
from homebound.logs.parser import SystemChange
from homebound.mapdb.store import LIFETIME_EXPIRED, Store, needs_migration
from homebound.mapping.tracker import (BLOCK_CUT_OFF, BLOCK_REMOVED, BLOCK_WATCHED, CUT_OFF_GRACE, MapChanged,
                                       NotMapped, UnknownJump)
from homebound.mapping.watch import (MAX_WATCHED, QUIET_TIME, WATCH_SETTING, WatchAlerts, WatchHit, chain_exits, check,
                                     distance, map_watch_list, set_map_watch_list, watched_ids)
from homebound.settings import Settings
from homebound.ui.state import AppState
from homebound.ui.watch_list import WatchList, jumps_label, watch_text
from ui_helpers import find_action, isolated_settings, pick, toast_text

WATCHED = UNIVERSE.by_name("Rancer").id   # k-space, not a trade hub (those are route destinations)
C5 = pick_system("C5", exclude={HOME.id})
C3 = pick_system("C3", exclude={HOME.id, C5.id})
C2 = pick_system("C2", exclude={HOME.id})


def at_jumps(start: int, jumps: int) -> int:
    """Return a system exactly ``jumps`` stargate jumps from ``start`` (the lowest ID of those)."""
    gates = UNIVERSE.gate_graph()
    seen, queue = {start: 0}, deque([start])
    while queue:
        current = queue.popleft()
        for neighbour in gates.get(current, ()):
            if neighbour not in seen:
                seen[neighbour] = seen[current] + 1
                queue.append(neighbour)
    return min(system_id for system_id, distance_ in seen.items() if distance_ == jumps)


def exit_at(store: Store, jumps: int, from_id: int = WATCHED) -> int:
    """Map a k-space exit ``jumps`` gate jumps from ``from_id``, linked to home by a wormhole; return it."""
    exit_id = at_jumps(from_id, jumps)
    store.add_system(exit_id)
    store.add_connection(HOME.id, exit_id)
    return exit_id


@pytest.fixture
def store() -> Store:
    """An empty map with home on it."""
    map_store = Store(":memory:")
    map_store.add_system(HOME.id)
    return map_store


# ---------------------------------------------------------------- the lists

def test_the_map_watch_list_is_a_map_setting_of_ids(store) -> None:
    """IDs in order without repeats, stored compact; empty clears; bad values read as nothing."""
    assert map_watch_list(store) == []
    assert set_map_watch_list(store, [WATCHED, C5.id, WATCHED]) is True
    assert map_watch_list(store) == [WATCHED, C5.id] and store.map_setting(WATCH_SETTING) == f"[{WATCHED},{C5.id}]"
    assert set_map_watch_list(store, [WATCHED, C5.id]) is False
    assert set_map_watch_list(store, []) is True and store.map_setting(WATCH_SETTING) == ""
    store.set_map_setting(WATCH_SETTING, "not json")
    assert map_watch_list(store) == []
    with pytest.raises(ValueError):
        set_map_watch_list(store, list(range(1, MAX_WATCHED + 2)))
    with pytest.raises(TypeError):
        set_map_watch_list(store, ["Jita"])


@pytest.mark.parametrize("value, expected", [
    (None, []), ("", []), ("[1,2]", [1, 2]), ("[]", []), ("{}", None), ("[true]", None), ("[0]", None),
    ("[1.5]", None), ("nope", None), (json.dumps(list(range(1, MAX_WATCHED + 2))), None), (5, None)])
def test_watched_ids_parses_only_a_list_of_system_ids(value, expected) -> None:
    """The one parser the app, the protocol and the server use."""
    assert watched_ids(value) == expected


def test_settings_keep_the_private_list_and_the_range(monkeypatch, tmp_path) -> None:
    """Both are user settings; a range that isn't offered and a bad list keep the defaults."""
    settings = isolated_settings(monkeypatch, tmp_path)
    assert settings.watched_private == [] and settings.watch_jumps == 5
    taken = Settings()
    assert taken._apply({"watched_private": ["Rancer"], "watch_jumps": 10}) == 2
    assert (taken.watched_private, taken.watch_jumps) == (["Rancer"], 10)
    kept = Settings()
    kept._apply({"watched_private": ["Rancer", 3], "watch_jumps": 3})
    assert (kept.watched_private, kept.watch_jumps) == ([], 5)
    assert Settings()._apply({"watch_jumps": True}) == 0


# ---------------------------------------------------------------- distance and check

def test_a_wormhole_system_is_reached_when_it_is_on_the_map(store) -> None:
    """W-space, Thera and Pochven count only on the map, however close a hole is."""
    assert distance(store, UNIVERSE, C5.id, 10) is None
    store.add_system(C5.id)
    assert distance(store, UNIVERSE, C5.id, 10) == WatchHit(C5.id, 0, C5.id)
    thera = UNIVERSE.by_name("Thera").id
    assert distance(store, UNIVERSE, thera, 10) is None


@pytest.mark.parametrize("max_jumps", [1, 2, 5, 10])
def test_a_k_space_system_is_reached_at_exactly_max_jumps(store, max_jumps) -> None:
    """A mapped exit at max_jumps counts, one further doesn't."""
    far = exit_at(store, max_jumps + 1)
    assert distance(store, UNIVERSE, WATCHED, max_jumps) is None
    store.remove_system(far)
    near = exit_at(store, max_jumps)
    assert distance(store, UNIVERSE, WATCHED, max_jumps) == WatchHit(WATCHED, max_jumps, near)


def test_only_live_connections_make_an_exit(store) -> None:
    """A mapped system with no hole, or only an expired one, isn't a way in."""
    lone = at_jumps(WATCHED, 1)
    store.add_system(lone)
    assert chain_exits(store) == set() and distance(store, UNIVERSE, WATCHED, 5) is None
    connection = store.add_connection(HOME.id, lone)
    assert chain_exits(store) == {HOME.id, lone} and distance(store, UNIVERSE, WATCHED, 5).jumps == 1
    store.update_connection(connection.id, lifetime=LIFETIME_EXPIRED, lifetime_set_at=datetime.now(timezone.utc).isoformat())
    assert distance(store, UNIVERSE, WATCHED, 5) is None


def test_a_k_space_system_on_the_map_is_zero_jumps(store) -> None:
    """Mapped itself counts as reached, with or without a hole."""
    store.add_system(WATCHED)
    assert distance(store, UNIVERSE, WATCHED, 1) == WatchHit(WATCHED, 0, WATCHED)


def test_check_returns_the_reached_systems_once_each(store) -> None:
    """Unknown IDs are skipped; repeats are checked once."""
    exit_id = exit_at(store, 2)
    store.add_system(C5.id)
    hits = check(store, UNIVERSE, [WATCHED, C5.id, C3.id, WATCHED, 99], 5)
    assert list(hits) == [WATCHED, C5.id] and hits[WATCHED].via_id == exit_id
    assert check(store, UNIVERSE, [WATCHED], 1) == {}
    with pytest.raises(TypeError):
        distance(store, UNIVERSE, "Jita", 5)


# ---------------------------------------------------------------- alerts

def test_an_alert_fires_once_and_again_only_after_the_quiet_time() -> None:
    """True again within the quiet time is quiet; false for longer, then true, fires again."""
    alerts, now = WatchAlerts(), datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    hit = WatchHit(WATCHED, 3, 1)
    assert alerts.update({WATCHED: hit}, now) == [hit]
    assert alerts.update({WATCHED: hit}, now + timedelta(minutes=5)) == []
    assert alerts.update({}, now + timedelta(minutes=10)) == []
    assert alerts.update({WATCHED: hit}, now + timedelta(minutes=30)) == []        # back within the hour
    assert alerts.update({}, now + timedelta(minutes=40)) == []
    assert alerts.update({WATCHED: hit}, now + timedelta(minutes=40) + QUIET_TIME) == [hit]


def test_silent_updates_accept_and_forget() -> None:
    """A silent update and accept() take a hit without alerting; forget() lets it alert again."""
    alerts, now = WatchAlerts(), datetime(2026, 11, 12, 12, tzinfo=timezone.utc)
    hit, other = WatchHit(WATCHED, 3, 1), WatchHit(C5.id, 0, C5.id)
    assert alerts.update({WATCHED: hit}, now, silent=True) == []
    assert alerts.update({WATCHED: hit}, now) == []
    alerts.accept(other)
    assert alerts.update({WATCHED: hit, C5.id: other}, now) == []
    alerts.forget(WATCHED)
    assert alerts.update({WATCHED: hit, C5.id: other}, now) == [hit]
    alerts.forget()
    assert alerts.update({WATCHED: hit}, now) == [hit]
    with pytest.raises(TypeError):
        alerts.update([hit], now)
    with pytest.raises(TypeError):
        alerts.accept(WATCHED)


# ---------------------------------------------------------------- the cut-off mark is a system column

def cut(simulation: MapSimulation, when: datetime) -> list[int]:
    """Home -> C5 -> C3 with the pilot in C3, then lose home <-> C5 at ``when``; return the cut piece."""
    simulation.jump(C5.name)
    simulation.jump(C3.name)
    return simulation.tracker.disconnect(simulation.store.connection(HOME.id, C5.id).id, when)


def test_the_cut_off_mark_is_the_cut_off_at_column(simulation) -> None:
    """disconnect writes the time into the system row (so it syncs); the read checks the piece still holds."""
    when = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
    cut(simulation, when)
    assert simulation.store.system(C5.id).cut_off_at == when.isoformat(timespec="seconds")
    assert simulation.store.system(C3.id).cut_off_at is None
    simulation.store.add_connection(HOME.id, C3.id)    # found again: the mark lapses at once
    assert simulation.tracker.cut_off_since(C5.id) is None and simulation.store.system(C5.id).cut_off_at
    simulation.tracker.expire(when + timedelta(minutes=5))
    assert simulation.store.system(C5.id).cut_off_at is None    # and is taken out by the next expiry


def test_a_lapsed_mark_doesnt_count_when_the_piece_is_cut_again(simulation) -> None:
    """A piece found and lost again starts a new countdown, not the old one."""
    first = datetime(2026, 11, 1, 9, tzinfo=timezone.utc)
    cut(simulation, first)
    link = simulation.store.add_connection(HOME.id, C5.id)
    later = first + timedelta(hours=3)
    assert simulation.tracker.disconnect(link.id, later) == sorted([C5.id, C3.id])
    assert simulation.tracker.cut_off_since(C5.id) == later


def test_an_old_map_file_gets_the_cut_off_column(tmp_path) -> None:
    """A map from before the column is migrated (and backed up first: needs_migration says so)."""
    path = tmp_path / "old.db"
    Store(path).close()
    import sqlite3
    database = sqlite3.connect(path)
    drop_columns(database, "systems", ("cut_off_at",))
    database.close()
    assert needs_migration(path)
    with Store(path) as migrated:
        migrated.add_system(HOME.id, cut_off_at="2026-11-01T09:00:00+00:00")
        assert migrated.system(HOME.id).cut_off_at == "2026-11-01T09:00:00+00:00"


def test_other_members_in_a_cut_off_piece_keep_it(simulation) -> None:
    """A shared character of another member inside keeps the piece past the grace time, like an own one."""
    now = simulation.time
    simulation.store.set_character(1, "Pilot", HOME.id, now.isoformat())
    cut(simulation, now)
    simulation.store.set_character(1, "Pilot", HOME.id, now.isoformat())
    assert simulation.tracker.expire(now + timedelta(hours=2), occupied={C3.id, None}) == 0
    assert simulation.store.has_system(C5.id)
    assert simulation.tracker.expire(now + timedelta(hours=2)) == 1
    assert not simulation.store.has_system(C5.id)


# ---------------------------------------------------------------- not mapped from

def live_simulation() -> MapSimulation:
    """A scripted map whose clock is now, so removals (stamped with the real clock) line up with jumps."""
    simulation = MapSimulation()
    simulation.time = datetime.now(timezone.utc)
    return simulation


def test_a_watched_system_off_the_map_is_not_mapped_from() -> None:
    """Jumping out of a watched hole isn't mapped; into it from the chain is, and then out of it is too."""
    simulation = live_simulation()
    set_map_watch_list(simulation.store, [C5.id])
    simulation.jump(C5.name, first=True, character=2)          # another pilot logs in inside the watched hole
    events = simulation.jump(C3.name, character=2)
    blocked = next(event for event in events if isinstance(event, NotMapped))
    assert (blocked.from_id, blocked.to_id, blocked.blocked_id, blocked.reason) == (C5.id, C3.id, C5.id, BLOCK_WATCHED)
    assert not simulation.store.has_system(C5.id) and not simulation.store.has_system(C3.id)
    assert not any(isinstance(event, MapChanged) for event in events)
    simulation.jump(C5.name, character=2)                      # unmapped C3 into the watched hole: not mapped
    assert not simulation.store.has_system(C5.id)
    events = simulation.jump(C5.name)                         # the pilot at home jumps in: it joins the chain
    assert simulation.store.connection(HOME.id, C5.id) and not any(isinstance(event, NotMapped) for event in events)
    simulation.jump(C2.name)
    assert simulation.store.connection(C5.id, C2.id)


def test_the_private_list_blocks_too_and_watched_k_space_isnt_asked_about() -> None:
    """A private watch blocks like the map's; a k-space jump out of a watched system gives no question."""
    simulation = live_simulation()
    simulation.tracker.private_watched = {WATCHED}
    simulation.jump("Rancer", first=True, character=2)
    events = simulation.jump("Amarr", character=2)
    assert not any(isinstance(event, UnknownJump) for event in events)
    assert next(event for event in events if isinstance(event, NotMapped)).reason == BLOCK_WATCHED
    assert simulation.tracker.watched() == {WATCHED}


def test_a_piece_cut_off_over_an_hour_ago_is_only_mapped_back_to_the_chain() -> None:
    """Within the hour the exfil maps as usual; after it, only a jump onto the chain goes on the map."""
    simulation = live_simulation()
    cut(simulation, simulation.time)
    events = simulation.jump(C2.name)                          # 3 minutes after the cut: mapped
    assert simulation.store.connection(C3.id, C2.id) and not any(isinstance(event, NotMapped) for event in events)
    simulation.time += CUT_OFF_GRACE
    other = pick_system("C2", exclude={HOME.id, C2.id})
    events = simulation.jump(other.name)
    assert next(event for event in events if isinstance(event, NotMapped)).reason == BLOCK_CUT_OFF
    assert not simulation.store.has_system(other.id)
    assert simulation.tracker.not_mapped_from(C3.id, simulation.time) == BLOCK_CUT_OFF
    simulation.jump(C2.name)                                   # the pilot comes back into the piece
    simulation.jump(HOME.name)                                 # and finds the way home: mapped, the piece is back
    assert simulation.store.connection(C2.id, HOME.id) and simulation.tracker.cut_off_since(C5.id) is None
    assert simulation.tracker.not_mapped_from(C3.id) is None


def test_a_cut_off_piece_is_found_again_from_the_chain() -> None:
    """A jump from the chain into an old cut-off piece is mapped (that ends the cut)."""
    simulation = live_simulation()
    cut(simulation, simulation.time - 2 * CUT_OFF_GRACE)
    simulation.store.set_character(1, "Pilot", HOME.id, simulation.time.isoformat())
    simulation.jump(C3.name)
    assert simulation.store.connection(HOME.id, C3.id)


def test_a_system_removed_over_an_hour_ago_is_not_mapped_from() -> None:
    """A deleted chain: a pilot still inside maps on for an hour, then no more; re-adding it ends that."""
    simulation = live_simulation()
    simulation.jump(C5.name)
    simulation.store.remove_system(C5.id)                     # the chain is deleted with the pilot inside
    assert simulation.tracker.removed_since(C5.id) is not None
    assert simulation.tracker.not_mapped_from(C5.id) is None   # within the hour
    later = datetime.now(timezone.utc) + CUT_OFF_GRACE + timedelta(minutes=1)
    assert simulation.tracker.not_mapped_from(C5.id, later) == BLOCK_REMOVED
    assert simulation.tracker.mapping_blocked(C5.id, C3.id, later) == (C5.id, BLOCK_REMOVED)
    assert simulation.tracker.mapping_blocked(C5.id, HOME.id, later) is None     # back to the chain is fine
    assert simulation.tracker.not_mapped_from(C5.id, later + timedelta(days=3)) is None   # long forgotten
    simulation.store.add_system(C5.id)
    assert simulation.tracker.removed_since(C5.id) is None


def test_a_cut_off_piece_that_went_is_not_mapped_from_at_once(simulation) -> None:
    """Removed by the countdown, its systems count as gone since the cut, so a straggler can't restart it."""
    now = simulation.time
    simulation.store.set_character(1, "Pilot", HOME.id, now.isoformat())
    cut(simulation, now)
    simulation.store.set_character(1, "Pilot", HOME.id, now.isoformat())
    simulation.tracker.expire(now + CUT_OFF_GRACE)
    assert simulation.tracker.removed_since(C3.id) == now
    assert simulation.tracker.not_mapped_from(C3.id, now + CUT_OFF_GRACE) == BLOCK_REMOVED


def test_the_removed_list_is_bounded_and_survives_bad_values(simulation, monkeypatch) -> None:
    """Only the newest are kept; a damaged value reads as empty."""
    from homebound.mapping import tracker as tracker_module
    monkeypatch.setattr(tracker_module, "REMOVED_LIMIT", 2)
    for system in (C5, C3, C2):
        simulation.store.add_system(system.id)
        simulation.store.remove_system(system.id)
    assert len(simulation.tracker._removed()) == 2
    simulation.store.put_value(tracker_module.REMOVED_KEY, "[]")
    assert simulation.tracker._removed() == {}


# ---------------------------------------------------------------- AppState

def test_state_adds_and_removes_on_both_lists(state) -> None:
    """The map's list goes into the map file, the private one into the settings; each system once."""
    changed = []
    state.watchListChanged.connect(lambda: changed.append(1))
    assert state.add_watch(WATCHED) and state.add_watch(C5.id, private=True)
    assert map_watch_list(state.store) == [WATCHED] and state.settings.watched_private == [C5.name]
    assert state.watch_entries() == [(WATCHED, False), (C5.id, True)] and state.tracker.watched() == {WATCHED, C5.id}
    assert not state.add_watch(WATCHED, private=True) and not state.add_watch(99)
    assert state.set_watch_private(WATCHED, True) and state.watch_entries() == [(C5.id, True), (WATCHED, True)]
    assert not state.set_watch_private(WATCHED, True)
    assert state.remove_watch(C5.id) and state.remove_watch(WATCHED) and not state.remove_watch(WATCHED)
    assert state.watch_entries() == [] and len(changed) == 6
    state.settings.watched_private = ["Rancer", "Nowhere", "rancer"]
    state.store.set_map_setting(WATCH_SETTING, f"[{WATCHED}]")
    assert state.watch_entries() == [(WATCHED, False)] and state.private_watch_ids() == [WATCHED]


def test_the_map_list_is_full_at_its_limit(state, monkeypatch) -> None:
    """The map's list stops at MAX_WATCHED (the server refuses more); the private one doesn't."""
    from homebound.ui import state as state_module
    monkeypatch.setattr(state_module, "MAX_WATCHED", 1)
    assert state.add_watch(WATCHED) and not state.add_watch(C5.id) and state.add_watch(C5.id, private=True)


def test_state_alerts_once_when_the_chain_reaches_a_watched_system(state) -> None:
    """A wormhole jump into a watched hole emits watchHit once; already reached when added: no alert."""
    hits: list[WatchHit] = []
    state.watchHit.connect(hits.append)
    state.add_watch(C5.id)
    state.on_system_change(SystemChange(1, "Pilot", HOME.name, datetime.now(timezone.utc), first=True))
    state.on_system_change(SystemChange(1, "Pilot", C5.name, datetime.now(timezone.utc)))
    state._watch_timer.stop()
    assert [hit.system_id for hit in state.check_watches()] == [C5.id] and [hit.system_id for hit in hits] == [C5.id]
    assert state.check_watches() == [] and len(hits) == 1
    state.add_watch(C3.id)
    state.store.add_system(C3.id)
    state.remove_watch(C3.id)
    state.add_watch(C3.id)                                    # reached already when added: listed, not alerted
    assert state.check_watches() == []


def test_a_tracked_jump_into_a_watched_system_alerts_after_the_debounce(state, qt_application) -> None:
    """mapChanged starts the 1 s timer; when it runs, the hit is emitted."""
    hits: list[WatchHit] = []
    state.watchHit.connect(hits.append)
    state.add_watch(C5.id)
    state.on_system_change(SystemChange(1, "Pilot", HOME.name, datetime.now(timezone.utc), first=True))
    state.on_system_change(SystemChange(1, "Pilot", C5.name, datetime.now(timezone.utc)))
    assert state._watch_timer.isActive() and hits == []
    state._watch_timer.timeout.emit()
    assert [hit.system_id for hit in hits] == [C5.id]


def test_a_jump_not_mapped_is_emitted(state) -> None:
    """The NotMapped event of a blocked jump reaches the window as jumpNotMapped."""
    seen: list[NotMapped] = []
    state.jumpNotMapped.connect(seen.append)
    state.add_watch(C5.id)
    state.on_system_change(SystemChange(2, "Other", C5.name, datetime.now(timezone.utc), first=True))
    state.on_system_change(SystemChange(2, "Other", C3.name, datetime.now(timezone.utc)))
    assert [event.blocked_id for event in seen] == [C5.id]


def test_start_and_a_map_switch_take_what_is_reached_silently(state, monkeypatch, tmp_path) -> None:
    """What the chain reaches already when a map opens is listed, not alerted; the range change too."""
    hits: list[WatchHit] = []
    state.watchHit.connect(hits.append)
    state.store.add_system(C5.id)
    set_map_watch_list(state.store, [C5.id])
    state.start()
    state.stop()
    assert hits == [] and C5.id in state.watch_alerts.active
    state.settings.add_map("Other")
    state.switch_map("Other")
    assert state.watch_alerts.active == set() and state.watch_entries() == []
    state.store.add_system(HOME.id)
    exit_at(state.store, 4)
    state.add_watch(WATCHED, private=True)
    state.set_watch_jumps(2)
    state.set_watch_jumps(5)
    assert hits == [] and state.settings.watch_jumps == 5
    with pytest.raises(ValueError):
        state.set_watch_jumps(3)


def test_expiry_passes_other_members_locations_and_checks_the_lists(state) -> None:
    """remote_occupied() is what the expiry keeps; with nothing removed the lists are checked."""
    state.remote_presence = {7: {"system_id": C3.id}, 8: {"system_id": None}}
    assert state.remote_occupied() == {C3.id}
    seen = []
    state.tracker.expire = lambda now=None, occupied=(): seen.append(set(occupied)) or 0
    checked = []
    state.check_watches = lambda silent=False: checked.append(silent) or []
    assert state.expire() == 0 and seen == [{C3.id}] and checked == [False]


# ---------------------------------------------------------------- the Watch section and the window

@pytest.fixture
def watch(state, qt_application, modal) -> WatchList:
    """A shown Watch section."""
    section = WatchList(state)
    section.resize(300, 300)
    section.show()
    qt_application.processEvents()
    return section


def row_texts(section: WatchList) -> list[str]:
    """Return the text of each row."""
    return [section.list.item(row).text() for row in range(section.list.count())]


def test_the_section_lists_both_lists_with_their_distance(state, watch) -> None:
    """Rows say the distance and whose entry it is; an empty list says so."""
    assert row_texts(watch) == ["Nothing watched"] and watch.entry(0) is None
    exit_id = exit_at(state.store, 3)
    state.add_watch(WATCHED)
    state.add_watch(C5.id, private=True)
    assert row_texts(watch) == [f"Rancer · 3 j via {state.namer.display(exit_id)}", f"{C5.name} · — · private"]
    assert watch.entry(0)[:2] == (WATCHED, False) and "shared" in watch.list.item(0).toolTip()
    state.set_watch_jumps(2)
    assert row_texts(watch)[0] == "Rancer · —" and watch.range_label.text() == "k-space: 2 jumps"
    state.store.add_system(C5.id)
    state.mapChanged.emit()
    assert row_texts(watch)[1] == f"{C5.name} · on the map · private"
    assert jumps_label(1) == "adjacent only" and watch_text(state, WATCHED, None) == "—"


def test_the_section_keeps_the_lists_across_a_switch_and_a_restart(state, watch, monkeypatch, tmp_path) -> None:
    """The private list follows the user to another map; the map's list stays with its map."""
    state.add_watch(WATCHED)
    state.add_watch(C5.id, private=True)
    state.settings.add_map("Other")
    state.switch_map("Other")
    assert row_texts(watch) == [f"{C5.name} · — · private"]
    state.switch_map("Main")
    assert [entry[:2] for entry in (watch.entry(0), watch.entry(1))] == [(WATCHED, False), (C5.id, True)]
    state.settings.save()
    state.stop()
    reopened = AppState(UNIVERSE, Settings.load())
    try:
        assert reopened.watch_entries() == [(WATCHED, False), (C5.id, True)]
    finally:
        reopened.stop()


def test_adding_by_name_and_the_toasts(state, watch) -> None:
    """Typed names and map names work; unknown, repeated and full each say why."""
    toasts: list[tuple[str, str]] = []
    watch.toast.connect(lambda kind, text: toasts.append((kind, text)))
    watch.add_field.setText("rancer")
    watch.private_box.setChecked(True)
    assert watch._add_typed() and watch.add_field.text() == "" and state.watch_entries() == [(WATCHED, True)]
    assert not watch.add_watch("Rancer") and not watch.add_watch("Nowhere") and not watch.add_watch(" ")
    state.store.add_system(C5.id)
    assert watch.add_watch(state.namer.display(C5.id))
    assert [kind for kind, _ in toasts] == ["ok", "err", "err", "err", "ok"]
    assert toasts[-1][1] == f"Watching {C5.name}: on the map"


def test_the_full_map_list_says_so(state, watch, monkeypatch) -> None:
    """A full map list gives an error toast."""
    from homebound.ui import state as state_module
    monkeypatch.setattr(state_module, "MAX_WATCHED", 0)
    toasts = []
    watch.toast.connect(lambda kind, text: toasts.append(text))
    assert not watch.add_watch("Rancer") and "already" in toasts[-1]


def test_the_row_menu_moves_shows_and_removes(state, watch, modal) -> None:
    """Make private / Share, Show on the map (the nearest exit) and Stop watching."""
    exit_id = exit_at(state.store, 1)
    state.add_watch(WATCHED)
    shown: list[int] = []
    watch.showRequested.connect(shown.append)
    position = watch.list.visualItemRect(watch.list.item(0)).center()
    modal.menu_handler = pick("Make private")
    watch._context_menu(position)
    assert state.watch_entries() == [(WATCHED, True)]
    modal.menu_handler = pick("Share with this map")
    watch._context_menu(position)
    assert state.watch_entries() == [(WATCHED, False)]
    modal.menu_handler = pick("Show on the map")
    watch._context_menu(position)
    assert shown == [exit_id]
    modal.menu_handler = pick("Stop watching")
    watch._context_menu(position)
    assert state.watch_entries() == [] and not watch.show_system(0)
    watch._context_menu(QPoint(5, 290))                         # nothing under the mouse: no menu


def test_the_range_menu_sets_the_setting(state, watch, modal) -> None:
    """The ⚙ menu offers the four ranges and sets watch_jumps."""
    menu = watch.range_menu()
    assert [action.text() for action in menu.actions() if action.isCheckable()] == [
        "Adjacent only", "2 jumps", "5 jumps", "10 jumps"]
    find_action(menu, "10 jumps").trigger()
    assert state.settings.watch_jumps == 10
    modal.menu_handler = pick("Adjacent only")
    watch.show_range_menu()
    assert state.settings.watch_jumps == 1


def test_the_window_shows_a_hit_with_show_and_route(window) -> None:
    """The toast names the exit; Show selects it, Route also adds the watched system as a destination."""
    state = window.state
    exit_id = exit_at(state.store, 2)
    state.mapChanged.emit()
    window._watch_hit(WatchHit(WATCHED, 2, exit_id))
    toast = window.toasts.toasts[-1]
    assert toast_text(toast) == f"A chain is 2 jumps from watched Rancer, via {state.namer.display(exit_id)}"
    show, route = toast.buttons
    assert (show.text(), route.text()) == ("Show", "Route")
    show.click()
    assert state.selected == exit_id and WATCHED not in state.route_destination_ids()
    state.select(0)
    window._watch_hit(WatchHit(WATCHED, 2, exit_id))
    window.toasts.toasts[-1].buttons[1].click()
    assert state.selected == exit_id and WATCHED in state.route_destination_ids()
    window._watch_hit(WatchHit(WATCHED, 2, exit_id))
    window.toasts.toasts[-1].buttons[1].click()                # a destination already: not added twice
    assert state.route_destination_ids().count(WATCHED) == 1
    window._watch_hit(WatchHit(C5.id, 0, C5.id))
    assert toast_text(window.toasts.toasts[-1]) == f"Watched {C5.name} is on the map now"
    assert not window.show_system(C3.id)


def test_the_window_says_why_a_jump_wasnt_mapped(window) -> None:
    """Each reason has its own words."""
    from PySide6.QtWidgets import QLabel
    for reason, words in ((BLOCK_WATCHED, "is watched"), (BLOCK_CUT_OFF, "cut off"), (BLOCK_REMOVED, "taken off")):
        window._jump_not_mapped(NotMapped("Pilot", C5.id, C3.id, C5.id, reason))
        toast = window.toasts.toasts[-1]
        assert toast_text(toast) == f"Not mapped: {C5.name} → {C3.name} (Pilot)"
        assert any(words in label.text() for label in toast.findChildren(QLabel))


def test_the_watch_list_sits_in_the_routes_dock_and_shows_on_the_map(window) -> None:
    """The window wires the section's show request to centring the system."""
    child = pick_system("C5", exclude={HOME.id})
    window.state.dispatch(window.state.tracker.add_wormhole(HOME.id, child.id))
    window.route_dock.watch.showRequested.emit(child.id)
    assert window.state.selected == child.id


def test_a_late_check_after_the_map_closed_does_nothing(state) -> None:
    """The delayed watch check can fire after its map was closed (switch, restore, quit): it checks nothing."""
    state.add_watch(C5.id)
    state.store.close()
    assert state.check_watches() == []


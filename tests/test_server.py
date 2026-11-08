"""The sync server in-process: login, sessions, sync, conflicts, roles, billing, prune, presence, corp maps."""
from datetime import timedelta

import pytest

from conftest import HOME, pick_system
from homebound.sync.hlc import format_hlc
from homebound_server.billing import map_price, monthly_total
from homebound_server.db import iso, parse_iso, utc_now
from server_helpers import Harness, Socket, system_op

A, B = "aaaaaaaa", "bbbbbbbb"
C5 = pick_system("C5", exclude=(HOME.id,)).id
C3 = pick_system("C3").id
C2 = pick_system("C2").id


@pytest.fixture
def harness() -> Harness:
    """A fresh server."""
    return Harness()


def setup_map(harness: Harness, isk: float = 1e9):
    """Kira owns a map with home on it; Bob is invited as an editor. Returns (map, kira token, bob token)."""
    kira = harness.login(91000001, "Kira")
    bob = harness.login(91000002, "Bob")
    harness.credit(kira, isk)
    map_id = harness.make_map(kira, [system_op(HOME.id, 1, A, notes="home"),
                                     {"e": "setting", "k": "home_system", "f": {"value": HOME.name},
                                      "hlc": format_hlc(1, 0), "c": A, "full": True}])
    code = harness.api("POST", f"/api/maps/{map_id}/invites", kira, json={"role": "editor"}).json()["code"]
    assert harness.api("POST", f"/api/invites/{code}/accept", bob).json()["role"] == "editor"
    return map_id, kira, bob


# ---------------------------------------------------------------- login and sessions

def test_login_creates_an_account_and_a_session(harness) -> None:
    """The loopback login ends in a session token; the code works once only."""
    token = harness.login(91000001, "Kira")
    account = harness.api("GET", "/api/account", token).json()
    assert account["characters"][0]["name"] == "Kira" and account["payer_code"].startswith("HB-")
    assert harness.api("GET", "/api/account", "nope").status_code == 401
    assert harness.client.post("/api/login/exchange", json={"code": "used", "state": "client-state"}).status_code == 401


def test_login_without_a_port_shows_a_code(harness) -> None:
    """When the loopback is blocked the browser shows a code to paste."""
    to_eve = harness.client.get("/login", params={"state": "s"}, follow_redirects=False)
    state = to_eve.headers["location"].split("state=")[1]
    page = harness.client.get(f"/dev/sso?state={state}&char_id=91000009&name=Zed")
    assert "Paste this code" in page.text


def test_alts_link_to_the_same_account_and_unlink(harness) -> None:
    """A link code adds an alt; the last character can't be unlinked."""
    token = harness.login(91000001, "Kira")
    link = harness.api("POST", "/api/account/characters", token).json()["link_code"]
    harness.login(91000003, "Kira Alt", link=link)
    account = harness.api("GET", "/api/account", token).json()
    assert [character["name"] for character in account["characters"]] == ["Kira", "Kira Alt"]
    other = harness.login(91000004, "Other")
    link = harness.api("POST", "/api/account/characters", other).json()["link_code"]
    to_eve = harness.client.get("/login", params={"port": "5000", "state": "x", "link": link}, follow_redirects=False)
    state = to_eve.headers["location"].split("state=")[1]
    refused = harness.client.get(f"/dev/sso?state={state}&char_id=91000001&name=Kira")
    assert "already linked" in refused.text
    # unlinking the alt revokes the sessions; signing in again works
    assert harness.api("DELETE", "/api/account/characters/91000003", token).status_code == 200
    token = harness.login(91000001, "Kira")
    assert harness.api("DELETE", "/api/account/characters/91000001", token).status_code == 400


def test_sessions_expire_and_can_be_put_off_once(harness) -> None:
    """After 90 days a re-login is due; "Do it later" gives 36 hours, once."""
    token = harness.login(91000001, "Kira")
    harness.service.db.execute("UPDATE sessions SET expires_at=?", (iso(utc_now() - timedelta(minutes=1)),))
    response = harness.api("GET", "/api/account", token)
    assert response.status_code == 401 and response.json()["error"] == "relogin_due"
    assert harness.api("POST", "/api/sessions/current/defer", token).status_code == 200
    assert harness.api("GET", "/api/account", token).status_code == 200
    assert harness.api("POST", "/api/sessions/current/defer", token).status_code == 403


def test_devices_are_listed_and_signed_out(harness) -> None:
    """Each login is a device; signing one out ends only that session."""
    first = harness.login(91000001, "Kira", device="desk")
    second = harness.login(91000001, "Kira", device="laptop")
    listed = harness.api("GET", "/api/sessions", first).json()
    assert {session["device"] for session in listed["sessions"]} == {"desk", "laptop"}
    other = next(session["id"] for session in listed["sessions"] if session["id"] != listed["current"])
    harness.api("DELETE", f"/api/sessions/{other}", first)
    assert harness.api("GET", "/api/account", second).status_code == 401
    assert harness.api("POST", "/api/logout", first).status_code == 200
    assert harness.api("GET", "/api/account", first).status_code == 401


def test_a_sold_character_loses_its_old_account(harness) -> None:
    """A character dropping into an NPC corp must log in again; a new owner hash takes it off the account."""
    harness.eve.corps[91000001] = 98000001
    token = harness.login(91000001, "Kira")
    harness.eve.corps[91000001] = 1000044            # into an NPC corp
    assert harness.service.poll_affiliations()["flagged"] == [91000001]
    assert harness.api("GET", "/api/account", token).json()["error"] == "relogin_due"
    harness.eve.owner_hashes[91000001] = "new-owner"
    new_token = harness.login(91000001, "Kira")
    old_account = harness.service.db.value("SELECT count(*) FROM accounts")
    assert old_account == 2 and harness.api("GET", "/api/account", token).status_code == 401
    assert harness.api("GET", "/api/account", new_token).json()["maps"] == []


# ---------------------------------------------------------------- sync

def test_ops_reach_the_other_member_and_resends_are_deduplicated(harness) -> None:
    """A push is acked to the sender and broadcast to the others; the same batch twice is applied once."""
    map_id, kira, bob = setup_map(harness)
    first, second = Socket(harness, kira, A), Socket(harness, bob, B)
    snapshot = second.join(map_id)
    assert snapshot["t"] == "snapshot" and any(entity["k"] == HOME.id for entity in snapshot["entities"])
    first.join(map_id, snapshot["seq"])
    ack = first.push(map_id, [system_op(C5, 10, A, HOME.id, 1)], batch_id="batch-1")
    assert ack["t"] == "ack" and ack["stamped"]
    broadcast = second.until("ops")
    assert broadcast["seq"] == ack["seq"] and broadcast["ops"][0]["k"] == C5
    assert broadcast["ops"][0]["f"]["added_at"] == ack["stamped"][f"system:{C5}"]["added_at"]
    again = first.push(map_id, [system_op(C5, 10, A, HOME.id, 1)], batch_id="batch-1")
    assert again["seq"] == ack["seq"]
    assert harness.service.hub.seq(map_id) == ack["seq"]
    first.close()
    second.close()


def test_a_client_inside_the_tail_gets_only_the_missing_ops(harness) -> None:
    """Rejoining with a recent seq gives ops_since; a seq older than the tail gives a snapshot."""
    map_id, kira, bob = setup_map(harness)
    socket = Socket(harness, kira, A)
    seq = socket.join(map_id)["seq"]
    socket.push(map_id, [system_op(C5, 10, A, HOME.id, 1)])
    socket.close()
    other = Socket(harness, bob, B)
    reply = other.join(map_id, seq)
    assert reply["t"] == "ops_since" and len(reply["batches"]) == 1
    harness.service.db.execute("DELETE FROM ops")
    assert other.join(map_id, seq)["t"] == "snapshot"
    other.close()


def test_the_first_placement_wins_and_the_loser_gets_the_server_state(harness) -> None:
    """Two members mapping the same hole: the second push is a conflict with current[]."""
    map_id, kira, bob = setup_map(harness)
    first, second = Socket(harness, kira, A), Socket(harness, bob, B)
    first.join(map_id)
    second.join(map_id)
    assert first.push(map_id, [system_op(C5, 10, A, HOME.id, 1)])["t"] == "ack"
    reject = second.push(map_id, [system_op(C3, 11, B, HOME.id, 1)])
    assert reject["t"] == "reject" and reject["code"] == "conflict"
    assert any(entity["k"] == C5 for entity in reject["current"])
    unknown = second.push(map_id, [system_op(1, 12, B)])
    assert unknown["code"] == "invalid"
    dangling = second.push(map_id, [{"e": "signature", "k": [C2, "ABC-123"], "f": {"name": "x"}, "full": True,
                                     "hlc": format_hlc(13, 0), "c": B}])
    assert dangling["code"] == "conflict"
    first.close()
    second.close()


def test_viewers_and_web_views_cant_push_and_home_is_owner_only(harness) -> None:
    """A viewer gets read_only, an editor changing home gets forbidden; a role change applies at once."""
    map_id, kira, bob = setup_map(harness)
    socket = Socket(harness, bob, B)
    socket.join(map_id)
    home = {"e": "setting", "k": "home_system", "f": {"value": "J100001"}, "hlc": format_hlc(20, 0), "c": B}
    assert socket.push(map_id, [home])["code"] == "forbidden"
    bob_id = harness.api("GET", "/api/account", bob).json()["account_id"]
    assert harness.api("PATCH", f"/api/maps/{map_id}/members/{bob_id}", kira, json={"role": "viewer"}).status_code == 200
    status = socket.until("status")
    assert status["role"] == "viewer"
    assert socket.push(map_id, [system_op(C5, 21, B, HOME.id, 1)])["code"] == "read_only"
    socket.close()


def test_a_removed_member_is_told_and_can_rejoin_no_more(harness) -> None:
    """Kicking sends not_member on the open socket; a later join is refused too."""
    map_id, kira, bob = setup_map(harness)
    socket = Socket(harness, bob, B)
    socket.join(map_id)
    bob_id = harness.api("GET", "/api/account", bob).json()["account_id"]
    harness.api("DELETE", f"/api/maps/{map_id}/members/{bob_id}", kira)
    assert socket.until("error")["code"] == "not_member"
    socket.send({"t": "join", "id": 9, "map": map_id, "last_seq": 0})
    assert socket.until("error")["code"] == "not_member"
    socket.close()


def test_hello_checks(harness) -> None:
    """Bad tokens, old apps and bad client IDs are refused."""
    token = harness.login(91000001, "Kira")
    with harness.client.websocket_connect("/ws") as ws:
        ws.send_json({"t": "hello", "token": "bad", "protocol": 1, "app": "0.1.0", "client_id": A})
        assert ws.receive_json()["code"] == "unauthorized"
    with harness.client.websocket_connect("/ws") as ws:
        ws.send_json({"t": "hello", "token": token, "protocol": 1, "app": "0.0.1", "client_id": A})
        assert ws.receive_json()["code"] == "upgrade_required"
    with harness.client.websocket_connect("/ws") as ws:
        ws.send_json({"t": "hello", "token": token, "protocol": 1, "app": "0.1.0", "client_id": "NOPE"})
        assert ws.receive_json()["code"] == "invalid"
    socket = Socket(harness, token, A)
    socket.send({"t": "ping", "id": 5})
    assert socket.until("pong")["re"] == 5
    socket.close()


# ---------------------------------------------------------------- roles and owner transfer

def test_managers_invite_but_cant_make_managers(harness) -> None:
    """A manager invites editors; making managers is owner-only."""
    map_id, kira, bob = setup_map(harness)
    bob_id = harness.api("GET", "/api/account", bob).json()["account_id"]
    harness.api("PATCH", f"/api/maps/{map_id}/members/{bob_id}", kira, json={"role": "manager"})
    assert harness.api("POST", f"/api/maps/{map_id}/invites", bob, json={"role": "editor"}).status_code == 200
    assert harness.api("POST", f"/api/maps/{map_id}/invites", bob, json={"role": "manager"}).status_code == 403
    carl = harness.login(91000005, "Carl")
    assert harness.api("POST", f"/api/maps/{map_id}/invites", carl, json={"role": "viewer"}).status_code == 403


def test_the_longest_standing_manager_takes_over_and_pays(harness) -> None:
    """When the owner leaves, the oldest manager owns the map and the bill moves to them."""
    map_id, kira, bob = setup_map(harness)
    kira_id = harness.api("GET", "/api/account", kira).json()["account_id"]
    bob_id = harness.api("GET", "/api/account", bob).json()["account_id"]
    harness.api("PATCH", f"/api/maps/{map_id}/members/{bob_id}", kira, json={"role": "manager"})
    harness.api("DELETE", f"/api/maps/{map_id}/members/{kira_id}", kira)
    assert harness.service.maps.role(map_id, bob_id) == "owner"
    payer = harness.service.db.value("SELECT payer_id FROM maps WHERE id=?", (map_id,))
    assert payer == harness.service.maps.payer_for_account(bob_id)


# ---------------------------------------------------------------- billing

def test_prices_rank_maps_by_seats() -> None:
    """The plan's examples (§7)."""
    tiers = [(100, 25), (50, 15), (25, 5)]
    maps = lambda *seats: [{"id": str(index), "seats": count, "created_at": str(index)} for index, count in enumerate(seats)]
    assert monthly_total(maps(3), tiers)[0] == 100 and monthly_total(maps(4), tiers)[0] == 125
    assert monthly_total(maps(4, 3), tiers)[0] == 175
    assert monthly_total(maps(4, 6), tiers)[0] == 240
    assert monthly_total(maps(3, 3, 3, 3), tiers)[0] == 175
    assert monthly_total(maps(3, 3, 3, 5), tiers)[0] == 225
    assert map_price(5, 50, tiers) == 0


def test_a_first_map_needs_money_or_the_one_trial(harness) -> None:
    """Without balance the first map starts the trial; a second map or alt trial is refused."""
    kira = harness.login(91000001, "Kira")
    response = harness.api("POST", "/api/maps", kira, json={"name": "One", "client_id": A, "ops": []})
    assert response.json()["billing"] == "trial"
    again = harness.api("POST", "/api/maps", kira, json={"name": "Two", "client_id": A, "ops": []})
    assert again.json()["error"] == "trial_limit"
    status = harness.api("GET", f"/api/maps/{response.json()['map_id']}", kira).json()["status"]
    assert status["state"] == "trial"
    # the trial character can't start another trial on a new account
    harness.service.db.execute("INSERT INTO accounts (id, created_at) VALUES (999, ?)", (iso(utc_now()),))
    harness.service.db.execute("UPDATE characters SET account_id=999 WHERE char_id=91000001")
    other = harness.login(91000001, "Kira")
    refused = harness.api("POST", "/api/maps", other, json={"name": "Three", "client_id": A, "ops": []})
    assert refused.json()["error"] == "trial_used"
    assert harness.service.db.value("SELECT count(*) FROM admin_events WHERE kind='trial_refused'") == 1


def test_wallet_donations_are_matched_by_character_or_code(harness) -> None:
    """A donation from a linked character or with the payer code counts; others go to the admin."""
    kira = harness.login(91000001, "Kira")
    code = harness.api("GET", "/api/account", kira).json()["payer_code"]
    harness.eve.journal = [
        {"id": 1, "ref_type": "player_donation", "first_party_id": 91000001, "amount": 100e6, "reason": ""},
        {"id": 2, "ref_type": "player_donation", "first_party_id": 99999999, "amount": 50e6, "reason": f"for {code}"},
        {"id": 3, "ref_type": "player_donation", "first_party_id": 99999998, "amount": 10e6, "reason": "hi"},
        {"id": 4, "ref_type": "bounty_prizes", "first_party_id": 1, "amount": 5e6},
    ]
    assert harness.service.poll_wallet() == {"applied": 2, "unmatched": 1}
    assert harness.service.poll_wallet() == {"applied": 0, "unmatched": 0}      # journal ids count once
    assert harness.api("GET", "/api/account", kira).json()["balance"] == 150e6
    assert harness.service.db.value("SELECT count(*) FROM admin_events WHERE kind='unmatched_donation'") == 1


def lapse(harness: Harness, payer: int, ago: timedelta) -> None:
    """Make a payer's money run out ``ago`` before now."""
    moment = iso(utc_now() - ago)
    harness.service.db.execute("UPDATE billing SET balance_isk=0, paid_until=?, billed_until=?, lapsed_at=NULL, "
                               "trial_until=NULL WHERE payer_id=?", (moment, moment, payer))


def test_a_missed_payment_warns_then_goes_read_only_and_deletes(harness) -> None:
    """First miss: 7 editable days, then read-only; paying restores; 2 months after paid_until it is deleted."""
    map_id, kira, bob = setup_map(harness)
    payer = harness.service.db.value("SELECT payer_id FROM maps WHERE id=?", (map_id,))
    billing = harness.service.billing
    lapse(harness, payer, timedelta(hours=1))
    assert billing.evaluate(payer) == "warning"
    assert "editable until" in billing.status(map_id, 1)["warning"]
    assert harness.service.db.value("SELECT count FROM payment_strikes WHERE subject_kind='char' "
                                    "AND subject_id=91000001") == 1
    lapse(harness, payer, timedelta(days=8))
    assert billing.evaluate(payer) == "read_only"
    socket = Socket(harness, bob, B)
    socket.join(map_id)
    assert socket.push(map_id, [system_op(C5, 30, B, HOME.id, 1)])["code"] == "read_only"
    billing.credit(payer, 1e9)
    assert billing.evaluate(payer) == "active"
    assert socket.push(map_id, [system_op(C5, 31, B, HOME.id, 1)])["t"] == "ack"
    socket.close()
    lapse(harness, payer, timedelta(days=61))
    assert harness.service.sweep_billing() == [map_id] and harness.service.maps.map(map_id) is None


def test_a_repeat_miss_is_read_only_at_once(harness) -> None:
    """A character with a strike in the last 12 months gets no grace days."""
    map_id, kira, bob = setup_map(harness)
    payer = harness.service.db.value("SELECT payer_id FROM maps WHERE id=?", (map_id,))
    harness.service.db.execute("INSERT INTO payment_strikes (subject_kind, subject_id, count, last_at) VALUES "
                               "('char', 91000001, 1, ?)", (iso(utc_now() - timedelta(days=100)),))
    lapse(harness, payer, timedelta(hours=1))
    assert harness.service.billing.evaluate(payer) == "read_only"


def test_the_balance_runs_down_and_paid_until_moves_with_seats(harness) -> None:
    """paid_until is balance / daily cost; a new seat brings it closer."""
    map_id, kira, bob = setup_map(harness, isk=100e6)     # 2 seats: 100M a month
    payer = harness.service.db.value("SELECT payer_id FROM maps WHERE id=?", (map_id,))
    days = (parse_iso(harness.service.billing.row(payer)["paid_until"]) - utc_now()).total_seconds() / 86400
    assert 29.9 < days < 30.1
    quote = harness.api("GET", f"/api/billing/quote?map={map_id}&seats=2", kira).json()
    assert (quote["before"], quote["after"]) == (100e6, 125e6)


# ---------------------------------------------------------------- prune and presence

def test_auto_prune_removes_old_connections_and_unlinked_systems(harness) -> None:
    """Connections go after 2 days; systems after 4 unless linked to home, pinned or home."""
    map_id, kira, bob = setup_map(harness)
    socket = Socket(harness, kira, A)
    socket.join(map_id)
    socket.push(map_id, [system_op(C5, 40, A, HOME.id, 1),
                         {"e": "connection", "k": sorted([HOME.id, C5]), "f": {"mass": "fresh"}, "full": True,
                          "hlc": format_hlc(40, 0), "c": A},
                         system_op(C3, 40, A, pinned=1),
                         system_op(C2, 40, A)])
    service = harness.service
    assert service.prune(utc_now() + timedelta(days=1)) == 0
    assert service.prune(utc_now() + timedelta(days=3)) == 1            # the connection
    state = service.hub.state(map_id)
    assert not state.live("connection", tuple(sorted([HOME.id, C5])))
    service.prune(utc_now() + timedelta(days=5))
    assert state.live("system", HOME.id) and state.live("system", C3)
    assert not state.live("system", C5) and not state.live("system", C2)
    assert socket.until("ops")["by"] is None                              # broadcast as server ops
    socket.close()


def test_presence_is_shared_only_for_own_characters(harness) -> None:
    """Members see each other's shared characters; others' characters are refused; null removes."""
    map_id, kira, bob = setup_map(harness)
    first, second = Socket(harness, kira, A), Socket(harness, bob, B)
    first.join(map_id)
    second.join(map_id)
    first.send({"t": "presence", "map": map_id, "char_id": 91000001, "system_id": C5})
    seen = second.until("presence")
    assert (seen["name"], seen["system_id"], seen["afk"]) == ("Kira", C5, False)
    first.send({"t": "presence", "map": map_id, "char_id": 91000002, "system_id": C5})
    assert first.until("error")["code"] == "forbidden"
    first.send({"t": "presence", "map": map_id, "char_id": 91000001, "system_id": None})
    assert second.until("presence")["system_id"] is None
    first.close()
    second.close()


def test_presence_goes_after_the_socket_has_been_gone_10_minutes(harness) -> None:
    """A closed socket's characters stay 10 minutes, then drop."""
    map_id, kira, bob = setup_map(harness)
    socket = Socket(harness, kira, A)
    socket.join(map_id)
    socket.send({"t": "presence", "map": map_id, "char_id": 91000001, "system_id": C5})
    socket.send({"t": "ping", "id": 3})
    socket.until("pong")
    socket.close()
    hub = harness.service.hub
    entry = hub.presence[map_id][91000001]
    assert entry.gone_since is not None and hub.expire_presence(entry.gone_since + 60) == 0
    assert hub.expire_presence(entry.gone_since + 601) == 1


# ---------------------------------------------------------------- corp maps

def test_corp_maps_follow_the_ceo_and_drop_leavers(harness) -> None:
    """Only the CEO makes a corp map; the switch lets corp members view; leaving the corp removes them."""
    eve = harness.eve
    eve.corps |= {91000001: 98000001, 91000002: 98000001, 91000003: 98000001}
    eve.corp_info[98000001] = {"name": "Hole Dwellers", "ceo_id": 91000001}
    ceo, member, director = (harness.login(91000001, "Ceo"), harness.login(91000002, "Member"),
                             harness.login(91000003, "Director"))
    assert harness.api("POST", "/api/maps", member, json={"name": "Corp", "client_id": A, "corp": True,
                                                          "char_id": 91000002}).json()["error"] == "forbidden"
    payer = harness.service.maps.payer_for_corp(98000001)
    harness.service.billing.credit(payer, 1e9)
    map_id = harness.make_map(ceo, name="Corp", corp=True, char_id=91000001)
    assert harness.api("POST", f"/api/maps/{map_id}/corp-join", member).status_code == 403   # switch off
    harness.api("POST", f"/api/maps/{map_id}/corp", ceo, json={"corp_join": True, "seat_limit": 2})
    assert harness.api("POST", f"/api/maps/{map_id}/corp-join", member).json()["role"] == "viewer"
    assert harness.api("POST", f"/api/maps/{map_id}/corp-join", director).json()["error"] == "full"
    # an invite comes first: the newest self-joined viewer makes room
    code = harness.api("POST", f"/api/maps/{map_id}/invites", ceo, json={"role": "editor"}).json()["code"]
    harness.api("POST", f"/api/invites/{code}/accept", director)
    roles = {member["names"][0]: member["role"] for member in harness.service.maps.members(map_id)}
    assert roles == {"Ceo": "owner", "Director": "editor"}
    # the CEO changes: the owner role moves, the old CEO stays as a manager
    eve.corp_info[98000001] = {"name": "Hole Dwellers", "ceo_id": 91000003}
    harness.service.poll_affiliations()
    roles = {member["names"][0]: member["role"] for member in harness.service.maps.members(map_id)}
    assert roles == {"Ceo": "manager", "Director": "owner"}
    # the old CEO leaves the corp and is removed straight away
    eve.corps[91000001] = 98000002
    harness.service.poll_affiliations()
    assert [member["names"][0] for member in harness.service.maps.members(map_id)] == ["Director"]


# ---------------------------------------------------------------- account and admin

def test_account_export_and_delete(harness) -> None:
    """The GDPR export lists the account's data; deleting leaves maps and ends sessions."""
    map_id, kira, bob = setup_map(harness)
    export = harness.api("GET", "/api/account/export", kira).json()
    assert export["characters"][0]["name"] == "Kira" and export["owned_maps"][0]["id"] == map_id
    assert harness.api("POST", "/api/account/delete", kira, json={}).status_code == 400
    assert harness.api("POST", "/api/account/delete", kira, json={"confirm": "DELETE"}).status_code == 200
    assert harness.api("GET", "/api/account", kira).status_code == 401
    bob_id = harness.api("GET", "/api/account", bob).json()["account_id"]
    assert harness.service.maps.role(map_id, bob_id) == "owner"


def test_admin_cli_credits_and_lists(harness, tmp_path, capsys) -> None:
    """The admin CLI works on the server database."""
    from homebound_server.__main__ import main
    database = tmp_path / "admin.sqlite"
    other = Harness.__new__(Harness)
    from homebound_server.config import Config
    from homebound_server.service import Service
    from conftest import UNIVERSE
    service = Service(Config(database=database, dev=True), universe=UNIVERSE)
    service.auth.attach_character(__import__("homebound_server.esi", fromlist=["SsoCharacter"]).SsoCharacter(
        91000001, "Kira", "h"), None, None)
    service.db.close()
    assert main(["--dev", "--db", str(database), "admin", "credit", "Kira", "100000000"]) == 0
    assert '"balance_isk": 100000000' in capsys.readouterr().out
    assert main(["--dev", "--db", str(database), "admin", "maps"]) == 0
    del other


def test_health_and_webhook_outbox(harness) -> None:
    """Health answers; events wait in the outbox until a webhook takes them."""
    assert harness.client.get("/api/health").json()["ok"] is True
    harness.service.events.server_problem("disk 81%")
    assert harness.service.events.flush() == 0              # no webhook configured: kept

    class Fake:
        def __init__(self):
            self.sent = []

        def post(self, url, json):
            self.sent.append(json)
            import httpx
            return httpx.Response(204, request=httpx.Request("POST", url))

    harness.service.events.webhook = "https://discord.example/webhook"
    fake = Fake()
    assert harness.service.events.flush(fake) == 1 and "disk 81%" in fake.sent[0]["embeds"][0]["description"]
    assert harness.service.events.flush(fake) == 0


def test_payments_get_a_receipt_and_unassigned_ones_a_problem_mail(harness) -> None:
    """The receiving character mails each sender once: a receipt, or why the ISK couldn't be assigned."""
    kira = harness.login(91000001, "Kira")
    code = harness.api("GET", "/api/account", kira).json()["payer_code"]
    harness.eve.corp_info[98000001] = {"name": "Hole Dwellers", "ceo_id": 91000077}
    harness.eve.journal = [
        {"id": 1, "ref_type": "player_donation", "first_party_id": 91000001, "amount": 100e6, "reason": ""},
        {"id": 2, "ref_type": "player_donation", "first_party_id": 99999999, "amount": 50e6, "reason": "HB-ZZZZ"},
        {"id": 3, "ref_type": "player_donation", "first_party_id": 99999998, "amount": 10e6, "reason": "hi"},
        {"id": 4, "ref_type": "corporation_account_withdrawal", "first_party_id": 98000001, "amount": 5e6},
    ]
    harness.service.poll_wallet()
    sent = {mail["to"]: mail for mail in harness.eve.sent}
    assert set(sent) == {91000001, 99999999, 99999998, 91000077}
    assert all(mail["from"] == 95000001 for mail in harness.eve.sent)
    assert sent[91000001]["subject"] == "Homebound: payment received"
    assert "100,000,000 ISK" in sent[91000001]["body"] and code in sent[91000001]["body"]
    assert "nothing is charged" in sent[91000001]["body"]                 # no maps yet
    assert "HB-ZZZZ" in sent[99999999]["body"] and "could not be assigned" in sent[99999999]["subject"]
    assert "isn't linked" in sent[99999998]["body"]
    assert "Hole Dwellers" in sent[91000077]["body"]                      # corp payments go to the CEO
    harness.eve.journal.append({"id": 5, "ref_type": "player_donation", "first_party_id": 91000001, "amount": 1e6})
    harness.service.poll_wallet()
    assert len(harness.eve.sent) == 5                                      # one mail per payment, ever


def test_receipts_say_how_long_the_maps_are_paid(harness) -> None:
    """With a map, the receipt has the new paid-until date."""
    map_id, kira, bob = setup_map(harness)
    harness.eve.journal = [{"id": 7, "ref_type": "player_donation", "first_party_id": 91000001, "amount": 300e6}]
    harness.service.poll_wallet()
    assert "paid until" in harness.eve.sent[-1]["body"] and "EVE time" in harness.eve.sent[-1]["body"]


def test_throttled_and_failing_mails_are_retried_later(harness) -> None:
    """EVE's 520 throttle leaves the mail queued; other errors count attempts until it gives up."""
    import httpx

    from homebound_server.mailer import MAX_ATTEMPTS
    harness.login(91000001, "Kira")
    request = httpx.Request("POST", "https://esi")
    harness.eve.send_error = httpx.HTTPStatusError("520", request=request, response=httpx.Response(520, request=request))
    harness.eve.journal = [{"id": 1, "ref_type": "player_donation", "first_party_id": 91000001, "amount": 1e6}]
    harness.service.poll_wallet()
    assert harness.eve.sent == [] and harness.service.db.value("SELECT attempts FROM outgoing_mails") == 0
    harness.eve.send_error = httpx.HTTPStatusError("400", request=request, response=httpx.Response(400, request=request))
    for _ in range(MAX_ATTEMPTS + 2):
        harness.service.send_mails()
    assert len(harness.service.mailer.given_up()) == 1
    harness.eve.send_error = None
    assert harness.service.send_mails() == 0


def test_the_receivers_own_mails_are_not_requests(harness) -> None:
    """Sent receipts show up in the receiver's mail list; only mails from others become admin events."""
    harness.eve.mails = [{"mail_id": 1, "from": 95000001, "subject": "Homebound: payment received"},
                         {"mail_id": 2, "from": 91000001, "subject": "more maps please"}]
    assert harness.service.poll_mail() == 1


def test_the_receiver_link_stores_its_token_once(harness) -> None:
    """The admin link signs the receiving character in with the wallet and mail scopes; it works once."""
    from urllib.parse import parse_qs, urlparse
    link = urlparse(harness.service.create_receiver_link())
    to_eve = harness.client.get(f"{link.path}?{link.query}", follow_redirects=False)
    assert to_eve.status_code == 302
    state = parse_qs(urlparse(to_eve.headers["location"]).query)["state"][0]
    back = harness.client.get("/dev/sso", params={"state": state, "char_id": 95000001, "name": "Homebound Billing"},
                              follow_redirects=False)
    done = harness.client.get(back.headers["location"])
    assert done.status_code == 200 and "Homebound Billing" in done.text
    assert harness.service.receiver() == (95000001, "fake-refresh-95000001")
    again = harness.client.get(f"{link.path}?{link.query}", follow_redirects=False)
    assert again.status_code == 403


def test_the_receiver_link_refuses_another_character(harness) -> None:
    """With HB_RECEIVER_CHAR_ID set, signing in with someone else stores nothing."""
    from urllib.parse import parse_qs, urlparse
    link = urlparse(harness.service.create_receiver_link())
    to_eve = harness.client.get(f"{link.path}?{link.query}", follow_redirects=False)
    state = parse_qs(urlparse(to_eve.headers["location"]).query)["state"][0]
    back = harness.client.get("/dev/sso", params={"state": state, "char_id": 91000001, "name": "Kira"},
                              follow_redirects=False)
    assert harness.client.get(back.headers["location"]).status_code == 403
    assert harness.service.receiver() == (95000001, "")


class FakeWebhook:
    """Collects what would be posted to Discord."""

    def __init__(self) -> None:
        """Nothing posted yet."""
        self.sent: list[dict] = []

    def post(self, url, json):
        """Keep the message and answer like Discord."""
        import httpx
        self.sent.append(json["embeds"][0])
        return httpx.Response(204, request=httpx.Request("POST", url))


def posted(harness: Harness) -> list[dict]:
    """Flush the outbox to a fake webhook and return the embeds."""
    harness.service.events.webhook = "https://discord.example/webhook"
    hook = FakeWebhook()
    harness.service.events.flush(hook)
    return hook.sent


def test_webhook_messages_have_names_isk_and_admin_hints(harness) -> None:
    """Unknown senders are named through ESI; accounts show their characters; amounts are ISK."""
    kira = harness.login(91000001, "Kira")
    account_id = harness.api("GET", "/api/account", kira).json()["account_id"]
    harness.eve.known_names[99999998] = "Random Pilot"
    harness.eve.journal = [{"id": 3, "ref_type": "player_donation", "first_party_id": 99999998, "amount": 10e6,
                            "reason": "hi"}]
    harness.service.poll_wallet()
    harness.service.events.map_limit_request(91000001, account_id, 6, "more maps please")
    unmatched, request = [embed for embed in posted(harness) if "Unmatched" in embed["title"] or "limit" in embed["title"]]
    assert "10,000,000 ISK" in unmatched["description"]
    assert "[Random Pilot](https://evewho.com/character/99999998)" in unmatched["description"]
    assert unmatched["footer"]["text"].endswith("refunded 3") and unmatched["timestamp"]
    fields = {field["name"]: field["value"] for field in request["fields"]}
    assert fields["Account"] == f"a{account_id} (Kira)" and fields["Subject"] == "more maps please"
    assert f"map-limit a{account_id}" in request["footer"]["text"]


def test_billing_messages_can_be_muted(harness) -> None:
    """HB_MUTE_BILLING drops billing state changes; other events still go out."""
    harness.service.events.mute_billing = True
    assert harness.service.events.billing_state(1, "paid", "warning") is None
    harness.service.events.mute_billing = False
    map_id, kira, bob = setup_map(harness)
    payer = harness.service.db.value("SELECT payer_id FROM maps WHERE id=?", (map_id,))
    harness.service.events.billing_state(payer, "paid", "read_only")
    embed = posted(harness)[-1]
    assert "read-only" in embed["description"] and "Kira" in embed["description"]


def test_config_reads_the_new_settings(monkeypatch) -> None:
    """The mute switch and the disk threshold come from the environment."""
    from homebound_server.config import Config
    monkeypatch.setenv("HB_MUTE_BILLING", "1")
    monkeypatch.setenv("HB_DISK_WARN_PERCENT", "90")
    config = Config.from_environment()
    assert config.mute_billing is True and config.disk_warn_percent == 90


def test_a_full_disk_warns_once_a_day(tmp_path) -> None:
    """Over the threshold: one server problem, not one every 10 minutes; under it again: reset."""
    from collections import namedtuple
    harness = Harness(tmp_path)
    Usage = namedtuple("Usage", "total used free")
    full, fine = (lambda _: Usage(100 * 2**30, 85 * 2**30, 15 * 2**30)), (lambda _: Usage(100, 10, 90))
    assert harness.service.check_disk(full) == 85
    harness.service.check_disk(full)
    problems = [embed for embed in posted(harness) if "problem" in embed["title"]]
    assert len(problems) == 1 and "85% full" in problems[0]["description"]
    harness.service.check_disk(fine)
    harness.service.check_disk(full)
    assert len([embed for embed in posted(harness) if "problem" in embed["title"]]) == 1   # a new warning


def test_several_starts_in_a_few_minutes_look_like_a_restart_loop(harness) -> None:
    """The first start is a grey note; the third within 10 minutes is a red alert."""
    for _ in range(3):
        harness.service.started()
    embeds = posted(harness)
    assert [embed["title"] for embed in embeds] == ["Homebound · Server started"] * 2 + ["Homebound · Restart loop?"]
    assert "3 starts in 10 minutes" in embeds[-1]["description"]


def test_a_mail_that_keeps_failing_is_reported(harness) -> None:
    """When a receipt is given up, the admin hears about it."""
    import httpx

    from homebound_server.mailer import MAX_ATTEMPTS
    harness.login(91000001, "Kira")
    request = httpx.Request("POST", "https://esi")
    harness.eve.send_error = httpx.HTTPStatusError("400", request=request, response=httpx.Response(400, request=request))
    harness.eve.journal = [{"id": 1, "ref_type": "player_donation", "first_party_id": 91000001, "amount": 1e6}]
    harness.service.poll_wallet()
    for _ in range(MAX_ATTEMPTS):
        harness.service.send_mails()
    given_up = [embed for embed in posted(harness) if "not delivered" in embed["title"]]
    assert len(given_up) == 1 and "[Kira]" in given_up[0]["description"]


def link_receiver(harness: Harness, char_id: int = 95000001):
    """Run the receiver link in the browser and return the final page."""
    from urllib.parse import parse_qs, urlparse
    link = urlparse(harness.service.create_receiver_link())
    to_eve = harness.client.get(f"{link.path}?{link.query}", follow_redirects=False)
    state = parse_qs(urlparse(to_eve.headers["location"]).query)["state"][0]
    back = harness.client.get("/dev/sso", params={"state": state, "char_id": char_id, "name": "Homebound Billing"},
                              follow_redirects=False)
    return harness.client.get(back.headers["location"])


def test_the_receiver_token_is_encrypted_in_the_database(harness) -> None:
    """With HB_TOKEN_ENC_KEY the DB only holds ciphertext; a wrong key is a clear error."""
    from cryptography.fernet import Fernet
    harness.config.token_key = Fernet.generate_key().decode()
    assert link_receiver(harness).status_code == 200
    stored = harness.service.db.value("SELECT value FROM meta WHERE key='receiver_refresh_token'")
    assert stored.startswith("v1:") and "fake-refresh" not in stored
    assert harness.service.receiver() == (95000001, "fake-refresh-95000001")
    harness.config.token_key = Fernet.generate_key().decode()
    with pytest.raises(RuntimeError, match="doesn't match"):
        harness.service.receiver()


def test_production_refuses_to_link_without_a_key(harness) -> None:
    """Outside dev mode the token is never stored in plain text."""
    from urllib.parse import urlparse
    harness.config.dev = False
    link = urlparse(harness.service.create_receiver_link())
    page = harness.client.get(f"{link.path}?{link.query}", follow_redirects=False)
    assert page.status_code == 403 and "HB_TOKEN_ENC_KEY" in page.text
    assert harness.service.db.value("SELECT value FROM meta WHERE key='receiver_refresh_token'") is None


def test_the_wallet_poller_pings_the_healthcheck(harness) -> None:
    """Each successful poll pings healthchecks.io; a failed one doesn't."""
    pings = []
    harness.config.healthcheck_url = "https://hc-ping.com/abc"
    harness.service.http_get = lambda url, timeout: pings.append(url)
    harness.service.poll_wallet()
    harness.eve.fail = True
    with pytest.raises(Exception):
        harness.service.poll_wallet()
    assert pings == ["https://hc-ping.com/abc"]


def test_the_database_waits_for_locks(tmp_path) -> None:
    """Litestream holds short locks; the server waits instead of failing ("database is locked")."""
    from homebound_server.db import Database
    database = Database(tmp_path / "server.sqlite")
    assert database.value("PRAGMA journal_mode") == "wal"
    assert database.value("PRAGMA busy_timeout") == 5000

# Homebound Cloud Sync: design plan

> **STATUS: implemented (2026-11-08) and merged into `master` (1.0.0), phases 0–9 of §11. The web view (phase 10) is still deferred.**
> Where the build differs from this design, or fills a gap in it, see §21. Open questions are in §20.
> Design review 2026-11-07: decisions went into §3, §5–§8, §10, §11 and the new §15–§19. All items are decided, apart from the open questions in §20. The protocol is specified in [`sync-protocol.md`](sync-protocol.md).

## 1. Goals and non-goals

**Goals**
- Several people in a group or corp edit the same map live, like Google Docs. A personal map is a group of one.
- Clients are local-first. The app keeps working offline on its local SQLite and merges when the connection comes back.
- The server footprint is tiny: one small VPS, holding only current state plus a short tail of diffs.
- Access costs a monthly ISK payment, checked automatically.
- There is a **read-only web view** that also works on phone browsers, so members can check the map away from their PC or without the app installed. It can't be used to add to or edit the map. **Deferred:** it is a secondary goal and not part of the first implementation run (see §10).

**Non-goals for now**
- The web view (deferred, see above).
- Editing from the web.
- Server-side zKill, or ESI for map data. Clients keep doing their own zKill lookups.
- A full edit history or undo on the server.
- Scaling past roughly 100 groups.

## 2. Architecture

```
 client A (PySide6)          sync server (1 process)            client B
 local SQLite  ── ops ──▶  auth · membership · seq · merge  ◀── ops ── local SQLite
               ◀─ ops ──   current state + op tail (SQLite) ── ops ─▶
                           wallet poller (ESI, server only)
                           auto-prune timer
                                   │ read-only
                                   ▼
                           web view (desktop + phone browser)
```

- **Client:** the existing `homebound/mapdb` SQLite stays the working copy. A new `homebound/sync/` package records local changes as ops and applies remote ops.
- **Server:** a single Python process (Starlette + uvicorn/`websockets`) using SQLite in WAL mode, behind Caddy for TLS. Python lets the server reuse the client's protocol and merge code (`homebound/sync/protocol.py`).
- **The server only:** authenticates, checks membership and role, gives each op a sequence number, merges it into the current state, broadcasts it, polls the wallet, runs the auto-prune timer, and serves the static web view.
- **Hosting:** Hetzner (decided, see §14 and [`hosting-hetzner.md`](hosting-hetzner.md)).
- **Domain:** `homebound.luthopian.de`. Caddy handles Let's Encrypt automatically.

## 3. Sync model

**An operation log plus per-field last-writer-wins (LWW), ordered by a Hybrid Logical Clock (HLC).** This is simpler than OT or a full CRDT and fits row and column data well.

### Entity keys
These are the natural keys already in `homebound/mapdb/schema.sql`. No UUIDs are needed.

| entity     | key                              | notes |
|------------|----------------------------------|-------|
| system     | `solarSystemID`                  | already the PK |
| connection | `(min(a,b), max(a,b))`           | `connections.id` is AUTOINCREMENT and stays local; it never goes over the wire |
| signature  | `(system_id, sig_id)`            | already the PK |
| setting    | `key`                            | for each map: naming scheme, home system. These move from the app settings into a new synced `map_settings` table. The existing `key_value` table holds per-user UI state (sig table sort order, collapsed groups) and stays local. |

### Ops
```json
{"entity": "system", "key": 31002238, "fields": {"x": 120.5, "notes": "farm"}, "hlc": "…", "client_id": "…"}
{"entity": "signature", "key": [31002238, "ABC-123"], "delete": true, "hlc": "…", "client_id": "…"}
```

### Merge rules
- **Plain fields** (notes, owner, mass, lifetime, type, custom name …): for each field, the higher `(hlc, client_id)` wins. The result is the same whatever order ops arrive in, so every replica converges. An edit made offline with an older HLC loses against a newer one.
- **Structural fields: first to reach the server wins.** `parent_id` and `hole_index` form one compound field, `placement`, with a single HLC, so they always change together. The server checks every push against its current state and **rejects** a placement that would create a cycle or reuse a `(parent_id, hole_index)` slot that is already taken. A rejected op comes back as `conflict` with the server's value. The client applies the server value and recomputes its names. If both clients were up to date they compute the same hole index anyway. The conflict only happens when one of them was behind.
- **`level_index` is not stored.** It is derived from `(added_at, system id)`, where `added_at` is the server's time (see below). Every replica therefore names the same way.
- **Pasting a sig list produces a single batch.** The server applies a batch all at once or not at all.

### Deletes and tombstones
- A delete is a tombstone with its own HLC.
- **The client cascades.** Deleting a system sends explicit tombstones for its connections and signatures, and clears the placement of its children, all in the same batch. The schema's `ON DELETE CASCADE` must not be relied on for synced data.
- **Only a full upsert brings an entity back.** For example, a system that gets added again after you jump into it. An update to a few fields of a tombstoned entity is dropped.
- **An op that refers to a tombstoned system is rejected**, unless the same batch adds that system again. An example is a sig pasted into a system that someone else just deleted.
- **Tombstones are kept for as long as the map exists. There is no TTL.** They are only a key and an HLC, so even years of them stay small. A client that comes back after a long time offline cannot bring back a deleted chain, because its stale rows carry older HLCs than the tombstones. Deletes that were already applied are never sent again.

### Server time
- The server stamps `added_at`, `created_at` and `lifetime_set_at` when it first receives an entity. The client's value is only a local placeholder until the ack arrives.
- Auto-prune and lifetime expiry therefore don't depend on members' clocks.

### Reconnecting with unsent changes
- The client first applies the remote ops (or the snapshot), then replays its outbox on top and pushes it again.
- Ops in the outbox keep their original HLC, so merging stays deterministic.

### What syncs
| data | syncs? | notes |
|---|---|---|
| systems, connections, signatures | yes | apart from the exceptions below |
| system `x`/`y` | shared default | The position from the first placement syncs. When a user drags a node, the new position becomes a **local override** that doesn't sync, so members don't move each other's layout. A "Reset to shared layout" action clears the overrides. |
| `confirmed`, `pinned`, `notes`, owner, wormhole fields | yes | |
| sig `state = missing` | no | Purely local: rows missing from *your* last paste. |
| sig `state = new` | no | Derived locally: `created_at` is within the last ~5 min, or the sig is newer than your last paste. |
| sig `seen_at` | yes | |
| `characters` | no | Shared through presence instead. |
| `zkill` | no | Each client fetches its own. |
| `key_value` (UI state) | no | |
| `map_settings` (home, naming scheme) | yes | Only the owner can change the home system (§5). |

### Undo
- Undo reverses **your last action** using the inverse ops recorded with it. It no longer restores a whole snapshot.
- Undo is tied to **one specific action**: the action whose Undo toast or entry you pick, not "whatever happened last". Each action keeps its own inverse ops, so undoing it never touches other actions, yours or anyone else's.
- If someone else has changed a field since that action, undo leaves that field alone, so it never overwrites other members' later edits.

### What the server keeps
- The materialised current rows: field values plus the HLC for each field, and all tombstones.
- An op tail of the last 7 days or 10k ops per map, whichever is smaller.
- A client that reconnects inside the tail gets just the missing ops. A client that is older gets a full snapshot, tombstones included. A map is a few hundred rows, so a snapshot is only a few KB.

### Known edge cases
- **Concurrent `notes` edits:** LWW keeps only one of them. At first the client can show "edited by X while you were typing". A text CRDT for notes is a later option.
- **Two members jump the same hole at once:** both trackers add the same connection, which has the same natural key, so the two adds merge. If they choose different hole indexes, the structural rule applies: the first to reach the server wins and the other client takes the server's value.

### Presence (live character locations)
- Presence is ephemeral and **in memory on the server only**. It is never written to disk.
- Sharing is opt-in for each character and each map. The data comes from the client's local chat log watcher, as it does now.
- If the same character is reported by two clients, the newest report wins.
- **AFK and offline:** EVE's chat logs don't record a logout. The client only sees when a log file was last written (`homebound/logs/watcher.py`).
  - **AFK:** a character with no location update for **1 hour** is shown as AFK, with its last system.
  - **Offline:** a character is dropped from presence when its client says so (`system_id: null`), meaning its log has been quiet longer than the client's online window, the user stopped sharing, or the app closed cleanly. It is also dropped when the client's socket has been gone for 10 minutes.
  - If a future log source does show a logout, the client sends `null` straight away.
- All roles can see presence, including viewers and the web view.

### Auto-prune
Pruning runs on the server and is emitted as normal delete ops, so every client converges on the same result.
- **Connections:** removed automatically **2 days** after `created_at`. Wormholes don't live longer than that.
- **Systems:** removed automatically **4 days** after `added_at`.
- **Never auto-removed:** the home system and systems marked `pinned`. A system is also kept while it still has a live connection on the path back to home.
- Everything else is only removed by hand.
- Local maps that are not synced can use the same rule as an option in settings.

## 4. Protocol (WebSocket, JSON; msgpack later if size matters)

This is the overview. The full message schemas, limits, error codes and server-side validation are in [`sync-protocol.md`](sync-protocol.md).

| direction | message | meaning |
|-----------|---------|---------|
| C→S | `hello{token, map_id, last_seq}` | Join a map. The server replies with `ops_since{…}` or `snapshot{…}` |
| C→S | `push{client_batch_id, ops[]}` | Send local changes (owners and editors only) |
| S→C | `ack{client_batch_id, seq_from, seq_to}` | The changes are durable on the server |
| S→C | `ops{seq, ops[]}` | Another member's changes, or auto-prune |
| C↔S | `presence{char_id, system_id}` | Broadcast only, not stored |
| S→C | `status{paid_until, state, warning}` | Billing and lapse state, shown on launch |
| S→C | `error{code}` | `unauthorized`, `not_member`, `read_only` (viewer, web or lapsed), `rate_limited` |

- The client keeps a `sync_outbox` and resends any batch that has no ack.
- The server ignores a `client_batch_id` it has already seen, so resending is safe.
- The web view uses the same socket, receive-only, and the server rejects `push` from it.

## 5. Roles

| role    | edit map | change home | invite / manage members | billing | see presence | web view |
|---------|----------|-------------|-------------------------|---------|--------------|----------|
| owner   | ✓        | ✓           | ✓                       | ✓       | ✓            | ✓        |
| manager | ✓        | ✗           | ✓                       | ✗       | ✓            | ✓        |
| editor  | ✓        | ✗           | ✗                       | ✗       | ✓            | ✓        |
| viewer  | ✗        | ✗           | ✗                       | ✗       | ✓            | ✓        |

- The owner and managers can invite people. Managers can't promote anyone to manager or owner. Invites are codes or links with a role, an expiry date and a use count.
- **Role changes take effect at the moment the server records them.** A push that reached the server before the change is applied. A push that arrives afterwards and is no longer allowed is rejected (`read_only`). The client shows those changes as "not saved: you can no longer edit this map" and leaves them out of the map.
- **Owner transfer:** when the owner leaves or deletes their account, the manager who has been on the map longest becomes the new owner. A tie is broken at random. If there is no manager, the longest-standing editor takes over. With no members left, the map lapses as usual (§7). The new owner pays from their own balance from then on. The old owner's balance stays on the old account.
- Corp maps have fixed rules for owner and managers (§16).

## 6. Identity and auth

- **EVE SSO, with no scopes.** It only proves which character the user is.
- **Desktop flow:**
  1. The app opens a browser at `https://<server>/login`.
  2. The server runs the SSO flow and holds the client secret.
  3. The server redirects to a loopback port on the client (`http://127.0.0.1:<port>/cb`) with a long-lived session token.
- **Web flow:** the same SSO login, ending in a normal session cookie.
- An account can link several characters (alts), each with its own SSO login.
- The server looks up each character's corporation using the public `/characters/affiliation/` endpoint, which needs no scope. This is needed to count missed payments per corp (§7).
- The token is stored in the system keyring if one is available. Otherwise it goes in `DATA_DIR` with 0600 permissions.
- The client still makes **no ESI calls**. SSO is only used for login.

### Desktop login details (loopback)
After the EVE login in the browser, the browser has to hand the result back to the desktop app. The app listens on `127.0.0.1` and the server redirects the browser there.

**The EVE developer page only ever sees the server's fixed callback** (`https://homebound.luthopian.de/sso/callback`). EVE sends the browser to the server, and the server then sends it on to the app's random local port. The random port never has to be registered with EVE. The dev app uses the fixed `http://localhost:8080/sso/callback` of the dev server.
1. The app picks a **random free port** and a random `state` value, then opens `https://<server>/login?port=…&state=…`.
2. After SSO, the server redirects to `http://127.0.0.1:<port>/cb?code=…&state=…`. The `code` is **a one-time code that expires after 60 s**, not the session token itself.
3. The app checks that `state` matches. This stops another program or website from injecting its own login. It then exchanges the code for the session token over HTTPS. The token never shows up in the browser history or in a URL.
4. **Fallback** if the loopback port is blocked (firewall, antivirus, sandbox): the browser shows a short code, and the user pastes it into the login dialog.

### Sessions
- The server issues its own opaque session tokens: 32 random bytes, stored only as a hash.
- There is **one session per device**, with a device name, the creation time and the last use.
- Lifetime: 90 days, renewed on use. The user has to log in again with SSO at least every 90 days.
- **"Later" button:** when a forced re-login is due (the 90 days are up, or a transfer check, see below), the prompt offers **"Do it later"** next to "Sign in". It pushes the expiry back by **36 hours**, once. After that, signing in is required. This way the mapper keeps working in the middle of an op. For a transfer check, the character stays on its maps during the 36 hours, so a sold character would keep access for at most 36 hours longer.
- Sessions are revoked on logout, when a character is unlinked, and when a character transfer is detected (below). The account dialog lists all devices, each with a "Sign out" button.
- Web view (once it exists): the same tokens in an HttpOnly, Secure, SameSite=Lax cookie that lasts 30 days.

### Accounts and characters
- The first SSO login creates an account. Further characters are linked from the account dialog, each with its own SSO login.
- A character can be unlinked unless it is the account's last one. An account is deleted from the account dialog: it leaves all maps, owned maps transfer (§5), and the balance is forfeited after a confirmation.
- The UI for all of this is still missing. It is listed in `design-system/missing-mockups/README.md` §11.

### Character transfer (sold characters)
A character sold through the character bazaar must not keep its old owner's access.
- EVE SSO returns a `CharacterOwnerHash`, which changes when the character changes owner. The server stores it on every login.
- A character can only be transferred while it is in an NPC corp. The server already polls `/characters/affiliation/`, so it can **flag every character that drops into an NPC corp**.
- While a character is flagged, its sessions require a fresh SSO login. If the owner hash differs from the stored one, the character is removed from its account and its maps, and its sessions are revoked.
- The flag is cleared once the character is in a player corp again and has been verified once.
- The 90-day re-login (Sessions above) catches any transfer that the corp check misses.

## 7. ISK payments

### Receiver
- A dedicated character, **Homebound Billing** (character ID `2124836346`), receives the ISK, so payments are obviously related to the mapper.
- The server admin links that character once, with the scopes `esi-wallet.read_character_wallet.v1`, `esi-mail.read_mail.v1` and `esi-mail.send_mail.v1` (`homebound-server admin receiver-link`).
- **Receipts:** after each counted payment, the receiving character sends the payer an EVE mail with the amount, the account code, the new balance and the paid-until date. A payment that can't be assigned gets a mail too: what went wrong (unknown code, unlinked character, corp without corp maps) and what to do. Corp payments are mailed to the corp's CEO. Mails go through an outbox (`outgoing_mails`), one per journal entry ever, a few per run because EVE throttles mail sending.

### Prices (per 30 days)

Every map includes 3 seats. Each extra seat costs the per-seat rate of the map's tier.

**A seat is one member account**, i.e. one person, whatever role they have. An account's alts don't use extra seats. Viewers count as seats too, otherwise a big group could add everyone as a free viewer.

| tier | base (includes 3 seats) | each seat after 3 |
|---|---|---|
| **Map 1** | 100M ISK | +25M ISK |
| **Map 2** | 50M ISK | +15M ISK |
| **Map 3** | 25M ISK | +5M ISK |
| **Map 4 and later** | free | free* |

\* A cheaper map can't have more seats than the maps above it without paying for them at the higher rate. The server does this by **ranking the payer's maps by seat count, largest first**, and billing them as map 1, map 2, and so on. The cheaper tiers therefore always have the fewest seats.
- Seat 4 or higher on a cheaper map costs whatever tier the map lands in once ranked. The cost can't be dodged by putting the most seats on a free map.
- Ties are ranked by creation date (oldest first). The total is the same either way.
- Ranks are recalculated whenever seats or maps change.

Other rules:
- One map has exactly one home system. Another home system means another map.
- All maps of one payer can be paid by the same character.
- **At most 6 cloud maps per account.** For more, the user sends an EVE mail to the receiving character. The admin is notified (§17) and can raise the limit for that account in the admin CLI. The server reads those mails automatically: the one-time admin link of the receiving character also requests `esi-mail.read_mail.v1`. Each request becomes a webhook event.
- Prices come from the server (`status` and the billing endpoint), so a price change doesn't need a client update.

### Trial
- **A 3-day trial, once per account.** It covers one map with the base 3 seats.
- So that alts can't each start a trial, it is also recorded for every character linked to the account. A character that has ever been part of a trial can't start another one on a new account.
- When the trial ends without a payment, the normal first-miss rules apply (7 editable days, then read-only).

Examples:
- One map with 3 seats is 100M. With 4 seats it is 125M.
- Two maps with 4 and 3 seats: 125M + 50M = **175M**.
- Two maps where the second one has more seats, 4 and 6: the 6-seat map is ranked map 1 (100M + 3 × 25M = 175M) and the 4-seat map is ranked map 2 (50M + 15M = 65M), for a total of **240M**.
- Four maps with 3 seats each: 100M + 50M + 25M + 0 = **175M**.
- A 4th map with 5 seats while maps 1 to 3 have 3 seats each: ranking moves the 5-seat map to map 1, so the total is 100M + 2 × 25M + 50M + 25M + 0 = **225M**.

### Matching payments
- The server polls `GET /characters/{id}/wallet/journal/` within the ESI cache window (about 1 hour). It looks for `ref_type = player_donation`.
- `first_party_id` must be one of the account's linked characters.
- The `reason` field may carry the account code (for example `HB-7Q2K`), so that a donation from an unlinked alt can still be matched. Otherwise the payer's character ID is enough.
- Corp wallet payments (`corporation_account_withdrawal`) and corp balances are covered in §16.

### Unmatched donations
- ISK can't be sent back automatically. A donation that matches no account and no code is therefore **posted to the admin Discord webhook** (§17), so the admin can refund it by hand.
- The message includes how many unmatched donations that character has sent before (in total and in the last 30 days).
- Policy: refunds are at the admin's discretion. A character that sends a lot of unmatched donations gets no refund. If this is abused, the policy may change to "no refunds". The terms (§2) already say "ISK is not refunded, except at the operator's discretion".

### Balance model
- There is **one ISK balance per paying account**, covering all of its maps, because a map's price depends on the others.
- The daily cost is `sum(tier prices) / 30`. `paid_until` is worked out as `balance / daily_cost`.
- Changing seats or maps re-projects `paid_until`. The app shows the new monthly total before the owner confirms the change.
- Every journal `id` is stored, so the same payment is never counted twice.

### When a payment is missed
This applies to all of the account's maps together.
- Strikes go against the **paying** character and that character's corp at the time of the miss. Other linked characters and other members aren't affected.
- **First miss** for that character *and* corp:
  1. The maps stay **editable for 7 days**. A warning shows on every launch and in the web view.
  2. After that they are **read-only**.
  3. The server deletes them **2 months** after `paid_until`.
- **Repeat miss**, meaning that character or that corporation has a strike that is still active:
  1. There are no 7 editable days. The maps are **read-only right away**.
  2. The server still deletes them after 2 months.
- **Strikes expire** after **12 months** of paying on time.
- Paying at any point before deletion restores full access straight away.
- Local copies on clients are never touched.
- Misses are recorded in `payment_strikes(subject_kind[char|corp], subject_id, count, last_at)`.

### Admin CLI
On the server: manual credit, refunds, comp accounts, resetting strikes, listing maps, and force delete.

## 8. Server data model (SQLite)

```
accounts        (id, created_at, map_limit DEFAULT 6, trial_used_at, deleted_at)
characters      (char_id PK, account_id, name, corp_id, corp_checked_at,
                 owner_hash, transfer_flag)                             -- §6 character transfer
sessions        (token_hash PK, account_id, device, created_at, last_used_at, expires_at, revoked_at)
login_codes     (code_hash PK, account_id, state, expires_at)          -- one-time loopback codes, 60 s
payers          (id, kind[account|corp], subject_id)                   -- who a map bills to (§16)
billing         (payer_id PK, code, balance_isk, paid_until,
                 state[trial|active|warning|read_only|deleted])
maps            (id, payer_id, home_system_id, created_at)             -- tier and seats are computed
members         (map_id, account_id, role[owner|manager|editor|viewer], joined_at)
invites         (code PK, map_id, role, expires_at, uses_left, created_by)
entities        (map_id, kind, key, data_json, hlc_json, deleted_hlc)  -- current state + tombstones, kept forever
ops             (map_id, seq, batch_id, account_id, payload, received_at) -- tail, trimmed on a timer
seen_batches    (map_id, batch_id PK)                                  -- dedup of resends, trimmed with the tail
payments        (journal_id PK, char_id, corp_id, ref_type, amount, payer_id, applied_at)
unmatched       (journal_id PK, char_id, amount, reason, seen_at, refunded_at)
trials          (char_id PK, account_id, started_at)                   -- one trial per character ever
payment_strikes (subject_kind[char|corp], subject_id, count, last_at)
admin_events    (id, kind, payload_json, created_at, sent_at)          -- outbox for the Discord webhook (§17)
```

Expected size: KB per map, and a few MB for the whole server. The smallest Hetzner VPS or an Oracle free-tier VM is plenty.

## 9. Notes for the local app now, so sync stays possible

These are **not tasks**. They are notes for whoever builds the local mapper:
- Route every map write through `Store` (`homebound/mapdb/store.py`). Later it will be the single place where ops are emitted.
- Never use `connections.id` as a reference outside the local DB. Use `(a, b)`.
- Keep timestamps in UTC ISO format. `now_iso()` already does this.
- If possible, derive `level_index` and other naming order deterministically from existing data, not from a stored counter.
- One map has one home system. Keep `home_system` tied to the map, not only to the app-wide settings.
- Don't rely on `ON DELETE CASCADE` / `SET NULL` for map data. Store should delete dependants explicitly so it can emit an op for each one.
- Group the writes of one user action (a paste, a delete) into one Store batch.
- Undo should reverse an action (inverse changes), not restore a snapshot.

A check of the code on 2026-11-07 found that all of these still need work: `remove_system` relies on the cascades, `next_level_index` stores a counter, home and scheme live in `settings.maps`, and `Store.restore` restores a snapshot. That is phase 0 in §11.

## 10. Client and web changes (later)

- `homebound/sync/`:
  - `hlc.py`
  - `protocol.py` (shared with the server)
  - `client.py` (a WebSocket worker in a QThread or asyncio)
  - `apply.py` (applies remote ops through `Store` without echoing them back as local ops)
- New local tables:
  - `sync_meta(map_id, server_url, last_seq, client_id)`
  - `field_clock(entity, key, field, hlc)`
  - `sync_outbox(batch_id, payload, created_at)`
  - `map_settings(key, value)`, synced (home system, naming scheme)
  - `layout_override(system_id, x, y)`, local only (§3 "What syncs")
  - `undo_log(action_id, inverse_ops, created_at)`, local only
- Desktop UI, following `design-system/`:
  - a sync status pill (offline, syncing, live, warning, read-only)
  - login
  - a "Share map / invite" dialog (owner only)
  - member and role management
  - billing status ("paid until …", how to pay, the map code and the price for the current seats)
  - a lapse warning on launch
  - opt-in presence toggles for each character
  - account dialog: linked characters, add or unlink an alt, devices and sessions, delete account
  - "Make cloud map" in Settings (§15)
  - All of these are listed as missing mockups in `design-system/missing-mockups/README.md` §11.
- Web view (**deferred**, not part of the first implementation run): static HTML/JS served by the server, using the same design tokens (`design-system/tokens.css`).
  - It renders the chain plus presence and has a responsive phone layout, e.g. a chain list or tree instead of the canvas on small screens.
  - It is read-only.
  - Open question for later: scheme names, layout and system info (class, statics, effects) are computed in Python from the local SDE DB. Either the server sends them already computed, or that logic is ported to JS.

## 11. Phases

0. **Local prerequisites** (no network code): every write goes through Store, with explicit cascades instead of `ON DELETE CASCADE`. Store gets a batch scope. `level_index` is derived. Home and naming scheme move into `map_settings`. Undo uses inverse ops instead of snapshots.
1. HLC, the op model and merge code shared by client and server, including the structural checks (cycles, hole slots) and tombstones. Property tests show that applying ops in any order converges.
2. A server MVP: SSO login (loopback with a one-time code), sessions, a single map, WebSocket relay, snapshot and tail. A dev server and test harness (§18) come with it.
3. The client sync worker, outbox, conflict handling and status UI. "Make cloud map".
4. Groups, invites, roles (with managers) and owner transfer.
5. Auto-prune (connections after 2 days, systems after 4).
6. Wallet polling, pricing, balance, trial, map limit, strikes, unmatched donations, and lapse and delete handling. The Discord webhook (§17).
7. Corp maps (§16).
8. Presence (live character locations).
9. Hardening: rate limits, backups, basic metrics, the admin CLI and the GDPR export (§19).
10. (Deferred) a read-only web view with a phone layout.

## 12. Decided: no end-to-end encryption

The server can read map data. This keeps server-side merge, snapshots, auto-prune and the web view simple.

Protection comes from:
- TLS everywhere
- an encrypted server disk and encrypted backups
- presence (live locations) kept in memory only and never stored
- deleting data on lapse as described in §7

This decision can be revisited if a group asks for it.

## 13. Decided: no free trial

The local app is the free version. Cloud sync is paid from the first day.

## 14. Hosting and backups

**Decided: Hetzner.** The setup spec is in [`hosting-hetzner.md`](hosting-hetzner.md). The Oracle notes below are kept for reference.

### Oracle Cloud Always Free
**Idle reclaim:** Oracle reclaims Always Free VMs that are *idle*, not ones that are switched off. Running 24/7 doesn't help.
- An instance counts as idle when, over 7 days, its 95th-percentile CPU, network and (for A1) memory use all stay under about 20%. Oracle has changed this threshold over time, from 10% to 15% to 20%.
- An idle relay like this would almost certainly fall under it.
- The usual fix is to upgrade the account to Pay As You Go. Always Free resources stay free on PAYG, and idle reclaim doesn't apply there. Set a budget alert at €1 so nothing is billed by surprise.
- The alternative is a "keep busy" load script. That is fragile and works against the "resource light" goal.

**Other caveats:**
- A1 (ARM) capacity is often unavailable in popular regions.
- The free tier can change at Oracle's discretion.

**Storage included:**
- 200 GB of block storage, with 5 volume backups.
- About 20 GB of Object Storage, which is S3-compatible.
- 2 Autonomous Databases. These are Oracle DB, not SQLite, so we won't use them.

### Hetzner
- The smallest VPS costs a few € a month.
- There is no reclaim policy, and it's predictable.
- Backups cost 20% extra on the VPS price. A Storage Box can be added for off-site copies.

### Backup plan (either host)
- Use **Litestream** to stream the server's SQLite continuously to S3-compatible object storage. It restores to any point in time.
- Back up to a **different provider** from the VPS, so it is truly off-site:
  - on Oracle: Hetzner Storage Box, Backblaze B2, or a home machine
  - on Hetzner: OCI Object Storage (free), B2, or a home machine
- Encrypt the backups at rest, e.g. with `age`, or through Litestream's own encryption if it is available then.
- Restore-test the backups monthly.
- Don't back up live presence. It is never stored anyway.

## 15. Local map ↔ cloud map lifecycle

- **Making a cloud map:** a "Make cloud map" button in the map's Settings. The user picks one of their linked characters that has enough balance (or a free trial). The map is uploaded as a snapshot, linked to that character's account, and becomes a cloud map from then on.
- **Joining a map when a local map has the same home:** both are kept. The cloud map becomes a new entry in the map menu, and the local map isn't touched.
- **Leave, kick, lapse or server deletion:** the local copy turns into a **plain local map**. It keeps all its data, is unlinked from the server, and stays editable.
- **Read-only while edits are pending:** see §5 (role changes) and the `read_only` handling in `sync-protocol.md`.
- **Changing the home system:** allowed, by the owner only.
- **Several maps in the app:** the app already keeps several maps, one SQLite file each (`settings.maps`), with one of them active.
  - Cloud maps get a cloud marker in the map menu.
  - **Every linked cloud map stays connected and syncs in the background**, even when it isn't the active one. That costs little: one socket per map, or one socket that multiplexes them all.
  - Jumps are still only tracked on the active map, as now.
  - Presence is sent for each map where the user has turned it on.

## 16. Corp maps

- A corp map is billed to the **corporation**, not to a person. The corp has its own balance and payer code (for example `HBC-4M8T`).
- **Owner:** always the corp's current **CEO**. The CEO is public (`/corporations/{id}/` → `ceo_id`, no scope needed). The server checks it daily, and when the CEO changes, the owner role moves with it.
- **Managers:** the **CEO adds them by hand**. Director roles aren't public in ESI, and an extra scope isn't worth it. When the CEO changes, the managers stay.
- **Members:** invited as usual. Members don't join automatically by corp membership.
- **Optional "corp members may join as viewer" switch:**
  - With the switch on, any character in the corp can join as a viewer without an invite, up to the map's **seat limit**. A manager sets that limit, and it is what the corp pays for.
  - **Invited members come first.** When an invite would push the map over the seat limit, the viewer who joined through the switch most recently loses access to make room. That viewer's local copy becomes a local map (§15).
  - Self-joined viewers never push the map over the seat limit. When it is full, joining fails with "This map is full".
- **Payments:** ISK counts towards the corp's balance, not the CEO's, when either:
  - it comes from the corp wallet (`ref_type = corporation_account_withdrawal`, `first_party_id` = corp ID), or
  - it is a player donation with the corp's code in `reason`, from any character.
  A player donation from a corp member without a code counts towards that person's own balance.
- **Pricing:** the corp is a payer of its own. Its maps are ranked among themselves, separately from its members' personal maps. The map limit (6) applies per corp.
- **Strikes:** a missed payment on a corp map is a corp strike.
- **Leaving the corp:** a member who leaves the corp is removed from its corp maps **straight away**, as soon as the affiliation poll sees it. Managers and the owner are also removed (the CEO always stays owner).

## 17. Admin notifications (Discord webhook)

The server posts to the admin's personal Discord webhook (`HB_DISCORD_WEBHOOK` in `/etc/homebound/env`). Messages go through the `admin_events` table, so a Discord outage doesn't lose them.

| event | contents |
|---|---|
| Map limit request | an EVE mail to the receiving character asking for more maps: sender, account, current maps |
| Trial refused | a character that already had a trial tried to start one on another account: character, both accounts |
| Unmatched donation | character, amount, reason text, how many unmatched donations that character sent before (total / 30 days) |
| Server problem | wallet poller failed or has been silent for more than 2 h, ESI errors, a failed backup or restore test, disk over 80%, crash and restart loops |
| Billing state changes (optional, muted with `HB_MUTE_BILLING=1`) | a map going to warning, read-only or deleted |
| EVE mail not delivered | a receipt or problem mail that failed 5 times: recipient, subject, last error |
| Server started | each start of the server process; 3 starts within 10 minutes turn into a red "Restart loop?" alert |
| Receiving character linked | the admin link was completed |
| Character transferred | a sold character was taken off its old account |

Messages are Discord embeds. Characters and corps are shown by name (from the database, else ESI `/universe/names/`) with an EveWho link, accounts as `a12 (Kira, Kira Alt)`, the form the admin CLI takes. Where there is an admin action, the footer has the command (`admin refunded <journal id>`, `admin map-limit a12 <n>`). The disk is checked every 10 minutes against `HB_DISK_WARN_PERCENT` (default 80), with at most one warning a day while it stays full.

The healthchecks.io checks in `hosting-hetzner.md` §9 stay as a second channel in case the server itself is down. An outbound webhook can't report that.

## 18. Development and testing

- **Dev server:** `uv run homebound-server --dev`, with a local SQLite. It also has a fake SSO mode that logs in a test character without contacting EVE.
- **A second EVE SSO application** with the callback `http://localhost:8080/sso/callback`, for testing against real SSO.
- **Tests:**
  - property tests for merge and convergence (phase 1)
  - an in-process server with several simulated clients: offline, reconnect, conflict and role-change scenarios
  - billing tests on recorded wallet journal fixtures
- **No staging server** at first. The dev server plus the test harness are enough until real groups use it.
- **Packaging:** `homebound_server/` and its dependencies (Starlette, uvicorn) stay out of the PyInstaller client build. The client only bundles `homebound/sync/`.

## 19. Admin, abuse and GDPR

- **Admin CLI** (§7), plus: raise an account's map limit, mark an unmatched donation as refunded, suspend an account, and look up an account by character.
- **Abuse:** suspended accounts lose access without a refund (terms §5). Rate limits are defined in `sync-protocol.md`.
- **GDPR requests:** an export (JSON with the account, characters, memberships, payments and the maps it owns) and a deletion, both available through the admin CLI and later from the account dialog. That covers the "[sobald vorhanden]" line in the datenschutz draft.

## 20. Open questions

- Legal placeholders (name, email, dates; no postal address, by decision). These get filled in at the end.

## 21. Implementation notes (2026-11-08)

Code: `homebound/sync/` (shared protocol, merge, client), `homebound/ui/sync_manager.py` and `homebound/ui/cloud.py` (app), `homebound_server/` (server, `uv run homebound-server --dev`). Tests: `tests/test_sync_merge.py`, `test_sync_client.py`, `test_sync_socket.py`, `test_server.py`, `test_ui_cloud.py`.

Decisions taken while building, where the design was open or left a gap:
- **Sessions** expire 90 days after the SSO login and are not extended by use, so "log in again at least every 90 days" (§6) holds. "Renewed on use" would contradict that, so it was dropped.
- **Merge state:** each entity keeps per-field stamps `<hlc>@<client_id>` plus the newest full-upsert and the newest tombstone stamp. It exists while the first is newer. Ops therefore carry the sender's client ID (`c`) on the wire, and snapshots send the full state with clocks, tombstones included.
- **Rejects:** for `conflict`, `read_only`, `forbidden`, `invalid` and `too_large` the client drops the batch and rejoins for a fresh snapshot, with its other unsent batches replayed on top. `reject.current[]` is still sent, but the client doesn't need it.
- **Level order** is derived from `(added_at, id)`, so when a sibling that was added earlier is removed, the names after it move up (e.g. A2b becomes A2a).
- **Undo** keeps the inverse changes in memory with its toast. There is no `undo_log` table.
- **Owner transfer** falls back to the longest-standing viewer if there is no manager or editor.
- **Corp CEO change:** the previous owner stays on as a manager.
- **Trial:** a money-less trial covers one map with 3 seats. A second map, or a 4th member joining, is refused with `trial_limit` until there is a balance. A balance must cover at least one day at the new price before a paid map is made.
- **Balance** never goes below zero: days without money are not owed. `paid_until` stays at the moment the money ran out.
- **API paths:** account and map deletion are `POST /api/account/delete` (body `{"confirm": "DELETE"}`) and `POST /api/maps/{id}/delete`. Corp settings are `POST /api/maps/{id}/corp` and self-join is `POST /api/maps/{id}/corp-join`.
- **Free maps for listed owners:** `HB_FREE_MAPS` (e.g. `alliance:99000001=1,corp:98000001=2,char:91000001=1`) gives free maps, seats included.
  - A person gets the count of any entry that matches one of their linked characters (its alliance, corp or the character itself).
  - A corp payer gets its own `corp:` entry, and an `alliance:` entry only while it is that alliance's **executor**. The executor's corp map is the "alliance map", and other corps of the alliance pay for their corp maps as usual. So `alliance:X=1` means one free personal map for every member, plus one free corp map for the executor corp.
  - When several entries match, the highest count wins. The free maps are the top-ranked ones, so any further map is billed at its own rank's price: with 2 free maps, a third one costs the map-3 price.
  - A payer whose maps are all free stays `active` without a balance and doesn't use up the trial. The map limit still applies.
  - Alliances come from the hourly affiliation poll (`corporations.alliance_id`). Executors come from `/alliances/{id}/`, polled hourly for the alliances named in the setting (table `alliances`). Leaving the alliance, or losing the executor role, ends the free maps within about an hour, and the normal lapse rules take over.
  - `admin free-maps` lists the configured entries and the payers they cover now.
- **Access lists** (`map_access`): owners and managers can add alliances, corps and characters to a map, by exact name (ESI `/universe/ids/`) or ID. Only accounts with a matching character can accept an invite or use the self-join switch, so a leaked invite code is useless to anyone else. A refused invite is not used up.
  - A corp map always allows its own corp, plus its list. This is how the rest of an alliance gets onto the executor's corp map.
  - A personal map with an empty list stays open to anyone with an invite. Its first entry closes it.
  - Members who stop matching are removed, both when the list changes and by the hourly affiliation poll. The owner always stays. This replaces the old "corp leavers" rule.
  - API: `POST /api/maps/{id}/access` (`{"kind": "alliance|corp|char", "value": "<name or id>"}`) and `DELETE /api/maps/{id}/access/{kind}/{id}`. `GET /api/maps/{id}` returns `access` and `restricted`. In the app, this is the "Who may join" section of the Members dialog.
- **SSO:** the access token's claims are read without checking the JWT signature again, because the token comes straight from CCP's token endpoint in exchange for the client secret. This hasn't been tried against real EVE SSO yet; dev mode and the tests use the fake.

Not done yet:
- the web view (deferred, phase 10)
- Litestream backups and metrics (ops setup, `hosting-hetzner.md`)
- the designed mockups for the sync UI (`design-system/missing-mockups/README.md` §11): the dialogs are functional, built from the existing dialog parts


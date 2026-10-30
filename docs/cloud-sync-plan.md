# Homebound Cloud Sync: design plan

> **STATUS: WIP. This is a design only. Do not implement it yet.**
> Open questions are in §13. Nothing here should change the local-mapper work that is going on now, except the small "keep sync possible" notes in §9.

## 1. Goals and non-goals

**Goals**
- Several people in a group or corp edit the same map live, like Google Docs. A personal map is a group of one.
- Clients are local-first. The app keeps working offline on its local SQLite and merges when the connection comes back.
- The server footprint is tiny: one small VPS, holding only current state plus a short tail of diffs.
- Access costs a monthly ISK payment, checked automatically.
- There is a **read-only web view** that also works on phone browsers, so members can check the map away from their PC or without the app installed. It can't be used to add to or edit the map.

**Non-goals for now**
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
- **Hosting:** a Hetzner VPS (the smallest one is enough) or the Oracle Cloud free tier.
- **Domain:** a custom domain, e.g. `homebound.<tld>` or `homebound.luthopian.de`. Caddy handles Let's Encrypt automatically.

## 3. Sync model

**An operation log plus per-field last-writer-wins (LWW), ordered by a Hybrid Logical Clock (HLC).** This is simpler than OT or a full CRDT and fits row and column data well.

### Entity keys
These are the natural keys already in `homebound/mapdb/schema.sql`. No UUIDs are needed.

| entity     | key                              | notes |
|------------|----------------------------------|-------|
| system     | `solarSystemID`                  | already the PK |
| connection | `(min(a,b), max(a,b))`           | `connections.id` is AUTOINCREMENT and stays local; it never goes over the wire |
| signature  | `(system_id, sig_id)`            | already the PK |
| setting    | `key`                            | for each map: naming scheme, home system |

### Ops
```json
{"entity": "system", "key": 31002238, "fields": {"x": 120.5, "notes": "farm"}, "hlc": "…", "client_id": "…"}
{"entity": "signature", "key": [31002238, "ABC-123"], "delete": true, "hlc": "…", "client_id": "…"}
```

### Merge rules
- For each field, the higher `(hlc, client_id)` wins. The result is the same whatever order ops arrive in, so every replica converges.
- A delete is a tombstone with its own HLC. An upsert with a later HLC brings the entity back. Tombstones are dropped after N days.
- Pasting a sig list produces a **single batched op**, so a paste is applied all at once.

### What the server keeps
- The materialised current rows: field values plus the HLC for each field.
- An op tail, for example the last 7 days or 10k ops per map.
- A client that reconnects inside the tail gets just the missing ops. A client that is older gets a full snapshot. A map is a few hundred rows, so a snapshot is only a few KB.

### Known edge cases
- **Concurrent `notes` edits:** LWW keeps only one of them. At first the client can show "edited by X while you were typing". A text CRDT for notes is a later option.
- **Naming and `level_index`:** two people can add systems at the same depth at the same moment. A stored counter would then give both the same name. Fix: derive the order deterministically, by `added_at` (HLC) and then system ID, instead of storing a counter. See §9.

### Presence (live character locations)
- Presence is ephemeral and **in memory on the server only**. It is never written to disk.
- Sharing is opt-in for each character. The data comes from the client's local chat log watcher, as it does now.
- All roles can see presence, including viewers and the web view.

### Auto-prune
Pruning runs on the server and is emitted as normal delete ops, so every client converges on the same result.
- **Connections:** removed automatically **2 days** after `created_at`. Wormholes don't live longer than that.
- **Systems:** removed automatically **4 days** after `added_at`.
- **Never auto-removed:** the home system and systems marked `pinned`. A system is also kept while it still has a live connection on the path back to home.
- Everything else is only removed by hand.
- Local maps that are not synced can use the same rule as an option in settings.

## 4. Protocol (WebSocket, JSON; msgpack later if size matters)

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

| role   | edit map | invite / manage members | billing | see presence | web view |
|--------|----------|-------------------------|---------|--------------|----------|
| owner  | ✓        | ✓                       | ✓       | ✓            | ✓        |
| editor | ✓        | ✗                       | ✗       | ✓            | ✓        |
| viewer | ✗        | ✗                       | ✗       | ✓            | ✓        |

Only the owner can invite people. Invites are codes or links with a role, an expiry date and a use count.

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

## 7. ISK payments

### Receiver
- A dedicated **character named after the mapper** receives the ISK, so payments are obviously related to it. The name is still to be picked.
- The server admin links that character once, with scope `esi-wallet.read_character_wallet.v1`.

### Prices (per 30 days)

Every map includes 3 seats. Each extra seat costs the per-seat rate of the map's tier.

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

### Balance model
- There is **one ISK balance per paying account**, covering all of its maps, because a map's price depends on the others.
- The daily cost is `sum(tier prices) / 30`. `paid_until` is worked out as `balance / daily_cost`.
- Changing seats or maps re-projects `paid_until`. The app shows the new monthly total before the owner confirms the change.
- Every journal `id` is stored, so the same payment is never counted twice.

### When a payment is missed
This applies to all of the account's maps together.
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
accounts        (id, created_at)
characters      (char_id PK, account_id, name, corp_id, corp_checked_at)
accounts_billing(account_id PK, code, balance_isk, paid_until,
                 state[active|warning|read_only|deleted])
maps            (id, owner_account, home_system_id, seats, created_at)   -- tier is computed, not stored
members         (map_id, account_id, role[owner|editor|viewer])
invites         (code PK, map_id, role, expires_at, uses_left)
entities        (map_id, kind, key, data_json, hlc_json, deleted_hlc)   -- current state only
ops             (map_id, seq, batch_id, payload)                        -- tail, trimmed on a timer
payments        (journal_id PK, char_id, amount, map_id, applied_at)
payment_strikes (subject_kind, subject_id, count, last_at)
```

Expected size: KB per map, and a few MB for the whole server. The smallest Hetzner VPS or an Oracle free-tier VM is plenty.

## 9. Notes for the local app now, so sync stays possible

These are **not tasks**. They are notes for whoever builds the local mapper:
- Route every map write through `Store` (`homebound/mapdb/store.py`). Later it will be the single place where ops are emitted.
- Never use `connections.id` as a reference outside the local DB. Use `(a, b)`.
- Keep timestamps in UTC ISO format. `now_iso()` already does this.
- If possible, derive `level_index` and other naming order deterministically from existing data, not from a stored counter.
- One map has one home system. Keep `home_system` tied to the map, not only to the app-wide settings.

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
- Desktop UI, following `design-system/`:
  - a sync status pill (offline, syncing, live, warning, read-only)
  - login
  - a "Share map / invite" dialog (owner only)
  - member and role management
  - billing status ("paid until …", how to pay, the map code and the price for the current seats)
  - a lapse warning on launch
  - opt-in presence toggles for each character
- Web view: static HTML/JS served by the server, using the same design tokens (`design-system/tokens.css`).
  - It renders the chain plus presence and has a responsive phone layout, e.g. a chain list or tree instead of the canvas on small screens.
  - It is read-only.

## 11. Phases

1. HLC, the op model and merge code shared by client and server. Property tests show that applying ops in any order converges.
2. A server MVP: SSO login, a single map, WebSocket relay, snapshot and tail.
3. The client sync worker, outbox and status UI.
4. Groups, invites (owner only) and roles.
5. Auto-prune (connections after 2 days, systems after 4).
6. Wallet polling, pricing, balance, strikes, and lapse and delete handling.
7. Presence (live character locations).
8. A read-only web view with a phone layout.
9. Hardening: rate limits, backups, basic metrics, and the admin CLI.

## 12. Decided: no end-to-end encryption

The server can read map data. This keeps server-side merge, snapshots, auto-prune and the web view simple.

Protection comes from:
- TLS everywhere
- an encrypted server disk and encrypted backups
- presence (live locations) kept in memory only and never stored
- deleting data on lapse as described in §7

This decision can be revisited if a group asks for it.

## 13. Open questions

- **Free trial:** yes or no, and how long?
- **Mapper character name:** for example "Homebound Mapper" (the name must be free in-game).
- **Hosting:** Hetzner or Oracle free tier? Oracle is free but can reclaim idle VMs, while Hetzner costs about €4/month and is predictable.

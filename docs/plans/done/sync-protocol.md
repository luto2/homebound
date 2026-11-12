# Homebound Sync: protocol spec

> **STATUS: implemented (2026-11-08) in `homebound/sync/` and `homebound_server/`. Differences: ops carry `c` (the sender's client ID), snapshots send each entity's full merged state with clocks, and the delete endpoints are POSTs (see the plan's §21).**
> It belongs with [`cloud-sync-plan.md`](cloud-sync-plan.md), whose §3 has the merge rules and §5 the roles.

## 1. Transport

- **One WebSocket per client**, at `wss://homebound.luthopian.de/ws`. A client with several cloud maps opens them all on this socket (`join` for each map), so it never needs more than one connection.
- Messages are **JSON text frames, one message per frame**. msgpack can be added later as a negotiated option, if size ever matters.
- HTTPS (`/api/…`) handles everything that isn't live: login code exchange, account, invites, billing and health.
- The session token is sent in the first message (`hello`), not in the URL. URLs end up in logs.

## 2. Envelope

Every message has this shape:

```json
{"t": "push", "id": 17, "map": "m_3fk2", ...}
```

| field | meaning |
|---|---|
| `t` | message type (§4) |
| `id` | a client-chosen number for requests. Replies echo it as `re`. Server pushes have no `id`. |
| `map` | the map ID, on every map-scoped message |

The server ignores unknown fields, and so does the client. That lets each side add optional fields without a version bump.

## 3. Versioning

- `hello` carries `protocol: 2` and `app: "0.4.2"`; `welcome` carries the server's `protocol` too. The current version is **2** (since 2026-11-11); version 1 is everything from before the version gate.
- The server supports the current protocol version and the one before it. An older client gets `error{code: "upgrade_required", min_app: "…"}` and the app shows "Please update Homebound to keep syncing". Local editing keeps working.
- A breaking change bumps `protocol`. Adding optional fields to a message doesn't, but adding a field or entity to the **ops** does: `op_from_json` refuses what it doesn't know, so an old server would reject the batch as `invalid` and an old client would fail on it and ask for it again forever.

### The version gate

What a version added to the ops is listed in `protocol.GATED_FIELDS` and `GATED_ENTITIES`, with the version that added it. Each side only sends those to a peer that speaks at least that version:

- **The server** keeps each socket's `hello.protocol` (`protocol.peer_protocol`: a missing or odd value is 1, a higher one than its own is its own). Everything it sends a socket goes through `protocol.message_for_peer`: `ops`, `ops_since` batches, `snapshot` entities and `reject.current` lose the fields and entities that socket doesn't know (from `f` and `clocks`). A batch is never dropped, only emptied, so an old client's `seq` has no gap.
- **The client** takes the server's version from `welcome.protocol` (missing: 1, a server from before the gate). It strips its pushes the same way (`protocol.ops_for_peer`), the outbox resend too; a batch with nothing left is taken out of the outbox instead of being sent (its values stay local). The upload of a new cloud map (`POST /api/maps`) is stripped by the same rule; `GET /api/account` says the server's `protocol` for when the socket isn't up yet.
- **A full upsert from an older sender** doesn't carry the new fields. When it brings an entity (back) to life, the server fills them with their defaults (`GatedField.default`, in `merge.stamp_server_times`), so a deleted system that an old client re-adds doesn't come back with the label it had before. A full upsert of a live entity leaves them alone.
- An op that reaches a server from an older client never has gated parts, so the server always checks ops against the full current schema.

## 4. Messages

### Session

| dir | message | fields | reply |
|---|---|---|---|
| C→S | `hello` | `token`, `protocol`, `app`, `client_id` (a random ID, stable per install) | `welcome{account_id, protocol, characters[], server_time}` or `error` |
| C→S | `ping` | — | `pong{server_time}` |
| S→C | `bye` | `reason` (`session_revoked`, `server_restart`, `upgrade_required`) | the socket then closes |

- `client_id` is the tie-breaker in `(hlc, client_id)`. It must be unique for each install, not each account.
- The client sends a `ping` every 30 s. The server drops a socket that has been silent for 90 s.
- `server_time` lets the client limit its HLC drift. The client warns if its clock is more than 60 s off.

### Map

| dir | message | fields | reply |
|---|---|---|---|
| C→S | `join` | `map`, `last_seq` (0 = none) | `ops_since{map, from_seq, to_seq, batches[]}` or `snapshot{map, seq, entities[], tombstones[]}`, then `status` |
| C→S | `leave` | `map` | — |
| C→S | `push` | `map`, `batch_id`, `ops[]` | `ack` or `reject` |
| S→C | `ack` | `re`, `map`, `batch_id`, `seq`, `stamped{}` | — |
| S→C | `reject` | `re`, `map`, `batch_id`, `code`, `detail`, `current[]` | — |
| S→C | `ops` | `map`, `seq`, `batch_id`, `by` (account ID), `ops[]`, `stamped{}` | — |
| S→C | `status` | `map`, `role`, `state`, `paid_until`, `warning`, `price_month`, `read_only_reason` | — |
| S→C | `members` | `map`, `members[]` | sent on join and on every change |

- **`ack.stamped`** carries the server's times (`added_at`, `created_at`, `lifetime_set_at`) for new entities in the batch. The client writes them over its placeholder values (plan §3, "Server time").
- **`ops`** is the same batch, broadcast to the other members, with the same `stamped` values. The sender never receives its own batch as `ops`. It gets the `ack` instead.
- **Snapshot or tail:** if `last_seq` is still in the op tail, the server sends `ops_since`, otherwise a `snapshot`. A snapshot always includes all tombstones.

### Presence

| dir | message | fields |
|---|---|---|
| C→S | `presence` | `map`, `char_id`, `system_id` (null = offline / stop sharing), `state` (protocol 2, optional) |
| S→C | `presence` | `map`, `char_id`, `name`, `system_id`, `at`, `afk`, `state` (protocol 2) |
| S→C | `presence_all` | `map`, `entries[]` (sent on join) |

- **Protocol 2** adds `state` to both directions (`protocol.GATED_PRESENCE_FIELDS`): `"online"` (the character's client window is open) or `"recent"` (no window list on that computer, but the log was written in the last 30 minutes). The client sends it only to a protocol 2 server and unless the user turned off "Also share whether the game is open" (on by default since 2026-11-12); the server relays `null` for "not said" (an old client, or the switch off) and strips the field for protocol 1 sockets. Any other value counts as `null`.
- A client shares a character while it is playing: its client window is open, or, where windows can't be listed, its log was written in the last 30 minutes. A client that is seen to close sends `system_id: null` at once (before protocol 2 the client kept sharing for 24 hours after the last log write).
- Presence is kept in memory only. After 1 h without an update the character shows as AFK. `system_id: null`, or the client's socket being gone for 10 minutes, drops it (plan §3).
- The S→C `presence` also carries `afk: true|false`.
- The server only accepts presence for characters linked to the sending account.

## 5. Ops

```json
{"e": "system", "k": 31002238, "f": {"notes": "farm", "pinned": 1}, "hlc": "0193a1b2c3d4-0007"}
{"e": "system", "k": 31002238, "placement": {"parent_id": 31000005, "hole_index": 1}, "hlc": "…"}
{"e": "connection", "k": [31000005, 31002238], "f": {"mass": "reduced"}, "hlc": "…"}
{"e": "signature", "k": [31002238, "ABC-123"], "del": true, "hlc": "…"}
{"e": "setting", "k": "naming_scheme", "f": {"value": "letters"}, "hlc": "…"}
```

| field | meaning |
|---|---|
| `e` | entity: `system`, `connection`, `signature`, `setting`, and since protocol 2 `alias` |
| `k` | natural key (plan §3). Connections use `[min, max]`. |
| `f` | changed plain fields only |
| `placement` | the structural compound field (systems only) |
| `full` | `true` on a full upsert (a new entity, or one brought back after a delete): `f` then holds every field |
| `del` | tombstone |
| `hlc` | `<48-bit ms hex>-<16-bit counter hex>`. Every op in a batch may share one HLC. |

- The fields of each entity are `protocol.FIELDS`. **Protocol 2 added** (`protocol.GATED_FIELDS`, `GATED_ENTITIES`):
  - `system.label` (the scheme name stored when the system was attached, plan-stable-hole-names.md), `system.static` (reached through the parent's static, 0/1) and `system.next_hole` (the highest hole number given out in the system). A full upsert from an older client that brings a system back sets them to `null`, `0`, `0`. `next_hole` is a counter: it merges as the **highest** value, not the newest (`protocol.MAX_FIELDS`), and a client never writes a lower one into its map.
  - The `alias` entity (plan-system-alias.md): `{"e": "alias", "k": <solar system ID>, "f": {"name", "set_at"}}`, a remembered custom name that outlives the system's place on the map. The key must be a real solar system; it needn't be on the map. Removed with `del`; auto-prune never touches it.
  - A label never goes away through sync: a remote op whose label is `null` leaves a label the client already has. A system that arrives without a label (placed by an older client) is named by each install itself, and that name stays local (it isn't pushed), so two installs can't rename each other.
- A batch is **atomic**: either all of its ops apply, or the whole batch is rejected.
- One user action is one batch. A sig paste of 30 rows is one batch, and so is a system delete together with its cascades.

- **Backfill (since 2026-11-12):** once per map file, a client sends the server what only it has: labels, the static flag (when the server never had one), a higher `next_hole` and sig numbers the server lacks (`LocalReplica.backfill`). These are plain updates whose `hlc` is just above the stamp of the value they fill, not the clock of now, so they lose to anything the server got meanwhile. Only to a protocol 2 server.

## 6. Server-side checks

The server checks these, in this order, before a batch is applied:

1. **Auth and role:** the session is valid, the account is a member, its role may edit, and the map isn't read-only. Changing `home_system` is owner-only; changing `naming_scheme` needs the owner or a manager (since 2026-11-12; the app disables the scheme controls for the others, `SyncManager.scheme_lock`). → `read_only` / `not_member` / `forbidden`
2. **Limits** (§8). → `too_large` / `rate_limited`
3. **Shape:** known entity and field names, and types and value ranges that match the client schema (mass and lifetime enums, sig ID pattern `^[A-Z]{3}-\d{3}$`, notes up to 4,000 characters). → `invalid`
4. **System IDs are real** solar systems. The server ships the same `universe.sqlite` as the client. It needs that file for prune ("path to home") anyway. → `invalid`
5. **References:** a connection's two systems, and a sig's system, exist or are added in the same batch. Neither may be a tombstone that the batch doesn't re-add with `full`. → `conflict`
6. **Structure:** a `placement` must not create a cycle or take a `(parent_id, hole_index)` slot that another live system holds. → `conflict`
7. **Merge:** plain fields go through LWW (the batch can't fail here, it may just lose some fields). The batch gets the next `seq` and is broadcast.
8. **Hole numbers given twice** (since 2026-11-12, `merge.settle_hole_numbers`): a sig whose new `hole_index` another live sig of that system already has, or a hole of that system (a child placed there that doesn't hang off this sig), gets the system's next free number instead (one past every number seen there, deleted sigs too), and the system's `next_hole` goes up in the same batch. A full upsert of a sig that is live with a number already (pasted again offline) keeps the server's number. So the number stays with the sig whose number reached the server first (`seq` order), and an already synced number wins over an edit made offline. The changed numbers go back in `ack.stamped` (`{"hole_index": n}`) and in the broadcast ops, so every client shows the same. The app warns the sender ("… is hole 2 now: number 1 was given to another sig there first") and moves a system it mapped through that sig to the new number. Numbers a batch only moves between its own sigs (a return-hole swap) are left alone. Older clients take the stamped number without the warning; before the server has this, the duplicate shows with a ⚠ as before.

**`reject.current[]`** holds the server's current state of each entity behind the conflict. The client writes it locally (dropping its own losing ops for those entities), recomputes names and shows a short toast, e.g. "A3 was already mapped by Kira; your version was replaced".

## 7. Errors

| code | where | client behaviour |
|---|---|---|
| `relogin_due` | `hello` | A re-login is due. The app offers "Sign in" or a single "Do it later" (+36 h, `POST /api/sessions/current/defer`). |
| `unauthorized` | `hello` | The session is invalid or expired. The app shows "Sign in again" and keeps working locally. |
| `upgrade_required` | `hello` | "Please update Homebound". The app keeps working locally. |
| `not_member` | `join`, `push` | The app unlinks the map and keeps it as a local map (plan §15). |
| `read_only` | `push` | The batch is dropped from the outbox. The app shows "Not saved: you can no longer edit this map" and reverts those entities to the server state. |
| `forbidden` | `push` | The op needs a higher role (e.g. a home change by a non-owner). Handled the same way as `read_only`. |
| `conflict` | `push` | Apply `current[]` (§6). |
| `invalid` | `push` | A client bug. The batch is dropped, logged, and an error toast is shown. It is never retried. |
| `too_large` | `push` | The app splits the batch if it can, otherwise handles it like `invalid`. |
| `rate_limited` | any | The app waits `retry_after` seconds, then resends. |
| `map_deleted` | `join` | The app unlinks the map and keeps it as a local map. |
| `server_error` | any | The app resends after backoff. |

## 8. Limits

| limit | value |
|---|---|
| message size | 256 KB |
| ops per batch | 500 |
| batches per socket | 20 / s burst, 5 / s sustained |
| presence updates per character | 1 / s |
| logins / login-code exchanges per IP | 10 / min |
| sockets per account | 5 |
| entities per map | 20,000 (far above real use, it only stops abuse) |

## 9. Reconnect and resend

- Reconnect uses exponential backoff with jitter: 1 s, 2 s, 4 s … capped at 60 s, and reset after a minute of being connected.
- After `join`, the client applies `ops_since`/`snapshot` first, then resends its whole outbox in order (plan §3, "Reconnecting with unsent changes").
- **Dedup:** the server keeps every `batch_id` it has applied (`seen_batches`) for as long as the op tail. A resent batch that was already applied gets the original `ack` again and is not applied twice.
- An outbox batch older than the op tail can no longer be deduplicated. It goes through the normal checks again. Thanks to LWW and tombstones, applying it again is harmless.
- **Ordering:** each client's batches are applied in the order it sent them. Between clients, the server's `seq` order holds.

## 10. HTTPS API (outline)

| method | path | purpose |
|---|---|---|
| GET | `/login?port&state` | starts SSO (plan §6) |
| GET | `/sso/callback` | EVE SSO callback |
| POST | `/api/login/exchange` | one-time code → session token |
| POST | `/api/logout` | revoke the current session |
| GET / DELETE | `/api/sessions[/id]` | list or revoke devices |
| POST | `/api/sessions/current/defer` | the one-time "Do it later": +36 h before the re-login |
| GET | `/api/account` | characters, payer code, balance, maps |
| POST / DELETE | `/api/account/characters[/id]` | link (starts SSO) or unlink an alt |
| DELETE | `/api/account` | delete the account (plan §6) |
| GET | `/api/account/export` | GDPR export |
| POST | `/api/maps` | make a cloud map: home, character, starting snapshot |
| POST | `/api/maps/{id}/invites` | create an invite (owner and managers) |
| POST | `/api/invites/{code}/accept` | join a map |
| PATCH / DELETE | `/api/maps/{id}/members/{account}` | change a role, or remove a member |
| GET | `/api/billing/quote?…` | price preview before changing seats or maps |
| GET | `/api/health` | DB OK, poller's last run, version |

The `/api` routes take the session token as `Authorization: Bearer …`.

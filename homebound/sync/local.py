"""The sync side of one local map file: clocks, outbox, and applying what the server sends (plan §3, §10).

Three local-only tables live in the map file next to the map:

* ``sync_meta``: the cloud map ID, server, last seen ``seq`` and the newest HLC.
* ``sync_entities``: the merged state (field values and stamps, tombstones) of every entity, the
  server's plus this install's own unacknowledged ops. The map tables show its visible part.
* ``sync_outbox``: local batches the server hasn't acknowledged, in order.

Remote changes are written through the Store in a ``remote`` batch, so they are not sent back and
can't be undone. Only synced columns are written; local ones (sig ``state``, layout overrides) stay.
"""
import json
import uuid
from typing import Any

from ..mapdb.store import (ENTITY_TABLES, ENTITY_WHERE, MAP_SETTING_KEYS, NAMES_BACKFILL_KEY, Action, Store,
                           key_values, utc_now_iso)
from .hlc import MAX_COUNTER, HybridClock, format_hlc, parse_hlc
from .merge import Effect, EntityState, MapState, apply_op
from .protocol import (FIELDS, LAYOUT_FIELDS, PROTOCOL, Op, full_ops_for_rows, key_from_json, key_from_text, key_text,
                       op_from_json, ops_for_peer, ops_from_action, row_key)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sync_entities (kind TEXT NOT NULL, key TEXT NOT NULL, state TEXT NOT NULL,
                                          PRIMARY KEY (kind, key));
CREATE TABLE IF NOT EXISTS sync_outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT UNIQUE NOT NULL,
                                        payload TEXT NOT NULL, created_at TEXT NOT NULL);
"""



class LocalReplica:
    """Sync bookkeeping of one map file, on top of its open :class:`Store`."""

    def __init__(self, store: Store) -> None:
        """Create the sync tables if needed."""
        self.store = store
        self.database = store.database
        self.database.executescript(SCHEMA)

    # ------------------------------------------------------------ meta
    def meta(self, key: str, default: str | None = None) -> str | None:
        """Return a ``sync_meta`` value."""
        row = self.database.execute("SELECT value FROM sync_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: Any) -> None:
        """Store a ``sync_meta`` value (None removes it)."""
        if value is None:
            self.database.execute("DELETE FROM sync_meta WHERE key=?", (key,))
        else:
            self.database.execute("INSERT OR REPLACE INTO sync_meta (key, value) VALUES (?,?)", (key, str(value)))
        self.store._commit()

    @property
    def map_id(self) -> str | None:
        """The cloud map ID, or None for a local map."""
        return self.meta("map_id")

    @property
    def last_seq(self) -> int:
        """The newest server ``seq`` applied here."""
        return int(self.meta("last_seq", "0"))

    def link(self, map_id: str, server: str, seq: int = 0) -> None:
        """Make this file a cloud map's local copy."""
        self.set_meta("map_id", map_id)
        self.set_meta("server", server)
        self.set_meta("last_seq", seq)
        self.store.layout_local = True

    def unlink(self) -> None:
        """Turn the file back into a plain local map (leave, kick, lapse or server deletion, plan §15).

        The map keeps its data; layout overrides become the positions.
        """
        for row in self.database.execute("SELECT system_id, x, y FROM layout_override").fetchall():
            self.database.execute("UPDATE systems SET x=?, y=? WHERE id=?", (row[1], row[2], row[0]))
        self.database.execute("DELETE FROM layout_override")
        for table in ("sync_meta", "sync_entities", "sync_outbox"):
            self.database.execute(f"DELETE FROM {table}")
        self.store._commit()
        self.store.layout_local = False

    # ------------------------------------------------------------ entity states
    def entity(self, entity: str, key: Any) -> EntityState | None:
        """Return the merged state of an entity, or None if it was never seen."""
        row = self.database.execute("SELECT state FROM sync_entities WHERE kind=? AND key=?",
                                    (entity, key_text(key))).fetchone()
        return EntityState.from_json(json.loads(row[0])) if row else None

    def _save_entity(self, entity: str, key: Any, state: EntityState) -> None:
        """Store an entity's merged state."""
        self.database.execute("INSERT OR REPLACE INTO sync_entities (kind, key, state) VALUES (?,?,?)",
                              (entity, key_text(key), json.dumps(state.to_json())))

    def state(self) -> MapState:
        """Return the whole merged state."""
        return MapState({(row[0], key_from_text(row[1])): EntityState.from_json(json.loads(row[2]))
                         for row in self.database.execute("SELECT kind, key, state FROM sync_entities")})

    def newest_stamp(self) -> str | None:
        """Return the newest HLC this map has seen (to start the clock after a restart)."""
        return self.meta("hlc")

    # ------------------------------------------------------------ local edits
    def record(self, action: Action, clock: HybridClock, client_id: str) -> dict | None:
        """Turn a local action into an outbox batch and merge it into the local clocks.

        Returns:
            The ``push`` message to send, or None if nothing synced changed.
        """
        if action.origin != "local":
            return None
        hlc = clock.tick()
        ops = ops_from_action(action.synced(), hlc, client_id)
        if not ops:
            return None
        for op in ops:
            state, _ = apply_op(self.entity(op.entity, op.key), op)
            self._save_entity(op.entity, op.key, state)
        for op in self._raised_counters(action, hlc, client_id):
            state, _ = apply_op(self.entity(op.entity, op.key), op)
            self._save_entity(op.entity, op.key, state)
            ops.append(op)
        batch_id = f"{client_id}-{uuid.uuid4().hex[:12]}"
        payload = [op.to_json() for op in ops]
        self.database.execute("INSERT INTO sync_outbox (batch_id, payload, created_at) VALUES (?,?,?)",
                              (batch_id, json.dumps(payload), utc_now_iso()))
        self.set_meta("hlc", hlc)
        return {"t": "push", "map": self.map_id, "batch_id": batch_id, "ops": payload}

    def _raised_counters(self, action: Action, hlc: str, client_id: str) -> list[Op]:
        """Return ops for the hole counters an action raised (``Store._raise_hole_counters``).

        The store raises a system's ``next_hole`` after the action's changes are taken, so the
        change of a sig or a placed system doesn't show it. Each system whose counter is now above
        what sync knows gets an op with the new value.
        """
        systems = set()
        for change in action.changes:
            after = change.after or {}
            if change.entity == "signature" and after.get("hole_index"):
                systems.add(after["system_id"])
            elif change.entity == "system" and after.get("parent_id") and after.get("hole_index"):
                systems.add(after["parent_id"])
        ops = []
        for system_id in sorted(systems):
            state, row = self.entity("system", system_id), self.store._row("system", system_id)
            counter = (row or {}).get("next_hole") or 0
            if state is not None and state.exists and counter > (state.fields.get("next_hole") or 0):
                ops.append(Op("system", system_id, {"next_hole": counter}, hlc, client_id))
        return ops

    @property
    def backfill_pending(self) -> bool:
        """Whether names and numbers only this file has still have to go to the server once."""
        return self.store.get_value(NAMES_BACKFILL_KEY) != "done"

    def backfill(self, client_id: str) -> dict | None:
        """Send the server, once, the names and numbers that only this computer has (owner, 2026-11-12).

        Labels from before protocol 2, names this install gave systems an older client placed, the
        static flag and the numbers the upgrade of an old map file filled in for unlinked sigs
        (``naming.freeze_legacy_names``) never left this computer. Each one the merged state lacks
        (no label, no number, a static flag never synced, a lower hole counter) goes into one outbox
        batch, and the file is marked done.

        The ops carry a stamp just above the one of the value they fill, not the clock of now: a
        value someone set on the server meanwhile is newer and wins, so the backfill only fills what
        is empty there, and two installs that backfill the same field end up with the same value.

        Args:
            client_id: This install's client ID.

        Returns:
            The ``push`` message, or None if there was nothing to send.
        """
        ops = []
        for row in self.database.execute("SELECT id, label, static, next_hole FROM systems ORDER BY id").fetchall():
            state = self.entity("system", row["id"])
            if state is None or not state.exists:
                continue
            values = {}
            if row["label"] and not state.fields.get("label"):
                values["label"] = row["label"]
            if row["static"] and "static" not in state.clocks:
                values["static"] = 1
            if (row["next_hole"] or 0) > (state.fields.get("next_hole") or 0):
                values["next_hole"] = row["next_hole"]
            if values:
                ops.append(self._backfill_op("system", row["id"], values, state, client_id))
        for row in self.database.execute("SELECT system_id, signature_id, hole_index FROM signatures "
                                         "WHERE hole_index IS NOT NULL ORDER BY system_id, signature_id").fetchall():
            key = (row["system_id"], row["signature_id"])
            state = self.entity("signature", key)
            if state is not None and state.exists and row["hole_index"] and not state.fields.get("hole_index"):
                ops.append(self._backfill_op("signature", key, {"hole_index": row["hole_index"]}, state, client_id))
        self.store.put_value(NAMES_BACKFILL_KEY, "done")
        if not ops:
            return None
        for op in ops:
            merged, _ = apply_op(self.entity(op.entity, op.key), op)
            self._save_entity(op.entity, op.key, merged)
        batch_id = f"{client_id}-{uuid.uuid4().hex[:12]}"
        payload = [op.to_json() for op in ops]
        self.database.execute("INSERT INTO sync_outbox (batch_id, payload, created_at) VALUES (?,?,?)",
                              (batch_id, json.dumps(payload), utc_now_iso()))
        self.store._commit()
        return {"t": "push", "map": self.map_id, "batch_id": batch_id, "ops": payload}

    @staticmethod
    def _backfill_op(entity: str, key: Any, values: dict, state: EntityState, client_id: str) -> Op:
        """Return a backfill op stamped just above the newest stamp of the fields it fills (or the entity's)."""
        base = max([state.clocks.get(name, "") for name in values] + [state.alive]).split("@", 1)[0]
        milliseconds, counter = parse_hlc(base) if base else (0, 0)
        hlc = format_hlc(milliseconds, counter + 1) if counter < MAX_COUNTER else format_hlc(milliseconds + 1, 0)
        return Op(entity, key, values, hlc, client_id)

    def outbox(self) -> list[dict]:
        """Return the unacknowledged batches as ``push`` messages, oldest first."""
        return [{"t": "push", "map": self.map_id, "batch_id": row[0], "ops": json.loads(row[1])}
                for row in self.database.execute("SELECT batch_id, payload FROM sync_outbox ORDER BY seq")]

    def snapshot_ops(self, clock: HybridClock, client_id: str, protocol: int = PROTOCOL) -> list[dict]:
        """Return full upserts of the whole map, to upload it as a new cloud map (plan §15).

        Args:
            clock: The HLC.
            client_id: This install's client ID.
            protocol: What the server speaks; what it doesn't know is left out (the version gate).
        """
        rows: dict[str, list[dict]] = {}
        for entity, table in ENTITY_TABLES.items():
            rows[entity] = [dict(row) for row in self.database.execute(f"SELECT * FROM {table}")]
        rows["setting"] = [row for row in rows["setting"] if row["key"] in MAP_SETTING_KEYS]
        return ops_for_peer([op.to_json() for op in full_ops_for_rows(rows, clock.tick(), client_id)], protocol)

    def seed(self, ops: list[dict]) -> None:
        """Take the uploaded snapshot into the local clocks (the map was just made from it)."""
        for message in ops:
            op = op_from_json(message)
            state, _ = apply_op(self.entity(op.entity, op.key), op)
            self._save_entity(op.entity, op.key, state)
        self.store._commit()

    # ------------------------------------------------------------ from the server
    def apply_ops(self, messages: list[dict], seq: int | None = None, stamped: dict | None = None) -> Action:
        """Merge a batch from the server and write what changed into the map.

        Args:
            messages: The ops in wire form.
            seq: The batch's ``seq``; stored as ``last_seq``.
            stamped: Server times (already inside the ops; kept for symmetry with ``ack``).

        Returns:
            The remote Store action (what the map shows differently now).
        """
        effects = []
        with self.store.batch(origin="remote") as action:
            for message in messages:
                op = op_from_json(message)
                state, effect = apply_op(self.entity(op.entity, op.key), op)
                self._save_entity(op.entity, op.key, state)
                effects.append((effect, state))
            for effect, state in effects:
                if effect.visible:
                    self._materialise(effect.entity, effect.key, state)
            self._fix_placements()
            if seq is not None:
                self.database.execute("INSERT OR REPLACE INTO sync_meta (key, value) VALUES ('last_seq', ?)", (str(seq),))
            newest = max((message["hlc"] for message in messages), default=None)
            if newest and newest > (self.meta("hlc") or ""):
                self.database.execute("INSERT OR REPLACE INTO sync_meta (key, value) VALUES ('hlc', ?)", (newest,))
        return action

    def apply_snapshot(self, entities: list[dict], seq: int) -> Action:
        """Replace the local clocks with the server's, put the outbox back on top, and make the map match.

        Returns:
            The remote Store action.
        """
        with self.store.batch(origin="remote") as action:
            self.database.execute("DELETE FROM sync_entities")
            for item in entities:
                self._save_entity(item["e"], key_from_json(item["e"], item["k"]), EntityState.from_json(item))
            for batch in self.outbox():
                for message in batch["ops"]:
                    op = op_from_json(message)
                    state, _ = apply_op(self.entity(op.entity, op.key), op)
                    self._save_entity(op.entity, op.key, state)
            self.reconcile()
            self.database.execute("INSERT OR REPLACE INTO sync_meta (key, value) VALUES ('last_seq', ?)", (str(seq),))
        return action

    def acknowledge(self, batch_id: str, seq: int, stamped: dict) -> Action:
        """The server has a batch: drop it from the outbox and take the server's times.

        Returns:
            The remote Store action (the stamped times).
        """
        with self.store.batch(origin="remote") as action:
            self.database.execute("DELETE FROM sync_outbox WHERE batch_id=?", (batch_id,))
            for text, values in (stamped or {}).items():
                entity, key_json = text.split(":", 1)
                key = key_from_text(key_json)
                state = self.entity(entity, key)
                if state is None:
                    continue
                state.fields.update(values)          # same stamp as our op: only the value changes
                self._save_entity(entity, key, state)
                if state.exists:
                    self._materialise(entity, key, state)
            if seq > self.last_seq + 1:
                pass   # other batches in between arrive as ops; last_seq moves with them
            elif seq == self.last_seq + 1:
                self.database.execute("INSERT OR REPLACE INTO sync_meta (key, value) VALUES ('last_seq', ?)", (str(seq),))
        return action

    def renumbered(self, batch_id: str, stamped: dict) -> list[tuple[int, str, int, int]]:
        """Return the sigs of an outbox batch whose bookmark number the server changed (``settle_hole_numbers``).

        Call it before :meth:`acknowledge`, which takes the batch out of the outbox.

        Returns:
            ``(system id, signature id, number sent, number kept)`` for each, in batch order.
        """
        row = self.database.execute("SELECT payload FROM sync_outbox WHERE batch_id=?", (batch_id,)).fetchone()
        if row is None or not stamped:
            return []
        changed = []
        for message in json.loads(row[0]):
            sent = (message.get("f") or {}).get("hole_index")
            if message.get("e") != "signature" or message.get("del") or not sent:
                continue
            kept = stamped.get(f"signature:{key_text(key_from_json('signature', message['k']))}", {}).get("hole_index")
            if kept and kept != sent:
                changed.append((message["k"][0], message["k"][1], sent, kept))
        return changed

    def drop_batch(self, batch_id: str) -> None:
        """Forget a batch the server refused (it comes back right only through a fresh snapshot)."""
        self.database.execute("DELETE FROM sync_outbox WHERE batch_id=?", (batch_id,))
        self.store._commit()

    # ------------------------------------------------------------ writing the map
    def reconcile(self) -> None:
        """Make the map tables show exactly the visible merged state (inside an open batch)."""
        state = self.state()
        visible = {key: entity for key, entity in state.entities.items() if entity.exists}
        for entity, table in (("signature", "signatures"), ("connection", "connections"), ("system", "systems"),
                              ("setting", "map_settings"), ("alias", "aliases"), ("ship", "ships"),
                              ("structure", "structures")):
            for row in [dict(row) for row in self.database.execute(f"SELECT * FROM {table}")]:
                key = row_key(entity, row)
                if entity == "setting" and key not in MAP_SETTING_KEYS:
                    continue
                if (entity, key) not in visible:
                    self._write(entity, key, None)
        order = {"setting": 0, "system": 1, "connection": 2, "signature": 3, "alias": 4, "ship": 5, "structure": 6}
        for (entity, key), entity_state in sorted(visible.items(), key=lambda item: (order[item[0][0]], str(item[0][1]))):
            self._materialise(entity, key, entity_state, placement=False)
        self._fix_placements(force=True)

    def _materialise(self, entity: str, key: Any, state: EntityState, placement: bool = True) -> None:
        """Write one entity's visible state into its map row (or delete the row)."""
        if not state.exists:
            self._write(entity, key, None)
            return
        values = {name: value for name, value in state.fields.items() if name in FIELDS[entity]}
        if entity == "system":
            parent, hole = state.placement
            if not placement or (parent is not None and not self.store.has_system(parent)):
                parent = None if not self.store.has_system(key) else self.store._row("system", key)["parent_id"]
                hole = None if parent is None else hole
            values |= {"parent_id": parent, "hole_index": hole}
        self._write(entity, key, values)

    def _fix_placements(self, force: bool = False) -> None:
        """Give systems the placement of their merged state once their parent is on the map."""
        for (entity, key), state in self.state().entities.items():
            if entity != "system" or not state.exists or not self.store.has_system(key):
                continue
            parent, hole = state.placement
            row = self.store._row("system", key)
            if (row["parent_id"], row["hole_index"]) == (parent, hole):
                continue
            if parent is None or self.store.has_system(parent):
                self.store._update_row("system", key, {"parent_id": parent, "hole_index": hole})

    def _write(self, entity: str, key: Any, values: dict | None) -> None:
        """Upsert or delete a map row with synced values only (inside an open batch)."""
        store = self.store
        current = store._row(entity, key)
        if values is None:
            if current is None:
                return
            store._touch(entity, key)
            if entity == "system":
                for child in store.children(key):
                    store._update_row("system", child.id, {"parent_id": None})   # keeps its number (Store.remove_system)
                self.database.execute("DELETE FROM signatures WHERE system_id=?", (key,))
                self.database.execute("DELETE FROM ships WHERE system_id=?", (key,))   # structures stay
                self.database.execute("DELETE FROM connections WHERE system_a=? OR system_b=?", (key, key))
                self.database.execute("DELETE FROM layout_override WHERE system_id=?", (key,))
            self.database.execute(f"DELETE FROM {ENTITY_TABLES[entity]} WHERE {ENTITY_WHERE[entity]}", key_values(key))
            return
        if entity == "connection" and not (store.has_system(key[0]) and store.has_system(key[1])):
            return
        if entity in ("signature", "ship") and not store.has_system(key[0]):
            return
        if current is not None:
            changes = {name: value for name, value in values.items()
                       if current.get(name) != value and not (entity == "system" and name in LAYOUT_FIELDS)}
            if entity == "system":
                if changes.get("next_hole") is not None and changes["next_hole"] < (current.get("next_hole") or 0):
                    del changes["next_hole"]      # the counter only goes up (Store._raise_hole_counters)
                if "label" in changes and changes["label"] is None and current.get("label"):
                    del changes["label"]          # a name never goes away on its own (an older client's re-add)
            if entity in ("connection", "signature") and changes:
                changes["updated_at"] = utc_now_iso()
            if entity == "setting":
                changes = {"value": values.get("value")} if current.get("value") != values.get("value") else {}
            if entity == "alias" and not changes.get("set_at", True):
                del changes["set_at"]             # the column is required; keep the time we have
            if changes:
                store._update_row(entity, key, changes)
            return
        store._touch(entity, key)
        now = utc_now_iso()
        if entity == "system":
            row = {"id": key, "added_at": now, **values}
        elif entity == "connection":
            row = {"system_a": key[0], "system_b": key[1], "created_at": now, "updated_at": now, **values}
        elif entity == "signature":
            row = {"system_id": key[0], "signature_id": key[1], "created_at": now, "updated_at": now,
                   "seen_at": now, **values}
            row["seen_at"] = row["seen_at"] or now
        elif entity == "alias":
            row = {"system_id": key, "name": values.get("name"), "set_at": values.get("set_at") or now}
        elif entity == "ship":
            row = {"system_id": key[0], "ship_key": key[1], **values, "created_at": values.get("created_at") or now}
        elif entity == "structure":
            row = {"system_id": key[0], "type_name": key[1], "name": key[2], **values,
                   "created_at": values.get("created_at") or now}
        else:
            row = {"key": key, "value": values.get("value")}
        row = {name: value for name, value in row.items() if value is not None or name in ("parent_id", "hole_index")}
        columns = ", ".join(f'"{name}"' for name in row)
        self.database.execute(f"INSERT INTO {ENTITY_TABLES[entity]} ({columns}) VALUES ({','.join('?' * len(row))})",
                              list(row.values()))


def effects_touch_structure(action: Action) -> bool:
    """Return whether a remote action changed systems or connections (the map needs a rebuild)."""
    return any(change.entity in ("system", "connection") for change in action.changes)


__all__ = ["LocalReplica", "Effect", "Op", "effects_touch_structure"]

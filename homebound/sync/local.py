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

from ..mapdb.store import Action, Store, utc_now_iso
from .hlc import HybridClock
from .merge import Effect, EntityState, MapState, apply_op
from .protocol import FIELDS, LAYOUT_FIELDS, Op, full_ops_for_rows, key_from_json, key_to_json, op_from_json, ops_from_action

SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sync_entities (kind TEXT NOT NULL, key TEXT NOT NULL, state TEXT NOT NULL,
                                          PRIMARY KEY (kind, key));
CREATE TABLE IF NOT EXISTS sync_outbox (seq INTEGER PRIMARY KEY AUTOINCREMENT, batch_id TEXT UNIQUE NOT NULL,
                                        payload TEXT NOT NULL, created_at TEXT NOT NULL);
"""
TABLES = {"system": "systems", "connection": "connections", "signature": "signatures", "setting": "map_settings"}


def _key_text(key: Any) -> str:
    """Return the stored form of an entity key."""
    return json.dumps(key_to_json("", key))


def _key_value(text: str) -> Any:
    """Return an entity key from its stored form."""
    value = json.loads(text)
    return tuple(value) if isinstance(value, list) else value


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
                                    (entity, _key_text(key))).fetchone()
        return EntityState.from_json(json.loads(row[0])) if row else None

    def _save_entity(self, entity: str, key: Any, state: EntityState) -> None:
        """Store an entity's merged state."""
        self.database.execute("INSERT OR REPLACE INTO sync_entities (kind, key, state) VALUES (?,?,?)",
                              (entity, _key_text(key), json.dumps(state.to_json())))

    def state(self) -> MapState:
        """Return the whole merged state."""
        return MapState({(row[0], _key_value(row[1])): EntityState.from_json(json.loads(row[2]))
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
        ops = ops_from_action(action, hlc, client_id)
        if not ops:
            return None
        for op in ops:
            state, _ = apply_op(self.entity(op.entity, op.key), op)
            self._save_entity(op.entity, op.key, state)
        batch_id = f"{client_id}-{uuid.uuid4().hex[:12]}"
        payload = [op.to_json() for op in ops]
        self.database.execute("INSERT INTO sync_outbox (batch_id, payload, created_at) VALUES (?,?,?)",
                              (batch_id, json.dumps(payload), utc_now_iso()))
        self.set_meta("hlc", hlc)
        return {"t": "push", "map": self.map_id, "batch_id": batch_id, "ops": payload}

    def outbox(self) -> list[dict]:
        """Return the unacknowledged batches as ``push`` messages, oldest first."""
        return [{"t": "push", "map": self.map_id, "batch_id": row[0], "ops": json.loads(row[1])}
                for row in self.database.execute("SELECT batch_id, payload FROM sync_outbox ORDER BY seq")]

    def snapshot_ops(self, clock: HybridClock, client_id: str) -> list[dict]:
        """Return full upserts of the whole map, to upload it as a new cloud map (plan §15)."""
        rows: dict[str, list[dict]] = {}
        for entity, table in TABLES.items():
            rows[entity] = [dict(row) for row in self.database.execute(f"SELECT * FROM {table}")]
        rows["setting"] = [row for row in rows["setting"] if row["key"] in ("home_system", "naming_scheme")]
        return [op.to_json() for op in full_ops_for_rows(rows, clock.tick(), client_id)]

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
                key = _key_value(key_json)
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
                              ("setting", "map_settings")):
            for row in [dict(row) for row in self.database.execute(f"SELECT * FROM {table}")]:
                key = self._row_key(entity, row)
                if entity == "setting" and key not in ("home_system", "naming_scheme"):
                    continue
                if (entity, key) not in visible:
                    self._write(entity, key, None)
        order = {"setting": 0, "system": 1, "connection": 2, "signature": 3}
        for (entity, key), entity_state in sorted(visible.items(), key=lambda item: (order[item[0][0]], str(item[0][1]))):
            self._materialise(entity, key, entity_state, placement=False)
        self._fix_placements(force=True)

    @staticmethod
    def _row_key(entity: str, row: dict) -> Any:
        """Return the natural key of a map row."""
        if entity == "system":
            return row["id"]
        if entity == "connection":
            return (row["system_a"], row["system_b"])
        if entity == "signature":
            return (row["system_id"], row["signature_id"])
        return row["key"]

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
                    store._update_row("system", child.id, {"parent_id": None, "hole_index": None})
                self.database.execute("DELETE FROM signatures WHERE system_id=?", (key,))
                self.database.execute("DELETE FROM connections WHERE system_a=? OR system_b=?", (key, key))
                self.database.execute("DELETE FROM layout_override WHERE system_id=?", (key,))
            where = {"system": "id=?", "connection": "system_a=? AND system_b=?",
                     "signature": "system_id=? AND signature_id=?", "setting": "key=?"}[entity]
            self.database.execute(f"DELETE FROM {TABLES[entity]} WHERE {where}", list(key) if isinstance(key, tuple) else [key])
            return
        if entity == "connection" and not (store.has_system(key[0]) and store.has_system(key[1])):
            return
        if entity == "signature" and not store.has_system(key[0]):
            return
        if current is not None:
            changes = {name: value for name, value in values.items()
                       if current.get(name) != value and not (entity == "system" and name in LAYOUT_FIELDS)}
            if entity in ("connection", "signature") and changes:
                changes["updated_at"] = utc_now_iso()
            if entity == "setting":
                changes = {"value": values.get("value")} if current.get("value") != values.get("value") else {}
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
        else:
            row = {"key": key, "value": values.get("value")}
        row = {name: value for name, value in row.items() if value is not None or name in ("parent_id", "hole_index")}
        columns = ", ".join(f'"{name}"' for name in row)
        self.database.execute(f"INSERT INTO {TABLES[entity]} ({columns}) VALUES ({','.join('?' * len(row))})",
                              list(row.values()))


def effects_touch_structure(action: Action) -> bool:
    """Return whether a remote action changed systems or connections (the map needs a rebuild)."""
    return any(change.entity in ("system", "connection") for change in action.changes)


__all__ = ["LocalReplica", "Effect", "Op", "effects_touch_structure"]

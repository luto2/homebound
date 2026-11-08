"""Auto-prune on the server, emitted as normal delete ops (plan §3 "Auto-prune")."""
from datetime import datetime, timedelta

from homebound.sync.merge import MapState
from homebound.sync.protocol import Op

CONNECTION_DAYS = 2
SYSTEM_DAYS = 4


def _older(text: str | None, limit: datetime) -> bool:
    """Return whether an ISO time is before ``limit`` (a missing time counts as old)."""
    if not text:
        return True
    return datetime.fromisoformat(text) < limit


def prune_ops(state: MapState, home_id: int | None, now: datetime, hlc: str, client_id: str) -> list[Op]:
    """Return the deletes that auto-prune makes on a map now.

    Connections go 2 days after ``created_at``. Systems go 4 days after ``added_at``, except home,
    pinned systems and systems still linked to home by live connections. A pruned system takes its
    connections and signatures with it and its children lose their placement, as a client delete does.

    Args:
        state: The map's merged state.
        home_id: The home system, or None.
        now: The current time (UTC).
        hlc: The server's HLC value for these ops.
        client_id: The server's client ID.
    """
    systems = state.live_systems()
    connections = {key: entity for (kind, key), entity in state.entities.items()
                   if kind == "connection" and entity.exists}
    dead_connections = {key for key, entity in connections.items()
                        if _older(entity.fields.get("created_at"), now - timedelta(days=CONNECTION_DAYS))}
    neighbours: dict[int, set[int]] = {}
    for a, b in set(connections) - dead_connections:
        neighbours.setdefault(a, set()).add(b)
        neighbours.setdefault(b, set()).add(a)
    reachable, frontier = set(), [home_id] if home_id in systems else []
    while frontier:
        current = frontier.pop()
        if current in reachable:
            continue
        reachable.add(current)
        frontier.extend(neighbours.get(current, ()))
    dead_systems = {system_id for system_id, entity in systems.items()
                    if system_id != home_id and not entity.fields.get("pinned") and system_id not in reachable
                    and _older(entity.fields.get("added_at"), now - timedelta(days=SYSTEM_DAYS))}
    for a, b in connections:
        if a in dead_systems or b in dead_systems:
            dead_connections.add((a, b))
    ops = [Op("connection", key, {}, hlc, client_id, delete=True) for key in sorted(dead_connections)]
    for (kind, key), entity in sorted(state.entities.items(), key=lambda item: str(item[0])):
        if kind == "signature" and entity.exists and key[0] in dead_systems:
            ops.append(Op("signature", key, {}, hlc, client_id, delete=True))
    for system_id, entity in sorted(systems.items()):
        if system_id not in dead_systems and entity.placement[0] in dead_systems:
            ops.append(Op("system", system_id, {}, hlc, client_id, (None, None), True))
    ops += [Op("system", system_id, {}, hlc, client_id, delete=True) for system_id in sorted(dead_systems)]
    return ops

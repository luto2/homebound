"""Wormhole lifetime states, shared by the map store, the routes and the painting helpers."""
from datetime import timedelta

# Wormhole lifetime state, stored in the `lifetime` column of connections and signatures.
# lifetime_set_at is when the current state was set; the hole is gone LIFETIME_REMAINING after that.
LIFETIME_STABLE, LIFETIME_4H, LIFETIME_1H, LIFETIME_EXPIRED = 0, 1, 2, 3
LIFETIME_STATES = (LIFETIME_STABLE, LIFETIME_4H, LIFETIME_1H, LIFETIME_EXPIRED)
LIFETIME_LABELS = {LIFETIME_STABLE: "Stable", LIFETIME_4H: "< 4h", LIFETIME_1H: "< 1h", LIFETIME_EXPIRED: "Expired"}
# (chip text, colour token) of the lifetime states that show a chip
LIFETIME_CHIPS = {LIFETIME_4H: ("◷ <4h", "edge-eol"), LIFETIME_1H: ("◷ <1h", "edge-eol"),
                  LIFETIME_EXPIRED: ("✕ EXP", "danger")}
LIFETIME_REMAINING = {LIFETIME_4H: timedelta(hours=4), LIFETIME_1H: timedelta(hours=1),
                      LIFETIME_EXPIRED: timedelta(minutes=10)}  # expired: keep it visible for a few minutes

# OverlayStates

The overlay in its four states, each over a different game backdrop to check legibility: dark space, a white-to-orange explosion edge, a bright busy UI panel.

1. **Normal** (click-through): header shows the followed character (marker + name) and the mode. The chain is a tree (box-drawing prefixes in mono, max 4 levels), with the current row highlighted.
2. **K-space distance**: the character has left the chain through k-space; their row appears under the entrance system as "Perimeter HS 1.0 · **3j** from entrance" and is the current row. Rows outside the active branch can collapse.
3. **Unlocked**: 2px dashed `overlay-unlocked` frame, an amber "UNLOCKED" chip in the header, grip dots on top, background raised to `op-overlay-unlocked`, and a footer hint for locking. Mouse events are accepted only in this state.
4. **Empty / hidden**: "Not in a mapped chain" with the last known system; with no client focused the overlay is hidden, except in *Pinned · always shown*, where the message says why.

**Legibility recipe** (all states): fill `overlay-bg` at `op-overlay-bg` (78%, slider 55–92%), 1px `overlay-frame`, every text run haloed with 2px `overlay-halo`, weight ≥ 600, text ≥ 11px, rows `size-overlay-row`. Width `size-overlay-w` (320; 280–360 allowed); about 15 rows fit in 330px.

Qt: `QWidget` with `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool`, `WA_TranslucentBackground`, `WA_ShowWithoutActivating`; click-through via `Qt.WindowTransparentForInput` (toggle the flag and re-`show()` when unlocking). On Plasma Wayland, positioning is handled by the KWin script; on X11, `move()` works.

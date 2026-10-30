# OverlayRow

One 20px line in the overlay: name · class · J-name · effect · kills 1h/24h · owner.

- Base `overlay-row` (12px/600), J-name and kills `overlay-mono` (11px). Nothing in the overlay is smaller than 11px.
- **Legibility over any background**: text is `overlay-ink` with a 2px `overlay-halo` outline (85% black) drawn *under* the glyph fill, on a fill of `overlay-bg` at 78%. Together these keep about 4.5:1 or better against white flashes and against black space; the preview puts the same rows over an explosion.
- **Current system**: `overlay-current` fill, ▶ caret in `accent`, weight 800. Never colour alone.
- **K-space row**: replaces J-name + effect with the distance "(Jita) 3j from A" — the jump count in `accent`, mono.
- **Recent kill**: the `zk-recent` diamond leads the kills column.
- Badges and tags inside a row keep their solid fills and get no halo.

Qt (QPainter in `paintEvent` of a frameless `QWidget`): build a `QPainterPath.addText()` per run, `strokePath(path, QPen(halo, 2, cap=RoundCap, join=RoundJoin))`, then `fillPath(path, ink)`. Cache the paths per row; rebuild only when the text changes.

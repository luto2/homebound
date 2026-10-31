# SiteIcon

One glyph per signature group, used in the SignatureTable group headers, the SigEditor group combo and anywhere a site type is named. Same grid and stroke as EffectIcon: 16×16, 1.4px main strokes, 1.1px detail strokes, round caps and joins.

| Group | Glyph | Ink |
|---|---|---|
| Wormhole | tunnel: ring with an off-centre inner ring and a dot | `accent` |
| Combat site | two crossed blades with guards | `danger` |
| Data site | chip: square with a core and pins on four sides | `ink-muted` |
| Relic site | broken arch on a base line, a fallen fragment | `warning` |
| Gas site | cloud outline with one inner wisp | `success` |
| Ore site | faceted rock | `warning` |
| Ice site | six-armed crystal with small barbs | `accent` |
| Unscanned | dashed rounded square with a ? | `ink-faint` |

- **The shape identifies the group, the colour only supports it.** Relic and Ore share `warning`, Wormhole and Ice share `accent`; their outlines are different (arch vs rock, ring vs star). The greyscale row in the preview is the test.
- No glyph reuses an effect silhouette: Wolf-Rayet is the hexagon and "no effect" is the dashed circle, so Ice has no hexagon and Unscanned is a dashed *square*.
- **Anomalies use the same glyph** as the signature group of the same kind. The group header says `Anomalies ·` in `ink-muted` before the title; a second glyph set would double what players need to learn.
- Sizes: 14px (`size-icon`) in tables, headers and combos; 12px is the floor. Always paired with the group word; the name is also the tooltip and accessible name.
- Files: the *Sites* asset group, `site-<group>.svg`, single ink `#e6edf2`. The path data is also in `components/bundle.js` → `SITE`.

Qt: load each SVG once with `QSvgRenderer`, render to a `QPixmap` at `14 × devicePixelRatio`, then tint with `QPainter.CompositionMode_SourceIn` filled with `color(token)`. Cache by `(group, token, dpr)` in `QPixmapCache`. In `sig_table.py` this replaces the `BULLET` shapes: draw the pixmap where `_bullet` draws today and keep the same token map.

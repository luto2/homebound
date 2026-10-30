# EffectIcon

One geometric glyph per wormhole effect, drawn on a 16×16 grid with 1.3–1.5px strokes so each one has a different silhouette.

| Effect | Glyph | Ink |
|---|---|---|
| Pulsar | burst: dot + 8 rays | `effect-pulsar` |
| Black Hole | filled disc in a tilted ring | `effect-blackhole` |
| Cataclysmic Variable | large disc + small ring, joined | `effect-cataclysmic` |
| Magnetar | dot with crossed field loops | `effect-magnetar` |
| Red Giant | circle, lower half filled | `effect-redgiant` |
| Wolf-Rayet | hexagon with 3 spokes | `effect-wolfrayet` |
| none | dashed empty circle (or omit) | `effect-none` |

- 14px (`size-icon`) in nodes and tables, 13px in the overlay, never under 12px.
- In nodes and the overlay the icon stands alone and shows the full name as a tooltip; in the details panel it's always followed by the effect name.
- The SVGs in the *Effects* asset group use `ink`. Recolour them in Qt with `QPainter.CompositionMode_SourceIn`, or build the `QPainterPath` from the same path data (see `components/bundle.js` → `FX`).

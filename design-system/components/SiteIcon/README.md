# SiteIcon

One icon per signature group, used in the SignatureTable group headers, the SigEditor group combo and anywhere a site type is named. The icons are **EVE Online's own in-space bracket icons**, so they match what players see in the client (and in Wanderer).

| Group | Icon (IEC `Icons/items/Brackets/`) | Tint token |
|---|---|---|
| Wormhole | `wormhole.png` | `accent` |
| Combat site | `combatSite_16.png` | `danger` |
| Data site | `data_Site_16.png` | `ink-muted` |
| Relic site | `relic_Site_16.png` | `warning` |
| Gas site | `harvestableCloud.png` | `success` |
| Ore site | `ore_Site_16.png` | `warning` |
| Ice site | `iceField.png` | `accent` |
| Unscanned | Homebound's own dashed ? glyph (no EVE counterpart) | `ink-faint` |

- **Source:** The official EVE Online Image Export Collection, Uprising V21.03 (`Uprising_V21.03_Icons.zip`). EVE Online and its images are the intellectual property of Fenris Creations (formerly CCP Games); Homebound is free, non-commercial and not affiliated with Fenris Creations. See `homebound/theme/sites/NOTICE.md`.
- **Tinting:** the originals are white with a dark drop shadow. Tint by **brightness**: each pixel becomes the token colour with alpha = original alpha × lightness, so the shadow drops out on dark rows and the shape stays crisp. A plain `SourceIn` fill would turn the shadow into a thick outline.
- **Size:** native 16×16. Draw at 16px (pixel-exact at 1×); at fractional or 2× scaling use smooth scaling. Don't go below 16.
- The shapes come from the game, so colour is still a support signal: Relic and Ore share `warning`, Wormhole and Ice share `accent`, and their EVE silhouettes differ.
- **Anomalies** use the same icon as the signature group of the same kind; the header says `Anomalies ·`.

Qt: `painting.site_pixmap(group, size, device_pixel_ratio)` builds and caches the tinted pixmap; `painting.draw_site(painter, x, y, group)` draws it. Used by `signature_table.py` (group column and header rows) and the SigEditor group combo (`QIcon`).

Preview note: the browser previews tint with a CSS luminance mask, the same rule as the app.

Homebound is a ship instrument for wormhole chains: dark, dense, quiet until something matters. It runs next to or on top of a busy sci-fi game client in a dark room, so every rule below serves two goals: **read it in one glance** and **act on it in one click**. Decoration that doesn't carry information doesn't ship.

Everything here is buildable with Qt Widgets, QSS and `QGraphicsView`/`QPainter` (PySide6, Qt 6, KDE Plasma). No blur, no web fonts, no CSS-only effects. The look is original; don't borrow the game's UI, logos or art.

## Principles

1. **Information first.** The custom name, class and mass state are the three things read most; give them the strongest type and colour. Everything else is muted.
2. **Never colour alone.** Every coloured meaning also has a label, glyph, shape, line width or dash pattern. A greyscale screenshot must still say everything.
3. **One click, or zero.** Preselect the best guess, show the shortcut, make Enter do the common thing.
4. **Quiet until it matters.** Neutrals do most of the work; saturated colour is reserved for classes, warnings and the current system.
5. **Same signal everywhere.** A C5 badge, a critical connection or a recent kill looks the same on the map, in the table, in the overlay and in the tray.

## Content fundamentals

- **Voice**: terse, operational, second person only when asking ("Which sig did you take?"). No exclamation marks, no jokes in the UI.
- **Casing**: sentence case for buttons and titles ("Paste sigs", "Delete missing"); UPPERCASE only for `ui-label` captions, `overlay-header` and chips (CRIT, EOL, HOME).
- **Game terms stay as players say them**: sig, static, K162, EOL, crit, frig hole, J-name, k-space, chain, roll. Don't spell out "End of Life" in tight spaces; do in tooltips.
- **Numbers**: mono, tabular. Ages as `1h 12m`, `34m`, `1d 2h`; jumps as `3j`; kills as `1h/24h` → `3/41`; mass as `<50%`, `<10%`.
- **Names**: custom name first, real name second (`A2a` then `J120617`; `8 (Jita)` then `Sirseshin`).
- **Errors say what was expected**: "Folder not found. Expected …/logs/Chatlogs containing Local_*.txt".
- No emoji. Glyphs used: ✓ ✕ ▲ ! ? ◷ ⌂ ▶ ⧉ → ● ○.

## Colour

Dark only for now (`dark` theme); a light theme is optional and low priority.

- **Neutrals**: canvas `bg-0`, window `bg-1`, panels/dialogs/nodes `bg-2`, inputs/headers `bg-3`, hover `bg-4`. Dividers `line-1`; control borders `line-2` (3:1). Text `ink` → `ink-muted` → `ink-faint`; `ink-disabled` only for disabled controls. Every bright fill (class badges, owner tags, markers, primary buttons) takes `ink-inverse` text.
- **Accent** (`accent`, ice blue) means *you are here / you chose this*: selection, focus, primary action, current overlay row. Nothing else.
- **Status**: `success` teal ✓, `warning` amber ▲, `danger` vermillion ✕/!. Success is teal so it sits off the red–green axis and stays distinct from danger for deuteranopes; each always carries its glyph. `*-dim` variants are 15% fills behind `ink`. Stale data uses `stale` hatching plus a label: a `SCAN 26h` tab on map nodes, "stale 14m" elsewhere.
- **Space classes** (13): `class-c1` … `class-c6` run cool to hot (sky → blue → teal → olive → orange → red) so difficulty reads as temperature; Thera violet, C13 silver with a notch, Drifter pink, Pochven bronze with a cut corner. **W-space badges are filled, k-space (`class-hs/ls/ns`) are outlined** and carry the security number. Labels are always printed.
- **Effects** (7): each has its own glyph *and* an `effect-*` ink; the glyph is what identifies it.
- **Connections**: `edge-fresh` neutral, `edge-reduced` amber, `edge-critical` vermillion, `edge-eol` violet casing, `edge-frig` teal, `edge-unknown` grey. Width and dash do the primary work (see ConnectionEdge).
- **zKill**: bar count (0–3) is the signal; `zk-low/med/high` back it up; `zk-recent` yellow diamond means a kill in the last 10 minutes.
- **Owners**: `owner-1` … `owner-10`, user-assigned, always with the ticker. **Characters**: `char-1` … `char-6`, each slot bound to a fixed shape (circle, square, triangle, diamond, hexagon, star).

## Typography

Fonts are system fonts, never downloaded at runtime:

- **Sans**: Noto Sans (Plasma's default UI font; SIL Open Font License 1.1) → Cantarell → DejaVu Sans.
- **Mono**: Hack (Plasma's default fixed font; MIT + Bitstream Vera licence) → Noto Sans Mono (OFL 1.1) → DejaVu Sans Mono.
- If the app ships a font for consistency across distros, ship **Noto Sans Mono** (OFL 1.1 allows bundling; include `OFL.txt`) and register it with `QFontDatabase.addApplicationFont`.

Use `mono-sig` for signature IDs, `mono-data`/`mono-small` for J-names, WH types, percentages, ages and kill counts (enable tabular figures: `QFont.setFeature("tnum", 1)` in Qt ≥ 6.7, or rely on the mono face). Use `ui-body` for widget text, `ui-small` in tables, `ui-label` for uppercase captions, `ui-title` for panel and dialog titles.

**Map zoom**: `node-name` is 14px at 100%. Detail drops at 60% (compact node) and 35% (name only, fixed 13px on screen). No map text renders below 9px on screen; hide it instead.

**Overlay**: `overlay-row` 12px/600 base, `overlay-mono` 11px, `overlay-header` 11px/700 caps. The 11px floor is absolute; the user may scale up to 14px.

## Spacing, radii, borders

- 2-based steps `sp-2` … `sp-32`; default gap `sp-8`, panel padding `sp-12`, dialog padding `sp-16`, map grid cell = node height / 3 (≈ 20.7px, see MapWindow).
- Square panels and tables (`radius-0`); `radius-1` badges, `radius-2` controls, `radius-3` nodes/overlay/toasts, `radius-4` dialogs/menus. Nothing is pill-shaped.
- Borders over shadows. Shadows only lift floating things: `shadow-node`, `shadow-toast`, `shadow-dialog`.
- Fixed metrics live in the `size` family: controls 26px, rows 22px, toolbar 36px, nodes 168×62, docks 220/440, overlay 320 wide with 20px rows.

## States

| State | Treatment |
|---|---|
| Hover | fill to `bg-4` |
| Selected | `accent-dim` fill + 2px `accent` edge/outline |
| Focus | 2px `accent` ring, 1px outside |
| Warning | `warning` border/text + ▲, `warning-dim` fill |
| Danger | `danger` border/text + ✕ or !, `danger-dim` fill |
| Success | `success` + ✓ |
| Disabled | `ink-disabled` text, `line-1` border, `bg-2` fill, icons at `op-disabled` |
| Stale (rescan due) | 45° hatch, content at `op-stale`, grey `SCAN 26h` tab on the top edge after `▲ UNCONFIRMED`; a never-scanned w-space node has only a `NO SCAN` tab, no hatch |

Focus ring and all meaningful marks meet 3:1 on every surface; all text tokens meet 4.5:1 on the grounds their notes name.

## The overlay

A frameless, always-on-top, click-through window over the game, showing one character's chain as a tree (see OverlayRow and OverlayStates). The legibility recipe is fixed: `overlay-bg` at 78% (55–92% allowed), a 1px `overlay-frame`, 2px `overlay-halo` outline under every glyph, weight ≥ 600, nothing below 11px. Apply opacity to the fill, never `setWindowOpacity`, so text stays solid.

## Iconography

- **Site icons**: EVE Online's own bracket icons for each signature group (EVE Online Image Export Collection), tinted to the group token; only Unscanned uses our own glyph. See SiteIcon.
- **Effect glyphs**: the *Effects* asset group, 16×16 SVG, single ink `#e6edf2` (`ink`). Their path data also lives in `components/bundle.js` (`FX`) for building `QPainterPath`s.
- **UI glyphs** are Unicode from the sans face (✓ ✕ ▲ ⌂ ⧉ ↗ ⚙ ◷); if a glyph is missing in the fallback font, swap it for a Breeze icon from the system theme via `QIcon.fromTheme` (e.g. `edit-copy`, `configure`, `go-home`).
- No app logo exists yet. The toolbar and cover use the wordmark "HOMEBOUND" in plain type next to a two-node glyph built from the edge and node styles; replace it when a mark is designed.

## Screens

Mockups are in the component list under *Screens*: **MapWindow** (a 12-system chain with every node, edge and sig state), **OverlayStates** (normal, k-space distance, unlocked, empty), **NamingDialog** and **Settings**. The other dialogs are in **Dialog** (sig picker, connection editor), **SigEditor** (signature editor, all states), **PromptDialog** (new/rename map, rename/add system), **ConfirmDialog** (delete map, plus the Undo toast for other destructive actions) and **StartupError**. The unknown-jump toast is in **Toast**; menus are in **TrayMenu**, **ContextMenu** (node, edge, background, character row) and **MapMenu** (map switcher).

Implementation notes for QSS and QPainter are in the *Qt implementation* section.

# Plan: paint tests that can see a paint bug

Status: implemented (2026-11-10, commits `9e72e56`, `a7980f7`). Deviations: edge tests assert at least 4 of 8 samples; the no-route row is compared with the routed paint (the text covers the base pixel); the old `render()` helper in `tests/test_ui_routes.py` and the other open decisions are left as they were.

Depends on: nothing. Step 1 (docstrings) and step 2 (pixel checks) are separate commits.

Source: `TODO.md` item 8 and `docs/findings/findings.md` T1. `plan-test-fixes.md` ("Not changing") accepts
that many paint tests only assert `not image.isNull()`, on the condition that their docstrings say no more
than "doesn't raise". Six of them say more.

## Problem as verified

Read against the current code:

- `ConnectionEdge.paint` (`homebound/ui/map_scene.py:~500`), `RouteDelegate.paint`
  (`homebound/ui/route_dock.py:~95`) and `BadgeStrip.paintEvent` (`homebound/ui/system_panel.py:~76`) exist
  as described. The edge and route tests only check `not image.isNull()` or call a helper and drop the
  result (`render()` in `tests/test_ui_routes.py:54` returns an image that no test inspects; one test
  reads `_paint_strip`'s return value, which counts squares, not pixels).
- `paint_item()` (`tests/test_ui_map_scene.py:59`) fills the image with 0 first, so a no-op `paint` leaves a
  fully transparent image, and `QImage(400, 200)` is never null. `BadgeStrip.grab()` is never null either.
- I did not re-run the mutation table in T1 (270 tests, 4 files). The trial below confirms the mechanism:
  a no-op paint leaves exactly the pixels the new checks look at empty.

The six docstrings that overclaim ("paint" with no "doesn't raise"). Proposed wording keeps the test
bodies as they are:

| # | Where | Now | Proposed |
|---|---|---|---|
| 1 | `tests/test_ui_map_scene.py:361` `test_paint_every_variant` | "Nodes with owner, stale scan, unconfirmed state, custom name, zKill stats, characters and hover paint." | "Painting nodes with owner, stale scan, unconfirmed state, custom name, zKill stats, characters and hover doesn't raise." |
| 2 | `tests/test_ui_map_scene.py:378` `test_paint_subtitle_and_effect` | "A renamed w-space system with an effect and a renamed k-space system (real name as subtitle) paint." | "Painting a renamed w-space system with an effect and a renamed k-space system (real name as subtitle) doesn't raise." |
| 3 | `tests/test_ui_map_scene.py:576` `test_paint_states` | "Edges paint in every mass, lifetime, frigate, confirmation and type state, hovered and selected or not." | "Painting an edge in every mass, lifetime, frigate, confirmation and type state, hovered and selected or not, doesn't raise." |
| 4 | `tests/test_ui_panels.py:1073` `test_painting_all_row_kinds` | "Every kind of row paints, fresh, selected, hovered, stale and with the copied feedback." | "Grabbing the table with every kind of row (fresh, selected, hovered, stale, copied) doesn't raise." |
| 5 | `tests/test_ui_panels.py:1129` `test_panel_paints_with_a_full_table` | "The whole panel renders with missing banner, signatures and anomalies." | "Grabbing the panel with a missing banner, signatures and anomalies doesn't raise, and the banner is visible." (the `isVisible` assert is real) |
| 6 | `tests/test_ui_panels.py:106` `test_badge_strip_paints_all_variants` | "Class badge, effect and statics paint for a wormhole system, a k-space one and none." | "Painting the strip for a wormhole system, a k-space one and none doesn't raise." (step 2 adds the pixel test; the inline comment "no system: paints nothing" on line 109 stays untested until then) |

`test_paint_through_scene_render` (`test_ui_map_scene.py:~590`) already checks that some pixel is non-zero,
so its docstring is fine. `test_paint_every_variant` and `test_paint_subtitle_and_effect` are the two the
`SystemNode` mutation can partly hit (T1: 5 tests fail), but nothing in their own assertions does, so the
rewording stands.

## Proposed change

### 1. Docstrings (`tests/test_ui_map_scene.py`, `tests/test_ui_panels.py`)

Apply the six rewordings above. No code change.

### 2. Pixel checks

All of them read pixels the code under test sets directly from theme tokens, at points that sit inside a
solid fill. None reads text or a glyph edge, so fonts don't matter, and none depends on antialiasing at
an edge of a shape.

**Edges** (new tests in the edge test class in `tests/test_ui_map_scene.py`, next to `test_paint_states`;
reuse `paint_item` and `edge_for`, no new helper). Paint at zoom 1.0 onto the transparent image, translate
by the `paint_item` offset (80, 60), and sample `edge.path.pointAtPercent(t)` for t in 0.1 to 0.9, skipping
the middle 0.3 to 0.7 band where the label plate (text and chips) sits.

| Check | Assert | Mutation caught |
|---|---|---|
| `test_fresh_edge_is_stroked` | At least half of the samples are fully opaque and within a few levels of `color("edge-fresh")`; a pixel 40 px off the curve is transparent. | No-op `ConnectionEdge.paint`: every pixel is 0. Also `stroke_edge` not called. |
| `test_dashed_and_dotted_edges_are_stroked` (parametrised: critical, unknown, lifetime 4h, lifetime 1h) | At least one sample has alpha > 0 (dashes and dots leave gaps, so never assert a count or a given point). | Same no-op, per pen. |
| `test_expired_edge_is_faded` | The 0.25 sample has 0 < alpha < 255 (opacity 0.4 on the main pen). | Dropping the `setOpacity(0.4)` in `stroke_edge`; the no-op. |
| `test_selected_edge_adds_an_underlay` | Number of pixels with alpha > 0 is larger selected than unselected, and the unselected count is > 0. | No-op paint (0 vs 0 fails the second assert); dropping the `isSelected()` branch. |

**Route rows** (new tests in `tests/test_ui_routes.py`; one helper `paint_row(dock, row, state)` that fills
a 300 x 40 `QImage` with an opaque base colour, paints one index and returns the image; the existing
`render()` paints three states onto one transparent image, which can't be sampled per state):

| Check | Assert | Mutation caught |
|---|---|---|
| `test_route_row_ground_follows_state` | With `State_MouseOver`, pixel (150, 2) equals `color("bg-4")`. With `State_Selected` it differs from both the plain and the hover pixel. Plain equals the base colour. | No-op `RouteDelegate.paint`: all three equal the base, so hover fails. Also `row_background` not called. |
| `test_route_strip_squares_are_painted` | For a routed row (select Jita, row 1), the centre of the first strip square, (12, 30) for a 300 x 40 row, equals `painting.security_color(...)` of that hop's system; the neighbour at (22, 30) likewise. Pixel (12, 30) of a row with no route (`route is None`) stays the base colour. | No-op `RouteDelegate.paint`; `_paint_strip` skipped; squares drawn at the wrong place. |

**Badge strip** (`tests/test_ui_panels.py`, next to `test_badge_strip_paints_all_variants`):

| Check | Assert | Mutation caught |
|---|---|---|
| `test_badge_strip_draws_the_class_badge` | For `HOME` (C5, filled badge), `strip.grab().toImage().pixelColor(3, 11)` equals `color(painting.CLASSES["C5"][0])`. With no system, the same pixel is the widget ground. For a k-space system, pixel (1, 11) differs from the ground. | No-op `BadgeStrip.paintEvent`: the pixel is the ground. |

The k-space outline is 1.5 px wide, so only "differs from the ground" is safe there, not an exact colour.

Not covered on purpose: the label plate and chips on edges, the route names and "N j" text, the effect name
and static codes in the strip. All of them are text, and a text assertion is where the flakiness would
come from.

## Trial

Written under the scratchpad, outside the repo, with the real fixtures (`make_state`, `scene`, `dock`),
`QT_QPA_PLATFORM=offscreen`, one run each. Results:

- Fresh edge, sample at t = 0.25: `#9fb2c1`, alpha 255, equal to `color("edge-fresh")`. A pixel 40 px away:
  alpha 0. 22 of the 24 samples outside the plate band were non-transparent.
- Dashed and unconfirmed variants: critical 22, unknown 15, unconfirmed 21 covered samples out of 24, so
  gaps are real and an "at least one" assert is the stable form.
- Selected vs plain edge, non-transparent pixels: 1612 vs 916.
- Route row, hover, pixel (150, 2): `#242b32`, alpha 255. Selected over a transparent base reads back as a
  half-transparent tint (alpha 148 at the bottom line), which is why the base must be opaque.
- First strip square at (12, 30): `#48f0c0` in all three states, equal to `security_color`; 11 squares.
- Badge strip, C5 pixels x = 0..4 at y = 11: all `#f09a3e`, equal to the token. HS (outlined) at x = 0..4:
  `#5fd38a`, `#377350`, then the ground.

Not tried: lifetime 4h, lifetime 1h, expired and frigate-only edges; a wormhole-hop (round dot) square;
the mutation run itself (see risks).

## Risks

- Flakiness. The checks avoid text, glyph edges and exact dash positions. What is left is the offscreen
  platform's rasteriser, which is deterministic for a given Qt build. Exact colour equality at a stroke
  centre could drift by a level on another Qt version, hence "within a few levels" for edges. Square
  centres and the badge pixel are inside plain fills and matched exactly in the trial.
- Coordinates are hard-coded (12, 30), (150, 2), (3, 11). If `strip_origin`, `ROW_HEIGHT` or the badge
  position changes, the tests fail with a message that should name the geometry. Computing them from
  `strip_squares` would be shorter, but then a bug in that function hides itself.
- Dashed pens at zoom 0.3 would behave differently. The checks run at zoom 1.0 only.
- Alpha on the transparent image: `QImage.pixelColor` on ARGB32 returns un-premultiplied values, so a
  translucent underlay reads with odd RGB. Assertions on alpha and counts only, not on RGB, for those.
- Mutation proof. Each check is written to fail under the plain mutation (no-op `paint`) and I read it
  that way, but I have not run the mutations. The implementation step must do it once per check and
  record it, the way `plan-test-fixes.md` did for steps 1 and 2.
- Interaction with the segfault investigation in `tests/test_ui_panels.py` paint paths. Another agent is on
  it. The `BadgeStrip` test and the docstring edits at lines 106, 1073 and 1129 are in that file, so they
  may conflict textually or may land while a crash there masks results. The edge and route tests are in
  other files and use only `QPainter` on a `QImage` (no widget `grab()`) for the edges and routes. The
  badge test uses `strip.grab()` like the existing one. If the segfault turns out to be in `grab()` of
  widgets, put that test last or hold it until the crash is fixed. No coordination was done.
- Cost: seven or eight small tests, each paints one item. Runtime should not be noticeable.

## Open decisions

1. Hard-coded geometry vs `strip_squares`/`strip_origin` for the square position. Plan says hard-coded.
2. Exact colour tolerance for edges (a few levels, or exact as in the trial).
3. Whether to also cover `SignatureDelegate`, `Overlay` and `CharacterDelegate` (T1 shows 1 failing test
   each). Left out; they are not in the TODO item.
4. Whether the `render()` helper in `tests/test_ui_routes.py` should be deleted once `paint_row` exists. It
   is only used by tests that don't read the result.
5. Whether `test_paint_through_scene_render` should also assert something about edges so the scene-level
   path is covered (it only checks that any pixel is non-zero, and nodes alone satisfy that).
6. The `plan-test-fixes.md` "Not changing" section: keep as is (it stays true) or add a pointer here.

## Done when

- The six docstrings say "doesn't raise" and no more.
- A no-op `ConnectionEdge.paint`, a no-op `RouteDelegate.paint` and a no-op `BadgeStrip.paintEvent` each fail
  at least one test, shown by running the mutation once per check.
- `uv run pytest -q` is green.

# Plan: remove duplicated functionality

Status: planned 2026-11-02, nothing implemented yet.

Depends on: nothing. Each step stands alone and can be its own commit; steps 1 and 2 first,
because they fix behaviour, the rest is cleanup.

## 1. One expiry time for connections (behaviour drift)

When a connection disappears is computed in three places:

- `mapping/tracker.py` `Tracker.expire`: dead when the type lifetime from `created_at` runs out
  (`DEFAULT_LIFETIME_HOURS` = 48 if the type is unknown) **or** `LIFETIME_REMAINING[lifetime]`
  from `lifetime_set_at` runs out.
- `ui/map_scene.py` `removed_in`: the tooltip countdown, which only uses `lifetime_set_at`.
- `ui/wormhole_fields.py` `lifetime_help_text`: `set_at + LIFETIME_REMAINING[...]` again.

So an EOL hole whose type lifetime ends sooner shows a countdown that's too long.

Plan:
- Add `expires_at(connection, universe) -> datetime | None` next to `Tracker.expire` (earliest of
  the two limits; None when there's neither).
- `expire` uses it (`now > expires_at(...)`); `removed_in` and `lifetime_help_text` format it.
- Test: a type with a short lifetime set to `< 4h` late in its life; `removed_in` matches when
  `expire` drops it.

## 2. One "known wormhole type" lookup (inconsistent case handling)

`universe.wormhole_type(t) if t and t.upper() != "K162" else None` is written out about 9 times:

- `mapping/tracker.py:89, 189` use `.upper()`
- `ui/dialogs.py:316`, `ui/signature_table.py:309, 329`, `ui/wormhole_fields.py:573` compare
  case-sensitively
- `ui/signature_table.py:191, 368` skip the K162 check entirely

Repeated with it in `signature_table.py`:
- "the set destination, else the type's first destination" (`:310, 330, 371`)
- "this signature is one of the system's statics" (`:191, 369`)

Plan:
- `Universe.known_type(code) -> WormholeType | None`: None for empty, unknown or K162, case-insensitive.
- `signature_destination(universe, signature) -> str | None` and
  `is_static_signature(universe, system_id, signature) -> bool` (in `mapping/naming.py` next to
  `static_slot_for_class`, or in `tracker.py` next to `hole_size`).
- Replace every call site. Test: lower-case `k162` and `h296`.

## 3. Version and HTTP client

- `backup.py:58` `app_version()` is a copy of `homebound/__init__.py:5`. Delete it and import the
  package one. Keep the `BackupInfo.app_version` field.
- `USER_AGENT` hard-codes `homebound/0.1` in `zkill.py:19` and `build_static.py:36`. Add a new
  `homebound/http.py` with `user_agent(purpose)` built from `app_version()` and
  `make_client() -> httpx.Client` (headers, gzip, timeout 30). zkill, eve_scout and build_static use
  it, and eve_scout no longer imports from zkill.
- `parse_utc(text) -> datetime | None` for ISO times with `Z` (`eve_scout._expiry`,
  `zkill.summarize`), in the same module or `validation.py`.

## 4. Background fetchers

`EveScoutFetcher` and `ZkillFetcher` duplicate:
- the callable checks in `__init__`
- `start`
- `_status`
- client setup and teardown in `_run`
- the `thread`, `_stopping` and `enabled` fields

Plan: a small `BackgroundFetcher` base class (in `http.py`) that owns the thread, the client and
`_status`, with the subclasses keeping their own loop (queue vs. timer). Only worth doing together
with step 3; skip it if the base class ends up bigger than the duplicated code.

## 5. Shared constants and chip tables

- Recent kill: `overlay.RECENT_KILL_WINDOW` vs. the hard-coded `timedelta(minutes=10)` in
  `map_scene.py:357`. Add `had_recent_kill(stats, now=None)` to `zkill.py` and use it in both.
  Put the staleness checks (`zkill.is_fresh`, `system_panel.ZKILL_STALE_AFTER` at `:302`) next to
  it, as `zkill.age(stats)`.
- Lifetime chips: `wormhole_fields.lifetime_segments` types `"◷ <4h"`, `"◷ <1h"` and `"✕ EXP"`
  again. Use `store.LIFETIME_CHIPS`, and add the colour token to it (`edge-eol` / `danger`, also
  needed by `ConnectionEdge._chips`).
- Mass chips: `painting.EDGE_CHIPS`, `signature_table.MASS_CHIPS` (subset, text only) and the
  literals in `wormhole_fields.mass_segments`. Keep `painting.EDGE_CHIPS` and drop the other two.
- Ship sizes: `settings.SHIP_SIZES` and `universe/db.SIZES` are both `S, M, L, XL`. Settings
  imports `SIZES`.

## 6. Qt widget helpers

- Caption label: `settings_dialog.caption`, `wormhole_fields.caption_label` and
  `Dialog.caption` (`dialogs.py:191`). Keep one free function (in `dialogs.py` or a new
  `ui/widgets.py`). `Dialog.caption` calls it and adds the label to the body.
- Radio card: `dialogs.ChoiceRow` and `settings_dialog.Card` both pick their radio on click and
  set the `selected` property plus repolish on toggle. Use one base class (`RadioCard`) with the
  layouts as subclasses. Check that `ChoiceRow.clicked` stays.
- `help_label` (`settings_dialog.py:60`) moves with the caption helper if `ui/widgets.py` is created.

## 7. Painting helpers

- Edge stroke: `ConnectionEdge.paint` (`map_scene.py:550-559`) and `draw_edge_icon`
  (`wormhole_fields.py:279`) both draw the lifetime underlay, the mass pen and the 0.4 opacity
  for expired holes. Add `painting.stroke_edge(painter, path, mass, lifetime, pens, wider=False)`;
  the icon passes a straight `QPainterPath`. The cached `_EDGE_PENS` global goes away if
  `painting.edge_pens()` caches.
- List row background (accent when selected, `bg-4` on hover, `line-1` bottom line):
  `CharacterDelegate.paint`, `RouteDelegate.paint`, the signature delegate (`signature_table.py:409`).
  Add `painting.row_background(painter, rect, selected, hover)`.

## 8. Names and durations

- `character_dock._paint_location` (`:76-86`) rebuilds "map name + real name" by hand. Use
  `namer.with_real_name`, or `display` plus `universe` for the second, muted text. Keep the
  two-colour look.
- Looking up a system by its map name: `route_dock.destination_name` (`:238`) and the
  `map_window` find completer (`:783`) both collect `display`, `label` and the real name. Add
  `namer.names(system_id) -> set[str]`.
- Duration text: `scan_status.age_text`, `ScanStatus.short_age`, `map_scene.removed_in`,
  `lifetime_help_text`. Add `format_duration(delta, style="long" | "short" | "min")` in
  `scan_status.py` and keep the current outputs exactly (tests pin them).
- `age_text` is re-exported via `ui/map_scene.py` to five UI modules. Import it from
  `mapping.scan_status` directly and drop the re-export comment.

## 9. Small validation helpers

`isinstance(v, int) and not isinstance(v, bool)` is written inline in `eve_scout.py:50, 63` and
`settings.py:247-253`. Add `is_int(value)` / `is_number(value)` to `validation.py`.

## Not duplicates (checked)

- `overlay._paint_header` and `signature_table._paint_header` share only the name.
- `backup.restore_settings` already reuses `Settings._apply`.
- The many `paint` / `sizeHint` / `start` methods are Qt or thread overrides.

## Done when

- `uv run pytest` passes, with new tests for steps 1 and 2.
- `grep -rn '"K162"' homebound` only hits `Universe.known_type`, the K162 special cases in the
  editors (`wormhole_fields`, `dialogs.signature_text`) and `db.py`.
- `grep -rn 'homebound/0.1\|timedelta(minutes=10)' homebound` hits only `LIFETIME_REMAINING`.

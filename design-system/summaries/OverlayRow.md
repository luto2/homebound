# OverlayRow mockup

One line of the overlay: name, class, J-name, effect, kills and owner, readable over any game background.

Group: Overlay
Mockup: `design-system/previews/OverlayRow.html`
Spec: `design-system/components/OverlayRow/README.md`

Shows: Rows over dark space and over an explosion.

Implemented by:

- `_paint_row` in `homebound/ui/overlay.py`
- `_paint_row_right` in `homebound/ui/overlay.py`
- `OverlayFonts` in `homebound/ui/overlay.py`
- `draw_halo_text` in `homebound/theme/painting.py`
- `hub_text` in `homebound/ui/map_scene.py`

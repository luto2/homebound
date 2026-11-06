# CharacterMarker mockup

14px shape that shows where a character is. Slot N always pairs one colour with one shape.

Group: Badges
Mockup: `design-system/previews/CharacterMarker.html`
Spec: `design-system/components/CharacterMarker/README.md`

Shows: The six slots: circle, square, triangle, diamond, hexagon and star.

Implemented by:

- `draw_character_marker` in `homebound/theme/painting.py`
- `MARKER_POLYGONS` in `homebound/theme/painting.py`
- `slot_for` in `homebound/settings.py`
- `_paint_character_markers` in `homebound/ui/map_scene.py`
- `CharacterDelegate` in `homebound/ui/character_dock.py`
- `_paint_header` in `homebound/ui/overlay.py`

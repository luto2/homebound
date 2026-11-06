# ConnectionEdge mockup

The curve between two systems, with a label plate. States differ by width and dash first, colour second.

Group: Map
Mockup: `design-system/previews/ConnectionEdge.html`
Spec: `design-system/components/ConnectionEdge/README.md`

Shows: Fresh, reduced, critical, lifetime under 4h and 1h, expired, frigate only and unknown, with their chips.

Implemented by:

- `ConnectionEdge` in `homebound/ui/map_scene.py`
- `removed_in` in `homebound/ui/map_scene.py`
- `edge_pens` in `homebound/theme/painting.py`
- `stroke_edge` in `homebound/theme/painting.py`
- `EDGE_CHIPS` in `homebound/theme/painting.py`
- `LIFETIME_CHIPS` in `homebound/mapdb/store.py`
- `expires_at` in `homebound/mapping/tracker.py`

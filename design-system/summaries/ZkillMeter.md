# ZkillMeter mockup

Three-bar activity meter, plus a diamond for a kill in the last 10 minutes.

Group: Badges
Mockup: `design-system/previews/ZkillMeter.html`
Spec: `design-system/components/ZkillMeter/README.md`

Shows: The four levels and the recent-kill diamond.

Implemented by:

- `draw_zkill` in `homebound/theme/painting.py`
- `zkill_level` in `homebound/theme/painting.py`
- `draw_recent_kill` in `homebound/theme/painting.py`
- `had_recent_kill` in `homebound/zkill.py`
- `_draw_zkill` in `homebound/ui/map_scene.py`

# MapMenu mockup

Switches between maps and manages them. Each map is its own SQLite file.

Group: Navigation
Mockup: `design-system/previews/MapMenu.html`
Spec: `design-system/components/MapMenu/README.md`

Shows: The toolbar button, the menu with stats per map, the Delete map submenu and the map tag on the canvas.

Implemented by:

- `_fill_map_menu` in `homebound/ui/map_window.py`
- `switch_map` in `homebound/ui/map_window.py`
- `new_map` in `homebound/ui/map_window.py`
- `rename_map` in `homebound/ui/map_window.py`
- `delete_map` in `homebound/ui/map_window.py`
- `set_map_tag` in `homebound/ui/map_window.py`
- `switch_map` in `homebound/ui/state.py`
- `add_map` in `homebound/settings.py`

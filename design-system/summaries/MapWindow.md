# MapWindow mockup

The main window: toolbar, character and route docks, map canvas, system dock and status bar.

Group: Screens
Mockup: `design-system/previews/MapWindow.html`
Spec: `design-system/components/MapWindow/README.md`

Shows: The window at 1600 by 1000 with a 12-system chain.

Implemented by:

- `MapWindow` in `homebound/ui/map_window.py`
- `MapArea` in `homebound/ui/map_window.py`
- `Legend` in `homebound/ui/map_window.py`
- `MapScene` in `homebound/ui/map_scene.py`
- `MapView` in `homebound/ui/map_scene.py`
- `SystemPanel` in `homebound/ui/system_panel.py`
- `CharacterDock` in `homebound/ui/character_dock.py`
- `RouteDock` in `homebound/ui/route_dock.py`

Note: The mockup says "Find system or sig…". The app says "Find system (Ctrl+F)" and only finds systems.

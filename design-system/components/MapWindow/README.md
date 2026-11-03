# MapWindow

The main window at its 1600×1000 default: toolbar, character and routes docks (left, characters above routes), map canvas, system dock (right), status bar.

- **Toolbar** (`size-toolbar`), left to right: app mark + `HOMEBOUND` wordmark · map button · home button (shows the home system, opens Settings → Home system) · naming scheme combo · `OVERLAY` caption + overlay mode as a segmented control (it's switched mid-session) · find field ("Find system (Ctrl+F)", 220px) · spacer · `Fit chain` · `⚙ Settings`.
- **Character dock** (`size-panel-left`): marker + name + online state as text ("● online", "○ offline 2h"), and the current system as class badge + custom name + J-name. The row the overlay follows gets `accent-dim`. Full spec: CharacterDock in `missing-mockups/README.md` §10.
- **Routes dock**: below the characters, see RoutesDock.
- **Map button**: `▦ Main ▾` left of the home combo opens the MapMenu; with more than one map the window title becomes `Homebound · Main` and the canvas shows a `MAP Main` tag top-right.
- **Canvas**: `bg-0` with a line grid: hairline cells of node height / 3 (≈ 20.7px) in `grid-dot`, every 3rd line (one node height) in `line-1`; minor lines drop out below 45% zoom, the whole grid below 20%. Nodes snap to the cell. Nodes and edges as specified in SystemNode and ConnectionEdge. A legend (collapsible) bottom-left, zoom bottom-right, toasts stack above the zoom.
- **Right dock** ("System", `size-panel-right`): editable custom name + J-name, location line (map name · region · constellation · nearest trade hub), badges (class, effect, statics), owner row (ticker field, colour swatch, note), zKill line with Refresh, notes; then the Signatures title row, missing-sigs banner, the SignatureTable sized for 440px (short group words) and Delete selected. Full spec: SystemPanel in `missing-mockups/README.md` §9.
- **Status bar**: logs · zKill · EVE-Scout · KWin · backup, each with ✓ / ▲ / ✕ and text. See StatusBar.

Qt: `QMainWindow` with `QDockWidget`s (left/right, movable — users can put characters at the bottom), central `QGraphicsView` with `setRenderHint(Antialiasing)`, `ViewportUpdateMode.SmartViewportUpdate`, `drawBackground` for the line grid.

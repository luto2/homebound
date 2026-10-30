# MapWindow

The main window at its 1600×1000 default: toolbar, character dock (left), map canvas, details + signatures dock (right), status bar.

- **Toolbar** (`size-toolbar`): Home system combo, naming scheme combo, overlay mode as a segmented control (it's switched mid-session), find field (Ctrl+F), Fit chain, Settings.
- **Character dock** (`size-panel-left`): marker + name + online state as text ("● online", "○ offline 2h"), and the current system as class badge + custom name + J-name. The row the overlay follows gets `accent-dim`.
- **Canvas**: `bg-0` with a `grid-dot` grid every `sp-24`. Nodes and edges as specified in SystemNode and ConnectionEdge. A legend (collapsible) bottom-left, zoom bottom-right, toasts stack above the zoom.
- **Right dock** (`size-panel-right`): editable custom name, J-name, badges, effect, owner combo, zKill line, notes; then the SignatureTable sized for 440px (scan % as a number only, short group words).
- **Status bar**: log watcher and zKill health, each with ✓ / ▲ / ✕ and text, plus the KWin focus-script status.

Qt: `QMainWindow` with `QDockWidget`s (left/right, movable — users can put characters at the bottom), central `QGraphicsView` with `setRenderHint(Antialiasing)`, `ViewportUpdateMode.SmartViewportUpdate`, `drawBackground` for the dot grid.

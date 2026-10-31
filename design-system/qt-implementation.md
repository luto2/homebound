# Qt implementation

How to turn `tokens.json` into QSS for widgets and into `QColor`/`QPen`/`QFont` objects for `QPainter` and `QGraphicsView`, using the same names as the tokens.

## 1. Load tokens once

Copy `tokens.json` into the app package (e.g. `homebound/theme/tokens.json`) and wrap it in a small module so widgets never hard-code values.

```python
# homebound/theme/tokens.py
import json, re
from importlib.resources import files
from PySide6.QtGui import QColor, QFont, QPen
from PySide6.QtCore import Qt

_T = json.loads(files("homebound.theme").joinpath("tokens.json").read_text())
THEME = "dark"

def _resolve(v):
    if isinstance(v, dict):
        v = v.get(THEME) or next(iter(v.values()))
    m = re.fullmatch(r"\{(.+)\}", v)
    return _resolve(COLORS_RAW[m.group(1)]) if m else v

COLORS_RAW = {t["name"]: t["value"] for t in _T["color"]["tokens"]}
HEX = {k: _resolve(v) for k, v in COLORS_RAW.items()}           # '#rrggbb' or '#rrggbbaa'
PX  = {t["name"]: float(str(t["value"]).rstrip("px"))           # spacing, radius, border, size
       for fam in ("spacing", "radius", "border", "size") for t in _T[fam]["tokens"]}
OP  = {t["name"]: float(t["value"]) for t in _T["opacity"]["tokens"]}

def color(name: str) -> QColor:
    """Tokens are CSS-style #RRGGBBAA. Qt's QColor('#AARRGGBB') puts alpha FIRST, so convert."""
    h = HEX[name].lstrip("#")
    c = QColor("#" + h[:6])
    if len(h) == 8:
        c.setAlpha(int(h[6:], 16))
    return c

def qss_color(name: str) -> str:
    """QSS accepts rgba(); use it for any token with alpha."""
    c = color(name)
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})" if c.alpha() < 255 else c.name()
```

**Alpha order is the one real trap**: tokens use `#rrggbbaa` (CSS), Qt's string form is `#aarrggbb`. Always go through `color()`; never paste an 8-digit token into `QColor()` or QSS.

## 2. Generate the stylesheet

Write QSS as a template with `{token}` placeholders and fill it at startup, so a token change restyles the whole app.

```python
QSS = """
QWidget            { background: {bg-1}; color: {ink}; font-family: "Noto Sans"; font-size: 13px; }
QDockWidget > QWidget, QDialog { background: {bg-2}; }
QFrame[role="divider"] { background: {line-1}; max-height: 1px; }

QPushButton, QToolButton {
    background: {bg-3}; color: {ink}; border: 1px solid {line-2}; border-radius: {radius-2}px;
    min-height: {size-control-inner}px; padding: 0 {sp-12}px; }
QPushButton:hover, QToolButton:hover   { background: {bg-4}; }
QPushButton:pressed                    { background: {bg-2}; }
QPushButton:focus                      { border: 2px solid {accent}; }   /* QSS has no outline-offset */
QPushButton:disabled                   { color: {ink-disabled}; border-color: {line-1}; background: {bg-2}; }
QPushButton[variant="primary"]         { background: {accent}; border-color: {accent}; color: {ink-inverse}; font-weight: 600; }
QPushButton[variant="primary"]:hover   { background: {accent-hover}; border-color: {accent-hover}; }
QPushButton[variant="danger"]          { background: {danger-dim}; border-color: {danger}; color: {danger}; font-weight: 600; }
QPushButton[variant="ghost"]           { background: transparent; border-color: transparent; color: {ink-muted}; }

QLineEdit, QComboBox, QPlainTextEdit {
    background: {bg-3}; border: 1px solid {line-2}; border-radius: {radius-2}px; padding: 0 {sp-8}px;
    selection-background-color: {accent}; selection-color: {ink-inverse}; min-height: {size-control-inner}px; }
QLineEdit:focus, QComboBox:focus { border: 2px solid {accent}; }
QLineEdit[state="valid"]   { border-color: {success}; }
QLineEdit[state="invalid"] { border-color: {danger}; background: {danger-dim}; }
QLabel[role="help"]       { color: {ink-faint}; font-size: 12px; }
QLabel[role="help-ok"]    { color: {success}; font-size: 12px; }
QLabel[role="help-err"]   { color: {danger};  font-size: 12px; }
QLabel[role="caption"]    { color: {ink-muted}; font-size: 11px; font-weight: 600; letter-spacing: 0.6px; }

QTableView { background: {bg-2}; gridline-color: {line-1}; border: 0; font-size: 12px;
             selection-background-color: {accent-dim}; selection-color: {ink}; }
QTableView::item { padding: 0 {sp-8}px; border-bottom: 1px solid {line-1}; }
QTableView::item:hover { background: {bg-4}; }
QHeaderView::section { background: {bg-3}; color: {ink-muted}; border: 0; border-bottom: 1px solid {line-1};
                       padding: 0 {sp-8}px; font-size: 11px; font-weight: 600; height: {size-row}px; }

QMenu { background: {bg-2}; border: 1px solid {line-2}; border-radius: {radius-4}px; padding: {sp-4}px 0; }
QMenu::item { padding: 0 {sp-12}px 0 {sp-8}px; min-height: 26px; }
QMenu::item:selected { background: {accent}; color: {ink-inverse}; }
QMenu::item:disabled { color: {ink-disabled}; }
QMenu::separator { height: 1px; background: {line-1}; margin: {sp-4}px 0; }
QToolTip { background: {bg-3}; color: {ink}; border: 1px solid {line-2}; padding: {sp-4}px {sp-6}px; }
"""

def build_qss() -> str:
    vals = {k: qss_color(k) for k in HEX} | {k: f"{v:g}" for k, v in PX.items()}
    vals["size-control-inner"] = f"{PX['size-control'] - 2:g}"      # min-height excludes the border
    return re.sub(r"\{([A-Za-z0-9_.-]+)\}", lambda m: vals[m.group(1)], QSS)

app.setStyle("Fusion")          # predictable base; Breeze ignores parts of QSS
app.setStyleSheet(build_qss())
```

Notes:
- Use **Fusion** as the base style. Breeze draws some controls itself and partly ignores QSS.
- Variants and states are **dynamic properties** (`btn.setProperty("variant", "primary")`). After changing one at runtime call `w.style().unpolish(w); w.style().polish(w)`.
- QSS has no `outline-offset`, `box-shadow` or `letter-spacing` in em. Focus is drawn as a 2px border; shadows come from `QGraphicsDropShadowEffect` (only on dialogs and toasts — it's expensive); letter-spacing via `QFont.setLetterSpacing(QFont.AbsoluteSpacing, 0.6)`.
- Also set a `QPalette` from the same tokens (Window = `bg-1`, Base = `bg-3`, Text = `ink`, Highlight = `accent`, HighlightedText = `ink-inverse`, PlaceholderText = `ink-faint`), so native dialogs like `QFileDialog` match.

## 3. QPainter: map and overlay

Scene items read the same tokens; build pens and brushes once and reuse them.

```python
PENS = {
    "fresh":    QPen(color("edge-fresh"),    PX["edge-w-fresh"]),
    "reduced":  QPen(color("edge-reduced"),  PX["edge-w-reduced"]),
    "critical": QPen(color("edge-critical"), PX["edge-w-critical"], Qt.CustomDashLine),
    "unknown":  QPen(color("edge-unknown"),  PX["edge-w-fresh"],    Qt.CustomDashLine),
    "frig":     QPen(color("edge-frig"),     PX["edge-w-frig"]),
    "eol":      QPen(color("edge-eol"),      PX["edge-w-eol"],      Qt.DotLine, Qt.RoundCap),
}
# Qt dash lengths are in units of pen width: divide the px pattern by the width.
w = PX["edge-w-critical"]; PENS["critical"].setDashPattern([8 / w, 4 / w])
w = PX["edge-w-fresh"];    PENS["unknown"].setDashPattern([3 / w, 5 / w])
for p in PENS.values(): p.setCosmetic(False)   # edges scale with zoom; set True to keep screen width

class EdgeItem(QGraphicsPathItem):
    def paint(self, p, opt, w=None):
        p.setRenderHint(QPainter.Antialiasing)
        if self.eol:  p.strokePath(self.path(), PENS["eol"])           # casing first, under
        p.strokePath(self.path(), PENS["frig" if self.frig else self.mass])
```

**Nodes**: one `QGraphicsObject` per system with `ItemIsMovable | ItemIsSelectable | ItemSendsGeometryChanges`. Paint the body with `drawRoundedRect(rect, PX["radius-3"], PX["radius-3"])`; read `opt.levelOfDetailFromTransform(p.worldTransform())` and switch to the compact/minimal layouts at 0.6 / 0.35. Cache badge pixmaps (`QPixmapCache`) keyed by `(class, sec, devicePixelRatio)`.

**Hatch for stale**: `QBrush(color("line-1"), Qt.BDiagPattern)` over the node body, then paint content with `p.setOpacity(OP["op-stale"])`.

**Line grid**: override `QGraphicsView.drawBackground`, fill `bg-0`, then draw hairlines (`QPen(..., 1)`, `setCosmetic(True)`) on a square cell of **node height / 3** (62 / 3 ≈ 20.7 scene units), so a node is exactly 3 cells tall. Minor lines in `grid-dot`; every 3rd line (one node height) in `line-1`, drawn after the minor ones so it sits on top. Below 45% zoom draw only the major lines (minor ones would be < 9px apart); below 20% fill plain `bg-0`. Nodes snap to the cell; new children go 12 cells right and 5 cells down per sibling. App: `painting.draw_line_grid(p, rect, step, major_every=3, minor=zoom >= 0.45)`.

**Overlay text with halo**:

```python
def draw_halo_text(p: QPainter, x: float, y: float, text: str, font: QFont, ink: QColor):
    path = QPainterPath(); path.addText(x, y, font, text)
    p.strokePath(path, QPen(color("overlay-halo"), 2.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    p.fillPath(path, ink)
```

- Window: `Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool`, plus `Qt.WindowTransparentForInput` while locked; `setAttribute(Qt.WA_TranslucentBackground)`, `WA_ShowWithoutActivating`.
- Background: `QColor` from `overlay-bg` with alpha set from the opacity setting (`int(255 * op)`), drawn as a rounded rect `radius-3`; frame 1px `overlay-frame` (2px dashed `overlay-unlocked` when unlocked).
- Fonts: `QFont("Noto Sans", pointSize)` takes points; use `setPixelSize(12)` so the px tokens hold on HiDPI and fractional-scaling Plasma setups. Set `QFont.PreferAntialias`, and `setHintingPreference(QFont.PreferNoHinting)` for the halo paths (hinting makes them uneven).
- Cache each row's `QPainterPath`s; rebuild only when its text or the font size changes.

## 4. Fonts

```python
sans = QFont(["Noto Sans", "Cantarell", "DejaVu Sans"]); sans.setPixelSize(13)
mono = QFont(["Hack", "Noto Sans Mono", "DejaVu Sans Mono"]); mono.setPixelSize(12)
mono.setStyleHint(QFont.Monospace)
app.setFont(sans)
```

`QFont(families: list)` (Qt ≥ 6.0) gives the same fallback stack as the tokens. Size styles map 1:1: `ui-title` 15px/600, `ui-body` 13px, `ui-small` 12px, `ui-label` 11px/600 caps, `mono-sig` 13px/700, `mono-data` 12px, `mono-small` 11px, `node-name` 14px/700, `node-meta` 10px/600, `overlay-row` 12px/600, `overlay-mono` 11px/600, `overlay-header` 11px/700.

## 5. Tray

`QSystemTrayIcon` with a `QMenu` built from `QAction`s; put the four overlay modes in an exclusive `QActionGroup`. Call `menu.setStyleSheet(build_qss())` explicitly — under Plasma the tray menu may not inherit the app stylesheet.

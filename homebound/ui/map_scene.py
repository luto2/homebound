"""Map canvas: SystemNode, ConnectionEdge, MapScene, MapView (spec: SystemNode, ConnectionEdge, MapWindow)."""
import math
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject, QGraphicsScene, QGraphicsView, QStyle

from ..mapdb.store import LIFE_1H, LIFE_4H, LIFE_CHIP, LIFE_EXPIRED, LIFE_LABEL, LIFE_LEFT
from ..mapping.tracker import hole_size
from ..theme import painting as P
from ..universe.db import SIZE_NAMES
from ..theme.tokens import OP, PX, color, font
from ..zkill import is_fresh

W, H = PX["size-node-w"], PX["size-node-h"]
GRID = H / 3  # background grid cell: a third of a system node's height; nodes snap to it
STALE_AFTER = timedelta(hours=24)


def age_text(iso: str | None) -> str:
    if not iso:
        return ""
    d = datetime.now(timezone.utc) - datetime.fromisoformat(iso)
    m = int(d.total_seconds() // 60)
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 24:
        return f"{h}h {m}m" if m else f"{h}h"
    dd, h = divmod(h, 24)
    return f"{dd}d {h}h" if h else f"{dd}d"


def removed_in(conn) -> str:
    """Time until tracker.expire drops the connection, e.g. "7 min" / "2h 10m"."""
    if not conn.eol_at or conn.eol not in LIFE_LEFT:
        return "?"
    left = datetime.fromisoformat(conn.eol_at) + LIFE_LEFT[conn.eol] - datetime.now(timezone.utc)
    m = max(0, int(left.total_seconds() // 60))
    if m < 60:
        return f"{m} min"
    return f"{m // 60}h {m % 60}m"


def elide(text: str, f, width: float) -> str:
    fm = QFontMetricsF(f)
    return fm.elidedText(text, Qt.TextElideMode.ElideRight, width)


class SystemNode(QGraphicsObject):
    moved = Signal(int, float, float)

    def __init__(self, state, sid: int):
        super().__init__()
        self.state, self.sid = state, sid
        self.edges: list["ConnectionEdge"] = []
        self.hover = False
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
                      | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
        self.setAcceptHoverEvents(True)
        self.setZValue(2)
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self.refresh()

    def refresh(self):
        st = self.state
        self.sys = st.uni.by_id(self.sid)
        self.ms = st.store.system(self.sid)
        self.name = st.namer.label(self.sid)
        self.zk = st.store.zkill(self.sid) if st.settings.zkill_enabled else None
        self.chars = st.chars_in(self.sid)
        # added by hand and not seen yet, or attached to a hand-drawn connection nobody has jumped
        self.unconfirmed = bool(self.ms and (not self.ms.confirmed or
                                             any(not c.confirmed for c in st.store.connections_of(self.sid))))
        self.stale = bool(self.ms and self.ms.scanned_at and
                          datetime.now(timezone.utc) - datetime.fromisoformat(self.ms.scanned_at) > STALE_AFTER)
        self.setToolTip(self._tooltip())
        self.update()

    def _tooltip(self) -> str:
        s = self.sys
        if not s:
            return str(self.sid)
        bits = [f"<b>{self.state.namer.display(self.sid)}</b> · {s.name}", f"{s.cls} · {s.region}"]
        if s.effect:
            bits.append(P.EFFECT_NAMES[s.effect])
        if s.statics:
            bits.append("Statics: " + ", ".join(f"{t.name} → {'/'.join(t.dest)}" for t in self.state.uni.static_types(s.id)))
        if self.ms and self.ms.owner_ticker:
            bits.append(f"Owner: {self.ms.owner_ticker} {self.ms.owner_note or ''}")
        if self.chars:
            bits.append("Here: " + ", ".join(c[1] for c in self.chars))
        if self.unconfirmed:
            bits.append("▲ Unconfirmed: added by hand, or on a connection nobody has jumped yet")
        return "<br>".join(bits)

    @property
    def kspace(self) -> bool:
        return bool(self.sys and self.sys.kspace)

    def boundingRect(self) -> QRectF:
        return QRectF(-4, -12, W + 8, H + 16)

    def shape(self) -> QPainterPath:
        p = QPainterPath()
        p.addRoundedRect(QRectF(0, 0, W, H), PX["radius-3"], PX["radius-3"])
        return p

    def center(self) -> QPointF:
        return self.pos() + QPointF(W / 2, H / 2)

    # ------------------------------------------------------------ paint
    def paint(self, p: QPainter, opt, widget=None):
        lod = opt.levelOfDetailFromTransform(p.worldTransform())
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        h = H if lod >= 0.6 else (34 if lod >= 0.35 else 26)
        body = QRectF(0, 0, W, h)
        fill = color("bg-4") if self.hover else color("bg-1" if self.kspace else "bg-2")
        border = color("line-1" if self.kspace else "line-2")
        p.setPen(QPen(border, 1))
        p.setBrush(fill)
        p.drawRoundedRect(body.adjusted(.5, .5, -.5, -.5), PX["radius-3"], PX["radius-3"])
        if self.stale:
            p.setBrush(P.stale_brush())
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(body, PX["radius-3"], PX["radius-3"])
        if self.unconfirmed:
            p.setPen(QPen(color("warning"), 2, Qt.PenStyle.DashLine))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(body.adjusted(1, 1, -1, -1), PX["radius-3"], PX["radius-3"])
        if selected:
            p.setPen(QPen(color("accent"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(body.adjusted(-1, -1, 1, 1), PX["radius-3"] + 1, PX["radius-3"] + 1)
        self._tab_x = 8.0   # where the character markers start on the top edge
        if self.sid == self.state.home_id:
            P.draw_chip(p, W - 46, -7, "⌂ HOME", color("accent"), h=13)
        if self.unconfirmed:
            self._tab_x += P.draw_chip(p, 8, -7, "▲ UNCONFIRMED", color("warning"), h=13) + 4

        if self.stale:
            p.setOpacity(OP["op-stale"])
        if lod < 0.35:
            f = font("node-name", max(10, int(13 / max(lod, 0.05))))
            p.setFont(f)
            p.setPen(color("ink"))
            p.drawText(body.adjusted(6, 0, -6, 0), Qt.AlignmentFlag.AlignVCenter, elide(self.name, f, W - 12))
            self._chars(p)
            return
        x = 8
        nf = font("node-name")
        right = W - 8
        if lod >= 0.6 and self.zk and (self.zk.kills_24h or self.zk.kills_1h):
            right -= self._zk_width()
            self._draw_zk(p, right + 4, 7)
        p.setFont(nf)
        name = self.name
        sub = f" ({self.sys.name})" if self.kspace and name != self.sys.name and not (self.ms and self.ms.custom_name) else ""
        fm = QFontMetricsF(nf)
        p.setPen(color("ink"))
        nm = elide(name, nf, right - x)
        p.drawText(QPointF(x, 5 + fm.ascent()), nm)
        if sub:
            sx = x + fm.horizontalAdvance(nm) + 2
            sf = font("ui-small", 11)
            p.setFont(sf)
            p.setPen(color("ink-muted"))
            p.drawText(QPointF(sx, 5 + fm.ascent()), elide(sub.strip(), sf, right - sx))
        if lod < 0.6:
            P.draw_class_badge(p, W - 8 - P.class_badge_width(self.sys.cls), 9, self.sys.cls)
            self._chars(p)
            return
        # line 2: class · effect · J-name · statics
        y2 = 25
        cx = x + P.draw_class_badge(p, x, y2, self.sys.cls, self.sys.security if self.kspace else None) + 5
        if self.sys.effect:
            cx += P.draw_effect(p, cx, y2 + 1, self.sys.effect) + 5
        sx = W - 8
        for t in reversed(self.state.uni.static_types(self.sid)):
            d = t.dest[0] if t.dest else "?"
            sx -= self._static_w(d)
            P.draw_static(p, sx, y2, d)
            sx -= 3
        if not self.kspace:
            mf = font("mono-small")
            p.setFont(mf)
            p.setPen(color("ink-muted"))
            p.drawText(QRectF(cx, y2, sx - cx - 2, 16), Qt.AlignmentFlag.AlignVCenter,
                       elide(self.sys.name, mf, sx - cx - 2))
        # line 3: owner, stale
        y3 = 44
        if self.ms and self.ms.owner_ticker:
            P.draw_owner(p, x, y3, self.ms.owner_ticker, self.ms.owner_slot or 1, home=self.sid == self.state.home_id)
        if self.stale:
            sf = font("node-meta")
            p.setFont(sf)
            p.setPen(color("ink-faint"))
            p.drawText(QRectF(x, y3, W - 16, 14), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                       f"stale {age_text(self.ms.scanned_at)}")
        p.setOpacity(1)
        self._chars(p)

    def _static_w(self, cls):
        fm = QFontMetricsF(font("node-meta"))
        return 3 + fm.horizontalAdvance("→") + 2 + fm.horizontalAdvance(cls) + 4

    def _zk_level(self):
        if not self.zk:
            return "none", False
        recent = bool(self.zk.last_kill_at and datetime.now(timezone.utc)
                      - datetime.fromisoformat(self.zk.last_kill_at) < timedelta(minutes=10))
        return P.zk_level(self.zk.kills_1h, self.zk.kills_24h), recent

    def _zk_width(self):
        _, recent = self._zk_level()
        return 14 + (16 if recent else 0) + 4

    def _draw_zk(self, p, x, y):
        lvl, recent = self._zk_level()
        P.draw_zk(p, x, y, lvl, recent)

    def _chars(self, p):
        """Character markers on the top edge, after the UNCONFIRMED tag and clear of the HOME tab;
        whatever doesn't fit becomes a "+n" chip."""
        if not self.chars:
            return
        x = getattr(self, "_tab_x", 8.0)
        right = (W - 46 - 4) if self.sid == self.state.home_id else (W - 8)
        step, more_w = 16, 24          # marker 14 + gap 2; room for a "+n" chip
        n = len(self.chars)
        fit = max(0, int((right - x) // step))
        shown = n if n <= min(3, fit) else max(0, min(2, (right - x - more_w) // step))
        for _, _, slot in self.chars[:int(shown)]:
            x += P.draw_char_marker(p, x, -7, slot) + 2
        if shown < n:
            P.draw_chip(p, x, -6, f"+{n - int(shown)}", color("bg-4"), ink=color("ink"))

    # ------------------------------------------------------------ interaction
    def hoverEnterEvent(self, e):
        self.hover = True
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()

    def itemChange(self, change, value):
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionChange:
            return QPointF(round(value.x() / GRID) * GRID, round(value.y() / GRID) * GRID)
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            for e in self.edges:
                e.adjust()
        if change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged and value:
            self.state.select(self.sid)
        return super().itemChange(change, value)

    def mouseReleaseEvent(self, e):
        super().mouseReleaseEvent(e)
        if self.ms and (self.ms.x, self.ms.y) != (self.pos().x(), self.pos().y()):
            self.state.store.update_system(self.sid, x=self.pos().x(), y=self.pos().y())
            self.ms = self.state.store.system(self.sid)


class ConnectionEdge(QGraphicsItem):
    PENS = None

    def __init__(self, state, conn, na: SystemNode, nb: SystemNode):
        super().__init__()
        if ConnectionEdge.PENS is None:
            ConnectionEdge.PENS = P.edge_pens()
        self.state, self.conn, self.na, self.nb = state, conn, na, nb
        na.edges.append(self)
        nb.edges.append(self)
        self.hover = False
        self.setZValue(1)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.line = QLineF()
        self.adjust()

    def refresh(self, conn):
        self.conn = conn
        self.setToolTip(self._tooltip())
        self.update()

    def _tooltip(self):
        c = self.conn
        parts = [c.wh_type or "Unknown type",
                 {"fresh": "Mass fresh", "reduced": "Mass reduced (<50%)", "critical": "Mass critical (<10%)",
                  "unknown": "Mass unknown"}[c.mass]]
        if c.eol == LIFE_EXPIRED:
            parts.append(f"Expired · removed in {removed_in(c)}")
        elif c.eol:
            parts.append(f"Lifetime {LIFE_LABEL[c.eol]} (set {age_text(c.eol_at)} ago)")
        size = hole_size(self.state.uni, c.wh_type, c.size)
        if size:
            parts.append(SIZE_NAMES[size] + (" only" if size == "S" else ""))
        if not c.confirmed:
            parts.append("Unconfirmed: drawn by hand, nobody has jumped it yet")
        parts.append(f"Age {age_text(c.created_at)}")
        return " · ".join(parts)

    def adjust(self):
        """Curve from the facing sides of the two nodes (horizontal ports when side by side)."""
        self.prepareGeometryChange()
        a, b = self.na.sceneBoundingRect().adjusted(4, 12, -4, -4), self.nb.sceneBoundingRect().adjusted(4, 12, -4, -4)
        if b.left() >= a.right() or a.left() >= b.right():
            l, r = (a, b) if b.left() >= a.right() else (b, a)
            s, e = QPointF(l.right(), l.center().y()), QPointF(r.left(), r.center().y())
            dx = (e.x() - s.x()) / 2
            c1, c2 = s + QPointF(dx, 0), e - QPointF(dx, 0)
        else:
            t, u = (a, b) if a.center().y() <= b.center().y() else (b, a)
            s, e = QPointF(t.center().x(), t.bottom()), QPointF(u.center().x(), u.top())
            dy = (e.y() - s.y()) / 2
            c1, c2 = s + QPointF(0, dy), e - QPointF(0, dy)
        path = QPainterPath(s)
        path.cubicTo(c1, c2, e)
        self.path = path
        self.line = QLineF(s, e)
        self.setToolTip(self._tooltip())

    def boundingRect(self):
        return self.path.boundingRect().united(self._plate_rect()).adjusted(-8, -8, 8, 8)

    def shape(self):
        s = QPainterPathStroker()
        s.setWidth(12)
        out = s.createStroke(self.path)
        out.addRect(self._plate_rect())
        return out

    def _chips(self):
        c = self.conn
        chips = []
        if c.mass in P.EDGE_CHIPS:
            t, tok = P.EDGE_CHIPS[c.mass]
            chips.append((t, color(tok)))
        if c.frig:
            chips.append(("F", color("edge-frig")))
        if c.eol in LIFE_CHIP:
            chips.append((LIFE_CHIP[c.eol], color("danger") if c.eol == LIFE_EXPIRED else color("edge-eol")))
        if not c.confirmed:
            chips.append(("▲ UNCONF", color("warning")))
        return chips

    def _plate_rect(self) -> QRectF:
        f = font("edge-label")
        fm = QFontMetricsF(f)
        cf = QFontMetricsF(font("edge-label", 9))
        w = fm.horizontalAdvance(self.conn.wh_type or "?") + 10
        w += sum(cf.horizontalAdvance(t) + 6 + 3 for t, _ in self._chips())
        mid = self.path.pointAtPercent(0.5)
        return QRectF(mid.x() - w / 2, mid.y() - 9, w, 18)

    def paint(self, p: QPainter, opt, widget=None):
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        lod = opt.levelOfDetailFromTransform(p.worldTransform())
        c = self.conn
        path = self.path
        pens = ConnectionEdge.PENS
        if self.isSelected():
            p.strokePath(path, pens["selected"])
        if not c.confirmed:
            casing = QPen(color("warning"), 7, Qt.PenStyle.DashLine, Qt.PenCapStyle.FlatCap)
            casing.setDashPattern([3, 2])
            p.strokePath(path, casing)
        if c.eol in (LIFE_4H, LIFE_1H):
            p.strokePath(path, pens["eol1h" if c.eol == LIFE_1H else "eol"])
        pen = QPen(pens["frig" if c.frig else c.mass])
        if self.hover:
            pen.setWidthF(pen.widthF() + 1)
        if c.eol == LIFE_EXPIRED:   # fading out
            p.save()
            p.setOpacity(0.4)
            p.strokePath(path, pen)
            p.restore()
        else:
            p.strokePath(path, pen)
        if c.frig:
            m = self.path.pointAtPercent(0.5) + QPointF(0, 14)
            d = QPolygonF([m + QPointF(0, -5), m + QPointF(5, 0), m + QPointF(0, 5), m + QPointF(-5, 0)])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color("edge-frig"))
            p.drawPolygon(d)
        # label plate
        r = self._plate_rect()
        show_type = lod >= 0.5
        chips = self._chips()
        if not show_type and not chips:
            return
        p.setPen(QPen(color("accent") if self.hover else color("line-1"), 1))
        p.setBrush(color("edge-label-bg"))
        p.drawRoundedRect(r, 3, 3)
        x = r.left() + 5
        f = font("edge-label")
        p.setFont(f)
        p.setPen(color("ink"))
        t = c.wh_type or "?"
        p.drawText(QRectF(x, r.top(), 60, r.height()), Qt.AlignmentFlag.AlignVCenter, t)
        x += QFontMetricsF(f).horizontalAdvance(t) + 5
        for text, bg in chips:
            x += P.draw_chip(p, x, r.top() + 3, text, bg) + 3

    def hoverEnterEvent(self, e):
        self.hover = True
        self.setToolTip(self._tooltip())   # ages and the expiry countdown move on
        self.update()

    def hoverLeaveEvent(self, e):
        self.hover = False
        self.update()


class MapScene(QGraphicsScene):
    nodeContextMenu = Signal(int, object)     # sid, screen pos
    edgeContextMenu = Signal(int, object)     # connection id, screen pos
    edgeActivated = Signal(int)
    backgroundContextMenu = Signal(object, object)  # scene pos, screen pos
    linkRequested = Signal(int, int)          # draw a connection: from sid, to sid

    def __init__(self, state):
        super().__init__()
        self.state = state
        self.nodes: dict[int, SystemNode] = {}
        self.edges: dict[int, ConnectionEdge] = {}
        self.setSceneRect(-5000, -5000, 10000, 10000)
        self._link_from: SystemNode | None = None   # drawing a connection from this node
        self._link_line = None
        self._link_click = False                    # started from the menu: finish on the next click
        state.mapChanged.connect(self.rebuild)
        state.systemUpdated.connect(self.refresh_system)
        state.charactersChanged.connect(self.refresh_all)
        state.selectionChanged.connect(self._select)
        self.rebuild()

    def rebuild(self):
        st = self.state
        systems = {s.id: s for s in st.store.systems()}
        for sid in list(self.nodes):
            if sid not in systems:
                n = self.nodes.pop(sid)
                self.removeItem(n)
        for cid in list(self.edges):
            self.removeItem(self.edges.pop(cid))
        for n in self.nodes.values():
            n.edges.clear()
        for sid, ms in systems.items():
            n = self.nodes.get(sid)
            if n is None:
                n = self.nodes[sid] = SystemNode(st, sid)
                self.addItem(n)
            n.setPos(ms.x, ms.y)
            n.refresh()
        for c in st.store.connections():
            if c.a in self.nodes and c.b in self.nodes:
                e = self.edges[c.id] = ConnectionEdge(st, c, self.nodes[c.a], self.nodes[c.b])
                e.refresh(c)
                self.addItem(e)
        self._select(st.selected)

    def refresh_system(self, sid: int):
        if n := self.nodes.get(sid):
            n.refresh()
            for e in n.edges:
                c = self.state.store.connection_by_id(e.conn.id)
                if c:
                    e.refresh(c)

    def refresh_all(self):
        for n in self.nodes.values():
            n.refresh()

    def _select(self, sid: int):
        n = self.nodes.get(sid)
        if n and not n.isSelected():
            self.clearSelection()
            n.setSelected(True)

    def contextMenuEvent(self, e):
        item = self.itemAt(e.scenePos(), self.views()[0].transform()) if self.views() else None
        if isinstance(item, SystemNode):
            self.nodeContextMenu.emit(item.sid, e.screenPos())
        elif isinstance(item, ConnectionEdge):
            self.edgeContextMenu.emit(item.conn.id, e.screenPos())
        else:
            self.backgroundContextMenu.emit(e.scenePos(), e.screenPos())

    # ------------------------------------------------------------ drawing connections
    def node_at(self, pos) -> "SystemNode | None":
        return next((i for i in self.items(pos) if isinstance(i, SystemNode)), None)

    def start_link(self, sid: int, click_mode: bool = True):
        """Rubber-band line from sid; finished by releasing (Shift+drag) or clicking (menu) on another system."""
        from PySide6.QtWidgets import QGraphicsLineItem
        n = self.nodes.get(sid)
        if not n:
            return
        self.cancel_link()
        self._link_from, self._link_click = n, click_mode
        pen = QPen(color("warning"), 2, Qt.PenStyle.DashLine)
        pen.setCosmetic(True)
        self._link_line = QGraphicsLineItem(QLineF(n.center(), n.center()))
        self._link_line.setPen(pen)
        self._link_line.setZValue(10)
        self.addItem(self._link_line)
        for v in self.views():
            v.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def cancel_link(self):
        if self._link_line is not None:
            self.removeItem(self._link_line)
        self._link_from, self._link_line = None, None
        for v in self.views():
            v.viewport().unsetCursor()

    def _finish_link(self, pos):
        src, dst = self._link_from, self.node_at(pos)
        self.cancel_link()
        if src and dst and dst is not src:
            self.linkRequested.emit(src.sid, dst.sid)

    def mousePressEvent(self, e):
        if self._link_from is not None and self._link_click:
            self._finish_link(e.scenePos())
            e.accept()
            return
        if e.button() == Qt.MouseButton.LeftButton and e.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            if n := self.node_at(e.scenePos()):
                self.start_link(n.sid, click_mode=False)
                e.accept()
                return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._link_from is not None:
            dst = self.node_at(e.scenePos())
            end = dst.center() if dst and dst is not self._link_from else e.scenePos()
            self._link_line.setLine(QLineF(self._link_from.center(), end))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._link_from is not None and not self._link_click:
            self._finish_link(e.scenePos())
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self._link_from is not None:
            self.cancel_link()
            return
        super().keyPressEvent(e)

    def mouseDoubleClickEvent(self, e):
        item = self.itemAt(e.scenePos(), self.views()[0].transform()) if self.views() else None
        if isinstance(item, ConnectionEdge):
            self.edgeActivated.emit(item.conn.id)
            return
        super().mouseDoubleClickEvent(e)

    def chain_rect(self, sid: int | None = None) -> QRectF:
        items = list(self.nodes.values())
        if sid and sid in self.nodes:
            from ..mapping.chain import component, wh_adjacency
            members = component(wh_adjacency(self.state.store), sid)
            items = [self.nodes[m] for m in members if m in self.nodes]
        r = QRectF()
        for n in items:
            r = r.united(n.sceneBoundingRect())
        return r


class MapView(QGraphicsView):
    zoomChanged = Signal(float)

    def __init__(self, scene: MapScene):
        super().__init__(scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.SmartViewportUpdate)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setStyleSheet("background: transparent; border: 0;")

    def zoom(self) -> float:
        return self.transform().m11()

    def set_zoom(self, z: float):
        z = max(0.2, min(2.5, z))
        f = z / self.zoom()
        self.scale(f, f)
        self.zoomChanged.emit(self.zoom())

    def wheelEvent(self, e):
        self.set_zoom(self.zoom() * math.pow(1.0015, e.angleDelta().y()))

    def drawBackground(self, p: QPainter, rect: QRectF):
        if self.zoom() < 0.2:
            p.fillRect(rect, color("bg-0"))
        else:
            # minor lines would be < 9px apart below 0.45: show only the node-height lines then
            P.draw_line_grid(p, rect, GRID, 3, minor=self.zoom() >= 0.45)

    def fit(self, rect: QRectF):
        if rect.isNull():
            return
        self.fitInView(rect.adjusted(-80, -60, 80, 60), Qt.AspectRatioMode.KeepAspectRatio)
        if self.zoom() > 1.2:
            self.set_zoom(1.0)
        self.zoomChanged.emit(self.zoom())

"""Overlay over the EVE client: the followed character's chain (spec: OverlayRow, OverlayStates).

Frameless, translucent, always on top, click-through while locked. Everything is painted;
text uses the halo recipe so it reads over explosions and black space alike.
"""
from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ..theme import painting as P
from ..theme.tokens import OP, PX, color, font

MODE_LABEL = {"follow": "follow", "pinned_eve": "pinned", "pinned_always": "pinned · always"}


class Overlay(QWidget):
    unlockedChanged = Signal(bool)

    def __init__(self, state, focus):
        super().__init__(None)
        self.state, self.focus = state, focus
        self.unlocked = False
        self.focused_char: str | None = None  # from focus tracker
        self._drag: QPoint | None = None
        self.chain = None
        self.target: int | None = None
        self.setWindowTitle("Homebound overlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self._apply_flags()
        focus.focusChanged.connect(self._on_focus)
        for sig in (state.mapChanged, state.charactersChanged, state.settingsChanged):
            sig.connect(self.refresh)
        state.systemUpdated.connect(lambda _: self.refresh())
        self.resize(int(PX["size-overlay-w"]), 80)
        self._restore_pos()

    # ------------------------------------------------------------ window flags / position
    def _apply_flags(self):
        flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
                 | Qt.WindowType.WindowDoesNotAcceptFocus)
        if not self.unlocked:
            flags |= Qt.WindowType.WindowTransparentForInput
        vis = self.isVisible()
        self.setWindowFlags(flags)
        if vis:
            self.show()

    def set_unlocked(self, on: bool):
        if on == self.unlocked:
            return
        self.unlocked = on
        self._apply_flags()
        if not on:
            self._save_pos()
        self.unlockedChanged.emit(on)
        self.refresh()

    def _mode(self) -> str:
        return self.state.settings.overlay_mode

    def _restore_pos(self):
        pos = self.state.settings.overlay_pos.get(self._mode())
        if pos:
            self.move(int(pos[0]), int(pos[1]))
        else:
            self.move(80, 120)

    def _save_pos(self):
        self.state.settings.overlay_pos[self._mode()] = [self.x(), self.y()]
        self.state.settings.save()

    # ------------------------------------------------------------ state
    def _on_focus(self, char, rect):
        self.focused_char = char
        if char:
            cid = self.state.char_by_name(char)
            self.state.set_followed(cid)
        self.refresh()

    def _target(self) -> int | None:
        st = self.state
        mode = self._mode()
        if mode == "follow":
            return st.char_by_name(self.focused_char) if self.focused_char else None
        if mode in ("pinned_eve", "pinned_always"):
            return st.settings.overlay_pinned_char
        return None

    def should_show(self) -> bool:
        mode = self._mode()
        if mode == "off":
            return False
        if self.unlocked or mode == "pinned_always":
            return True
        if mode == "pinned_eve":
            return self.focused_char is not None and self.target is not None
        return self.target is not None  # follow: an EVE client with a known character is focused

    def refresh(self):
        self.target = self._target()
        self.chain = self.state.chain_for_char(self.target)
        if self.chain:
            self.state.zkill.request([r.system_id for r in self.chain.rows if not r.virtual])
        self._layout()
        if self.should_show():
            if not self.isVisible():
                self._restore_pos()
                self.show()
            self.update()
        else:
            self.hide()

    # ------------------------------------------------------------ metrics
    def _fonts(self):
        px = max(11, min(14, int(self.state.settings.overlay_text_px)))
        row = font("overlay-row", px)
        cur = QFont(row)
        cur.setWeight(QFont.Weight.ExtraBold)
        mono = font("overlay-mono", max(11, px - 1))
        hdr = font("overlay-header")
        for f in (row, cur, mono, hdr):
            f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
        return row, cur, mono, hdr

    def _row_h(self):
        return max(PX["size-overlay-row"], self.state.settings.overlay_text_px + 8)

    def _layout(self):
        rows = len(self.chain.rows) if self.chain else 2
        h = 26 + rows * self._row_h() + (20 if self.unlocked else 0) + 8
        if self.width() != int(PX["size-overlay-w"]) or self.height() != int(h):
            self.resize(int(PX["size-overlay-w"]), int(h))

    # ------------------------------------------------------------ paint
    def paintEvent(self, e):
        st = self.state
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        W, H = self.width(), self.height()
        frame = QRectF(1, 1, W - 2, H - 2)
        bg = color("overlay-bg")
        op = OP["op-overlay-unlocked"] if self.unlocked else max(OP["op-overlay-bg-min"], min(OP["op-overlay-bg-max"], st.settings.overlay_opacity))
        bg.setAlphaF(op)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(frame, PX["radius-3"], PX["radius-3"])
        if self.unlocked:
            pen = QPen(color("overlay-unlocked"), 2, Qt.PenStyle.DashLine)
            p.setPen(pen)
        else:
            p.setPen(QPen(color("overlay-frame"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(frame, PX["radius-3"], PX["radius-3"])

        row_f, cur_f, mono_f, hdr_f = self._fonts()
        ink, muted = color("overlay-ink"), color("overlay-ink-muted")
        x0 = PX["sp-8"]
        # header
        y = 6
        if self.unlocked:
            for i in range(3):
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(muted)
                p.drawEllipse(QPointF(W / 2 - 6 + i * 6, 4), 1.2, 1.2)
        x = x0
        name = None
        if self.target is not None:
            for cid, cname, _, _, slot in st.char_rows():
                if cid == self.target:
                    name = cname
                    x += P.draw_char_marker(p, x, y + 1, slot, 12) + 6
                    break
        hdr = f"FOLLOWING {name.upper()}" if name else "NO CHARACTER"
        fm = QFontMetricsF(hdr_f)
        P.draw_halo_text(p, x, y + fm.ascent() + 1, hdr, hdr_f, ink)
        right = W - x0
        mode = MODE_LABEL.get(self._mode(), "")
        if self.unlocked:
            right -= P.draw_chip(p, right - fm.horizontalAdvance("UNLOCKED") - 6, y + 1, "UNLOCKED",
                                 color("overlay-unlocked"), h=13) + 0
        elif mode:
            mw = fm.horizontalAdvance(mode)
            P.draw_halo_text(p, right - mw, y + fm.ascent() + 1, mode, hdr_f, muted)
        y = 26
        rh = self._row_h()
        if not self.chain or not self.chain.rows:
            loc = st.char_location(self.target) if self.target is not None else None
            s = st.uni.by_id(loc) if loc else None
            P.draw_halo_text(p, x0, y + rh * .7, "Not in a mapped chain", row_f, ink)
            sub = f"Last known: {s.name} ({s.cls})" if s else ("No EVE client focused" if self._mode() == "pinned_always" and self.focused_char is None else "Location unknown")
            P.draw_halo_text(p, x0, y + rh * 1.7, sub, mono_f, muted)
            self._footer(p, mono_f, muted)
            return
        for r in self.chain.rows:
            self._row(p, r, y, rh, row_f, cur_f, mono_f, ink, muted)
            y += rh
        self._footer(p, mono_f, muted)

    def _footer(self, p, f, muted):
        if self.unlocked:
            P.draw_halo_text(p, PX["sp-8"], self.height() - 9, "Drag to move · tray → Lock overlay", f, muted)

    def _row(self, p, r, y, rh, row_f, cur_f, mono_f, ink, muted):
        st = self.state
        W = self.width()
        s = st.uni.by_id(r.system_id)
        if not s:
            return
        if r.current:
            c = color("overlay-current")
            p.fillRect(QRectF(2, y, W - 4, rh), c)
        base = y + rh / 2 + QFontMetricsF(row_f).ascent() / 2 - 1
        x = PX["sp-8"]
        if r.current:
            P.draw_halo_text(p, x - 2, base, "▶", row_f, color("accent"))
        x += 10
        if r.prefix:
            x += P.draw_halo_text(p, x, base, r.prefix, mono_f, muted)
        f = cur_f if r.current else row_f
        if r.virtual:
            label = s.name
        else:
            label = st.namer.label(r.system_id)
        x += P.draw_halo_text(p, x, base, label, f, ink) + 6
        by = y + (rh - 16) / 2
        x += P.draw_class_badge(p, x, by, s.cls, s.security if s.kspace else None) + 6
        # right side: zKill + owner
        right = W - PX["sp-8"]
        ms = st.store.system(r.system_id) if not r.virtual else None
        if ms and ms.owner_ticker:
            ow = P.owner_width(ms.owner_ticker)
            P.draw_owner(p, right - ow, by, ms.owner_ticker, ms.owner_slot or 1, home=r.system_id == st.home_id)
            right -= ow + 6
        z = st.store.zkill(r.system_id) if st.settings.zkill_enabled else None
        if z:
            txt = f"{z.kills_1h}/{z.kills_24h}"
            tw = QFontMetricsF(mono_f).horizontalAdvance(txt)
            P.draw_halo_text(p, right - tw, base, txt, mono_f, ink if (z.kills_24h or 0) else muted)
            right -= tw + 4
            if z.last_kill_at and datetime.now(timezone.utc) - datetime.fromisoformat(z.last_kill_at) < timedelta(minutes=10):
                P.draw_recent(p, right - 7, y + rh / 2)
                right -= 18
        if r.virtual:
            entr = st.namer.label(r.entrance_id) if r.entrance_id else "?"
            j = f"{r.jumps_from}j"
            x += P.draw_halo_text(p, x, base, j, mono_f, color("accent")) + 4
            P.draw_halo_text(p, x, base, f"from {entr}", mono_f, ink)
            return
        if s.kspace:
            if label != s.name:
                P.draw_halo_text(p, x, base, f"({s.name})", mono_f, muted)
            return
        jn = s.name
        avail = right - x - (19 if s.effect else 0)
        if QFontMetricsF(mono_f).horizontalAdvance(jn) <= avail:
            x += P.draw_halo_text(p, x, base, jn, mono_f, muted) + 5
        if s.effect and x + 13 < right:
            P.draw_effect(p, x, y + (rh - 13) / 2, s.effect, 13)

    # ------------------------------------------------------------ drag when unlocked
    def mousePressEvent(self, e):
        if self.unlocked and e.button() == Qt.MouseButton.LeftButton:
            self._drag = e.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, e):
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, e):
        if self._drag is not None:
            self._drag = None
            self._save_pos()

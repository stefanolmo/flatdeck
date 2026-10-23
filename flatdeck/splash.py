"""Animated splash screen: a small board is drawn, then the isolation tool traces around its copper."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QElapsedTimer, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QPolygonF, QRadialGradient
from PySide6.QtWidgets import QApplication, QWidget

from . import geom, theme

W, H = 780, 440
CREDITS = "Stefano Ciurleo & Claude Code - 2026"
PX = 10.5                                  # pixels per mm of the artwork

PADS = [(4, 4), (4, 10), (4, 16), (12, 4), (12, 16), (14, 10), (20, 6), (20, 14), (26, 4), (26, 10), (26, 16)]
TRACES = [[(4, 4), (4, 16)], [(4, 10), (9, 10), (12, 13), (12, 16)], [(12, 4), (12, 8), (14, 10)],
          [(14, 10), (20, 10)], [(20, 6), (20, 14)], [(20, 6), (23, 4), (26, 4)], [(20, 14), (23, 16), (26, 16)],
          [(26, 4), (26, 16)]]


def _ease(x: float) -> float:
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


class SplashScreen(QWidget):
    finished = Signal()

    def __init__(self, version: str, duration_ms: int = 3000, parent=None):
        super().__init__(parent)
        self.version = version
        self.duration = duration_ms
        self.t = 0.0
        self.setWindowFlags(Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(W, H)
        self._clock = QElapsedTimer()
        self._tick = QTimer(self, interval=16)
        self._tick.timeout.connect(self._advance)
        self._build_art()

    # ------------------------------------------------------------------ artwork (computed with the real geometry code)
    def _build_art(self) -> None:
        copper = [geom.circle(x, y, 1.35) for x, y in PADS]
        for t in TRACES:
            copper += geom.stroke(np.array(t, dtype=float), 0.7)
        self.copper = geom.union(copper)
        loops = geom.order_nearest(geom.clean(geom.offset(self.copper, 0.85)), (0.0, 0.0))
        pts = [np.vstack([l, l[:1]]) for l in loops]
        self.loops = pts
        self.seg_len = [float(np.hypot(*np.diff(p, axis=0).T).sum()) for p in pts]
        self.total = sum(self.seg_len)

    def _map(self, a: np.ndarray, origin) -> list:
        return [QPointF(origin[0] + (x - 15) * PX, origin[1] - (y - 10) * PX) for x, y in a]

    def _traced(self, frac: float):
        """Loops (as point arrays) already cut plus the head position for a given fraction of the whole path."""
        target = frac * self.total
        done, head = [], None
        for p, ln in zip(self.loops, self.seg_len):
            if target >= ln:
                done.append(p)
                target -= ln
                continue
            seg = np.hypot(*np.diff(p, axis=0).T)
            cum = np.concatenate([[0.0], np.cumsum(seg)])
            k = int(np.searchsorted(cum, target))
            part = np.vstack([p[:k], [np.interp(target, cum, p[:, 0]), np.interp(target, cum, p[:, 1])]])
            done.append(part)
            head = part[-1]
            break
        if head is None and done:
            head = done[-1][-1]
        return done, head

    # ------------------------------------------------------------------ control
    def start(self) -> None:
        scr = QApplication.primaryScreen()
        if scr is not None:
            g = scr.availableGeometry()
            self.move(g.center().x() - W // 2, g.center().y() - H // 2)
        self.show()
        self.raise_()
        self._clock.start()
        self._tick.start()

    def _advance(self) -> None:
        self.t = float(self._clock.elapsed())
        if self.t >= self.duration:
            self._tick.stop()
            self.close()
            self.finished.emit()
            return
        self.update()

    def set_time(self, ms: float) -> None:
        """Freeze the animation at `ms` (used for screenshots and tests)."""
        self.t = ms
        self.update()

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        k = self.t / max(self.duration, 1)
        fade_in = _ease(self.t / 350.0)
        fade_out = 1.0 - _ease((self.t - (self.duration - 300)) / 300.0)
        p.setOpacity(max(0.0, min(fade_in, fade_out)))
        self._paint_card(p)
        self._paint_board(p, k)
        self._paint_text(p)
        self._paint_progress(p, k)
        p.end()

    def _paint_card(self, p: QPainter) -> None:
        rect = QRectF(8, 8, W - 16, H - 16)
        path = QPainterPath()
        path.addRoundedRect(rect, 22, 22)
        glow = QRadialGradient(QPointF(W * 0.68, H * 0.45), W * 0.62)
        glow.setColorAt(0, QColor(45, 212, 191, 38))
        glow.setColorAt(1, QColor(45, 212, 191, 0))
        g = QLinearGradient(0, 0, 0, H)
        g.setColorAt(0, QColor("#141b24"))
        g.setColorAt(1, QColor("#0a0e13"))
        p.fillPath(path, g)
        p.fillPath(path, QBrush(glow))
        p.setPen(QPen(QColor(theme.BORDER), 1.5))
        p.drawPath(path)
        p.setClipPath(path)

    def _paint_board(self, p: QPainter, k: float) -> None:
        origin = (W * 0.67, H * 0.50)
        # substrate
        bw, bh = 32 * PX, 22 * PX
        board = QRectF(origin[0] - bw / 2, origin[1] - bh / 2, bw, bh)
        a_board = _ease(k / 0.12)
        p.setPen(QPen(QColor(255, 255, 255, int(40 * a_board)), 1.2))
        p.setBrush(QColor(20, 38, 34, int(235 * a_board)))
        p.drawRoundedRect(board, 10, 10)
        # copper fades in, then the tool cuts around it
        a_cu = _ease((k - 0.05) / 0.2)
        cu = QColor(theme.LAYER_COLORS[0])
        cu.setAlpha(int(215 * a_cu))
        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        for a in self.copper:
            path.addPolygon(QPolygonF(self._map(a, origin)))
            path.closeSubpath()
        p.setPen(Qt.NoPen)
        p.setBrush(cu)
        p.drawPath(path)
        frac = _ease((k - 0.20) / 0.62)
        done, head = self._traced(frac)
        pen = QPen(QColor(theme.ACCENT), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for d in done:
            p.drawPolyline(QPolygonF(self._map(d, origin)))
        # drill holes appear at the end
        a_dr = _ease((k - 0.80) / 0.12)
        if a_dr > 0:
            p.setPen(QPen(QColor(255, 255, 255, int(200 * a_dr)), 1.2))
            p.setBrush(QColor(8, 12, 16, int(255 * a_dr)))
            for x, y in PADS:
                c = self._map(np.array([[x, y]]), origin)[0]
                p.drawEllipse(c, 0.55 * PX, 0.55 * PX)
        # the tool
        if head is not None and frac < 1.0:
            tip = self._map(np.array([head]), origin)[0]
            halo = QRadialGradient(tip, 24)
            halo.setColorAt(0, QColor(45, 212, 191, 170))
            halo.setColorAt(1, QColor(45, 212, 191, 0))
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(halo))
            p.drawEllipse(tip, 24, 24)
            p.setBrush(QColor("#e5e7eb"))
            p.drawEllipse(tip, 6.5, 6.5)
            p.setBrush(QColor(theme.ACCENT))
            p.drawEllipse(tip, 3.2, 3.2)

    def _paint_text(self, p: QPainter) -> None:
        x = 58
        f = QFont("sans", 46, QFont.Black)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
        p.setFont(f)
        p.setPen(QColor("#f1f5f9"))
        p.drawText(QPointF(x, H * 0.47), "Flat")
        w = p.fontMetrics().horizontalAdvance("Flat")
        p.setPen(QColor(theme.ACCENT))
        p.drawText(QPointF(x + w, H * 0.47), "Deck")
        p.setPen(Qt.NoPen)
        g = QLinearGradient(x, 0, x + 230, 0)
        g.setColorAt(0, QColor(theme.ACCENT))
        g.setColorAt(1, QColor(45, 212, 191, 0))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(x, H * 0.47 + 16, 230, 3), 1.5, 1.5)
        p.setFont(QFont("sans", 11))
        p.setPen(QColor(theme.MUTED))
        p.drawText(QPointF(x + 2, H * 0.47 + 50), CREDITS)
        p.setFont(QFont("sans", 10, QFont.DemiBold))
        p.setPen(QColor(theme.ACCENT))
        p.drawText(QPointF(x + 2, H * 0.47 + 76), f"Version {self.version}")
        p.setFont(QFont("sans", 8))
        p.setPen(QColor(255, 255, 255, 70))
        p.drawText(QPointF(x + 2, H - 38), "Gerber / Excellon → GRBL G-code")

    def _paint_progress(self, p: QPainter, k: float) -> None:
        x0, x1, y = 58.0, W - 58.0, H - 24.0
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(QRectF(x0, y, x1 - x0, 3), 1.5, 1.5)
        g = QLinearGradient(x0, 0, x1, 0)
        g.setColorAt(0, QColor(theme.ACCENT))
        g.setColorAt(1, QColor("#38bdf8"))
        p.setBrush(QBrush(g))
        p.drawRoundedRect(QRectF(x0, y, (x1 - x0) * min(k, 1.0), 3), 1.5, 1.5)

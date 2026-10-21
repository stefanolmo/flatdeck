"""2D board viewer: QPainter only (pan with drag, zoom with the wheel, double click = fit)."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from . import theme


def _poly_path(paths, closed=True) -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.OddEvenFill)
    for a in paths:
        if len(a) < 2:
            continue
        path.addPolygon(QPolygonF([QPointF(float(x), float(y)) for x, y in a]))
        if closed:
            path.closeSubpath()
    return path


class BoardView(QWidget):
    cursorMoved = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 320)
        self.setMouseTracking(True)
        self.project = None
        self.origin = (0.0, 0.0)
        self.selected = None
        self.scale = 8.0                        # px per mm
        self.centre = np.zeros(2)
        self.show_tool_width = True
        self._cache: dict = {}
        self._drag = None
        self.empty_message = "Apri un file Gerber o Excellon\n(oppure trascinalo qui)"

    # ----- data
    def set_project(self, project) -> None:
        self.project = project
        self.invalidate()

    def invalidate(self) -> None:
        self._cache.clear()
        self.update()

    def fit(self) -> None:
        b = self.project.board_bounds() if self.project else None
        if b is None:
            return
        w, h = max(b[2] - b[0], 1e-3), max(b[3] - b[1], 1e-3)
        self.scale = 0.9 * min(self.width() / w, self.height() / h)
        self.centre = np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2])
        self.update()

    # ----- cache
    def _cached(self, key, build):
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    # ----- painting
    def _apply(self, p: QPainter) -> None:
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(self.scale, -self.scale)
        p.translate(-self.centre[0], -self.centre[1])

    def to_world(self, sx: float, sy: float):
        return (self.centre[0] + (sx - self.width() / 2) / self.scale, self.centre[1] - (sy - self.height() / 2) / self.scale)

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#0b0f14"))
        if self.project is None or not self.project.layers:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, self.empty_message)
            p.end()
            return
        self._grid(p)
        p.save()
        self._apply(p)
        k = 0
        for lay in self.project.layers:
            if not lay.visible:
                continue
            sel = lay is self.selected
            if lay.kind == "copper":
                col = QColor(theme.LAYER_COLORS[k % len(theme.LAYER_COLORS)])
                k += 1
                col.setAlpha(210 if sel else 150)
                path = self._cached(("cu", id(lay), id(lay.gerber.polys)), lambda l=lay: _poly_path(l.gerber.polys))
                p.setPen(Qt.NoPen)
                p.setBrush(col)
                p.drawPath(path)
            elif lay.kind in ("silk", "mask"):
                path = self._cached(("sm", id(lay), id(lay.gerber.polys)), lambda l=lay: _poly_path(l.gerber.polys))
                p.setPen(Qt.NoPen)
                p.setBrush(QColor(241, 245, 249, 150) if lay.kind == "silk" else QColor(52, 211, 153, 45))
                p.drawPath(path)
            elif lay.kind == "outline":
                path = self._cached(("ol", id(lay), id(lay.gerber.strokes)), lambda l=lay: _poly_path([s for _, s in l.gerber.strokes], False))
                pen = QPen(QColor("#e5e7eb"), 1.4 if sel else 1.0)
                pen.setCosmetic(True)
                p.setPen(pen)
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
            elif lay.kind == "drill":
                self._draw_drill(p, lay)
            elif lay.kind == "job":
                self._draw_job(p, lay, sel)
        # origin
        ox, oy = self.project.origin()
        pen = QPen(QColor(theme.DANGER), 1.5)
        pen.setCosmetic(True)
        p.setPen(pen)
        s = 12 / self.scale
        p.drawLine(QPointF(ox - s, oy), QPointF(ox + s, oy))
        pen = QPen(QColor(theme.OK), 1.5)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.drawLine(QPointF(ox, oy - s), QPointF(ox, oy + s))
        p.restore()
        p.end()

    def _draw_drill(self, p: QPainter, lay) -> None:
        for i, t in enumerate(sorted(lay.drill.tools.values(), key=lambda t: t.number)):
            col = QColor(theme.DRILL_COLORS[i % len(theme.DRILL_COLORS)])
            pen = QPen(col, 1.2)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(QColor(11, 15, 20, 230))
            r = t.dia / 2
            for x, y in t.holes:
                p.drawEllipse(QPointF(float(x), float(y)), r, r)
            for s in t.slots:
                wide = QPen(col, t.dia, Qt.SolidLine, Qt.RoundCap)
                wide.setColor(QColor(col.red(), col.green(), col.blue(), 120))
                p.setPen(wide)
                p.drawPolyline(QPolygonF([QPointF(float(x), float(y)) for x, y in s]))

    def _draw_job(self, p: QPainter, lay, sel: bool) -> None:
        job = lay.job
        col = QColor(theme.JOB_COLORS.get(job.kind, theme.ACCENT))
        if job.paths:
            path = self._cached(("job", id(lay)), lambda: _poly_path([a for a, c in job.paths if c]))
            open_path = self._cached(("jobo", id(lay)), lambda: _poly_path([a for a, c in job.paths if not c], False))
            if self.show_tool_width and job.tool_dia > 0:
                wide = QColor(col.red(), col.green(), col.blue(), 70 if sel else 40)
                for pa in (path, open_path):
                    p.setPen(QPen(wide, job.tool_dia, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
                    p.setBrush(Qt.NoBrush)
                    p.drawPath(pa)
            pen = QPen(col, 1.8 if sel else 1.2)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.drawPath(open_path)
        if job.holes:
            pen = QPen(col, 1.5)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            for x, y, d in job.holes:
                r = d / 2
                p.drawEllipse(QPointF(x, y), r, r)
                p.drawLine(QPointF(x - r, y), QPointF(x + r, y))
                p.drawLine(QPointF(x, y - r), QPointF(x, y + r))

    def _grid(self, p: QPainter) -> None:
        # adaptive grid: 1, 2, 5 x 10^n mm with at least ~60 px between lines
        raw = 60 / self.scale
        mag = 10 ** math.floor(math.log10(raw))
        step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
        x0, y1 = self.to_world(0, 0)
        x1, y0 = self.to_world(self.width(), self.height())
        ox, oy = self.project.origin()
        p.setPen(QPen(QColor(255, 255, 255, 14), 1))
        p.setFont(QFont("sans", 7))
        gx = math.ceil((x0 - ox) / step) * step + ox
        while gx <= x1:
            sx = self.width() / 2 + (gx - self.centre[0]) * self.scale
            p.setPen(QColor(255, 255, 255, 14))
            p.drawLine(QPointF(sx, 0), QPointF(sx, self.height()))
            p.setPen(QColor(255, 255, 255, 70))
            p.drawText(QPointF(sx + 3, self.height() - 4), f"{gx - ox:g}")
            gx += step
        gy = math.ceil((y0 - oy) / step) * step + oy
        while gy <= y1:
            sy = self.height() / 2 - (gy - self.centre[1]) * self.scale
            p.setPen(QColor(255, 255, 255, 14))
            p.drawLine(QPointF(0, sy), QPointF(self.width(), sy))
            p.setPen(QColor(255, 255, 255, 70))
            p.drawText(QPointF(4, sy - 3), f"{gy - oy:g}")
            gy += step

    # ----- interaction
    def wheelEvent(self, ev) -> None:
        f = 1.2 if ev.angleDelta().y() > 0 else 1 / 1.2
        pos = ev.position()
        wx, wy = self.to_world(pos.x(), pos.y())
        self.scale = min(max(self.scale * f, 0.2), 5000)
        nx, ny = self.to_world(pos.x(), pos.y())
        self.centre += (wx - nx, wy - ny)
        self.update()

    def mousePressEvent(self, ev) -> None:
        if ev.button() in (Qt.LeftButton, Qt.MiddleButton, Qt.RightButton):
            self._drag = (ev.position(), self.centre.copy())

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position()
        if self._drag is not None:
            d = pos - self._drag[0]
            self.centre = self._drag[1] - np.array([d.x(), -d.y()]) / self.scale
            self.update()
        if self.project is not None:
            wx, wy = self.to_world(pos.x(), pos.y())
            ox, oy = self.project.origin()
            self.cursorMoved.emit(wx - ox, wy - oy)

    def mouseReleaseEvent(self, _ev) -> None:
        self._drag = None

    def mouseDoubleClickEvent(self, _ev) -> None:
        self.fit()

    def resizeEvent(self, _ev) -> None:
        self.update()

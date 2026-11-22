"""2D board viewer: QPainter only (pan with drag, zoom with the wheel, double click = fit).

A board with many toolpaths is expensive to draw (tens of thousands of points, antialiased, with wide translucent bands), so the
scene (grid, layers, jobs) is drawn into an off-screen picture that is only redrawn when something it shows changes. While you drag or
zoom, that picture is just moved/scaled (instant, however complex the board). When you stop:
  1. a fast picture is drawn at once (without antialiasing if the board is heavy), then
  2. a good one is drawn in small steps while the program is idle, and replaces it when it is ready (any new move cancels it).
The paths are simplified to what the zoom can show, and the wide bands are drawn once per job (so overlaps do not add up).
Slots, the slot being drawn and the origin mark are cheap and drawn live on top.
"""
from __future__ import annotations

import math
import time

import numpy as np
from PySide6.QtCore import QPointF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QApplication, QWidget

from . import geom, theme

CHUNK_POINTS = 1500          # a long path is drawn in pieces of about this many points (so a step of the progressive drawing stays short)
HEAVY_POINTS = 5000          # above this many points in the visible jobs the first picture is drawn without antialiasing
SETTLE_MS = 120              # no mouse/wheel activity for this long = the move is over
REFINE_IDLE_MS = 150         # then wait this long before starting the good picture
STEP_MS = 12                 # a step of the progressive drawing takes about this long
SIMPLIFY_PX = 0.6            # paths are simplified to this many screen pixels
BG = "#0b0f14"


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
    picked = Signal(float, float)           # click in pick mode: world coordinates
    drawPoint = Signal(float, float)        # click in slot-drawing mode
    drawFinished = Signal()                 # double click / Enter in slot-drawing mode
    drawCancelled = Signal()                # Escape in slot-drawing mode
    measured = Signal(float, float)         # measuring: (dx, dy) in mm between the two points

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
        self.pick_layer = None                  # when set, a click (without drag) emits `picked`
        self.draw_mode = False                  # slot drawing: clicks emit `drawPoint`
        self.draft: list = []                   # points of the slot being drawn
        self.slot_width = 2.0                   # width of the slot preview band
        self.slot_sel = -1
        self.measure_mode = False               # measuring: two clicks give the distance between the points
        self.measure_pts: list = []
        self._cursor = None
        self._cache: dict = {}                  # QPainterPaths of the layers
        self._lod: dict = {}                    # (id(job), level) -> (job, closed chunks, open chunks)
        self._scene: dict | None = None         # the off-screen picture and what it was drawn for
        self._refine: dict | None = None        # the good picture being drawn
        self._bands: list = []                  # band images being drawn (their painters must be ended before the images are freed)
        self._interacting = False
        self._settle = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self._settle.timeout.connect(self._end_interaction)
        self._idle = QTimer(self, singleShot=True, interval=REFINE_IDLE_MS)
        self._idle.timeout.connect(self._start_refine)
        self.renders = 0                        # pictures drawn so far (for the tests)
        self._drag = None
        if QApplication.instance() is not None:
            QApplication.instance().aboutToQuit.connect(self._cancel_refine)    # do not leave a painter open at exit
        self.empty_message = "Open a Gerber or Excellon file\n(or drag it here)"

    # ----- data
    def set_project(self, project) -> None:
        self.project = project
        self.invalidate()

    def invalidate(self) -> None:
        self._cache.clear()
        self._lod.clear()
        self._cancel_refine()
        self._scene = None
        self.update()

    def fit(self) -> None:
        b = self.project.board_bounds() if self.project else None
        if b is None:
            return
        w, h = max(b[2] - b[0], 1e-3), max(b[3] - b[1], 1e-3)
        self.scale = 0.9 * min(self.width() / w, self.height() / h)
        self.centre = np.array([(b[0] + b[2]) / 2, (b[1] + b[3]) / 2])
        self._cancel_refine()
        self.update()

    def _cached(self, key, build):
        if key not in self._cache:
            self._cache[key] = build()
        return self._cache[key]

    # ----- geometry of the view
    def _apply(self, p: QPainter) -> None:
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(self.scale, -self.scale)
        p.translate(-self.centre[0], -self.centre[1])

    def to_world(self, sx: float, sy: float):
        return (self.centre[0] + (sx - self.width() / 2) / self.scale, self.centre[1] - (sy - self.height() / 2) / self.scale)

    def _view_state(self) -> tuple:
        return (float(self.centre[0]), float(self.centre[1]), float(self.scale), self.width(), self.height())

    def _signature(self) -> tuple:
        """Everything the off-screen picture shows, in a form that is cheap to compare."""
        pr = self.project
        items = []
        for l in pr.layers:
            items.append((id(l), l.kind, l.visible, id(l.gerber.polys) if l.gerber is not None else 0,
                          id(l.gerber.strokes) if l.gerber is not None else 0, tuple(l.hv_points) if l.hv_points else (),
                          id(l.job) if l.job is not None else 0, l.opacity,
                          tuple(id(t.holes) for t in l.drill.tools.values()) if l.drill is not None else ()))
        return (tuple(items), id(self.selected), self.show_tool_width, tuple(pr.origin()), self.devicePixelRatioF())

    # ----- the off-screen picture
    def _margin(self) -> int:
        return max(self.width(), self.height()) // 3          # extra picture around the view, so a drag does not show empty edges at once

    def _new_pixmap(self) -> QPixmap:
        dpr, m = self.devicePixelRatioF(), self._margin()
        pm = QPixmap(int((self.width() + 2 * m) * dpr), int((self.height() + 2 * m) * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QColor(BG))
        return pm

    def _points_in_view(self) -> int:
        return sum(len(a) for l in self.project.layers if l.kind == "job" and l.visible for a, _ in l.job.paths)

    def _level(self) -> int:
        tol = SIMPLIFY_PX / self.scale
        return 0 if tol < 0.01 else min(int(math.ceil(math.log2(tol / 0.01))), 8)

    def _job_chunks(self, lay, level: int):
        key = (id(lay.job), level)
        hit = self._lod.get(key)
        if hit is None:
            tol = 0.01 * 2 ** level if level else 0.0
            groups = {True: [[]], False: [[]]}
            sizes = {True: 0, False: 0}
            for a, closed in lay.job.paths:
                a = geom.simplify(a, tol) if tol else a
                if sizes[closed] + len(a) > CHUNK_POINTS and groups[closed][-1]:
                    groups[closed].append([])
                    sizes[closed] = 0
                groups[closed][-1].append(a)
                sizes[closed] += len(a)
            hit = (lay.job, [_poly_path(g, True) for g in groups[True] if g], [_poly_path(g, False) for g in groups[False] if g])
            self._lod[key] = hit
        return hit[1], hit[2]

    def _scene_ops(self) -> list:
        """The drawing of the scene as a list of small steps (each takes a painter), so it can be run at once or little by little."""
        ops = [self._grid, self._apply]
        k = 0
        for lay in self.project.layers:
            if not lay.visible:
                continue
            sel = lay is self.selected
            ops.append(lambda p, o=lay.opacity: p.setOpacity(o))
            if lay.kind == "copper":
                col = QColor(theme.LAYER_COLORS[k % len(theme.LAYER_COLORS)])
                k += 1
                col.setAlpha(210 if sel else 150)
                path = self._cached(("cu", id(lay), id(lay.gerber.polys)), lambda l=lay: _poly_path(l.gerber.polys))
                ops.append(lambda p, path=path, col=col: self._fill(p, path, col))
                if lay.hv_points:
                    marked = self.project.hv_indices(lay)
                    hv = self._cached(("hv", id(lay), id(lay.gerber.polys), tuple(sorted(marked))),
                                      lambda l=lay, s=marked: _poly_path([q for i, isl in enumerate(l.islands()) if i in s for q in isl.paths()]))
                    ops.append(lambda p, hv=hv: self._fill(p, hv, QColor(248, 113, 113, 150), QColor(theme.DANGER), 1.6))
            elif lay.kind in ("silk", "mask"):
                path = self._cached(("sm", id(lay), id(lay.gerber.polys)), lambda l=lay: _poly_path(l.gerber.polys))
                ops.append(lambda p, path=path, kind=lay.kind: self._fill(
                    p, path, QColor(241, 245, 249, 150) if kind == "silk" else QColor(52, 211, 153, 45)))
            elif lay.kind == "outline":
                path = self._cached(("ol", id(lay), id(lay.gerber.strokes)), lambda l=lay: _poly_path([s for _, s in l.gerber.strokes], False))
                ops.append(lambda p, path=path, sel=sel: self._line(p, path, QColor("#e5e7eb"), 1.4 if sel else 1.0))
            elif lay.kind == "drill":
                ops.append(lambda p, lay=lay: self._draw_drill(p, lay))
            elif lay.kind == "job":
                ops += self._job_ops(lay, sel)
        return ops

    def _fill(self, p, path, brush: QColor, pen: QColor | None = None, width: float = 1.0) -> None:
        if pen is None:
            p.setPen(Qt.NoPen)
        else:
            q = QPen(pen, width)
            q.setCosmetic(True)
            p.setPen(q)
        p.setBrush(brush)
        p.drawPath(path)

    def _line(self, p, path, color: QColor, width: float) -> None:
        q = QPen(color, width)
        q.setCosmetic(True)
        p.setPen(q)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    def _job_ops(self, lay, sel: bool) -> list:
        job = lay.job
        col = QColor(theme.JOB_COLORS.get(job.kind, theme.ACCENT))
        ops: list = []
        if job.paths:
            closed, opened = self._job_chunks(lay, self._level())
            chunks = closed + opened
            if self.show_tool_width and job.tool_dia > 0 and chunks:
                wide = QColor(col.red(), col.green(), col.blue(), 70 if sel else 40)
                ctx: dict = {}
                ops.append(lambda p, ctx=ctx: self._band_begin(p, ctx))
                ops += [lambda p, pa=pa, ctx=ctx, c=col, w=job.tool_dia: self._band_chunk(ctx, pa, c, w) for pa in chunks]
                ops.append(lambda p, ctx=ctx, wide=wide: self._band_end(p, ctx, wide))
            ops += [lambda p, pa=pa, c=col, s=sel: self._line(p, pa, c, 1.8 if s else 1.2) for pa in chunks]
        if job.marks:
            marks = self._cached(("marks", id(lay)), lambda: _poly_path(job.marks))
            ops.append(lambda p, marks=marks: self._fill(p, marks, QColor(248, 113, 113, 190), QColor(theme.DANGER), 1.6))
        if job.holes:
            ops.append(lambda p, job=job, col=col: self._draw_holes(p, job, col))
        return ops

    # The bands of one job are drawn opaque into a transparent image and that image is laid over the picture with the band's
    # transparency: where two bands overlap the colour stays the same (they used to add up when drawn one by one).
    def _band_begin(self, p: QPainter, ctx: dict) -> None:
        dev = p.device()
        img = QImage(dev.width(), dev.height(), QImage.Format_ARGB32_Premultiplied)
        img.setDevicePixelRatio(dev.devicePixelRatioF())
        img.fill(0)
        q = QPainter(img)
        q.setRenderHint(QPainter.Antialiasing, bool(p.renderHints() & QPainter.Antialiasing))
        q.setTransform(p.transform())
        ctx["img"], ctx["painter"] = img, q
        self._bands.append(ctx)

    def _band_chunk(self, ctx: dict, path, col: QColor, width: float) -> None:
        pen = QPen(QColor(col.red(), col.green(), col.blue(), 255), max(width * self.scale * self.devicePixelRatioF(), 1.0), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
        pen.setCosmetic(True)
        ctx["painter"].setPen(pen)
        ctx["painter"].setBrush(Qt.NoBrush)
        ctx["painter"].drawPath(path)

    def _band_end(self, p: QPainter, ctx: dict, wide: QColor) -> None:
        ctx["painter"].end()
        if ctx in self._bands:
            self._bands.remove(ctx)
        p.save()
        p.resetTransform()
        p.setOpacity(p.opacity() * wide.alphaF())
        p.drawImage(0, 0, ctx["img"])
        p.restore()
        ctx.clear()

    def _draw_holes(self, p: QPainter, job, col: QColor) -> None:
        pen = QPen(col, 1.5)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        for x, y, d in job.holes:
            r = d / 2
            p.drawEllipse(QPointF(x, y), r, r)
            p.drawLine(QPointF(x - r, y), QPointF(x + r, y))
            p.drawLine(QPointF(x, y - r), QPointF(x, y + r))

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

    def _grid(self, p: QPainter) -> None:
        # adaptive grid: 1, 2, 5 x 10^n mm with at least ~60 px between lines
        raw = 60 / self.scale
        mag = 10 ** math.floor(math.log10(raw))
        step = next(m * mag for m in (1, 2, 5, 10) if m * mag >= raw)
        x0, y1 = self.to_world(0, 0)
        x1, y0 = self.to_world(self.width(), self.height())
        ox, oy = self.project.origin()
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

    def _render_scene(self, content: tuple, view: tuple) -> None:
        """Draw the whole scene now. Without antialiasing when it is heavy: a good picture follows when the program is idle."""
        aa = self._points_in_view() <= HEAVY_POINTS
        pm = self._new_pixmap()
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, aa)
        m = self._margin()
        p.translate(m, m)
        for op in self._scene_ops():
            op(p)
        p.end()
        self.renders += 1
        self._scene = {"pm": pm, "content": content, "view": view, "m": m, "aa": aa}

    # ----- the good picture, drawn in small steps
    def _start_refine(self) -> None:
        sc = self._scene
        if (sc is None or sc["aa"] or self._interacting or self.project is None or not self.isVisible()
                or sc["content"] != self._signature() or sc["view"] != self._view_state()):
            return
        self._cancel_refine()
        pm = self._new_pixmap()
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.translate(sc["m"], sc["m"])
        job = {"painter": p, "pm": pm, "ops": self._scene_ops(), "content": sc["content"], "view": sc["view"], "m": sc["m"]}
        self._refine = job
        QTimer.singleShot(0, lambda: self._refine_step(job))

    def _refine_step(self, job: dict) -> None:
        if self._refine is not job:
            return
        t0 = time.perf_counter()
        while job["ops"] and time.perf_counter() - t0 < STEP_MS / 1000:
            job["ops"].pop(0)(job["painter"])
        if job["ops"]:
            QTimer.singleShot(0, lambda: self._refine_step(job))
            return
        job["painter"].end()
        self._refine = None
        self.renders += 1
        self._scene = {"pm": job["pm"], "content": job["content"], "view": job["view"], "m": job["m"], "aa": True}
        self.update()

    def _cancel_refine(self) -> None:
        self._idle.stop()
        if self._refine is not None:
            for ctx in self._bands:                       # a step may have been cut off in the middle of a job's bands
                ctx["painter"].end()
            self._bands.clear()
            self._refine["painter"].end()
            self._refine = None

    # ----- painting
    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(BG))
        if self.project is None or not self.project.layers:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, self.empty_message)
            p.end()
            return
        self._paint_scene(p)
        p.setRenderHint(QPainter.Antialiasing)
        p.save()
        self._apply(p)
        self._draw_slots(p)
        self._draw_origin(p)
        p.restore()
        self._draw_measure(p)
        p.end()

    def _paint_scene(self, p: QPainter) -> None:
        view, content, sc = self._view_state(), self._signature(), self._scene
        if (sc is None or sc["content"] != content or sc["view"][3:] != view[3:] or (sc["view"] != view and not self._interacting)):
            self._cancel_refine()
            self._render_scene(content, view)
            sc = self._scene
        m = sc["m"]
        if sc["view"] == view:
            p.drawPixmap(-m, -m, sc["pm"])
            if not sc["aa"] and not self._interacting and self._refine is None:
                self._idle.start()
        else:                                              # being dragged / zoomed: the picture as it was, moved and scaled
            cx0, cy0, s0 = sc["view"][:3]
            cx, cy, s = view[:3]
            k, w, h = s / s0, self.width(), self.height()
            p.save()
            p.translate(w / 2 + (cx0 - cx) * s, h / 2 - (cy0 - cy) * s)
            p.scale(k, k)
            p.translate(-w / 2, -h / 2)
            p.drawPixmap(-m, -m, sc["pm"])
            p.restore()

    def _draw_origin(self, p: QPainter) -> None:
        ox, oy = self.project.origin()
        s = 12 / self.scale
        pen = QPen(QColor(theme.DANGER), 1.5)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.drawLine(QPointF(ox - s, oy), QPointF(ox + s, oy))
        pen = QPen(QColor(theme.OK), 1.5)
        pen.setCosmetic(True)
        p.setPen(pen)
        p.drawLine(QPointF(ox, oy - s), QPointF(ox, oy + s))

    def _measure_end(self):
        """The second point of the measurement: the click, or the cursor while it is still being chosen."""
        if len(self.measure_pts) >= 2:
            return self.measure_pts[1]
        return self._cursor if len(self.measure_pts) == 1 else None

    def _draw_measure(self, p: QPainter) -> None:
        if not self.measure_mode or not self.measure_pts:
            return
        b = self._measure_end()
        sx = lambda q: QPointF(self.width() / 2 + (q[0] - self.centre[0]) * self.scale, self.height() / 2 - (q[1] - self.centre[1]) * self.scale)
        a = self.measure_pts[0]
        pen = QPen(QColor("#fbbf24"), 1.6)
        p.setPen(pen)
        p.setBrush(QColor("#fbbf24"))
        pa = sx(a)
        p.drawEllipse(pa, 3.5, 3.5)
        if b is None:
            return
        pb = sx(b)
        p.drawLine(pa, pb)
        p.drawEllipse(pb, 3.5, 3.5)
        dx, dy = b[0] - a[0], b[1] - a[1]
        text = f"{math.hypot(dx, dy):.3f} mm    ΔX {dx:+.3f}   ΔY {dy:+.3f}"
        p.setFont(QFont("sans", 9))
        r = p.fontMetrics().boundingRect(text).adjusted(-6, -3, 6, 3)
        mid = (pa + pb) / 2
        r.moveCenter((mid + QPointF(0, -16)).toPoint())
        r.moveLeft(min(max(r.left(), 4), max(self.width() - r.width() - 4, 4)))
        r.moveTop(min(max(r.top(), 4), max(self.height() - r.height() - 4, 4)))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(11, 15, 20, 220))
        p.drawRoundedRect(r, 4, 4)
        p.setPen(QColor("#fbbf24"))
        p.drawText(r, Qt.AlignCenter, text)

    def set_measure_mode(self, on: bool) -> None:
        self.measure_mode = on
        self.measure_pts = []
        self._cursor = None
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        if on:
            self.setFocus()
        self.update()

    def _draw_slots(self, p: QPainter) -> None:
        col = QColor(theme.JOB_COLORS["slots"])
        for i, s in enumerate(self.project.slots):
            poly = QPolygonF([QPointF(float(x), float(y)) for x, y in s.pts])
            sel = i == self.slot_sel
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 90 if sel else 50), self.slot_width, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setBrush(Qt.NoBrush)
            p.drawPolyline(poly)
            pen = QPen(col, 2.2 if sel else 1.4, Qt.DashLine if s.auto else Qt.SolidLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            p.drawPolyline(poly)
        if self.draft:
            pen = QPen(QColor("#f1f5f9"), 1.6, Qt.DashLine)
            pen.setCosmetic(True)
            p.setPen(pen)
            pts = [QPointF(float(x), float(y)) for x, y in self.draft]
            if self._cursor is not None:
                pts.append(QPointF(*self._cursor))
            p.drawPolyline(QPolygonF(pts))
            p.setBrush(QColor("#f1f5f9"))
            for q in self.draft:
                p.drawEllipse(QPointF(float(q[0]), float(q[1])), 3 / self.scale, 3 / self.scale)

    # ----- interaction
    def _begin_interaction(self) -> None:
        self._interacting = True
        self._cancel_refine()
        self._settle.start()

    def _end_interaction(self) -> None:
        self._interacting = False
        self.update()

    def wheelEvent(self, ev) -> None:
        self._begin_interaction()
        f = 1.2 if ev.angleDelta().y() > 0 else 1 / 1.2
        pos = ev.position()
        wx, wy = self.to_world(pos.x(), pos.y())
        self.scale = min(max(self.scale * f, 0.2), 5000)
        nx, ny = self.to_world(pos.x(), pos.y())
        self.centre += (wx - nx, wy - ny)
        self.update()

    def set_pick_layer(self, layer) -> None:
        self.pick_layer = layer
        self.setCursor(Qt.CrossCursor if layer is not None else Qt.ArrowCursor)
        self.update()

    def set_draw_mode(self, on: bool) -> None:
        self.draw_mode = on
        if not on:
            self.draft = []
            self._cursor = None
        self.setCursor(Qt.CrossCursor if on else Qt.ArrowCursor)
        if on:
            self.setFocus()
        self.update()

    def keyPressEvent(self, ev) -> None:
        if self.measure_mode and ev.key() == Qt.Key_Escape:
            self.measure_pts = []
            self.update()
        elif self.draw_mode and ev.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.drawFinished.emit()
        elif self.draw_mode and ev.key() == Qt.Key_Escape:
            self.drawCancelled.emit()
        else:
            super().keyPressEvent(ev)

    def mousePressEvent(self, ev) -> None:
        if ev.button() in (Qt.LeftButton, Qt.MiddleButton, Qt.RightButton):
            self._drag = (ev.position(), self.centre.copy())

    def mouseMoveEvent(self, ev) -> None:
        pos = ev.position()
        if self._drag is not None:
            self._begin_interaction()
            d = pos - self._drag[0]
            self.centre = self._drag[1] - np.array([d.x(), -d.y()]) / self.scale
            self.update()
        if self.measure_mode and len(self.measure_pts) == 1:
            self._cursor = self.to_world(pos.x(), pos.y())
            self.update()
        if self.draw_mode and self.draft:
            self._cursor = self.to_world(pos.x(), pos.y())
            self.update()
        if self.project is not None:
            wx, wy = self.to_world(pos.x(), pos.y())
            ox, oy = self.project.origin()
            self.cursorMoved.emit(wx - ox, wy - oy)

    def mouseReleaseEvent(self, ev) -> None:
        click = (self._drag is not None and ev.button() == Qt.LeftButton and (ev.position() - self._drag[0]).manhattanLength() < 4)
        if click and self.measure_mode:
            q = self.to_world(ev.position().x(), ev.position().y())
            self.measure_pts = [q] if len(self.measure_pts) != 1 else self.measure_pts + [q]
            if len(self.measure_pts) == 2:
                a = self.measure_pts[0]
                self.measured.emit(q[0] - a[0], q[1] - a[1])
            self.update()
        elif click and self.pick_layer is not None:
            self.picked.emit(*self.to_world(ev.position().x(), ev.position().y()))
        elif click and self.draw_mode:
            self.drawPoint.emit(*self.to_world(ev.position().x(), ev.position().y()))
        self._drag = None

    def mouseDoubleClickEvent(self, _ev) -> None:
        if self.draw_mode:                                # the 2nd press of a double click arrives here, not in mousePressEvent
            self.drawFinished.emit()
        else:
            self.fit()

    def resizeEvent(self, _ev) -> None:
        self.update()

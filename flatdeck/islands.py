"""Connected copper islands (an outer contour with the holes inside it) of a Clipper union result."""
from __future__ import annotations

import numpy as np
import pyclipper

from . import geom


class Island:
    def __init__(self, outer: np.ndarray, holes: list):
        self.outer = outer
        self.holes = holes
        self._o = geom.to_c([outer])[0]
        self._h = geom.to_c(holes)

    def paths(self) -> list:
        return [self.outer] + list(self.holes)

    def contains(self, x: float, y: float) -> bool:
        c = [int(round(x * geom.SCALE)), int(round(y * geom.SCALE))]
        if pyclipper.PointInPolygon(c, self._o) == 0:
            return False
        return all(pyclipper.PointInPolygon(c, h) != 1 for h in self._h)

    def distance(self, x: float, y: float) -> float:
        """Distance from (x, y) to the island boundary."""
        return min(_dist_to_polyline(p, (x, y)) for p in self.paths())


def _dist_to_polyline(path: np.ndarray, pt) -> float:
    a = np.asarray(path, dtype=float)
    b = np.roll(a, -1, axis=0)
    d = b - a
    ln2 = np.maximum((d ** 2).sum(axis=1), 1e-18)
    t = np.clip(((np.asarray(pt) - a) * d).sum(axis=1) / ln2, 0, 1)
    proj = a + t[:, None] * d
    return float(np.hypot(*(proj - pt).T).min())


def split(polys) -> list:
    """Group a union result into islands."""
    outers = [np.asarray(p, dtype=float) for p in polys if geom.area(p) > 0]
    holes = [np.asarray(p, dtype=float) for p in polys if geom.area(p) <= 0]
    oc = [geom.to_c([o])[0] for o in outers]
    areas = [geom.area(o) for o in outers]
    mine: list = [[] for _ in outers]
    for h in holes:
        c = [int(round(h[0][0] * geom.SCALE)), int(round(h[0][1] * geom.SCALE))]
        best = None
        for i, o in enumerate(oc):
            if pyclipper.PointInPolygon(c, o) != 0 and (best is None or areas[i] < areas[best]):
                best = i
        if best is not None:
            mine[best].append(h)
    return [Island(o, h) for o, h in zip(outers, mine)]


def find(islands: list, x: float, y: float, tol: float = 0.0):
    """Index of the island at (x, y), or the nearest one within `tol` of its boundary, else None."""
    for i, isl in enumerate(islands):
        if isl.contains(x, y):
            return i
    if tol > 0 and islands:
        d = [isl.distance(x, y) for isl in islands]
        i = int(np.argmin(d))
        if d[i] <= tol:
            return i
    return None


def interior_point(isl: Island, x: float, y: float) -> tuple:
    """A point inside the island close to (x, y); robust against rounding when stored and used later."""
    if isl.contains(x, y):
        return (x, y)
    for r in (0.02, 0.05, 0.1, 0.2):
        for k in range(16):
            a = k * np.pi / 8
            px, py = x + r * np.cos(a), y + r * np.sin(a)
            if isl.contains(px, py) and isl.contains(px + 0.01, py) and isl.contains(px - 0.01, py):
                return (float(px), float(py))
    p = isl.outer[0]
    return (float(p[0]), float(p[1]))

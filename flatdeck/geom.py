"""Polygon helpers on top of Clipper (pyclipper). Public API works on float mm, Clipper sees integers."""
from __future__ import annotations

import math

import numpy as np
import pyclipper

SCALE = 10000.0        # Clipper integer units per mm (0.1 µm)
ARC_TOL = 0.004        # max sagitta of circle/arc approximations, mm

Paths = list  # list of (N,2) float arrays


def to_c(paths) -> list:
    return [np.rint(np.asarray(p, dtype=float) * SCALE).astype(np.int64).tolist() for p in paths if len(p) >= 2]


def from_c(paths) -> Paths:
    return [np.asarray(p, dtype=float) / SCALE for p in paths if len(p) >= 2]


def circle(cx: float, cy: float, r: float, tol: float = ARC_TOL) -> np.ndarray:
    n = 16 if r <= tol else max(16, int(math.ceil(math.pi / math.acos(max(-1.0, 1.0 - tol / r)))))
    n = min(-(-n // 4) * 4, 256)               # multiple of 4: vertices on the axes, bounding box exact
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.column_stack([cx + r * np.cos(a), cy + r * np.sin(a)])


def rect(cx: float, cy: float, w: float, h: float) -> np.ndarray:
    return np.array([[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2], [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]])


def regular_polygon(cx: float, cy: float, dia: float, n: int, rot_deg: float = 0.0) -> np.ndarray:
    a = np.radians(rot_deg) + np.arange(n) * 2 * math.pi / n
    return np.column_stack([cx + dia / 2 * np.cos(a), cy + dia / 2 * np.sin(a)])


def rotate(p, deg: float) -> np.ndarray:
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    p = np.asarray(p, dtype=float)
    return np.column_stack([p[:, 0] * c - p[:, 1] * s, p[:, 0] * s + p[:, 1] * c])


def hull(points) -> np.ndarray:
    """Convex hull (monotone chain), counter-clockwise."""
    pts = sorted({(round(float(x), 7), round(float(y), 7)) for x, y in np.asarray(points)})
    if len(pts) <= 2:
        return np.array(pts, dtype=float)

    def half(seq):
        h = []
        for q in seq:
            while len(h) >= 2 and (h[-1][0] - h[-2][0]) * (q[1] - h[-2][1]) - (h[-1][1] - h[-2][1]) * (q[0] - h[-2][0]) <= 0:
                h.pop()
            h.append(q)
        return h

    lo, up = half(pts), half(pts[::-1])
    return np.array(lo[:-1] + up[:-1], dtype=float)


def area(p) -> float:
    """Signed shoelace area (positive = counter-clockwise)."""
    p = np.asarray(p, dtype=float)
    if len(p) < 3:
        return 0.0
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def total_area(paths) -> float:
    """Area of a union result (outer contours positive, holes negative)."""
    return sum(area(p) for p in paths)


def bounds(paths):
    """(xmin, ymin, xmax, ymax) or None."""
    arrs = [np.asarray(p, dtype=float) for p in paths if len(p)]
    if not arrs:
        return None
    a = np.vstack(arrs)
    return (float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max()))


def union(paths, fill=pyclipper.PFT_NONZERO) -> Paths:
    c = to_c(paths)
    if not c:
        return []
    pc = pyclipper.Pyclipper()
    pc.AddPaths(c, pyclipper.PT_SUBJECT, True)
    return from_c(pc.Execute(pyclipper.CT_UNION, fill, fill))


def difference(subject, clip) -> Paths:
    s, c = to_c(subject), to_c(clip)
    if not s:
        return []
    pc = pyclipper.Pyclipper()
    pc.AddPaths(s, pyclipper.PT_SUBJECT, True)
    if c:
        pc.AddPaths(c, pyclipper.PT_CLIP, True)
    return from_c(pc.Execute(pyclipper.CT_DIFFERENCE, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO))


def offset(paths, delta: float, join=pyclipper.JT_ROUND, miter: float = 2.0) -> Paths:
    """Grow (delta > 0) or shrink closed polygons. Input must be normalised (use union first)."""
    c = to_c(paths)
    if not c:
        return []
    pc = pyclipper.PyclipperOffset(miter, ARC_TOL * SCALE)
    pc.AddPaths(c, join, pyclipper.ET_CLOSEDPOLYGON)
    return from_c(pc.Execute(delta * SCALE))


def stroke(points, width: float) -> Paths:
    """Polygon of a polyline drawn with a round pen of the given width."""
    pts = np.asarray(points, dtype=float)
    if len(pts) == 1 or np.allclose(pts, pts[0]):
        return [circle(pts[0][0], pts[0][1], width / 2)]
    pc = pyclipper.PyclipperOffset(2.0, ARC_TOL * SCALE)
    pc.AddPath(to_c([pts])[0], pyclipper.JT_ROUND, pyclipper.ET_OPENROUND)
    return from_c(pc.Execute(width / 2 * SCALE))


def clean(paths, dist: float = 0.003) -> Paths:
    """Drop near-collinear / duplicate vertices."""
    out = []
    for p in to_c(paths):
        q = pyclipper.CleanPolygon(p, dist * SCALE)
        if len(q) >= 3:
            out.append(q)
    return from_c(out)


def is_outer(path) -> bool:
    return area(path) > 0


def transform(paths, fn) -> Paths:
    return [fn(np.asarray(p, dtype=float)) for p in paths]


def path_length(p, closed: bool = False) -> float:
    p = np.asarray(p, dtype=float)
    if len(p) < 2:
        return 0.0
    d = float(np.hypot(*np.diff(p, axis=0).T).sum())
    if closed:
        d += float(np.hypot(*(p[0] - p[-1])))
    return d


def order_nearest(items: list, start=(0.0, 0.0), closed: bool = True) -> list:
    """Order polylines by nearest-neighbour travel. Closed loops are rotated to start at the nearest vertex;
    open polylines may be reversed. Returns new arrays."""
    if not items:
        return []
    arrs = [np.asarray(a, dtype=float) for a in items]
    verts = np.vstack(arrs)
    owner = np.concatenate([np.full(len(a), i) for i, a in enumerate(arrs)])
    offs = np.concatenate([[0], np.cumsum([len(a) for a in arrs])])
    alive = np.ones(len(arrs), bool)
    cur = np.asarray(start, dtype=float)
    out = []
    for _ in range(len(arrs)):
        mask = alive[owner]
        if closed:
            d = np.where(mask, np.hypot(*(verts - cur).T), np.inf)
            k = int(np.argmin(d))
        else:  # open: only endpoints are candidates
            d = np.full(len(verts), np.inf)
            ends = np.concatenate([offs[:-1], offs[1:] - 1])
            ends = ends[alive[owner[ends]]]
            d[ends] = np.hypot(*(verts[ends] - cur).T)
            k = int(np.argmin(d))
        i = int(owner[k])
        a = arrs[i]
        j = k - int(offs[i])
        if closed:
            a = np.roll(a, -j, axis=0)
        elif j != 0:
            a = a[::-1]
        out.append(a)
        alive[i] = False
        cur = a[0] if closed else a[-1]
        if closed:
            cur = a[0]
    return out

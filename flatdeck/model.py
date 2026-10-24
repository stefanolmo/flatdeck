"""Project model (no Qt): loaded layers, generated jobs, board-wide transforms and the G-code origin."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from . import cam, geom, islands
from .excellon import DrillFile, parse_excellon
from .gerber import GerberLayer, outline_loops, parse_gerber

GERBER_EXT = {".gbr", ".ger", ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gko", ".gm1", ".gml", ".gtp", ".gbp", ".cmp", ".sol"}
DRILL_EXT = {".drl", ".xln", ".exc", ".drd", ".txt"}
KIND_BY_EXT = {".gko": "outline", ".gm1": "outline", ".gml": "outline", ".gto": "silk", ".gbo": "silk",
               ".gts": "mask", ".gbs": "mask", ".gtp": "mask", ".gbp": "mask"}
BOTTOM_EXT = {".gbl", ".gbs", ".gbo", ".gbp", ".sol"}


@dataclass
class Layer:
    name: str
    kind: str                       # copper | outline | silk | mask | drill | job
    gerber: GerberLayer | None = None
    drill: DrillFile | None = None
    job: cam.CamResult | None = None
    visible: bool = True
    path: str = ""
    side: str = "top"               # top | bottom (from the file extension or name)
    hv_points: list = field(default_factory=list)       # one point inside every island marked high-voltage
    _cache: dict = field(default_factory=dict, repr=False)

    def bounds(self):
        if self.gerber is not None:
            return self.gerber.bounds()
        if self.drill is not None:
            return self.drill.bounds()
        return None

    def islands(self) -> list:
        if self.gerber is None:
            return []
        key = id(self.gerber.polys)
        if self._cache.get("key") != key:
            self._cache = {"key": key, "islands": islands.split(self.gerber.polys)}
        return self._cache["islands"]

    @property
    def warnings(self) -> list:
        src = self.gerber or self.drill or self.job
        return list(src.warnings) if src is not None else []


def sniff_kind(path: str, text: str) -> str:
    """'gerber' or 'drill' from the content (the extension is unreliable)."""
    if os.path.splitext(path)[1].lower() == ".xy" or text.lstrip().startswith("#"):
        raise ValueError("Unsupported format (neither Gerber nor Excellon): " + os.path.basename(path))
    head = text[:4000]
    if "%FS" in head or "%MO" in head or "G04" in head and "%ADD" in text[:20000]:
        return "gerber"
    if "M48" in head or "T01" in head or "INCH" in head or "METRIC" in head:
        return "drill"
    return "gerber" if os.path.splitext(path)[1].lower() in GERBER_EXT else "drill"


class Project:
    def __init__(self):
        self.layers: list[Layer] = []
        self.origin_mode = "board"          # board: lower-left corner of everything loaded | file: coordinates as in the files

    # ----- loading
    def load(self, path: str, kind: str | None = None) -> Layer:
        with open(path, "r", errors="replace") as f:
            text = f.read()
        name = os.path.basename(path)
        k = kind or sniff_kind(path, text)
        if k == "drill":
            df = parse_excellon(text, name)
            lay = Layer(name, "drill", drill=df, path=path)
        else:
            gl = parse_gerber(text, name)
            ext = os.path.splitext(path)[1].lower()
            low = name.lower()
            guess = KIND_BY_EXT.get(ext) or ("outline" if "edge" in low or "outline" in low or "contour" in low else "copper")
            lay = Layer(name, guess, gerber=gl, path=path)
            if guess in ("silk", "mask"):
                lay.visible = False
            lay.side = "bottom" if ext in BOTTOM_EXT or "bottom" in low else "top"
        self.layers.append(lay)
        return lay

    def remove(self, layer: Layer) -> None:
        self.layers.remove(layer)

    def data_layers(self) -> list:
        return [l for l in self.layers if l.kind != "job"]

    def board_bounds(self):
        """Bounding box of the board: the outline layer if there is one, else the copper/drill data."""
        for l in self.data_layers():
            if l.kind == "outline" and l.gerber is not None and l.gerber.strokes:
                pts = np.vstack([s for _, s in l.gerber.strokes])
                return (float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max()))
        return self.bounds()

    def mirror_axis(self) -> float:
        """X of the vertical axis the board is flipped about to work on its other side."""
        b = self.board_bounds()
        return (b[0] + b[2]) / 2 if b else 0.0

    def jobs(self) -> list:
        return [l for l in self.layers if l.kind == "job"]

    def add_job(self, res: cam.CamResult) -> Layer:
        lay = Layer(res.name, "job", job=res)
        self.layers.append(lay)
        return lay

    # ----- geometry
    def bounds(self):
        """Copper + drill data (silkscreen, mask and outline only if there is nothing else)."""
        bs = [b for b in (l.bounds() for l in self.data_layers() if l.kind in ("copper", "drill")) if b]
        bs = bs or [b for b in (l.bounds() for l in self.data_layers()) if b]
        if not bs:
            return None
        a = np.array(bs)
        return (float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 2].max()), float(a[:, 3].max()))

    def origin(self):
        b = self.board_bounds()
        if self.origin_mode == "file" or b is None:
            return (0.0, 0.0)
        return (b[0], b[1])

    def board_outline(self, margin: float = 1.0):
        """(outer_loop, inner_loops, note). Uses the outline layer if there is one, else the copper bounding box + margin."""
        for l in self.data_layers():
            if l.kind == "outline" and l.gerber is not None:
                closed, opened = outline_loops(l.gerber.strokes)
                if closed:
                    closed.sort(key=lambda a: -abs(geom.area(a)))
                    note = f"{len(opened)} tratti aperti ignorati" if opened else ""
                    return closed[0], closed[1:], note
        b = self.bounds()
        if b is None:
            raise ValueError("No file loaded")
        return cam.rect_outline(b, margin), [], "No outline layer: using the bounding box of the data + margin"

    def copper_paths(self, layer: Layer):
        return layer.gerber.polys

    # ----- high-voltage islands
    def hv_indices(self, layer: Layer) -> set:
        isl = layer.islands()
        found = {islands.find(isl, x, y) for x, y in layer.hv_points}
        return {i for i in found if i is not None}

    def toggle_hv(self, layer: Layer, x: float, y: float, tol: float = 0.0):
        """Mark / unmark the island at (x, y). Returns True (marked), False (unmarked) or None (no island there)."""
        isl = layer.islands()
        i = islands.find(isl, x, y, tol)
        if i is None:
            return None
        if i in self.hv_indices(layer):
            layer.hv_points = [(px, py) for px, py in layer.hv_points if islands.find(isl, px, py) != i]
            return False
        layer.hv_points.append(islands.interior_point(isl[i], x, y))
        return True

    def hv_paths(self, layer: Layer, hv: bool) -> list:
        """Contours of the HV islands (hv=True) or of all the others (hv=False)."""
        sel = self.hv_indices(layer)
        return [p for i, isl in enumerate(layer.islands()) if (i in sel) == hv for p in isl.paths()]

    # ----- transforms (all data layers together, so they stay registered)
    def _centre(self):
        b = self.board_bounds()
        return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) if b else (0.0, 0.0)

    def transform(self, op: str) -> None:
        cx, cy = self._centre()
        fns = {
            "mirror_x": lambda a: np.column_stack([2 * cx - a[:, 0], a[:, 1]]),
            "mirror_y": lambda a: np.column_stack([a[:, 0], 2 * cy - a[:, 1]]),
            "rot90": lambda a: np.column_stack([cx - (a[:, 1] - cy), cy + (a[:, 0] - cx)]),
            "rot270": lambda a: np.column_stack([cx + (a[:, 1] - cy), cy - (a[:, 0] - cx)]),
        }
        fn = fns[op]
        flips = op.startswith("mirror")
        for l in self.data_layers():
            if l.gerber is not None:
                g = l.gerber
                g.polys = geom.union([fn(p) for p in g.polys]) if flips else [fn(p) for p in g.polys]
                g.strokes = [(w, fn(s)) for w, s in g.strokes]
                if l.hv_points:
                    l.hv_points = [tuple(q) for q in fn(np.array(l.hv_points, dtype=float))]
            if l.drill is not None:
                for t in l.drill.tools.values():
                    t.holes = fn(t.holes) if len(t.holes) else t.holes
                    t.slots = [fn(s) for s in t.slots]
        self.layers = [l for l in self.layers if l.kind != "job"]   # generated G-code is now out of date

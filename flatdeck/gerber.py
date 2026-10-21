"""RS-274X (Gerber) parser: apertures, macros, linear/arc draws, regions, polarity, step & repeat.

The result is a union of polygons in mm (`GerberLayer.polys`: outer contours counter-clockwise, holes clockwise)
plus the centre lines of every draw (`GerberLayer.strokes`), which the cut-out tool uses on outline layers.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

from . import geom

INCH = 25.4


@dataclass
class GerberLayer:
    name: str = ""
    polys: list = field(default_factory=list)
    strokes: list = field(default_factory=list)     # (width_mm, (N,2) array)
    warnings: list = field(default_factory=list)

    def bounds(self):
        return geom.bounds(self.polys)


@dataclass
class Aperture:
    kind: str                                       # C R O P M
    shape: list                                     # polygons centred on the origin, mm
    dia: float = 0.0                                # nominal diameter for C (used as stroke width)


# --------------------------------------------------------------------------- macros
_NUM_RE = re.compile(r"^[0-9eE+\-*/(). ]*$")


def _eval(expr: str, var: dict) -> float:
    e = re.sub(r"\$(\d+)", lambda m: repr(float(var.get(int(m.group(1)), 0.0))), expr.strip())
    e = e.replace("x", "*").replace("X", "*")
    if not _NUM_RE.match(e):
        raise ValueError(f"espressione non valida: {expr}")
    return float(eval(e, {"__builtins__": {}}, {}))   # noqa: S307 - input restricted to digits and + - * / ( )


def _macro_shape(statements: list[str], args: list[float], scale: float) -> list:
    var = {i + 1: a for i, a in enumerate(args)}
    result: list = []

    def add(exposure: int, polys: list) -> None:
        nonlocal result
        if not polys:
            return
        if exposure:
            result = geom.union(result + polys)
        else:
            result = geom.difference(result, polys)

    for st in statements:
        st = st.strip()
        if not st or st.startswith("0 ") or st == "0":
            continue
        if st.startswith("$"):
            name, expr = st.split("=", 1)
            var[int(name[1:])] = _eval(expr, var)
            continue
        f = [_eval(x, var) for x in st.split(",")]
        code, p = int(f[0]), f[1:]
        if code == 1:                                    # circle: exposure, dia, cx, cy [, rot]
            poly = geom.circle(p[2], p[3], p[1] / 2)
            rot = p[4] if len(p) > 4 else 0.0
            add(int(p[0]), [geom.rotate(poly, rot)])
        elif code == 20:                                 # vector line: exp, width, x1,y1,x2,y2, rot
            (x1, y1, x2, y2), w = p[2:6], p[1]
            ang = math.atan2(y2 - y1, x2 - x1)
            ln = math.hypot(x2 - x1, y2 - y1)
            box = geom.rect(ln / 2, 0, ln, w)
            box = geom.rotate(box, math.degrees(ang)) + [x1, y1]
            add(int(p[0]), [geom.rotate(box, p[6] if len(p) > 6 else 0.0)])
        elif code == 21:                                 # centre line: exp, w, h, cx, cy, rot
            add(int(p[0]), [geom.rotate(geom.rect(p[3], p[4], p[1], p[2]), p[5] if len(p) > 5 else 0.0)])
        elif code == 22:                                 # lower-left line: exp, w, h, x, y, rot
            add(int(p[0]), [geom.rotate(geom.rect(p[3] + p[1] / 2, p[4] + p[2] / 2, p[1], p[2]), p[5] if len(p) > 5 else 0.0)])
        elif code == 4:                                  # outline: exp, n, x0,y0, ..., rot
            n = int(p[1])
            pts = np.array(p[2:2 + 2 * (n + 1)]).reshape(-1, 2)
            rot = p[2 + 2 * (n + 1)] if len(p) > 2 + 2 * (n + 1) else 0.0
            add(int(p[0]), [geom.rotate(pts[:-1] if n >= 3 else pts, rot)])
        elif code == 5:                                  # polygon: exp, n, cx, cy, dia, rot
            poly = geom.regular_polygon(0, 0, p[4], int(p[1]), p[5] if len(p) > 5 else 0.0)
            add(int(p[0]), [poly + [p[2], p[3]]])
        elif code == 7:                                  # thermal: cx, cy, od, id, gap, rot
            cx, cy, od, idia, gap = p[0], p[1], p[2], p[3], p[4]
            ring = geom.difference([geom.circle(0, 0, od / 2)], [geom.circle(0, 0, idia / 2)])
            bars = [geom.rect(0, 0, gap, od * 2), geom.rect(0, 0, od * 2, gap)]
            th = geom.difference(ring, bars)
            rot = p[5] if len(p) > 5 else 0.0
            add(1, [geom.rotate(q, rot) + [cx, cy] for q in th])
        else:
            raise ValueError(f"primitiva di macro {code} non supportata")
    return [q * scale for q in result]


# --------------------------------------------------------------------------- parser
class _Parser:
    def __init__(self, name: str):
        self.layer = GerberLayer(name=name)
        self.unit = 1.0
        self.fmt = (2, 4)
        self.zeros = "L"                    # L = leading zeros omitted, T = trailing omitted
        self.macros: dict[str, list[str]] = {}
        self.apertures: dict[int, Aperture] = {}
        self.ap: Aperture | None = None
        self.mode = 1                       # 1 linear, 2 CW, 3 CCW
        self.multi = True                   # G75 multi-quadrant
        self.region = False
        self.contour: list = []
        self.polarity = 1
        self.pos = np.zeros(2)
        self.events: list = []              # (polarity, [polygons])
        self.sr: list | None = None         # events collected inside a step&repeat block
        self.sr_par = (1, 1, 0.0, 0.0)
        self.last_op = 2
        self.warned: set = set()
        self.pend: list | None = None       # round-pen draw being accumulated, merged into one polyline
        self.pend_ap: Aperture | None = None

    def warn(self, msg: str) -> None:
        if msg not in self.warned:
            self.warned.add(msg)
            self.layer.warnings.append(msg)

    # ----- numbers
    def num(self, s: str) -> float:
        if "." in s:
            return float(s) * self.unit
        sign = -1 if s.startswith("-") else 1
        d = s.lstrip("+-")
        i, dec = self.fmt
        if self.zeros == "T":
            d = d.ljust(i + dec, "0")
        return sign * int(d or "0") / 10 ** dec * self.unit

    # ----- event sink
    def emit(self, polys: list) -> None:
        if not polys:
            return
        (self.sr if self.sr is not None else self.events).append((self.polarity, polys))

    # ----- apertures
    def define_aperture(self, body: str) -> None:
        m = re.match(r"ADD(\d+)([^,]+)(?:,(.*))?$", body)
        if not m:
            self.warn(f"Apertura non riconosciuta: {body}")
            return
        code, name, rest = int(m.group(1)), m.group(2), m.group(3) or ""
        args = [float(x) for x in re.split(r"[xX]", rest) if x.strip()] if rest else []
        u = self.unit
        try:
            if name == "C":
                dia = args[0] * u
                shape = [geom.circle(0, 0, dia / 2)]
                ap = Aperture("C", self._hole(shape, args[1:2], u), dia)
            elif name in ("R", "O"):
                w, h = args[0] * u, args[1] * u
                if name == "R":
                    shape = [geom.rect(0, 0, w, h)]
                else:
                    r = min(w, h) / 2
                    c = geom.stroke(np.array([[-(w - 2 * r) / 2, 0], [(w - 2 * r) / 2, 0]]) if w >= h
                                    else np.array([[0, -(h - 2 * r) / 2], [0, (h - 2 * r) / 2]]), 2 * r)
                    shape = c
                ap = Aperture(name, self._hole(shape, args[2:3], u))
            elif name == "P":
                dia, n = args[0] * u, int(args[1])
                shape = [geom.regular_polygon(0, 0, dia, n, args[2] if len(args) > 2 else 0.0)]
                ap = Aperture("P", self._hole(shape, args[3:4], u))
            elif name in self.macros:
                ap = Aperture("M", _macro_shape(self.macros[name], args, u))
            else:
                self.warn(f"Apertura D{code}: forma '{name}' sconosciuta")
                return
        except (ValueError, IndexError) as e:
            self.warn(f"Apertura D{code} non valida: {e}")
            return
        self.apertures[code] = ap

    @staticmethod
    def _hole(shape: list, hole: list, u: float) -> list:
        if hole and hole[0] > 0:
            return geom.difference(shape, [geom.circle(0, 0, hole[0] * u / 2)])
        return shape

    # ----- drawing
    def flash(self, p: np.ndarray) -> None:
        if self.region or self.ap is None:
            return
        self.emit([q + p for q in self.ap.shape])

    def stroke_path(self, pts: np.ndarray) -> None:
        ap = self.ap
        if ap is None:
            self.warn("Tracciato senza apertura selezionata")
            return
        if ap.kind == "C":
            if self.pend is not None and ap is self.pend_ap and np.allclose(self.pend[-1], pts[0], atol=1e-9):
                self.pend.extend(pts[1:].tolist())
            else:
                self.flush_stroke()
                self.pend, self.pend_ap = pts.tolist(), ap
            return
        self.flush_stroke()
        # non-round pens: sweep of the (convex) shape along each segment
        base = np.vstack(ap.shape) if ap.shape else np.zeros((1, 2))
        polys = []
        for a, b in zip(pts[:-1], pts[1:]):
            polys.append(geom.hull(np.vstack([base + a, base + b])))
        self.emit(polys)
        self.layer.strokes.append((0.0, pts))

    def flush_stroke(self) -> None:
        """Emit the accumulated round-pen polyline (Fritzing & co. write curves as thousands of tiny segments)."""
        if self.pend is not None:
            pts = np.array(self.pend)
            self.emit(geom.stroke(pts, self.pend_ap.dia))
            self.layer.strokes.append((self.pend_ap.dia, pts))
            self.pend = None

    def arc_points(self, p0, p1, ij) -> np.ndarray:
        i, j = ij
        if self.multi:
            centre = p0 + np.array([i, j])
        else:                                            # single quadrant: offsets are unsigned
            best = None
            for si in (1, -1):
                for sj in (1, -1):
                    c = p0 + np.array([si * i, sj * j])
                    err = abs(np.hypot(*(p0 - c)) - np.hypot(*(p1 - c)))
                    a0, a1 = math.atan2(*(p0 - c)[::-1]), math.atan2(*(p1 - c)[::-1])
                    sw = (a1 - a0) % (2 * math.pi) if self.mode == 3 else -((a0 - a1) % (2 * math.pi))
                    if abs(sw) > math.pi / 2 + 1e-6:
                        continue
                    if best is None or err < best[0]:
                        best = (err, c)
            centre = best[1] if best else p0 + np.array([i, j])
        r = float(np.hypot(*(p0 - centre)))
        a0 = math.atan2(p0[1] - centre[1], p0[0] - centre[0])
        a1 = math.atan2(p1[1] - centre[1], p1[0] - centre[0])
        sweep = a1 - a0
        full = bool(np.allclose(p0, p1, atol=1e-9))
        if self.mode == 2:                               # clockwise
            if sweep >= 0 or full:
                sweep -= 2 * math.pi
        else:
            if sweep <= 0 or full:
                sweep += 2 * math.pi
        if not self.multi and full:
            sweep = 0.0
        if r < 1e-9:
            return np.array([p0, p1])
        step = 2 * math.acos(max(-1.0, 1.0 - geom.ARC_TOL / r)) if r > geom.ARC_TOL else math.pi / 8
        n = max(2, int(math.ceil(abs(sweep) / step)))
        ang = a0 + sweep * np.arange(n + 1) / n
        pts = np.column_stack([centre[0] + r * np.cos(ang), centre[1] + r * np.sin(ang)])
        pts[0], pts[-1] = p0, p1
        return pts

    def close_contour(self) -> None:
        if len(self.contour) >= 3:
            self.emit([np.array(self.contour)])
        self.contour = []

    # ----- block processing
    def block(self, text: str) -> None:
        if text.startswith("G04") or text.startswith("G4 "):
            return
        codes = re.findall(r"([GDMXYIJ])([+-]?[\d.]+)", text)
        vals: dict[str, str] = {}
        op = None
        for c, v in codes:
            if c == "G":
                self.g_code(int(float(v)))
            elif c == "D":
                n = int(float(v))
                if n in (1, 2, 3):
                    op = n
                elif n >= 10:
                    self.flush_stroke()
                    if n in self.apertures:
                        self.ap = self.apertures[n]
                    else:
                        self.warn(f"Apertura D{n} usata ma non definita")
                        self.ap = None
            elif c == "M":
                pass
            else:
                vals[c] = v
        if not vals and op is None:
            return
        if op is None:
            op = self.last_op
        self.last_op = op
        if op != 1:
            self.flush_stroke()
        new = self.pos.copy()
        if "X" in vals:
            new[0] = self.num(vals["X"])
        if "Y" in vals:
            new[1] = self.num(vals["Y"])
        ij = (self.num(vals.get("I", "0")), self.num(vals.get("J", "0")))
        if op == 1:
            if self.mode == 1:
                pts = np.array([self.pos, new])
            else:
                pts = self.arc_points(self.pos, new, ij)
            if self.region:
                if not self.contour:
                    self.contour.append(self.pos.copy())
                self.contour.extend(pts[1:].tolist())
            elif np.allclose(pts[0], pts[-1]) and len(pts) == 2:
                if self.ap is not None and self.ap.kind == "C":
                    self.flash(new)
            else:
                self.stroke_path(pts)
        elif op == 2:
            if self.region:
                self.close_contour()
        else:
            self.flash(new)
        self.pos = new

    def g_code(self, g: int) -> None:
        if g in (36, 37):
            self.flush_stroke()
        if g in (1, 2, 3):
            self.mode = g
        elif g == 36:
            self.region = True
            self.contour = []
        elif g == 37:
            self.close_contour()
            self.region = False
        elif g == 70:
            self.unit = INCH
        elif g == 71:
            self.unit = 1.0
        elif g == 74:
            self.multi = False
        elif g == 75:
            self.multi = True
        elif g == 91:
            self.warn("Coordinate incrementali (G91) non supportate")

    def extended(self, stmts: list[str]) -> None:
        self.flush_stroke()
        head = stmts[0]
        if head.startswith("FS"):
            m = re.match(r"FS([LT])?([AI])?.*?X(\d)(\d)Y(\d)(\d)", head)
            if m:
                self.zeros = m.group(1) or "L"
                self.fmt = (int(m.group(3)), int(m.group(4)))
                if m.group(2) == "I":
                    self.warn("Coordinate incrementali (FSI) non supportate")
        elif head.startswith("MO"):
            self.unit = INCH if head[2:4] == "IN" else 1.0
        elif head.startswith("AM"):
            self.macros[head[2:]] = stmts[1:]
        elif head.startswith("AD"):
            self.define_aperture(head)
        elif head.startswith("LP"):
            self.flush_region()
            self.polarity = 0 if head[2:3] == "C" else 1
        elif head.startswith("SR"):
            self.step_repeat(head)
        elif head.startswith(("LM", "LR", "LS")):
            if head[2:] not in ("N", "0", "1"):
                self.warn("Trasformazioni di apertura (LM/LR/LS) ignorate")
        elif head.startswith("AB"):
            self.warn("Blocchi di apertura (AB) non supportati")
        # TF / TA / TO / TD attributes, IP, OF, SF, IN, LN: irrelevant for geometry

    def flush_region(self) -> None:
        if self.region:
            self.close_contour()

    def step_repeat(self, head: str) -> None:
        m = re.match(r"SRX(\d+)Y(\d+)I([\d.+-]+)J([\d.+-]+)", head)
        if self.sr is not None:                           # close the open block, replicating its events
            nx, ny, di, dj = self.sr_par
            for ix in range(nx):
                for iy in range(ny):
                    for pol, polys in self.sr:
                        self.events.append((pol, [q + [ix * di, iy * dj] for q in polys]))
            self.sr = None
        if m:                                             # open a new block
            self.sr = []
            self.sr_par = (int(m.group(1)), int(m.group(2)), float(m.group(3)) * self.unit, float(m.group(4)) * self.unit)

    def compose(self) -> None:
        self.flush_stroke()
        if self.sr is not None:
            self.step_repeat("SR")
        geo: list = []
        batch: list = []
        pol = 1
        for p, polys in self.events:
            if p != pol and batch:
                geo = geom.union(geo + batch) if pol else geom.difference(geo, geom.union(batch))
                batch = []
            pol = p
            batch.extend(polys)
        if batch:
            geo = geom.union(geo + batch) if pol else geom.difference(geo, geom.union(batch))
        self.layer.polys = geom.clean(geo)


def parse_gerber(text: str, name: str = "") -> GerberLayer:
    ps = _Parser(name)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "%":
            j = text.find("%", i + 1)
            if j < 0:
                break
            body = text[i + 1:j]
            stmts = [s.strip() for s in body.replace("\r", "").replace("\n", "").split("*") if s.strip()]
            if stmts:
                ps.extended(stmts)
            i = j + 1
        elif c in " \r\n\t":
            i += 1
        else:
            j = text.find("*", i)
            if j < 0:
                j = n
            blk = text[i:j].replace("\r", "").replace("\n", "").strip()
            if blk:
                if blk.startswith("M02") or blk == "M00":
                    break
                try:
                    ps.block(blk)
                except (ValueError, IndexError) as e:
                    ps.warn(f"Blocco ignorato '{blk[:30]}': {e}")
            i = j + 1
    ps.compose()
    if not ps.layer.polys:
        ps.warn("Nessuna geometria trovata nel file")
    return ps.layer


def outline_loops(strokes, tol: float = 0.02):
    """Chain outline strokes into closed loops. Returns (closed_loops, open_chains), arrays without a repeated end point."""
    chains = [np.asarray(s, dtype=float) for _, s in strokes if len(s) >= 2]
    closed, opened = [], []
    while chains:
        cur = chains.pop(0)
        grew = True
        while grew:
            if np.hypot(*(cur[0] - cur[-1])) <= tol and len(cur) >= 4:
                break
            grew = False
            for k, c in enumerate(chains):
                for cand in (c, c[::-1]):
                    if np.hypot(*(cur[-1] - cand[0])) <= tol:
                        cur = np.vstack([cur, cand[1:]])
                        chains.pop(k)
                        grew = True
                        break
                    if np.hypot(*(cur[0] - cand[-1])) <= tol:
                        cur = np.vstack([cand[:-1], cur])
                        chains.pop(k)
                        grew = True
                        break
                if grew:
                    break
        if np.hypot(*(cur[0] - cur[-1])) <= tol and len(cur) >= 4:
            closed.append(cur[:-1])
        else:
            opened.append(cur)
    return closed, opened

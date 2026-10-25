"""G-code generation for GRBL: isolation routing, drilling and board cut-out.

All geometry is in mm in the coordinates of the loaded files ("world"); `origin` is subtracted when writing G-code.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pyclipper

from . import __version__, geom, islands
from .excellon import DrillFile, DrillTool

RAPID_RATE = 1000.0       # mm/min, only used for the time estimate


def vbit_diameter(tip: float, angle_deg: float, depth: float) -> float:
    """Cutting width of a V-bit at the given depth (depth is taken as a magnitude)."""
    return tip + 2 * abs(depth) * math.tan(math.radians(angle_deg) / 2)


@dataclass
class CamResult:
    kind: str = ""
    name: str = ""
    gcode: str = ""
    paths: list = field(default_factory=list)        # (array, closed) in world coordinates, for the preview
    holes: list = field(default_factory=list)        # (x, y, dia) in world coordinates
    marks: list = field(default_factory=list)        # polygons to flag in the preview (copper the job would damage)
    tool_dia: float = 0.0
    warnings: list = field(default_factory=list)
    length: float = 0.0                              # cutting length, mm
    time_s: float = 0.0

    @property
    def lines(self) -> int:
        return self.gcode.count("\n")


@dataclass
class IsolationParams:
    tool_dia: float = 0.2
    passes: int = 1
    overlap: float = 0.2            # fraction of the tool diameter shared by adjacent passes
    mode: str = "all"               # all | ext | int
    margin: float = 0.0             # extra distance between the copper edge and the groove (traces stay this much wider)
    cut_z: float = -0.05
    travel_z: float = 2.0
    feed: float = 120.0
    plunge: float = 40.0
    spindle: int = 1000             # 0 = do not emit M3/M5
    mirror_x: float | None = None   # flip about the vertical line x = mirror_x (bottom side, board turned over)


@dataclass
class HVParams:
    """Copper-free band around the high-voltage islands, cut with a large flat end mill."""
    clearance: float = 3.0          # width of the band, measured from the HV copper edge
    tool_dia: float = 3.0
    overlap: float = 0.2
    cut_z: float = -0.1
    travel_z: float = 2.0
    feed: float = 100.0
    plunge: float = 40.0
    spindle: int = 1000
    mirror_x: float | None = None


@dataclass
class DrillParams:
    drill_z: float = -1.8
    travel_z: float = 2.0
    change_z: float = 15.0
    plunge: float = 40.0
    peck: float = 0.0               # 0 = single plunge
    slot_step: float = 0.6
    feed: float = 80.0              # slot milling feed
    spindle: int = 1000
    tools: dict = field(default_factory=dict)     # tool number -> enabled (missing = enabled)
    mirror_x: float | None = None


@dataclass
class CutoutParams:
    tool_dia: float = 1.0
    cut_z: float = -1.8
    step: float = 0.6
    travel_z: float = 2.0
    feed: float = 100.0
    plunge: float = 30.0
    gaps: str = "none"              # none | tb | lr | four
    gap_width: float = 3.0
    spindle: int = 1000


class _Writer:
    def __init__(self, shift, title: str, spindle: int, travel_z: float):
        self.shift = np.asarray(shift, dtype=float)
        self.lines = [f"(FlatDeck {__version__} - {title})", "G21 G90 G94"]
        self.spindle = spindle
        self.travel = travel_z
        self.feed = None
        self.pos = np.array([0.0, 0.0, travel_z])   # unknown start: assumed above the work
        self.cut_len = 0.0
        self.time = 0.0

    @staticmethod
    def n(v: float) -> str:
        s = f"{v:.3f}".rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s

    def raw(self, s: str) -> None:
        self.lines.append(s)

    def spindle_on(self) -> None:
        if self.spindle > 0:
            self.raw(f"M3 S{self.spindle}")
            self.raw("G4 P2")

    def start(self) -> None:
        self.raw(f"G0 Z{self.n(self.travel)}")
        self.spindle_on()

    def f(self, feed: float) -> str:
        if self.feed != feed:
            self.feed = feed
            return f" F{self.n(feed)}"
        return ""

    def rapid_z(self, z: float) -> None:
        self.raw(f"G0 Z{self.n(z)}")
        self.time += abs(z - self.pos[2]) / RAPID_RATE * 60
        self.pos[2] = z

    def rapid_xy(self, p) -> None:
        q = np.asarray(p, dtype=float) - self.shift
        self.raw(f"G0 X{self.n(q[0])} Y{self.n(q[1])}")
        self.time += float(np.hypot(*(q - self.pos[:2]))) / RAPID_RATE * 60
        self.pos[:2] = q

    def lift(self) -> None:
        if self.pos[2] != self.travel:
            self.rapid_z(self.travel)

    def feed_z(self, z: float, feed: float) -> None:
        self.raw(f"G1 Z{self.n(z)}{self.f(feed)}")
        self.time += abs(z - self.pos[2]) / feed * 60
        self.pos[2] = z

    def feed_xy(self, p, feed: float) -> None:
        q = np.asarray(p, dtype=float) - self.shift
        d = float(np.hypot(*(q - self.pos[:2])))
        if d < 1e-4:
            return
        self.raw(f"G1 X{self.n(q[0])} Y{self.n(q[1])}{self.f(feed)}")
        self.cut_len += d
        self.time += d / feed * 60
        self.pos[:2] = q

    def end(self) -> None:
        self.rapid_z(self.travel)
        if self.spindle > 0:
            self.raw("M5")
        self.raw(f"G0 X0 Y0")
        self.raw("M2")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def _flip(a, axis: float) -> np.ndarray:
    a = np.array(a, dtype=float)
    a[:, 0] = 2 * axis - a[:, 0]
    return a


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


# --------------------------------------------------------------------------- isolation
def _split_merged(base, loops, r: float, rb: float, warnings: list | None, marks: list | None) -> list:
    """Where copper islands are closer than the tool diameter their offset loops fuse into one ring around all of them and
    nothing is cut between them (a short circuit). Re-offset those clusters with a smaller radius, just enough to separate
    the loops: the tool then cuts into the copper edges by up to r - r' instead of leaving the gap uncut.
    `r` is the tool radius, `rb` the first-pass offset (r + safety margin). Gaps narrower than the tool radius cannot be fixed
    this way and are reported."""
    cu = None
    out, fixed, bad, nick = [], 0, 0, 0.0
    for f in islands.split(loops):
        if cu is None:
            cu = islands.split(base)
        inside = [c for c in cu if f.contains(*c.outer[0])]
        if len(inside) < 2:
            out += f.paths()
            continue
        sub = [q for c in inside for q in c.paths()]
        done = None
        k = 0
        while rb - k * 0.02 * r >= r / 2 - 1e-9:                 # shrink the offset, never below r / 2
            k += 1
            rp = rb - k * 0.02 * r
            if rp < r / 2 - 1e-9:
                break
            cand = geom.clean(geom.offset(sub, rp))
            if all(sum(1 for c in inside if g.contains(*c.outer[0])) <= 1 for g in islands.split(cand)):
                done, nick = cand, max(nick, r - rp)
                break
        if done is not None:
            out += done
            fixed += 1
            continue
        out += f.paths()
        bad += 1
        if marks is not None:
            for i, a in enumerate(inside):
                for b in inside[i + 1:]:
                    marks += [m for m in geom.intersection(geom.offset(a.paths(), r), geom.offset(b.paths(), r)) if abs(geom.area(m)) > 1e-5]
    if warnings is not None:
        if fixed:
            how = (f"letting the tool cut up to {nick:.3f} mm into the copper edges" if nick > 1e-9
                   else "reducing the safety margin there")
            warnings.append(f"{fixed} narrow gap(s) between copper islands isolated by {how}")
        if bad:
            warnings.append(f"{bad} gap(s) narrower than the tool radius could not be isolated and remain shorted (shown in red): "
                            "use a smaller tool, or a V-bit at less depth")
    return out


def isolation_loops(copper, p: IsolationParams, warnings: list | None = None, marks: list | None = None) -> list:
    """Closed tool-centre loops around the copper, one set per pass, innermost pass first."""
    _check(p.tool_dia > 0, "Tool diameter must be positive")
    _check(1 <= p.passes <= 50, "Passes must be between 1 and 50")
    _check(0 <= p.overlap < 1, "Overlap must be between 0 and 99%")
    _check(0 <= p.margin <= 2, "Safety margin must be between 0 and 2 mm")
    base = geom.union(copper)
    step = p.tool_dia * (1 - p.overlap)
    out = []
    for i in range(p.passes):
        loops = geom.offset(base, p.tool_dia / 2 + p.margin + i * step)
        if i == 0:
            loops = _split_merged(base, loops, p.tool_dia / 2, p.tool_dia / 2 + p.margin, warnings, marks)
        if p.mode == "ext":
            loops = [l for l in loops if geom.is_outer(l)]
        elif p.mode == "int":
            loops = [l for l in loops if not geom.is_outer(l)]
        loops = geom.clean(loops)
        if p.mirror_x is not None:
            loops = [_flip(l, p.mirror_x)[::-1] for l in loops]
        if not loops and warnings is not None:
            warnings.append(f"Pass {i + 1}: no toolpath (not enough room)")
        out.append(loops)
    return out


def isolate(copper, p: IsolationParams, origin=(0.0, 0.0), name: str = "Isolation") -> CamResult:
    _check(p.cut_z < 0, "Cutting depth must be negative")
    _check(p.travel_z > 0, "Safe Z must be positive")
    _check(p.feed > 0 and p.plunge > 0, "Feed rates must be positive")
    res = CamResult(kind="isolation", name=name, tool_dia=p.tool_dia)
    passes = isolation_loops(copper, p, res.warnings, res.marks)
    w = _Writer(origin, f"Isolation, tool {p.tool_dia:g} mm, {p.passes} passes", p.spindle, p.travel_z)
    w.start()
    cur = np.asarray(origin, dtype=float)
    n_loops = 0
    for loops in passes:
        for loop in geom.order_nearest(loops, cur):
            w.lift()
            w.rapid_xy(loop[0])
            w.feed_z(p.cut_z, p.plunge)
            for q in loop[1:]:
                w.feed_xy(q, p.feed)
            w.feed_xy(loop[0], p.feed)
            cur = loop[0]
            res.paths.append((loop, True))
            n_loops += 1
    w.end()
    if p.mirror_x is not None:
        res.marks = [_flip(m, p.mirror_x) for m in res.marks]
    if not n_loops:
        res.warnings.append("No toolpath generated")
    res.gcode, res.length, res.time_s = w.text(), w.cut_len, w.time
    return res


# --------------------------------------------------------------------------- high-voltage clearance
def hv_offsets(p: HVParams) -> list:
    """Tool-centre distances from the HV copper edge: the first pass is tangent to it, the last one reaches `clearance`."""
    step = p.tool_dia * (1 - p.overlap)
    last = max(p.clearance - p.tool_dia / 2, p.tool_dia / 2)
    offs = [p.tool_dia / 2]
    while offs[-1] < last - 1e-9:
        offs.append(min(offs[-1] + step, last))
    return offs


def hv_clearance(hv, others, p: HVParams, origin=(0.0, 0.0), name: str = "HV clearance") -> CamResult:
    """`hv`: contours of the high-voltage islands; `others`: contours of all the remaining copper of the same layer."""
    _check(len(hv) > 0, "No island is marked as high-voltage")
    _check(p.clearance > 0 and p.tool_dia > 0, "Clearance and tool diameter must be positive")
    _check(0 <= p.overlap < 1, "Overlap must be between 0 and 99%")
    _check(p.cut_z < 0 and p.travel_z > 0, "Depth must be negative and safe Z positive")
    _check(p.feed > 0 and p.plunge > 0, "Feed rates must be positive")
    res = CamResult(kind="hv", name=name, tool_dia=p.tool_dia)
    if p.clearance < p.tool_dia - 1e-9:
        res.warnings.append(f"Clearance {p.clearance:g} mm is smaller than the tool: the band will be {p.tool_dia:g} mm wide")
    base = geom.union(hv)
    zone = geom.offset(base, max(p.clearance, p.tool_dia))
    hit = geom.intersection(others, zone) if others else []
    hit = [h for h in hit if abs(geom.area(h)) > 1e-4]
    if hit:
        res.marks = hit
        res.warnings.append(f"{len(hit)} piece(s) of other copper lie within {max(p.clearance, p.tool_dia):g} mm of the HV islands "
                            "and would be milled away (shown in red): move them or reduce the clearance")
    offs = hv_offsets(p)
    w = _Writer(origin, f"HV clearance {p.clearance:g} mm, tool {p.tool_dia:g} mm, {len(offs)} passes", p.spindle, p.travel_z)
    w.start()
    cur = np.asarray(origin, dtype=float)
    for off in offs:
        loops = geom.clean(geom.offset(base, off))
        if p.mirror_x is not None:
            loops = [_flip(l, p.mirror_x)[::-1] for l in loops]
        for loop in geom.order_nearest(loops, cur):
            w.lift()
            w.rapid_xy(loop[0])
            w.feed_z(p.cut_z, p.plunge)
            for q in loop[1:]:
                w.feed_xy(q, p.feed)
            w.feed_xy(loop[0], p.feed)
            cur = loop[0]
            res.paths.append((loop, True))
    w.end()
    if p.mirror_x is not None:
        res.marks = [_flip(m, p.mirror_x) for m in res.marks]
    res.gcode, res.length, res.time_s = w.text(), w.cut_len, w.time
    return res


# --------------------------------------------------------------------------- drilling
def drill(df: DrillFile, p: DrillParams, origin=(0.0, 0.0), name: str = "Drilling") -> CamResult:
    _check(p.drill_z < 0, "Drilling depth must be negative")
    _check(p.travel_z > 0 and p.change_z >= p.travel_z, "Safe Z must be > 0 and tool-change Z ≥ safe Z")
    _check(p.plunge > 0 and p.feed > 0, "Feed rates must be positive")
    res = CamResult(kind="drill", name=name)
    w = _Writer(origin, "Drilling", p.spindle, p.travel_z)
    chosen = [t for t in sorted(df.tools.values(), key=lambda t: t.dia) if p.tools.get(t.number, True)]
    if p.mirror_x is not None:
        chosen = [DrillTool(t.number, t.dia, _flip(t.holes, p.mirror_x) if len(t.holes) else t.holes,
                            [_flip(s, p.mirror_x) for s in t.slots]) for t in chosen]
    if not chosen:
        raise ValueError("No tool selected")
    w.raw(f"G0 Z{w.n(p.change_z)}")
    cur = np.asarray(origin, dtype=float)
    for k, t in enumerate(chosen):
        if k > 0:
            if p.spindle > 0:
                w.raw("M5")
        w.rapid_z(p.change_z)
        w.raw(f"(MSG, Fit the {t.dia:.2f} mm bit and zero Z on the workpiece surface)")
        w.raw("M0")
        w.spindle_on()
        w.raw(f"(Tool T{t.number}: {t.dia:.3f} mm - {len(t.holes)} holes, {len(t.slots)} slots)")
        for h in geom.order_nearest([h.reshape(1, 2) for h in t.holes], cur):
            h = h[0]
            w.lift()
            w.rapid_xy(h)
            if p.peck > 0:
                z = 0.0
                while z > p.drill_z + 1e-9:
                    z = max(z - p.peck, p.drill_z)
                    w.feed_z(z, p.plunge)
                    if z > p.drill_z + 1e-9:
                        w.rapid_z(0.5)
                        w.rapid_z(z + 0.2)
            else:
                w.feed_z(p.drill_z, p.plunge)
            w.rapid_z(p.travel_z)
            cur = h
            res.holes.append((float(h[0]), float(h[1]), t.dia))
        for s in t.slots:
            w.lift()
            w.rapid_xy(s[0])
            z = 0.0
            fwd = True
            while z > p.drill_z + 1e-9:
                z = max(z - p.slot_step, p.drill_z)
                w.feed_z(z, p.plunge)
                seq = s[1:] if fwd else s[-2::-1]
                for q in seq:
                    w.feed_xy(q, p.feed)
                fwd = not fwd
            w.rapid_z(p.travel_z)
            res.paths.append((s, False))
            cur = s[-1]
        res.tool_dia = max(res.tool_dia, t.dia)
    w.end()
    res.gcode, res.length, res.time_s = w.text(), w.cut_len, w.time
    return res


# --------------------------------------------------------------------------- cut-out
def _point_to_s(loop: np.ndarray, pt) -> float:
    """Arc-length coordinate of the point of the closed loop nearest to `pt`."""
    P = np.vstack([loop, loop[:1]])
    d = np.diff(P, axis=0)
    seg = np.hypot(d[:, 0], d[:, 1])
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    best, bs = np.inf, 0.0
    for i in range(len(seg)):
        if seg[i] < 1e-12:
            continue
        t = float(np.clip(np.dot(np.asarray(pt) - P[i], d[i]) / seg[i] ** 2, 0, 1))
        dist = float(np.hypot(*(P[i] + t * d[i] - pt)))
        if dist < best:
            best, bs = dist, cum[i] + t * seg[i]
    return bs


def split_gaps(loop: np.ndarray, centres: list, width: float) -> list:
    """Open polylines left after cutting a gap of `width` (arc length) around each centre of the closed loop."""
    P = np.vstack([loop, loop[:1]])
    seg = np.hypot(*np.diff(P, axis=0).T)
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    L = float(cum[-1])
    if not centres or width <= 0:
        return [np.vstack([loop, loop[:1]])]
    P2 = np.vstack([P, P[1:]])
    cum2 = np.concatenate([cum, cum[1:] + L])

    def at(s: float) -> np.ndarray:
        return np.array([np.interp(s, cum2, P2[:, 0]), np.interp(s, cum2, P2[:, 1])])

    cs = sorted(c % L for c in centres)
    out = []
    for k, c in enumerate(cs):
        a = c + width / 2
        b = (cs[(k + 1) % len(cs)] - width / 2) + (L if k + 1 == len(cs) else 0.0)
        if b - a < 1e-6:
            continue
        inner = (cum2 > a) & (cum2 < b)
        out.append(np.vstack([at(a), P2[inner], at(b)]))
    return out


def cutout(outer, inner, p: CutoutParams, origin=(0.0, 0.0), name: str = "Cut-out") -> CamResult:
    """`outer`: closed loop of the board; `inner`: list of closed loops to be cut out inside the board."""
    _check(p.tool_dia > 0, "Tool diameter must be positive")
    _check(p.cut_z < 0 and p.step > 0, "Depth must be negative and step-down positive")
    _check(p.feed > 0 and p.plunge > 0, "Feed rates must be positive")
    res = CamResult(kind="cutout", name=name, tool_dia=p.tool_dia)
    r = p.tool_dia / 2
    loops = []                                         # (loop, gap_centres, is_board_outline)
    o = np.asarray(outer, dtype=float)
    if geom.area(o) < 0:
        o = o[::-1]
    for l in geom.clean(geom.offset([o], r), 0.005):
        centres = []
        if p.gaps != "none":
            x0, y0, x1, y1 = geom.bounds([o])
            xm, ym = (x0 + x1) / 2, (y0 + y1) / 2
            sides = {"tb": [(xm, y0), (xm, y1)], "lr": [(x0, ym), (x1, ym)],
                     "four": [(xm, y0), (xm, y1), (x0, ym), (x1, ym)]}[p.gaps]
            centres = [_point_to_s(l, s) for s in sides]
            if geom.path_length(l, True) < 4 * p.gap_width * len(centres):
                res.warnings.append("The tabs take up most of the perimeter")
        loops.append((l, centres, True))
    for l in inner:
        a = np.asarray(l, dtype=float)
        if geom.area(a) < 0:
            a = a[::-1]
        for q in geom.clean(geom.offset([a], -r), 0.005):
            loops.append((q, [], False))
    if not loops:
        raise ValueError("Outline too small for this tool")
    w = _Writer(origin, f"Cut-out, tool {p.tool_dia:g} mm", p.spindle, p.travel_z)
    w.start()
    depths = []
    z = 0.0
    while z > p.cut_z + 1e-9:
        z = max(z - p.step, p.cut_z)
        depths.append(z)
    # inner cut-outs first, board outline last, so the board stays held until the end
    for loop, centres, _ in sorted(loops, key=lambda t: t[2]):
        pieces = split_gaps(loop, centres, p.gap_width + p.tool_dia) if centres else [np.vstack([loop, loop[:1]])]
        for z in depths:
            for pc in pieces:
                w.lift()
                w.rapid_xy(pc[0])
                w.feed_z(z, p.plunge)
                for q in pc[1:]:
                    w.feed_xy(q, p.feed)
        for pc in pieces:
            res.paths.append((pc, len(centres) == 0))
    w.end()
    res.gcode, res.length, res.time_s = w.text(), w.cut_len, w.time
    return res


def rect_outline(bounds, margin: float) -> np.ndarray:
    x0, y0, x1, y1 = bounds
    return np.array([[x0 - margin, y0 - margin], [x1 + margin, y0 - margin], [x1 + margin, y1 + margin], [x0 - margin, y1 + margin]])

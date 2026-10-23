"""Excellon drill file parser (drills, G85 slots and routed slots)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

INCH = 25.4


@dataclass
class DrillTool:
    number: int
    dia: float                                      # mm
    holes: np.ndarray = field(default_factory=lambda: np.zeros((0, 2)))
    slots: list = field(default_factory=list)       # (N,2) polylines, N >= 2


@dataclass
class DrillFile:
    name: str = ""
    tools: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def bounds(self):
        pts = [t.holes for t in self.tools.values() if len(t.holes)] + [s for t in self.tools.values() for s in t.slots]
        if not pts:
            return None
        a = np.vstack(pts)
        return (float(a[:, 0].min()), float(a[:, 1].min()), float(a[:, 0].max()), float(a[:, 1].max()))

    def hole_count(self) -> int:
        return sum(len(t.holes) + len(t.slots) for t in self.tools.values())


def parse_excellon(text: str, name: str = "") -> DrillFile:
    df = DrillFile(name=name)
    unit = None
    fmt = None
    zeros = "L"                                     # L: leading zeros present (trailing omitted); T: trailing present
    header = "M48" in text
    defs: dict[int, float] = {}
    cur: int | None = None
    pos = np.zeros(2)
    holes: dict[int, list] = {}
    slots: dict[int, list] = {}
    route = False
    pen_down = False
    route_pts: list = []

    def scale() -> float:
        return INCH if unit == "INCH" else 1.0

    def num(s: str) -> float:
        if "." in s:
            return float(s) * scale()
        sign = -1 if s.startswith("-") else 1
        d = s.lstrip("+-")
        i, dec = fmt or ((2, 4) if unit == "INCH" else (3, 3))
        if zeros == "L":
            d = d.ljust(i + dec, "0")
        return sign * int(d or "0") / 10 ** dec * scale()

    def end_route() -> None:
        nonlocal route_pts
        if cur is not None and len(route_pts) >= 2:
            slots.setdefault(cur, []).append(np.array(route_pts))
        route_pts = []

    for raw in text.replace("\r", "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if line.startswith(";"):
            m = re.search(r"FILE_FORMAT\s*=\s*(\d+):(\d+)", line)
            if m:
                fmt = (int(m.group(1)), int(m.group(2)))
            continue
        if header:
            if line in ("%", "M95"):
                header = False
                continue
            up = line.upper()
            if up.startswith(("INCH", "METRIC")):
                unit = "INCH" if up.startswith("INCH") else "METRIC"
                if ",TZ" in up:
                    zeros = "T"
                elif ",LZ" in up:
                    zeros = "L"
                continue
            m = re.match(r"T(\d+)(?:[A-Z]*?)C([\d.]+)", up)
            if m:
                defs[int(m.group(1))] = float(m.group(2))
                continue
            if up.startswith("ICI"):
                df.warnings.append("Incremental coordinates (ICI) not supported")
            continue
        up = line.upper()
        if unit is None:
            unit = "INCH"
            df.warnings.append("Units not specified in the file: assuming inches")
        m = re.match(r"^T(\d+)$", up)
        if m:
            end_route()
            t = int(m.group(1))
            cur = t if t != 0 else None
            if cur is not None and cur not in defs:
                df.warnings.append(f"Tool T{cur} not defined")
                cur = None
            continue
        if up.startswith(("M30", "M00")):
            break
        if up in ("M15",):
            pen_down = True
            route_pts = [pos.copy()]
            continue
        if up in ("M16", "M17"):
            pen_down = False
            end_route()
            continue
        if up in ("G05", "G90", "G91", "G93", "G40", "G81") or up.startswith(("M71", "M72", "FMAT", "VER", "ICI")):
            if up == "G91":
                df.warnings.append("Incremental coordinates (G91) not supported")
            if up == "G05":
                route = False
            continue
        if up.startswith("R") and re.match(r"^R\d+", up):
            df.warnings.append("Repeat patterns (R) not supported")
            continue
        g = re.match(r"^G0?([01])", up)
        if g:
            route = True
            up = up[g.end():]
        slot = re.match(r"^X([+-]?[\d.]+)Y([+-]?[\d.]+)G85X([+-]?[\d.]+)Y([+-]?[\d.]+)$", up)
        if slot and cur is not None:
            a = np.array([num(slot.group(1)), num(slot.group(2))])
            b = np.array([num(slot.group(3)), num(slot.group(4))])
            slots.setdefault(cur, []).append(np.array([a, b]))
            pos = b
            continue
        xy = re.match(r"^(?:X([+-]?[\d.]+))?(?:Y([+-]?[\d.]+))?$", up)
        if xy and (xy.group(1) or xy.group(2)):
            new = pos.copy()
            if xy.group(1):
                new[0] = num(xy.group(1))
            if xy.group(2):
                new[1] = num(xy.group(2))
            pos = new
            if route:
                if pen_down:
                    route_pts.append(pos.copy())
            elif cur is not None:
                holes.setdefault(cur, []).append(pos.copy())
            else:
                df.warnings.append("Hole with no tool selected")
            continue
    end_route()
    for t, dia in defs.items():
        h = holes.get(t, [])
        s = slots.get(t, [])
        if h or s:
            df.tools[t] = DrillTool(t, dia * scale() if unit == "INCH" else dia, np.array(h).reshape(-1, 2), s)
    if not df.tools:
        df.warnings.append("No holes found in the file")
    return df

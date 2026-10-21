"""Builds a synthetic demo board around the real drill file (TrafficLight_drill.txt):
pads on every hole, traces between neighbours on the same row/column, and a rectangular outline.
The copper is NOT the real TrafficLight copper layer - it only exists so the demo has something to isolate."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from flatdeck.excellon import parse_excellon  # noqa: E402

here = os.path.dirname(os.path.abspath(__file__))
df = parse_excellon(open(os.path.join(here, "TrafficLight_drill.txt")).read())
holes = [(x, y, t.dia) for t in df.tools.values() for x, y in t.holes]
x0, y0, x1, y1 = df.bounds()


def fmt(v):                      # FSLAX46Y46, millimetres
    return f"{int(round(v * 1e6)):d}"


def pad_dia(d):
    return max(1.6, d + 0.8)


copper = ["%FSLAX46Y46*%", "%MOMM*%", "%ADD10C,0.4*%"]
dias = sorted({round(pad_dia(d), 3) for _, _, d in holes})
for i, d in enumerate(dias):
    copper.append(f"%ADD{11 + i}C,{d}*%")
copper.append("D10*")
seen = set()
for i, (x, y, d) in enumerate(holes):
    for j, (x2, y2, d2) in enumerate(holes[i + 1:], i + 1):
        if (abs(y - y2) < 1e-6 or abs(x - x2) < 1e-6) and 0 < (abs(x - x2) + abs(y - y2)) < 6.5 and (i, j) not in seen:
            seen.add((i, j))
            copper += [f"X{fmt(x)}Y{fmt(y)}D02*", f"X{fmt(x2)}Y{fmt(y2)}D01*"]
for i, d in enumerate(dias):
    copper.append(f"D{11 + i}*")
    for x, y, hd in holes:
        if round(pad_dia(hd), 3) == d:
            copper.append(f"X{fmt(x)}Y{fmt(y)}D03*")
copper.append("M02*")
open(os.path.join(here, "demo_copper.gbr"), "w").write("\n".join(copper) + "\n")

m = 3.0
rect = [(x0 - m, y0 - m), (x1 + m, y0 - m), (x1 + m, y1 + m), (x0 - m, y1 + m), (x0 - m, y0 - m)]
outline = ["%FSLAX46Y46*%", "%MOMM*%", "%ADD10C,0.1*%", "D10*", f"X{fmt(rect[0][0])}Y{fmt(rect[0][1])}D02*"]
outline += [f"X{fmt(x)}Y{fmt(y)}D01*" for x, y in rect[1:]] + ["M02*"]
open(os.path.join(here, "demo_outline.gbr"), "w").write("\n".join(outline) + "\n")
print(f"{len(holes)} fori, {len(seen)} piste -> demo_copper.gbr, demo_outline.gbr")

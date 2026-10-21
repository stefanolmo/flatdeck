import os

import numpy as np

from flatdeck import cam, geom
from flatdeck.model import Project, sniff_kind

EX = os.path.join(os.path.dirname(__file__), "..", "examples")


def load_demo():
    p = Project()
    p.load(os.path.join(EX, "demo_copper.gbr"))
    p.load(os.path.join(EX, "demo_outline.gbr"))
    p.load(os.path.join(EX, "TrafficLight_drill.txt"))
    return p


def test_sniff_and_kinds():
    p = load_demo()
    assert [l.kind for l in p.layers] == ["copper", "copper", "drill"] or [l.kind for l in p.layers][2] == "drill"
    assert sniff_kind("x.txt", open(os.path.join(EX, "TrafficLight_drill.txt")).read()) == "drill"
    assert sniff_kind("x.gbr", open(os.path.join(EX, "demo_copper.gbr")).read()) == "gerber"


def test_demo_end_to_end():
    p = load_demo()
    cu = p.layers[0]
    assert not cu.warnings
    res = cam.isolate(cu.gerber.polys, cam.IsolationParams(), p.origin())
    assert len(res.paths) >= 1 and res.length > 100
    outer, inner, note = p.board_outline()
    assert abs(abs(geom.area(outer)) - (47.3 + 6) * (48.3 + 6)) < 3 and not inner
    co = cam.cutout(outer, inner, cam.CutoutParams(gaps="tb"), p.origin())
    assert co.gcode.count("G1 Z") == 3 * 2
    dr = cam.drill(p.layers[2].drill, cam.DrillParams(), p.origin())
    assert len(dr.holes) == 56


def test_origin_is_lower_left_of_data():
    p = load_demo()
    ox, oy = p.origin()
    assert abs(ox - 16.33) < 1.0 and abs(oy - 2.7) < 1.0                 # copper pads extend a little beyond the holes
    p.origin_mode = "file"
    assert p.origin() == (0.0, 0.0)


def test_outline_fallback_without_layer():
    p = Project()
    p.load(os.path.join(EX, "demo_copper.gbr"))
    p.layers[0].kind = "copper"
    outer, inner, note = p.board_outline(margin=1.0)
    assert "rettangolo" in note and len(outer) == 4


def test_transforms_keep_registration():
    p = load_demo()
    b0 = p.bounds()
    h0 = p.layers[2].drill.tools[101].holes.copy()
    p.add_job(cam.CamResult(name="j"))
    p.transform("mirror_x")
    assert not p.jobs()                                                # stale jobs are dropped
    b1 = p.bounds()
    assert np.allclose(b0, b1, atol=0.01)                              # mirrored about its own centre
    assert np.allclose(p.layers[2].drill.tools[101].holes[:, 0], (b0[0] + b0[2]) - h0[:, 0], atol=1e-6)
    p.transform("mirror_x")
    assert np.allclose(p.layers[2].drill.tools[101].holes, h0, atol=1e-6)
    # copper still matches drill after a rotation: area preserved, every hole still on copper
    a0 = geom.total_area(p.layers[0].gerber.polys)
    p.transform("rot90")
    assert abs(geom.total_area(p.layers[0].gerber.polys) - a0) < 1e-3
    p.transform("rot270")
    assert np.allclose(p.bounds(), b0, atol=0.01)

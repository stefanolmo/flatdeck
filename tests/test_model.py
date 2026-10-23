import os

import numpy as np
import pyclipper
import pytest

from flatdeck import cam, geom
from flatdeck.model import Project, sniff_kind

EX = os.path.join(os.path.dirname(__file__), "..", "examples")
FILES = ("TrafficLight_contour.gm1", "TrafficLight_copperTop.gtl", "TrafficLight_copperBottom.gbl", "TrafficLight_drill.txt")


def load_board():
    p = Project()
    for f in FILES:
        p.load(os.path.join(EX, f))
    return p


def test_kinds_sides_and_sniffing():
    p = load_board()
    assert [(l.kind, l.side) for l in p.layers] == [("outline", "top"), ("copper", "top"), ("copper", "bottom"), ("drill", "top")]
    q = Project()
    q.load(os.path.join(EX, "TrafficLight_silkBottom.gbo"))
    q.load(os.path.join(EX, "TrafficLight_maskTop.gts"))
    assert [(l.kind, l.side, l.visible) for l in q.layers] == [("silk", "bottom", False), ("mask", "top", False)]
    assert sniff_kind("x.txt", open(os.path.join(EX, FILES[3])).read()) == "drill"
    assert sniff_kind("x.gtl", open(os.path.join(EX, FILES[1])).read()) == "gerber"


def test_unsupported_file_is_rejected(tmp_path):
    f = tmp_path / "x.xy"
    f.write_text("# Pick And Place List\n")
    with pytest.raises(ValueError):
        Project().load(str(f))


def test_real_board_registration():
    """Every drill hole lies on a pad of both copper layers (parsers agree on units, formats and offsets)."""
    p = load_board()
    holes = np.vstack([t.holes for t in p.layers[3].drill.tools.values()])
    assert len(holes) == 56
    for lay in p.layers[1:3]:
        for h in holes:
            c = [int(round(h[0] * geom.SCALE)), int(round(h[1] * geom.SCALE))]
            assert any(pyclipper.PointInPolygon(c, geom.to_c([q])[0]) != 0 for q in lay.gerber.polys)


def test_outline_is_one_closed_loop_with_origin_at_its_corner():
    p = load_board()
    outer, inner, note = p.board_outline()
    assert not inner and not note
    b = p.board_bounds()
    assert abs((b[2] - b[0]) - 68.35) < 0.1 and abs((b[3] - b[1]) - 53.1) < 0.1
    assert p.origin() == (b[0], b[1])
    assert abs(p.mirror_axis() - (b[0] + b[2]) / 2) < 1e-9
    p.origin_mode = "file"
    assert p.origin() == (0.0, 0.0)


def test_both_sides_end_to_end():
    p = load_board()
    for lay in p.layers[1:3]:
        res = cam.isolate(lay.gerber.polys, cam.IsolationParams(), p.origin())
        assert len(res.paths) > 30 and res.length > 300 and not res.warnings
    outer, inner, _ = p.board_outline()
    co = cam.cutout(outer, inner, cam.CutoutParams(gaps="four"), p.origin())
    assert co.gcode.count("G1 Z") == 3 * 4 and co.lines < 1500
    xs = [float(l.split("X")[1].split()[0]) for l in co.gcode.splitlines() if l.startswith("G1 X")]
    assert min(xs) > -1 and max(xs) < 69.5                              # tool centre stays one radius outside a 68.35 mm board


def test_bottom_side_mirror_flips_about_board_axis():
    p = load_board()
    bot = p.layers[2].gerber.polys
    a = cam.isolate(bot, cam.IsolationParams(), p.origin())
    b = cam.isolate(bot, cam.IsolationParams(mirror_x=p.mirror_axis()), p.origin())
    pa = np.vstack([l for l, _ in a.paths])
    pb = np.vstack([l for l, _ in b.paths])
    assert np.allclose(np.sort(2 * p.mirror_axis() - pa[:, 0]), np.sort(pb[:, 0]), atol=1e-6)
    assert np.allclose(np.sort(pa[:, 1]), np.sort(pb[:, 1]), atol=1e-6)
    d = p.layers[3].drill
    m = cam.drill(d, cam.DrillParams(mirror_x=p.mirror_axis()), p.origin())
    n = cam.drill(d, cam.DrillParams(), p.origin())
    assert np.allclose(sorted(2 * p.mirror_axis() - x for x, _, _ in n.holes), sorted(x for x, _, _ in m.holes), atol=1e-6)


def test_transforms_keep_registration():
    p = load_board()
    b0 = p.board_bounds()
    h0 = p.layers[3].drill.tools[101].holes.copy()
    p.add_job(cam.CamResult(name="j"))
    p.transform("mirror_x")
    assert not p.jobs()                                                # stale jobs are dropped
    assert np.allclose(b0, p.board_bounds(), atol=1e-6)                # mirrored about the board's own centre
    assert np.allclose(p.layers[3].drill.tools[101].holes[:, 0], (b0[0] + b0[2]) - h0[:, 0], atol=1e-6)
    p.transform("mirror_x")
    assert np.allclose(p.layers[3].drill.tools[101].holes, h0, atol=1e-6)
    a0 = geom.total_area(p.layers[1].gerber.polys)
    p.transform("rot90")
    assert abs(geom.total_area(p.layers[1].gerber.polys) - a0) < 1e-3
    p.transform("rot270")
    assert np.allclose(p.bounds(), load_board().bounds(), atol=1e-3)


def test_outline_fallback_without_layer():
    p = Project()
    p.load(os.path.join(EX, FILES[1]))
    outer, inner, note = p.board_outline(margin=1.0)
    assert "bounding box" in note and len(outer) == 4

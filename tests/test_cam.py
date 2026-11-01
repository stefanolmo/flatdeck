import math
import re

import numpy as np
import pytest

from flatdeck import cam, geom
from flatdeck.excellon import DrillFile, DrillTool, parse_excellon
from flatdeck.gerber import parse_gerber

PADS = "%FSLAX24Y24*%\n%MOMM*%\n%ADD10C,1.6*%\n%ADD11C,0.4*%\nD10*\nX00000Y00000D03*\nX100000Y00000D03*\nD11*\nX00000Y00000D02*\nX100000Y00000D01*\nM02*\n"


def coords(gcode):
    pts = []
    for l in gcode.splitlines():
        m = re.match(r"G[01] X(-?[\d.]+) Y(-?[\d.]+)", l)
        if m:
            pts.append((float(m.group(1)), float(m.group(2))))
    return np.array(pts)


def test_vbit():
    assert abs(cam.vbit_diameter(0.1, 30, 0.1) - (0.1 + 0.2 * math.tan(math.radians(15)))) < 1e-9


def test_isolation_single_loop_distance():
    g = parse_gerber(PADS)
    res = cam.isolate(g.polys, cam.IsolationParams(tool_dia=0.2), origin=(0, 0))
    assert len(res.paths) == 1                                   # pads + trace are one island
    loop = res.paths[0][0]
    # every point of the tool path is exactly tool radius away from the copper (distance to the trace axis >= 0.2+0.1)
    d = np.abs(loop[:, 1]) 
    on_trace = (loop[:, 0] > 0.9) & (loop[:, 0] < 9.1)
    assert np.allclose(d[on_trace], 0.2 + 0.1, atol=0.01) or np.allclose(d[on_trace], 0.8 + 0.1, atol=0.02)
    assert "M3 S1000" in res.gcode and res.gcode.rstrip().endswith("M2")
    assert res.length > 20


def test_isolation_passes_ext_int():
    ring = parse_gerber("%FSLAX24Y24*%\n%MOMM*%\n%ADD10C,0.5*%\nD10*\nG75*\nX50000Y00000D02*\nG03X50000Y00000I-50000J0D01*\nM02*\n")
    ext = cam.isolation_loops(ring.polys, cam.IsolationParams(tool_dia=0.2, passes=3, mode="ext"))
    inn = cam.isolation_loops(ring.polys, cam.IsolationParams(tool_dia=0.2, passes=3, mode="int"))
    allm = cam.isolation_loops(ring.polys, cam.IsolationParams(tool_dia=0.2, passes=1))
    assert [len(p) for p in ext] == [1, 1, 1] and [len(p) for p in inn] == [1, 1, 1] and len(allm[0]) == 2
    r = [np.hypot(*p[0][0]) for p in ext]
    assert r[0] < r[1] < r[2]
    assert abs((r[1] - r[0]) - 0.2 * 0.8) < 0.01                    # step = d * (1 - overlap)


def test_isolation_validates():
    g = parse_gerber(PADS)
    with pytest.raises(ValueError):
        cam.isolate(g.polys, cam.IsolationParams(cut_z=0.1))
    with pytest.raises(ValueError):
        cam.isolate(g.polys, cam.IsolationParams(tool_dia=0))


def test_origin_shift_and_modal_feed():
    g = parse_gerber(PADS)
    a = cam.isolate(g.polys, cam.IsolationParams(), origin=(0, 0))
    b = cam.isolate(g.polys, cam.IsolationParams(), origin=(-0.8, -0.8))
    pa = coords(a.gcode)[:-1]                                          # drop the final return to X0 Y0
    pb = coords(b.gcode)[:-1] - 0.8
    dist = np.hypot(*(pb[:, None, :] - pa[None, :, :]).transpose(2, 0, 1)).min(axis=1)
    assert dist.max() < 2e-3                                           # same vertices, shifted (G-code is rounded to 1 µm)
    assert len(re.findall(r"F120", a.gcode)) == 1                  # feed only written when it changes
    assert all(len(l) < 80 for l in a.gcode.splitlines())


def test_drill_file_real():
    d = parse_excellon(open("examples/TrafficLight_drill.txt").read())
    res = cam.drill(d, cam.DrillParams(), origin=(d.bounds()[0], d.bounds()[1]))
    assert res.gcode.count("M0\n") == 4                            # one tool change per bit
    assert len(res.holes) == 56
    plunges = [l for l in res.gcode.splitlines() if l.startswith("G1 Z")]
    assert len(plunges) == 56 and all("Z-1.8" in l for l in plunges)
    assert res.gcode.index("0.80 mm") < res.gcode.index("0.89 mm") < res.gcode.index("1.02 mm") < res.gcode.index("1.32 mm")


def test_drill_tool_selection_and_peck():
    d = parse_excellon(open("examples/TrafficLight_drill.txt").read())
    res = cam.drill(d, cam.DrillParams(tools={100: False, 102: False, 103: False}, peck=0.6))
    assert res.gcode.count("M0\n") == 1 and len(res.holes) == 10
    assert "Z-0.6" in res.gcode and "Z-1.2" in res.gcode and "Z-1.8" in res.gcode
    with pytest.raises(ValueError):
        cam.drill(d, cam.DrillParams(tools={t: False for t in d.tools}))


def test_drill_slot():
    d = DrillFile(tools={1: DrillTool(1, 1.0, np.zeros((0, 2)), [np.array([[0.0, 0], [4, 0]])])})
    res = cam.drill(d, cam.DrillParams(drill_z=-1.2, slot_step=0.6))
    xs = coords(res.gcode)
    assert len(xs) == 2 * 1 + 0 or len(xs) >= 2
    assert "Z-0.6" in res.gcode and "Z-1.2" in res.gcode


def test_split_gaps_lengths():
    sq = np.array([[0, 0], [20, 0], [20, 10], [0, 10.0]])
    parts = cam.split_gaps(sq, [cam._point_to_s(sq, (10, 0)), cam._point_to_s(sq, (10, 10))], 3.0)
    assert len(parts) == 2
    assert abs(sum(geom.path_length(p) for p in parts) - (60 - 6)) < 1e-6


def test_cutout_rectangle_with_gaps():
    outer = geom_rect = np.array([[0, 0], [40, 0], [40, 30], [0, 30.0]])
    res = cam.cutout(outer, [], cam.CutoutParams(tool_dia=1.0, gaps="four", gap_width=3.0, cut_z=-1.8, step=0.6))
    # 3 depths x 4 pieces
    assert res.gcode.count("G1 Z") == 12
    xy = coords(res.gcode)
    assert xy[:, 0].min() >= -0.5 - 1e-6 and xy[:, 0].max() <= 40.5 + 1e-6   # tool centre stays one radius outside
    full = cam.cutout(outer, [], cam.CutoutParams(gaps="none"))
    assert full.gcode.count("G1 Z") == 3
    assert full.length > res.length


def test_cutout_inner_first_and_inside_offset():
    outer = np.array([[0, 0], [40, 0], [40, 30], [0, 30.0]])
    inner = np.array([[10, 10], [20, 10], [20, 20], [10, 20.0]])
    res = cam.cutout(outer, [inner], cam.CutoutParams(tool_dia=1.0, gaps="none", step=1.8))
    first = res.paths[0][0]
    assert first[:, 0].min() >= 10.5 - 1e-6 and first[:, 0].max() <= 19.5 + 1e-6    # slot is cut with the tool inside it
    assert res.paths[-1][0][:, 0].max() > 40


def test_cutout_too_small():
    inner = np.array([[0, 0], [1.0, 0], [1.0, 1.0], [0, 1.0]])
    outer = np.array([[-10, -10], [10, -10], [10, 10], [-10, 10.0]])
    res = cam.cutout(outer, [inner], cam.CutoutParams(tool_dia=2.0, gaps="none"))
    assert len(res.paths) == 1                       # the 1 mm hole cannot be cut with a 2 mm tool: skipped, outline remains


def test_every_generated_line_is_plain_grbl():
    """Only G0/G1/G4/G21/G90/G94, M0/M2/M3/M5, comments; no line over GRBL's 80-char limit."""
    d = parse_excellon(open("examples/TrafficLight_drill.txt").read())
    g = parse_gerber(PADS)
    outer = np.array([[0, 0], [40, 0], [40, 30], [0, 30.0]])
    progs = [cam.drill(d, cam.DrillParams(peck=0.5)).gcode, cam.isolate(g.polys, cam.IsolationParams(passes=3)).gcode,
             cam.cutout(outer, [], cam.CutoutParams(gaps="four")).gcode]
    ok = re.compile(r"^(\(.*\)|G0 (X-?[\d.]+ Y-?[\d.]+|Z-?[\d.]+|X0 Y0)|G1 (X-?[\d.]+ Y-?[\d.]+|Z-?[\d.]+)( F[\d.]+)?|G4 P\d+|G21 G90 G94|M[0235]( S\d+)?)$")
    for prog in progs:
        for line in prog.splitlines():
            assert len(line) < 80 and ok.match(line), line
        assert prog.splitlines()[-1] == "M2"


def _two_pads(gap, d=1.0):
    return [geom.circle(0, 0, d / 2), geom.circle(d + gap, 0, d / 2)]


def _loops_enclosing(res, pt):
    import pyclipper
    c = [int(round(pt[0] * geom.SCALE)), int(round(pt[1] * geom.SCALE))]
    return sum(1 for l, _ in res.paths if pyclipper.PointInPolygon(c, geom.to_c([l])[0]) != 0)


def test_wide_gap_needs_no_fix():
    res = cam.isolate(_two_pads(0.3), cam.IsolationParams(tool_dia=0.2))
    assert len(res.paths) == 2 and not res.warnings and not res.marks


def test_gap_narrower_than_tool_is_still_separated():
    """Gap 0.15 < tool 0.2: the offset loops used to fuse into one ring and nothing was cut between the pads (short circuit)."""
    res = cam.isolate(_two_pads(0.15), cam.IsolationParams(tool_dia=0.2))
    assert len(res.paths) == 2 and not res.marks
    for pad_centre in ((0, 0), (1.15, 0)):
        assert _loops_enclosing(res, pad_centre) == 1                    # each pad has its own groove
    assert any("narrow gap" in w for w in res.warnings)
    # the groove between the pads lies on the gap midline: it never cuts more than r/2 into either pad
    allp = np.vstack([l for l, _ in res.paths])
    mid = allp[(allp[:, 0] > 0.5) & (allp[:, 0] < 0.65)]
    assert len(mid) and np.all(np.abs(mid[:, 0] - 0.575) < 0.07)


def test_gap_smaller_than_tool_radius_is_reported_not_hidden():
    res = cam.isolate(_two_pads(0.06), cam.IsolationParams(tool_dia=0.2))
    assert len(res.paths) == 1 and res.marks
    assert any("could not be isolated" in w for w in res.warnings)
    flipped = cam.isolate(_two_pads(0.06), cam.IsolationParams(tool_dia=0.2, mirror_x=0.0))
    assert abs(np.vstack(flipped.marks)[:, 0].mean() + np.vstack(res.marks)[:, 0].mean()) < 1e-6


def test_pad_inside_a_hole_of_another_island_is_not_confused():
    ring = geom.difference([geom.circle(0, 0, 3.0)], [geom.circle(0, 0, 2.0)])
    inner = [geom.circle(0, 0, 0.5)]
    res = cam.isolate(geom.union(ring + inner), cam.IsolationParams(tool_dia=0.2))
    assert not res.warnings and not res.marks and len(res.paths) == 3


def test_safety_margin_moves_every_pass_outwards():
    trace = geom.stroke(np.array([[0, 0], [10, 0.0]]), 0.4)
    a = cam.isolate(trace, cam.IsolationParams(tool_dia=0.2, passes=2))
    b = cam.isolate(trace, cam.IsolationParams(tool_dia=0.2, passes=2, margin=0.05))
    ya = sorted(float(np.abs(l[:, 1]).max()) for l, _ in a.paths)
    yb = sorted(float(np.abs(l[:, 1]).max()) for l, _ in b.paths)
    assert np.allclose(np.array(yb) - np.array(ya), 0.05, atol=0.002)
    with pytest.raises(ValueError):
        cam.isolate(trace, cam.IsolationParams(margin=-0.1))


def test_margin_is_given_up_in_a_narrow_gap_before_cutting_copper():
    pads = [geom.circle(0, 0, 0.5), geom.circle(1.0 + 0.27, 0, 0.5)]       # gap 0.27: fits a 0.2 tool, but not tool + 2 x 0.05 margin
    res = cam.isolate(pads, cam.IsolationParams(tool_dia=0.2, margin=0.05))
    assert len(res.paths) == 2 and not res.marks
    assert any("reducing the safety margin" in w for w in res.warnings)


def test_narrow_gap_fix_is_local_the_rest_of_the_trace_keeps_its_full_width():
    """Regression: the whole cluster used to be nicked, so a long trace far from the gap came out thinner than designed."""
    pads = [geom.circle(0, 0, 0.5), geom.circle(1.15, 0, 0.5)]                # gap 0.15 mm < tool 0.2 mm
    trace = geom.stroke(np.array([[0.0, 0.0], [0.0, -12.0]]), 0.4)
    res = cam.isolate(geom.union(pads + trace), cam.IsolationParams(tool_dia=0.2))
    assert any("narrow gap" in w for w in res.warnings) and not res.marks
    pts = np.vstack([l for l, _ in res.paths])
    far_end = pts[pts[:, 1] < -11.5]
    assert np.allclose(np.abs(far_end[:, 0]).max(), 0.3, atol=1e-3)           # trace half-width 0.2 + tool radius 0.1
    for centre in ((0, 0), (1.15, 0)):
        assert _loops_enclosing(res, centre) == 1                              # and the pads are still separated
    # every groove point stays at least r/2 from the copper of any island (the nick limit)
    copper = geom.union(pads + trace)
    near = geom.offset(copper, 0.05 - 1e-3)
    import pyclipper
    for p_ in pts:
        c = [int(round(p_[0] * geom.SCALE)), int(round(p_[1] * geom.SCALE))]
        assert not any(pyclipper.PointInPolygon(c, geom.to_c([q])[0]) == 1 for q in near if geom.area(q) > 0)


def test_header_states_origin_and_mirror_so_two_files_can_be_compared():
    d = parse_excellon(open("examples/TrafficLight_drill.txt").read())
    g = parse_gerber(PADS)
    a = cam.drill(d, cam.DrillParams(), origin=(18.199, 5.944)).gcode.splitlines()
    b = cam.isolate(g.polys, cam.IsolationParams(mirror_x=40.0), origin=(16.705, 4.449)).gcode.splitlines()
    assert a[1] == "(X0 Y0 = X18.199 Y5.944 in the CAD files: jobs with the same X0 Y0 line up)"
    assert b[1].startswith("(X0 Y0 = X16.705 Y4.449") and b[2] == "(Mirrored about X=40 in the CAD files: board turned over)"
    assert not any("Mirrored" in l for l in a)


def _pour_with_pad():
    """A 10 mm copper disc with a 8 mm round clearance in it and a 1 mm pad in the middle: the ring between them is left over."""
    ring = geom.difference([geom.circle(0, 0, 5.0)], [geom.circle(0, 0, 4.0)])
    return geom.union(ring + [geom.circle(0, 0, 0.5)])


def _stroke_union(res, width):
    cover = []
    for l, closed in res.paths:
        cover += geom.stroke(np.vstack([l, l[:1]]) if closed else l, width)
    return geom.union(cover)


def test_enclosed_island_is_cleared_when_enabled():
    cu = _pour_with_pad()
    off = cam.isolate(cu, cam.IsolationParams(tool_dia=0.2))
    on = cam.isolate(cu, cam.IsolationParams(tool_dia=0.2, clear_islands=True))
    assert len(on.paths) > len(off.paths) + 5 and on.length > off.length * 3
    assert any("enclosed copper island" in w and "cleared" in w for w in on.warnings) and not any("enclosed" in w for w in off.warnings)
    # the leftover ring (between the pad groove at r=0.6 and the clearance groove at r=3.9) is covered completely by the tool
    regions = cam.enclosed_regions([l for l, _ in off.paths], 0.1)
    left = geom.difference(regions, _stroke_union(on, 0.2))
    assert abs(geom.total_area(regions) - math.pi * (3.8 ** 2 - 0.7 ** 2)) < 0.3          # what the bands (r = 0.1 each side) left
    assert abs(geom.total_area(left)) < 0.005 * geom.total_area(regions), geom.total_area(left)   # only polygon-approximation slivers (µm wide)
    # and the design copper is untouched: no clearing loop gets closer than the tool radius to it
    from flatdeck import islands
    near = islands.split(geom.offset(cu, 0.1 - 0.003))                           # copper grown by (almost) the tool radius
    for l, _ in on.paths[len(off.paths):]:
        for q in l:
            assert not any(isl.contains(*q) for isl in near)


def test_clearing_does_nothing_where_grooves_already_removed_everything():
    ring = geom.difference([geom.circle(0, 0, 5.0)], [geom.circle(0, 0, 4.0)])
    pad = [geom.circle(0, 0, 3.8)]                                            # 0.2 mm gap: narrower than 2 tool radii
    res = cam.isolate(geom.union(ring + pad), cam.IsolationParams(tool_dia=0.2, clear_islands=True))
    plain = cam.isolate(geom.union(ring + pad), cam.IsolationParams(tool_dia=0.2))
    assert len(res.paths) == len(plain.paths)


def test_clearing_needs_inside_grooves_and_respects_mirror():
    cu = _pour_with_pad()
    ext = cam.isolate(cu, cam.IsolationParams(tool_dia=0.2, mode="ext", clear_islands=True))
    assert any("Outside only" in w for w in ext.warnings)
    a = cam.isolate(cu, cam.IsolationParams(tool_dia=0.2, clear_islands=True))
    b = cam.isolate(cu, cam.IsolationParams(tool_dia=0.2, clear_islands=True, mirror_x=10.0))
    assert len(a.paths) == len(b.paths)
    xa, xb = np.vstack([l for l, _ in a.paths])[:, 0], np.vstack([l for l, _ in b.paths])[:, 0]
    assert abs(xb.mean() - (20 - xa.mean())) < 1e-3


def test_no_enclosed_islands_is_reported_not_an_error():
    res = cam.isolate(_two_pads(0.5), cam.IsolationParams(tool_dia=0.2, clear_islands=True))
    assert any("No enclosed copper islands" in w for w in res.warnings) and len(res.paths) == 2


def test_triangle_between_three_pads_is_cleared():
    """Pads whose own groove bands overlap leave a floating triangle between them even though no groove loop encloses it."""
    d = 1.6
    pads = [geom.circle(0, 0, 0.5), geom.circle(d, 0, 0.5), geom.circle(d / 2, d * math.sqrt(3) / 2, 0.5)]
    off = cam.isolate(pads, cam.IsolationParams(tool_dia=0.4))
    on = cam.isolate(pads, cam.IsolationParams(tool_dia=0.4, clear_islands=True))
    assert len(on.paths) > len(off.paths)
    regions = cam.enclosed_regions([l for l, _ in off.paths], 0.2)
    assert len(regions) == 1 and 0.001 < abs(geom.area(regions[0])) < 0.5
    assert abs(geom.total_area(geom.difference(regions, _stroke_union(on, 0.4)))) < 1e-3     # nothing of it is left


def test_fancoil_bottom_with_wide_tool_and_margin_clears_every_enclosed_region_and_keeps_the_margin_strip():
    """The user's board (tool 2.5 mm, safety margin 0.2 mm): 16 enclosed regions between the groove bands, all removed;
    the strip of copper left around the traces by the margin is never touched."""
    from flatdeck import islands
    from flatdeck.model import Project
    p = Project()
    p.load("examples/BoardFancoil_copperBottom.gbl")
    cu = p.layers[0].gerber.polys
    par = cam.IsolationParams(tool_dia=2.5, margin=0.2)
    off = cam.isolate(cu, par)
    on = cam.isolate(cu, cam.IsolationParams(tool_dia=2.5, margin=0.2, clear_islands=True))
    regions = cam.enclosed_regions([l for l, _ in off.paths], 1.25)
    assert len(islands.split(regions)) == 16
    assert any("16 enclosed copper island(s)" in w for w in on.warnings)
    assert abs(geom.total_area(geom.difference(regions, _stroke_union(on, 2.5)))) < 0.005 * geom.total_area(regions)
    # the margin strip: copper grown by the margin stays outside every tool disc (tool centres never closer than margin + r)
    protected = islands.split(geom.offset(geom.union(cu), 0.2 + 1.25 - 0.01))
    for l, _ in on.paths[len(off.paths):]:
        for q in l:
            assert not any(isl.contains(*q) for isl in protected)


def _rect_check(cu, tool, margin, rel=0.005):
    """Run the rectangle option and verify: tangent rectangle, nothing but circuit+margin left inside it, circuit never touched."""
    off = cam.isolate(cu, cam.IsolationParams(tool_dia=tool, margin=margin))
    on = cam.isolate(cu, cam.IsolationParams(tool_dia=tool, margin=margin, clear_rect=True))
    r = tool / 2
    extra = [l for l, _ in on.paths[len(off.paths):]]
    assert extra and any("Rectangle" in w for w in on.warnings)
    # the rectangle touches the outer edge of the outermost groove (loops' bbox grown by the tool radius)
    lo = np.vstack([l for l, _ in off.paths])
    box = (lo[:, 0].min() - r, lo[:, 1].min() - r, lo[:, 0].max() + r, lo[:, 1].max() + r)
    ext = np.vstack(extra)
    assert ext[:, 0].min() >= box[0] + r - 1e-3 and ext[:, 0].max() <= box[2] - r + 1e-3     # the tool never leaves the rectangle
    assert ext[:, 1].min() >= box[1] + r - 1e-3 and ext[:, 1].max() <= box[3] - r + 1e-3
    swept = []
    for l in extra:
        swept += geom.stroke(np.vstack([l, l[:1]]), tool)
    swept = geom.union(swept)
    core = geom.offset(geom.union(cu), margin - 0.01)                                         # circuit + safety-margin strip
    assert abs(geom.total_area(geom.intersection(swept, core))) < 1e-3                         # never touched
    # everything else inside the rectangle (except the four rounded corners) is cut
    allswept = []
    for l, _ in on.paths:
        allswept += geom.stroke(np.vstack([l, l[:1]]), tool)
    rect = [cam.rect_outline(box, 0.0)]
    keep = geom.union(geom.offset(geom.union(cu), margin + 0.0) + allswept)
    left = geom.difference(rect, keep)
    corners = [cam.rect_outline((cx - r - 0.01, cy - r - 0.01, cx + r + 0.01, cy + r + 0.01), 0.0)
               for cx, cy in ((box[0], box[1]), (box[2], box[1]), (box[2], box[3]), (box[0], box[3]))]
    left = geom.difference(left, corners)
    assert abs(geom.total_area(left)) < rel * abs(geom.area(rect[0])), geom.total_area(left)
    return off, on


def test_rectangle_clears_everything_but_the_circuit():
    pads = [geom.circle(0, 0, 0.8), geom.circle(5, 0, 0.8), geom.circle(2.5, 4, 0.8)]
    trace = geom.stroke(np.array([[0, 0], [5, 0.0]]), 0.5) + geom.stroke(np.array([[0, 0], [2.5, 4.0]]), 0.5)
    off, on = _rect_check(geom.union(pads + trace), tool=0.4, margin=0.1)
    assert on.length > off.length * 3 and on.time_s > off.time_s


def test_rectangle_is_off_by_default_and_replaces_island_clearing():
    cu = _pour_with_pad()
    off = cam.isolate(cu, cam.IsolationParams(tool_dia=0.2))
    assert not any("Rectangle" in w for w in off.warnings)
    both = cam.isolate(cu, cam.IsolationParams(tool_dia=0.2, clear_islands=True, clear_rect=True))
    assert any("Rectangle" in w for w in both.warnings) and any("included in the rectangle" in w for w in both.warnings)
    assert not any("cleared with" in w for w in both.warnings)               # the islands pass is not run separately


def test_rectangle_mirrors_with_the_board():
    cu = geom.union([geom.circle(0, 0, 0.8), geom.circle(5, 0, 0.8)])
    a = cam.isolate(cu, cam.IsolationParams(tool_dia=0.4, clear_rect=True))
    b = cam.isolate(cu, cam.IsolationParams(tool_dia=0.4, clear_rect=True, mirror_x=10.0))
    assert len(a.paths) == len(b.paths)
    xa, xb = np.vstack([l for l, _ in a.paths])[:, 0], np.vstack([l for l, _ in b.paths])[:, 0]
    assert abs(xb.mean() - (20 - xa.mean())) < 1e-2


def test_fancoil_rectangle_with_wide_tool_keeps_circuit_and_margin():
    from flatdeck.model import Project
    p = Project()
    p.load("examples/BoardFancoil_copperBottom.gbl")
    off, on = _rect_check(p.layers[0].gerber.polys, tool=2.5, margin=0.2, rel=0.002)     # what is left: the sharp inside corners a round tool cannot reach
    assert on.time_s < off.time_s * 3                                         # area clearing, not an overlapping spiral over the whole board

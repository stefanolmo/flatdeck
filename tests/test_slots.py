import math
import os
import re

import numpy as np
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from flatdeck import cam, geom
from flatdeck.model import Project

EX = os.path.join(os.path.dirname(__file__), "..", "examples")
BOARD = (np.array([[0, 0], [40, 0], [40, 20], [0, 20.0]]), [])


def layout(gap_pad=3.5):
    """HV pads at x=5, low-voltage pads at x=5+4+gap, inside a 40 x 20 mm board."""
    hv = [geom.circle(5, y, 2.0) for y in (5, 10, 15)] + geom.stroke(np.array([[5.0, 5], [5, 15]]), 1.0)
    low = [geom.circle(5 + 2 + gap_pad + 1.5, y, 1.5) for y in (5, 10, 15)]
    return hv, low


def swept(paths, width):
    return geom.union([s for q in paths for s in geom.stroke(q, width)])


def test_auto_slot_runs_between_hv_and_low_voltage_and_keeps_its_distance():
    hv, low = layout()
    paths, warn = cam.auto_slots(hv, low, [], BOARD, 2.0, cam.AutoSlotParams(offset=1.75, reach=6, keepout=0.5))
    assert len(paths) == 1 and not warn
    cut = swept(paths, 2.0)
    assert abs(geom.total_area(geom.intersection(cut, geom.offset(geom.union(hv + low), 0.5 - 0.01)))) < 1e-3   # >= keep-out from all copper
    # it lies between the two groups: x of the slot between the HV pads (right edge x = 7) and the low-voltage ones (left edge x = 9.5)
    xs = paths[0][:, 0]
    assert xs.max() < 10.5
    # a slot is longer than the HV block's side it faces
    assert geom.path_length(paths[0]) > 12


def test_offset_without_room_gives_no_slot_there():
    hv, low = layout()
    ok, _ = cam.auto_slots(hv, low, [], BOARD, 2.0, cam.AutoSlotParams(offset=1.75, reach=6, keepout=0.5))
    far, _ = cam.auto_slots(hv, low, [], BOARD, 2.0, cam.AutoSlotParams(offset=2.6, reach=6, keepout=0.5))
    assert sum(map(geom.path_length, far)) < 0.5 * sum(map(geom.path_length, ok))                  # too close to the low-voltage copper
    wide, warn = cam.auto_slots(hv, low, [], BOARD, 3.5, cam.AutoSlotParams(offset=1.2, reach=6, keepout=0.5))
    assert any("leaves only" in w for w in warn)                                                  # offset too small for that tool


def test_no_slot_where_low_voltage_copper_is_out_of_reach():
    hv, low = layout(gap_pad=30)
    paths, warn = cam.auto_slots(hv, low, [], BOARD, 2.0, cam.AutoSlotParams(offset=1.75, reach=6, keepout=0.5))
    assert paths == [] and any("No slot suggested" in w for w in warn)


def test_slot_reaches_the_board_edge_and_stops_at_holes():
    hv = [geom.circle(3, 10, 2.0)]                                        # 1 mm from the left edge of the board
    low = [geom.circle(10, 10, 2.0)]
    paths, _ = cam.auto_slots(hv, low, [], BOARD, 2.0, cam.AutoSlotParams(offset=1.5, reach=12, keepout=0.3))
    assert paths
    ends = np.vstack([p[[0, -1]] for p in paths])
    on_edge = [(abs(e[0]) < 1e-3 or abs(e[1]) < 1e-3 or abs(e[1] - 20) < 1e-3 or abs(e[0] - 40) < 1e-3) for e in ends]
    assert sum(on_edge) >= 1 and all(0 <= p[:, 0].min() and p[:, 0].max() <= 40 for p in paths)     # clipped to the board
    # a drilled hole on the curve removes that stretch
    full = sum(map(geom.path_length, paths))
    probe = paths[0][len(paths[0]) // 2]
    paths2, _ = cam.auto_slots(hv, low, [(probe[0], probe[1], 1.0)], BOARD, 2.0, cam.AutoSlotParams(offset=1.5, reach=12, keepout=0.3))
    assert sum(map(geom.path_length, paths2)) < full - 1.5
    cut = swept(paths2, 2.0)
    assert abs(geom.total_area(geom.intersection(cut, geom.offset([geom.circle(probe[0], probe[1], 0.5)], 0.3 - 0.01)))) < 1e-3


def test_closed_curve_is_not_suggested_because_it_would_cut_the_island_out():
    hv = [geom.circle(20, 10, 1.5)]
    ring = [geom.circle(20 + 7 * math.cos(a), 10 + 7 * math.sin(a), 1.0) for a in np.linspace(0, 2 * math.pi, 16, endpoint=False)]
    paths, warn = cam.auto_slots(hv, ring, [], BOARD, 2.0, cam.AutoSlotParams(offset=1.5, reach=6, keepout=0.5))
    assert paths == [] and any("cut it out of the board" in w for w in warn)
    # the same island with low-voltage copper on one side only gets an open slot
    side, warn2 = cam.auto_slots(hv, ring[:4], [], BOARD, 2.0, cam.AutoSlotParams(offset=1.5, reach=6, keepout=0.5))
    assert side and not any("cut it out" in w for w in warn2)


def test_auto_slots_needs_marked_hv_copper():
    with pytest.raises(ValueError):
        cam.auto_slots([], [geom.circle(5, 5, 1)], [], BOARD, 2.0, cam.AutoSlotParams())


def gx(gcode):
    return np.array([(float(m.group(1)), float(m.group(2))) for l in gcode.splitlines()
                     for m in [re.match(r"G1 X(-?[\d.]+) Y(-?[\d.]+)", l)] if m])


def test_slot_gcode_goes_back_and_forth_to_full_depth_without_lifting():
    line = np.array([[5.0, 5], [20, 5], [20, 12]])
    res = cam.slots([line], cam.SlotParams(tool_dia=2.0, cut_z=-1.8, step=0.6), (0, 0), "s")
    zs = [l for l in res.gcode.splitlines() if l.startswith("G1 Z")]
    assert [re.search(r"Z(-?[\d.]+)", z).group(1) for z in zs] == ["-0.6", "-1.2", "-1.8"]
    assert res.gcode.count("G0 Z2") == 2                                  # start + end only: no lift between the three passes
    xy = gx(res.gcode)
    assert np.allclose(xy[0], [20, 5], atol=1e-3) or np.allclose(xy[0], [5, 5], atol=1e-3)
    assert res.tool_dia == 2.0 and res.kind == "slots" and res.length == pytest.approx(3 * 22.0, abs=0.01)
    assert not res.warnings and res.gcode.rstrip().endswith("M2") and all(len(l) < 80 for l in res.gcode.splitlines())


def test_wide_slot_is_pocketed_and_covers_the_whole_strip():
    line = np.array([[5.0, 5], [25, 5]])
    res = cam.slots([line], cam.SlotParams(tool_dia=2.0, width=5.0, cut_z=-1.8, step=1.8), (0, 0), "w")
    assert len(res.paths) == 1 and res.tool_dia == 5.0
    strip = geom.stroke(line, 5.0)
    xy = gx(res.gcode)
    cover = geom.union([s for l in _loops(res.gcode) for s in geom.stroke(np.vstack([l, l[:1]]), 2.0)])
    left = geom.difference(strip, cover)
    assert abs(geom.total_area(left)) < 0.02 * abs(geom.total_area(strip)), geom.total_area(left)
    assert xy[:, 1].min() >= 5 - 2.5 + 1.0 - 1e-3 and xy[:, 1].max() <= 5 + 2.5 - 1.0 + 1e-3   # tool stays inside the strip


def _loops(gcode):
    loops, cur = [], []
    for l in gcode.splitlines():
        m = re.match(r"G0 X(-?[\d.]+) Y(-?[\d.]+)", l)
        if m and not l.startswith("G0 X0 Y0"):
            if cur:
                loops.append(np.array(cur))
            cur = [(float(m.group(1)), float(m.group(2)))]
            continue
        m = re.match(r"G1 X(-?[\d.]+) Y(-?[\d.]+)", l)
        if m:
            cur.append((float(m.group(1)), float(m.group(2))))
    if cur:
        loops.append(np.array(cur))
    return loops


def test_slot_checks_flag_copper_holes_and_the_board_edge():
    hv, low = layout()
    bad = np.array([[3.0, 2.0], [3.0, 18.0]])                             # straight through the HV pads
    res = cam.slots([bad], cam.SlotParams(tool_dia=2.0), (0, 0), "x", copper=hv + low, holes=[], board=BOARD)
    assert res.marks and any("closer than" in w for w in res.warnings)
    out = cam.slots([np.array([[30.0, 10], [45, 10]])], cam.SlotParams(tool_dia=2.0), (0, 0), "x", copper=[], holes=[], board=BOARD)
    assert any("outside the board" in w for w in out.warnings)
    hole = cam.slots([np.array([[30.0, 4], [30, 16]])], cam.SlotParams(tool_dia=2.0), (0, 0), "x", copper=[], holes=[(30.5, 10, 1.0)], board=BOARD)
    assert hole.marks
    narrow = cam.slots([np.array([[30.0, 4], [30, 16]])], cam.SlotParams(tool_dia=0.8), (0, 0), "x")
    assert any("under 1 mm" in w for w in narrow.warnings)


def test_slot_validation_and_mirror():
    with pytest.raises(ValueError):
        cam.slots([], cam.SlotParams())
    with pytest.raises(ValueError):
        cam.slots([np.array([[0.0, 0], [5, 0]])], cam.SlotParams(cut_z=0.3))
    a = cam.slots([np.array([[5.0, 5], [15, 5]])], cam.SlotParams(), (0, 0), "a")
    b = cam.slots([np.array([[5.0, 5], [15, 5]])], cam.SlotParams(mirror_x=20.0), (0, 0), "b")
    assert abs(np.vstack([p for p, _ in b.paths])[:, 0].mean() - (40 - np.vstack([p for p, _ in a.paths])[:, 0].mean())) < 1e-6
    assert b.gcode.splitlines()[2].startswith("(Mirrored about X=20")


def test_real_board_suggestion_respects_the_keepout_on_both_layers():
    p = Project()
    for f in ("TrafficLight_contour.gm1", "TrafficLight_copperTop.gtl", "TrafficLight_copperBottom.gbl", "TrafficLight_drill.txt"):
        p.load(os.path.join(EX, f))
    bottom = p.layers[2]
    isl = bottom.islands()
    big = max(range(len(isl)), key=lambda i: geom.area(isl[i].outer))
    assert p.toggle_hv(bottom, *isl[big].outer[0]) is True
    warn = p.suggest_slots(2.0, cam.AutoSlotParams(offset=1.5, reach=8, keepout=0.5))
    assert p.slots and all(s.auto for s in p.slots)
    hv, others, holes = p.slot_obstacles()
    # keep-out from the low-voltage copper of BOTH layers and from the holes
    other_region = geom.union(others + [geom.circle(x, y, d / 2) for x, y, d in holes])
    cut = swept([s.pts for s in p.slots], 2.0)
    assert abs(geom.total_area(geom.intersection(cut, geom.offset(other_region, 0.5 - 0.01)))) < 1e-2
    n = len(p.slots)
    p.add_slot([[10, 10], [20, 10]])                                       # a hand-drawn one survives a new suggestion
    p.suggest_slots(2.0, cam.AutoSlotParams(offset=1.5, reach=8, keepout=0.5))
    assert sum(not s.auto for s in p.slots) == 1 and sum(s.auto for s in p.slots) == n


def test_slots_follow_board_transforms():
    p = Project()
    p.load(os.path.join(EX, "TrafficLight_contour.gm1"))
    p.load(os.path.join(EX, "TrafficLight_copperBottom.gbl"))
    b = p.board_bounds()
    cx = (b[0] + b[2]) / 2
    p.add_slot([[b[0] + 5, 10], [b[0] + 15, 12]])
    before = p.slots[0].pts.copy()
    p.transform("mirror_x")
    assert np.allclose(p.slots[0].pts[:, 0], 2 * cx - before[:, 0]) and np.allclose(p.slots[0].pts[:, 1], before[:, 1])
    p.transform("mirror_x")
    assert np.allclose(p.slots[0].pts, before)


def test_clip_open_splits_and_keeps_pieces():
    line = np.array([[0.0, 0], [10, 0]])
    inside = geom.clip_open([line], [geom.rect(5, 0, 4, 2)], True)
    outside = geom.clip_open([line], [geom.rect(5, 0, 4, 2)], False)
    assert len(inside) == 1 and abs(geom.path_length(inside[0]) - 4) < 1e-3
    assert len(outside) == 2 and abs(sum(map(geom.path_length, outside)) - 6) < 1e-3
    assert geom.clip_open([], [geom.rect(0, 0, 1, 1)]) == []


# ---------------------------------------------------------------- UI
@pytest.fixture(scope="module")
def app():
    from flatdeck import theme
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(theme.STYLESHEET)
    return a


@pytest.fixture()
def win(app, tmp_path):
    from PySide6.QtCore import QSettings
    from flatdeck.mainwindow import MainWindow
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path))
    w = MainWindow()
    w.resize(1360, 820)
    w.show()
    for f in ("TrafficLight_contour.gm1", "TrafficLight_copperTop.gtl", "TrafficLight_copperBottom.gbl", "TrafficLight_drill.txt"):
        w.load_file(os.path.join(EX, f))
    w.view.fit()
    yield w
    w.close()


def screen(win, x, y):
    return QPoint(int(win.view.width() / 2 + (x - win.view.centre[0]) * win.view.scale),
                  int(win.view.height() / 2 - (y - win.view.centre[1]) * win.view.scale))


def test_draw_a_slot_with_the_mouse(win):
    win.tabs.setCurrentWidget(win._slot_tab)
    win.slot_draw.setChecked(True)
    assert win.view.draw_mode
    b = win.project.board_bounds()
    QTest.mouseClick(win.view, Qt.LeftButton, Qt.NoModifier, screen(win, b[0] + 20, b[1] + 20))
    QTest.mouseClick(win.view, Qt.LeftButton, Qt.NoModifier, screen(win, b[0] + 30, b[1] + 20))
    QTest.mouseClick(win.view, Qt.LeftButton, Qt.NoModifier, screen(win, b[0] + 30, b[1] + 30))      # first click of a double click ...
    QTest.mouseDClick(win.view, Qt.LeftButton, Qt.NoModifier, screen(win, b[0] + 30, b[1] + 30))     # ... then the double-click event
    QTest.mouseRelease(win.view, Qt.LeftButton, Qt.NoModifier, screen(win, b[0] + 30, b[1] + 30))    # and its release: must not add a point
    assert len(win.project.slots) == 1 and len(win.project.slots[0].pts) == 3 and not win.view.draft
    assert win.slot_list.count() == 1 and "Drawn 1" in win.slot_list.item(0).text()
    win.view.grab()                                                        # preview paints with the slot and the rubber band
    # Escape cancels a draft, Enter finishes it
    QTest.mouseClick(win.view, Qt.LeftButton, Qt.NoModifier, screen(win, b[0] + 10, b[1] + 10))
    QTest.keyClick(win.view, Qt.Key_Escape)
    assert not win.view.draft and len(win.project.slots) == 1
    QTest.mouseClick(win.view, Qt.LeftButton, Qt.NoModifier, screen(win, b[0] + 10, b[1] + 10))
    QTest.mouseClick(win.view, Qt.LeftButton, Qt.NoModifier, screen(win, b[0] + 15, b[1] + 10))
    QTest.keyClick(win.view, Qt.Key_Return)
    assert len(win.project.slots) == 2
    win.slot_draw.setChecked(False)
    assert not win.view.draw_mode


def test_drawing_only_works_in_the_slots_tab_and_snaps_to_the_board_edge(win):
    win.slot_draw.setChecked(True)
    win.tabs.setCurrentIndex(0)
    assert not win.view.draw_mode
    win.tabs.setCurrentWidget(win._slot_tab)
    assert win.view.draw_mode
    b = win.project.board_bounds()
    win._draw_point(b[0] + 0.1, b[1] + 25)                                 # 0.1 mm from the left edge: snaps onto it
    assert abs(win.view.draft[0][0] - b[0]) < 1e-6
    win._draw_point(b[0] + 30, b[1] + 25)                                  # far from every edge: untouched
    assert abs(win.view.draft[1][0] - (b[0] + 30)) < 1e-9


def test_suggest_then_generate_and_remove(win, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    win.suggest_slots()                                                    # nothing is marked as high voltage yet
    assert shown and "Mark the HV copper" in shown[0] and not win.project.slots
    win.gen_slots()
    assert len(shown) == 2 and not win.project.jobs()                      # nothing to generate
    bottom = next(l for l in win.project.layers if l.name.endswith(".gbl"))
    isl = bottom.islands()
    big = max(range(len(isl)), key=lambda i: geom.area(isl[i].outer))
    win.project.toggle_hv(bottom, *isl[big].outer[0])
    win.slot_offset.setValue(1.5)
    win.slot_reach.setValue(8)
    win.suggest_slots()
    assert win.project.slots and win.slot_list.count() == len(win.project.slots)
    assert "Suggested" in win.slot_list.item(0).text() and "Suggested" in win.messages.toPlainText()
    win.slot_list.setCurrentRow(0)
    assert win.view.slot_sel == 0
    win.gen_slots()
    job = win.project.jobs()[-1].job
    assert job.kind == "slots" and job.tool_dia == 2.0 and "Slots, tool 2 mm" in job.gcode.splitlines()[0]
    assert win.project.jobs()[-1].name.startswith("Slots")
    n = len(win.project.slots)
    win.slot_list.setCurrentRow(0)
    win._slot_remove()
    assert len(win.project.slots) == n - 1
    win._slot_clear()
    assert not win.project.slots and win.slot_list.count() == 0


def test_slot_job_is_regenerated_when_the_origin_moves_and_mirror_is_optional(win):
    win.project.add_slot([[30, 20], [50, 20]])
    win.slot_mirror.setChecked(True)
    win.gen_slots()
    assert "(mirrored)" in win.project.jobs()[-1].name and "Mirrored about" in win.project.jobs()[-1].job.gcode
    win.slot_mirror.setChecked(False)
    win.slot_width.setValue(0)
    win.slot_dia.setValue(3.0)
    assert win.view.slot_width == 3.0
    win.slot_width.setValue(5.0)
    assert win.view.slot_width == 5.0
    win.gen_slots()
    assert win.project.jobs()[-1].job.tool_dia == 5.0                      # the preview band shows the slot width


def test_a_suggested_slot_does_not_trigger_its_own_keepout_warning():
    hv, low = layout()
    paths, _ = cam.auto_slots(hv, low, [], BOARD, 2.0, cam.AutoSlotParams(offset=1.5, reach=6, keepout=0.5))
    res = cam.slots(paths, cam.SlotParams(tool_dia=2.0, keepout=0.5), (0, 0), "s", copper=hv + low, holes=[], board=BOARD)
    assert not res.marks and not any("closer than" in w for w in res.warnings)


def test_narrowest_gap_is_measured():
    hv, low = layout(gap_pad=3.5)
    assert abs(cam.narrowest_gap(hv, low) - 3.5) < 0.02
    assert cam.narrowest_gap(hv, []) is None
    assert cam.narrowest_gap(hv, [geom.circle(500, 500, 1)]) is None


def test_when_no_slot_fits_the_reason_and_the_numbers_are_given():
    hv, low = layout(gap_pad=2.5)                                           # 2.5 mm between the copper
    paths, warn = cam.auto_slots(hv, low, [], BOARD, 3.0, cam.AutoSlotParams(offset=2.0, reach=6, keepout=0.5))
    assert paths == [] and len(warn) == 1
    assert "narrowest gap" in warn[0] and "2.50 mm" in warn[0] and "at most 1.50 mm" in warn[0]
    thin = cam.auto_slots(hv, low, [], BOARD, 1.4, cam.AutoSlotParams(offset=1.25, reach=6, keepout=0.5))
    assert thin[0] and not thin[1]                                          # a thinner tool does fit, no complaint
    wide, w2 = cam.auto_slots(hv, low, [], BOARD, 2.4, cam.AutoSlotParams(offset=1.25, reach=6, keepout=0.0))
    assert wide and not any("wider than that gap" in m for m in w2)                 # no keep-out: a 2.4 mm tool fits a 2.5 mm gap
    narrow = cam.auto_slots(hv, layout(gap_pad=1.0)[1], [], BOARD, 2.0, cam.AutoSlotParams(offset=1.5, reach=6, keepout=0.5))[1]
    assert "wider than that gap" in narrow[0]


def test_partial_room_is_reported_with_how_much():
    hv = [geom.circle(10, 10, 2.0), geom.circle(10, 16, 2.0)]
    low = [geom.circle(16.5, 10, 1.5), geom.circle(15, 16, 1.5)]            # 3 mm gap next to the first pad, 1 mm next to the second
    paths, warn = cam.auto_slots(hv, low, [], BOARD, 2.0, cam.AutoSlotParams(offset=1.5, reach=6, keepout=0.5))
    assert paths and any(m.startswith("Only ") and "mm of the HV edge" in m for m in warn)


def test_fancoil_upper_part_as_hv_explains_why_a_wide_tool_does_not_fit(app, tmp_path, monkeypatch):
    """The reported case: the narrowest gap is 2.51 mm, so a 3 mm (or 2 mm with 0.5 keep-out) slot cannot go between the two parts."""
    from PySide6.QtCore import QSettings
    from flatdeck.mainwindow import MainWindow
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path))
    w = MainWindow()
    w.show()
    w.load_file(os.path.join(EX, "BoardFancoil_copperBottom.gbl"))
    lay = w.project.layers[0]
    for i in lay.islands():
        if i.outer[:, 1].mean() > 40:
            w.project.toggle_hv(lay, *i.outer[0])
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    w.slot_dia.setValue(3.0)
    w.slot_offset.setValue(2.0)
    w.slot_reach.setValue(6)
    w.slot_smooth.setValue(0)
    w.suggest_slots()
    assert not w.project.slots and shown and "No slot could be suggested" in shown[0]
    assert "2.51 mm" in shown[0] and "at most 1.51 mm" in shown[0] and "2.51" in w.messages.toPlainText()
    w.slot_dia.setValue(1.5)
    w.slot_offset.setValue(1.5)
    w.slot_reach.setValue(6)
    w.slot_smooth.setValue(0)
    shown.clear()
    w.suggest_slots()
    assert w.project.slots and sum(s.length() for s in w.project.slots) > 50
    w.gen_slots()
    assert w.project.jobs()[-1].job.kind == "slots"
    w.close()


def wide_layout(gap=6.81):
    hv = [geom.circle(5, y, 2.0) for y in (5, 10, 15)] + geom.stroke(np.array([[5.0, 5], [5, 15]]), 1.0)
    low = [geom.circle(5 + 2 + gap + 1.5, y, 1.5) for y in (5, 10, 15)]
    return hv, low, (np.array([[0, 0], [60, 0], [60, 20], [0, 20.0]]), [])


def test_automatic_offset_puts_the_slot_in_the_middle_of_the_narrowest_gap():
    hv, low, board = wide_layout()
    paths, msgs = cam.auto_slots(hv, low, [], board, 3.0, cam.AutoSlotParams(offset=0, keepout=0.5))
    assert len(paths) == 1 and len(msgs) == 1 and msgs[0].startswith("Note: offset chosen automatically: 3.41 mm")
    xs = np.vstack(paths)[:, 0]
    assert abs(xs.max() - (7 + 3.405)) < 0.05                                 # the slot centre line, mid-way between x=7 and x=13.81
    hv2, low2 = layout(gap_pad=3.5)
    p2, m2 = cam.auto_slots(hv2, low2, [], BOARD, 2.0, cam.AutoSlotParams(offset=0, keepout=0.5))
    assert p2 and "1.75 mm" in m2[0]
    # when the gap is too narrow for the tool the automatic offset cannot help: the explanation comes with the numbers
    p3, m3 = cam.auto_slots(hv2, low2, [], BOARD, 3.5, cam.AutoSlotParams(offset=0, keepout=0.5))
    assert not p3 and any("at most" in m for m in m3)


def test_each_reason_for_an_empty_suggestion_is_named():
    hv, low, board = wide_layout()
    P = lambda o, reach=0: cam.auto_slots(hv, low, [], board, 3.0, cam.AutoSlotParams(offset=o, reach=reach, keepout=0.5, smooth=0))[1]
    reach = P(3.4, reach=1)
    assert "reach" in reach[-1] and "at least 4 mm" in reach[-1]
    far = P(6.5)
    assert "would touch copper along that gap" in far[-1] and "between 2.00 and 4.81" in far[-1]
    near = P(1.5)
    assert any("increase the offset (at least 2 mm)" in m for m in near)
    # the slot would lie outside the board outline
    off_board = cam.auto_slots(hv, low, [], (np.array([[40, 0], [60, 0], [60, 20], [40, 20.0]]), []), 3.0, cam.AutoSlotParams(offset=0, keepout=0.5))
    assert not off_board[0] and any("outside the board outline" in m for m in off_board[1])


def test_ui_defaults_are_automatic_offset_and_a_generous_reach(app, tmp_path):
    from PySide6.QtCore import QSettings
    from flatdeck.mainwindow import MainWindow
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path))
    w = MainWindow()
    assert w.slot_offset.value() == 0 and w.slot_offset.specialValueText() == "automatic"
    assert w.slot_reach.value() == 0 and w.slot_reach.specialValueText() == "automatic"
    assert "SLOT CENTRE LINE" in w.slot_reach.toolTip()
    w.close()


def test_fancoil_whole_upper_part_as_hv_works_with_every_default(app, tmp_path, monkeypatch):
    """The reported 6.81 mm case: all three rows of pads and their traces are HV. Automatic offset, reach and smoothing: one clean line."""
    from PySide6.QtCore import QSettings
    from flatdeck.mainwindow import MainWindow
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path))
    w = MainWindow()
    w.show()
    w.load_file(os.path.join(EX, "BoardFancoil_copperBottom.gbl"))
    lay = w.project.layers[0]
    for i in lay.islands():
        if i.outer[:, 1].mean() > 33:
            w.project.toggle_hv(lay, *i.outer[0])
    assert len(w.project.hv_indices(lay)) == 12
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    for tool in (2.0, 3.0):
        w.slot_dia.setValue(tool)
        w.suggest_slots()
        total = sum(s.length() for s in w.project.slots)
        assert 2 <= len(w.project.slots) <= 3 and total > 50, (tool, total)
        assert not shown                                                       # nothing to complain about: only the "Note:" about the offset
        assert "offset chosen automatically: 3.4" in w.messages.toPlainText()
    w.gen_slots()
    job = w.project.jobs()[-1].job
    assert job.kind == "slots" and not job.marks and not any("closer than" in m for m in job.warnings)
    w.close()

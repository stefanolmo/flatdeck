import math
import os

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from flatdeck import cam, geom, islands
from flatdeck.gerber import parse_gerber
from flatdeck.model import Project

EX = os.path.join(os.path.dirname(__file__), "..", "examples")
HEAD = "%FSLAX24Y24*%\n%MOMM*%\n"


def board(extra=""):
    """Three islands: a 10 mm wide pad with a hole at x=0, a small pad at x=20 and a pad inside the first one's hole."""
    return parse_gerber(HEAD + "%ADD10C,10*%\n%ADD11C,4*%\n%ADD12C,2*%\nD10*\nX00000Y00000D03*\n%LPC*%\nD11*\nX00000Y00000D03*\n%LPD*%\n"
                        "D12*\nX00000Y00000D03*\nX200000Y00000D03*\n" + extra + "M02*\n")


def project_with(layer):
    p = Project()
    p.load(os.path.join(EX, "TrafficLight_copperTop.gtl"))
    p.layers[0].gerber = layer
    p.layers[0]._cache.clear()
    return p, p.layers[0]


def test_split_islands_with_holes_and_nested_island():
    isl = islands.split(board().polys)
    assert len(isl) == 3
    ring = max(isl, key=lambda i: geom.area(i.outer))
    assert len(ring.holes) == 1
    assert ring.contains(4.0, 0.0) and not ring.contains(0.0, 0.0)           # not inside the hole
    inner = next(i for i in isl if i.contains(0.0, 0.0))
    assert not inner.holes and geom.area(inner.outer) < 4


def test_find_with_tolerance_and_interior_point():
    isl = islands.split(board().polys)
    assert islands.find(isl, 20.0, 0.0) is not None
    assert islands.find(isl, 21.2, 0.0) is None
    i = islands.find(isl, 21.2, 0.0, tol=0.5)
    assert i is not None and isl[i].contains(20.0, 0.0)
    x, y = islands.interior_point(isl[i], 21.05, 0.0)
    assert isl[i].contains(x, y)


def test_toggle_marks_and_unmarks():
    p, lay = project_with(board())
    assert p.toggle_hv(lay, 4.0, 0.0) is True
    assert p.toggle_hv(lay, 20.0, 0.0) is True
    assert len(p.hv_indices(lay)) == 2
    assert p.toggle_hv(lay, 4.0, 0.0) is False
    assert len(p.hv_indices(lay)) == 1
    assert p.toggle_hv(lay, 50.0, 50.0) is None
    hv, others = p.hv_paths(lay, True), p.hv_paths(lay, False)
    assert abs(geom.total_area(hv) - math.pi) < 0.05                        # the 2 mm pad at x=20
    assert abs(geom.total_area(others) - (math.pi * 25 - math.pi * 4 + math.pi)) < 0.2


def test_marks_survive_transforms():
    p = Project()
    p.load(os.path.join(EX, "TrafficLight_contour.gm1"))
    p.load(os.path.join(EX, "TrafficLight_copperBottom.gbl"))
    lay = p.layers[1]
    isl = lay.islands()
    big = max(range(len(isl)), key=lambda i: geom.area(isl[i].outer))
    c = isl[big].outer.mean(axis=0)
    assert p.toggle_hv(lay, c[0], c[1], tol=50) is True
    before = np.sort(np.vstack(p.hv_paths(lay, True)), axis=0).copy()
    area0 = geom.total_area(p.hv_paths(lay, True))
    for op in ("mirror_x", "rot90", "mirror_y", "rot270"):
        p.transform(op)
        assert len(p.hv_indices(lay)) == 1, op
    assert abs(geom.total_area(p.hv_paths(lay, True)) - area0) < 1e-3


def test_offsets_cover_the_band_without_gaps():
    assert cam.hv_offsets(cam.HVParams(clearance=3, tool_dia=3)) == [1.5]
    offs = cam.hv_offsets(cam.HVParams(clearance=6, tool_dia=3, overlap=0.2))
    assert offs[0] == 1.5 and abs(offs[-1] - 4.5) < 1e-9
    assert all(b - a <= 3 * 0.8 + 1e-9 for a, b in zip(offs, offs[1:]))
    assert cam.hv_offsets(cam.HVParams(clearance=3, tool_dia=2.5)) == [1.25, 1.75]


def test_clearance_is_measured_from_the_hv_edge():
    pad = [geom.circle(0, 0, 1.0)]
    res = cam.hv_clearance(pad, [], cam.HVParams(clearance=3, tool_dia=3))
    assert len(res.paths) == 1 and not res.warnings
    r = np.hypot(*res.paths[0][0].T)
    assert np.allclose(r, 1.0 + 1.5, atol=0.01)                              # tool centre one radius from the copper edge
    res = cam.hv_clearance(pad, [], cam.HVParams(clearance=6, tool_dia=3))
    radii = sorted({round(float(np.hypot(*l.T).mean()), 1) for l, _ in res.paths})
    assert radii == [2.5, 4.9, 5.5]                                          # 1+1.5, 1+3.9, 1+4.5
    assert "G1 Z-0.1" in res.gcode and res.gcode.count("M3 S1000") == 1


def test_conflict_with_other_copper_is_flagged():
    hv = [geom.circle(0, 0, 1.0)]
    near = [geom.circle(3.0, 0, 0.5)]                                        # 2 mm from the HV edge, inside a 3 mm band
    far = [geom.circle(10.0, 0, 0.5)]
    res = cam.hv_clearance(hv, near + far, cam.HVParams(clearance=3, tool_dia=3))
    assert res.marks and any("milled away" in w for w in res.warnings)
    ok = cam.hv_clearance(hv, far, cam.HVParams(clearance=3, tool_dia=3))
    assert not ok.marks and not ok.warnings


def test_clearance_smaller_than_tool_warns_and_validation():
    res = cam.hv_clearance([geom.circle(0, 0, 1)], [], cam.HVParams(clearance=2, tool_dia=3))
    assert any("smaller than the tool" in w for w in res.warnings)
    with pytest.raises(ValueError):
        cam.hv_clearance([], [], cam.HVParams())
    with pytest.raises(ValueError):
        cam.hv_clearance([geom.circle(0, 0, 1)], [], cam.HVParams(cut_z=0.2))


def test_mirror_flips_paths_and_marks():
    hv = [geom.circle(5, 0, 1.0)]
    near = [geom.circle(8.0, 0, 0.5)]
    a = cam.hv_clearance(hv, near, cam.HVParams())
    b = cam.hv_clearance(hv, near, cam.HVParams(mirror_x=10.0))
    xa = np.vstack([l for l, _ in a.paths])[:, 0]
    xb = np.vstack([l for l, _ in b.paths])[:, 0]
    assert abs(xb.mean() - (20 - xa.mean())) < 0.05
    assert abs(np.vstack(b.marks)[:, 0].mean() - (20 - np.vstack(a.marks)[:, 0].mean())) < 0.05


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
    for f in ("TrafficLight_copperTop.gtl", "TrafficLight_copperBottom.gbl", "TrafficLight_contour.gm1", "TrafficLight_drill.txt"):
        w.load_file(os.path.join(EX, f))
    yield w
    w.close()


def test_pick_hv_island_then_both_jobs(win):
    win.tabs.setCurrentWidget(win._hv_tab)
    win.hv_layer.setCurrentIndex(1)                                          # copperBottom
    win.hv_pick.setChecked(True)
    assert win.view.pick_layer is win._hv_layer()
    lay = win._hv_layer()
    isl = lay.islands()
    big = max(range(len(isl)), key=lambda i: geom.area(isl[i].outer))
    c = isl[big].outer[0]                                                    # a point on the island's edge
    win.view.picked.emit(float(c[0]), float(c[1]))                           # what a click on the view emits
    assert len(win.project.hv_indices(lay)) == 1 and "1 of" in win.hv_count.text()
    assert "1 HV" in win.layer_list.item(1).text()
    win.view.grab()                                                          # paints the red overlay without errors
    win.hv_dia.setValue(3.0)
    win.hv_clear_w.setValue(3.0)
    win.gen_hv()
    job = win.project.jobs()[-1].job
    assert job.kind == "hv" and job.tool_dia == 3.0 and job.paths
    win.view.grab()
    # the fine isolation of the same layer now leaves the HV island out
    win.iso_layer.setCurrentIndex(1)
    win.gen_isolation()
    skipped = win.project.jobs()[-1].job
    win.iso_skip_hv.setChecked(False)
    win.gen_isolation()
    full = win.project.jobs()[-1].job
    assert len(skipped.paths) < len(full.paths)
    win.hv_pick.setChecked(False)
    assert win.view.pick_layer is None


def test_hv_without_marks_asks_for_them(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    win.gen_hv()
    assert shown and not win.project.jobs()


def test_clear_marks(win):
    lay = win._hv_layer()
    win.project.toggle_hv(lay, *lay.islands()[0].outer.mean(axis=0), tol=50)
    win._hv_clear()
    assert not lay.hv_points and "0 of" in win.hv_count.text()

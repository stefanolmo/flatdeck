import os
import re

import numpy as np
import pytest

from flatdeck import cam, geom, islands
from flatdeck.model import Project

EX = os.path.join(os.path.dirname(__file__), "..", "examples")
BOARD = np.array([[0, 0], [60, 0], [60, 40], [0, 40.0]])


def circuit():
    """A small circuit in the middle of a 60 x 40 board: three pads and two traces."""
    pads = [geom.circle(25, 15, 1.2), geom.circle(35, 15, 1.2), geom.circle(30, 25, 1.2)]
    tr = geom.stroke(np.array([[25, 15], [35, 15.0]]), 0.5) + geom.stroke(np.array([[35, 15], [30, 25.0]]), 0.5)
    return geom.union(pads + tr)


def swept(items, width):
    return geom.union([s for a, c in items for s in geom.stroke(np.vstack([a, a[:1]]) if c else a, width)])


def run(tool=2.5, keep=1.0, tool_iso=0.4, margin=0.1, inner=None, board=BOARD):
    cu = circuit()
    iso = cam.IsolationParams(tool_dia=tool_iso, margin=margin)
    raw: list = []
    cam.isolation_loops(cu, iso, None, None, raw)
    items, box, target, tight = cam.frame_clearing(raw, tool_iso / 2, board, inner or [], tool / 2, keep, 0.2)
    return cu, raw, items, box, target, tight


def test_frame_removes_everything_between_the_circuit_rectangle_and_the_outline():
    cu, raw, items, box, target, tight = run()
    assert items and not tight
    tool = 2.5
    cover = swept(items, tool)
    # the circuit rectangle is the outer edge of the grooves (loops' bounding box grown by the tool radius of the isolation)
    lo = np.vstack([l for ls in raw for l in ls])
    assert box[0] == pytest.approx(lo[:, 0].min() - 0.2, abs=0.02) and box[3] == pytest.approx(lo[:, 1].max() + 0.2, abs=0.02)
    left = geom.difference(target, cover)
    corners = [cam.rect_outline((cx - 1.26, cy - 1.26, cx + 1.26, cy + 1.26), 0.0) for cx, cy in ((1, 1), (59, 1), (59, 39), (1, 39))]
    left = geom.difference(left, corners)
    assert abs(geom.total_area(left)) < 0.01 * abs(geom.total_area(target)), geom.total_area(left)
    assert abs(geom.total_area(target) - (58 * 38 - (box[2] - box[0]) * (box[3] - box[1]))) < 1.0


def test_frame_keeps_its_distance_from_the_outline_and_never_touches_the_circuit():
    for keep in (0.5, 1.0, 3.0):
        cu, raw, items, box, target, tight = run(keep=keep)
        cover = swept(items, 2.5)
        strip = geom.difference([BOARD], geom.offset([BOARD], -(keep - 0.01)))           # the keep-out strip along the outline
        assert abs(geom.total_area(geom.intersection(cover, strip))) < 1e-3, keep
        core = geom.offset(cu, 0.1 - 0.01)                                                # circuit + safety-margin strip
        assert abs(geom.total_area(geom.intersection(cover, core))) < 1e-3, keep


def test_inner_cutouts_are_respected():
    inner = [np.array([[10, 10], [20, 10], [20, 20], [10, 20.0]])]
    cu, raw, items, box, target, tight = run(inner=inner)
    cover = swept(items, 2.5)
    zone = geom.offset(inner, 1.0 - 0.01)
    assert abs(geom.total_area(geom.intersection(cover, zone))) < 1e-3


def test_circuit_too_close_to_the_outline_is_reported():
    side = np.array([[22, 0], [60, 0], [60, 40], [22, 40.0]])               # the circuit is ~1.8 mm from the left side only
    cu, raw, items, box, target, tight = run(board=side, keep=2.0)
    assert tight and items
    res = cam.frame(cu, cam.IsolationParams(tool_dia=0.4, margin=0.1), side, [], cam.FrameParams(keep=2.0), (0, 0), "f")
    assert any("closer than 2 mm to the board outline" in w for w in res.warnings)
    assert not run(board=side, keep=0.5)[5]
    small = np.array([[22, 11], [38, 11], [38, 29], [22, 29.0]])             # no room on any side for a 2.5 mm tool
    with pytest.raises(ValueError, match="closer than 2 mm"):
        cam.frame(cu, cam.IsolationParams(tool_dia=0.4, margin=0.1), small, [], cam.FrameParams(keep=2.0), (0, 0), "f")


def test_frame_job_gcode_paths_are_closed_or_open_as_they_should():
    cu = circuit()
    iso = cam.IsolationParams(tool_dia=0.4, margin=0.1)
    res = cam.frame(cu, iso, BOARD, [], cam.FrameParams(), (0, 0), "Frame")
    assert res.kind == "frame" and res.tool_dia == 2.5 and res.warnings and "cleared with" in res.warnings[-1]
    assert res.gcode.splitlines()[0].startswith("(FlatDeck") and "Frame, tool 2.5 mm, 1 mm from the outline" in res.gcode.splitlines()[0]
    assert res.gcode.count("G1 Z-0.1") == len(res.paths) and res.gcode.rstrip().endswith("M2")
    ok = re.compile(r"^(\(.*\)|G0 (X-?[\d.]+ Y-?[\d.]+|Z-?[\d.]+|X0 Y0)|G1 (X-?[\d.]+ Y-?[\d.]+|Z-?[\d.]+)( F[\d.]+)?|G4 P\d+|G21 G90 G94|M[0235]( S\d+)?)$")
    assert all(ok.match(l) and len(l) < 80 for l in res.gcode.splitlines())
    # a closed loop ends where it starts; an open piece does not come back
    for q, closed in res.paths:
        assert closed or np.hypot(*(q[0] - q[-1])) > 0.1


def test_frame_mirror_and_validation():
    cu = circuit()
    iso = cam.IsolationParams(tool_dia=0.4, margin=0.1)
    a = cam.frame(cu, iso, BOARD, [], cam.FrameParams(), (0, 0), "a")
    b = cam.frame(cu, iso, BOARD, [], cam.FrameParams(mirror_x=30.0), (0, 0), "b")
    xa, xb = np.vstack([q for q, _ in a.paths])[:, 0], np.vstack([q for q, _ in b.paths])[:, 0]
    assert abs(xb.mean() - (60 - xa.mean())) < 1e-6 and "Mirrored about X=30" in b.gcode
    with pytest.raises(ValueError):
        cam.frame(cu, iso, BOARD, [], cam.FrameParams(tool_dia=0), (0, 0), "x")
    with pytest.raises(ValueError):
        cam.frame(cu, iso, BOARD, [], cam.FrameParams(cut_z=0.5), (0, 0), "x")
    with pytest.raises(ValueError):
        cam.frame([], iso, BOARD, [], cam.FrameParams(), (0, 0), "x")
    full = np.array([[24, 13], [36, 13], [36, 27], [24, 27.0]])
    with pytest.raises(ValueError):
        cam.frame(cu, iso, full, [], cam.FrameParams(keep=2.0), (0, 0), "x")                     # nothing left to clear


def test_real_board_frame_with_the_defaults():
    p = Project()
    for f in ("TrafficLight_contour.gm1", "TrafficLight_copperBottom.gbl"):
        p.load(os.path.join(EX, f))
    cu = p.layers[1].gerber.polys
    outer, inner, _ = p.board_outline()
    iso = cam.IsolationParams(tool_dia=0.4, margin=0.1)
    res = cam.frame(cu, iso, outer, inner, cam.FrameParams(), p.origin(), "Frame")
    cover = []
    for q, closed in res.paths:
        cover += geom.stroke(np.vstack([q, q[:1]]) if closed else q, 2.5)
    cover = geom.union(cover)
    keep_strip = geom.difference([outer], geom.offset([outer], -(1.0 - 0.01)))
    assert abs(geom.total_area(geom.intersection(cover, keep_strip))) < 1e-2                    # 1 mm from the cut line
    assert abs(geom.total_area(geom.intersection(cover, geom.offset(cu, 0.1 - 0.01)))) < 1e-2   # circuit + margin untouched
    assert res.time_s < 12 * 60                                                                  # a 2.5 mm tool: minutes, not hours


# ---------------------------------------------------------------- UI
@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
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
    yield w
    w.close()


def test_frame_card_defaults_and_job(win):
    assert win.fr_dia.value() == 2.5 and win.fr_keep.value() == 1.0 and win.fr_depth.value() == 0.1 and not win.fr_mirror.isChecked()
    for f in ("TrafficLight_contour.gm1", "TrafficLight_copperBottom.gbl"):
        win.load_file(os.path.join(EX, f))
    win.iso_dia.setValue(0.4)
    win.iso_margin.setValue(0.1)
    win.gen_frame()
    job = win.project.jobs()[-1].job
    assert job.kind == "frame" and job.tool_dia == 2.5 and win.project.jobs()[-1].name.startswith("Frame")
    assert "Frame" in win.messages.toPlainText() and "cleared with" in win.messages.toPlainText()
    win.view.grab()
    win.fr_mirror.setChecked(True)
    win.gen_frame()
    assert "(mirrored)" in win.project.jobs()[-1].name and "Mirrored about" in win.project.jobs()[-1].job.gcode


def test_frame_needs_the_outline_layer(win, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    win.load_file(os.path.join(EX, "TrafficLight_copperBottom.gbl"))
    win.gen_frame()
    assert shown and "outline" in shown[0] and not win.project.jobs()

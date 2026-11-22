import json
import os
import shutil

import numpy as np
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox

from flatdeck import cam, geom, projectfile
from flatdeck.model import Project
from flatdeck.projectfile import ProjectError

EX = os.path.join(os.path.dirname(__file__), "..", "examples")
FILES = ("TrafficLight_contour.gm1", "TrafficLight_copperTop.gtl", "TrafficLight_copperBottom.gbl", "TrafficLight_drill.txt")


def board(tmp_path):
    """A project loaded from COPIES of the example files, so deleting them later is part of the test."""
    src = tmp_path / "originals"
    src.mkdir()
    p = Project()
    for f in FILES:
        shutil.copyfile(os.path.join(EX, f), src / f)
        p.load(str(src / f))
    return p, src


def signature(p):
    return [(l.name, l.kind, l.side, l.visible, None if l.bounds() is None else np.round(l.bounds(), 4).tolist(),
             round(geom.total_area(l.gerber.polys), 3) if l.gerber else None) for l in p.data_layers()]


def test_save_copies_the_files_and_writes_a_project_file(tmp_path):
    p, src = board(tmp_path)
    out = tmp_path / "proj"
    path = projectfile.save_project(p, str(out))
    assert path == str(out / projectfile.FILE) and projectfile.is_project(str(out))
    assert sorted(os.listdir(out / "files")) == sorted(FILES)
    for f in FILES:
        assert (out / "files" / f).read_bytes() == (src / f).read_bytes()                   # byte for byte
    data = json.loads((out / projectfile.FILE).read_text())
    assert data["format"] == 1 and [e["kind"] for e in data["layers"]] == ["outline", "copper", "copper", "drill"]
    assert all(l.path.startswith(str(out / "files")) for l in p.data_layers())                # the project now works on its copies


def test_round_trip_keeps_everything_even_when_the_originals_are_gone(tmp_path):
    p, src = board(tmp_path)
    bottom = p.layers[2]
    isl = bottom.islands()
    big = max(range(len(isl)), key=lambda i: geom.area(isl[i].outer))
    p.toggle_hv(bottom, *isl[big].outer[0])
    p.add_slot([[10.0, 10.0], [20.0, 12.0], [30.0, 12.0]], auto=True)
    p.add_slot([[40.0, 10.0], [50.0, 10.0]])
    p.layers[1].visible = False
    p.origin_mode = "file"
    p.transform("mirror_x")
    p.transform("rot90")
    before = signature(p)
    out = tmp_path / "proj"
    projectfile.save_project(p, str(out), {"fields": {"iso_dia": 0.4}})
    shutil.rmtree(src)                                                                        # the originals disappear
    q, ui, warnings = projectfile.load_project(str(out))
    assert signature(q) == before and not warnings and ui == {"fields": {"iso_dia": 0.4}}
    assert q.origin_mode == "file" and not q.layers[1].visible
    assert len(q.hv_indices(q.layers[2])) == 1 and len(q.layers[1].hv_points) == 0
    assert [(s.auto, np.round(s.pts, 4).tolist()) for s in q.slots] == [(s.auto, np.round(s.pts, 4).tolist()) for s in p.slots]
    for a, b in zip(p.data_layers(), q.data_layers()):
        assert a.ops == b.ops
        if a.drill:
            for n, t in a.drill.tools.items():
                assert np.allclose(t.holes, b.drill.tools[n].holes, atol=1e-5)
    # a toolpath made from the reopened project equals the one from the original
    r1 = cam.isolate(p.layers[2].gerber.polys, cam.IsolationParams(tool_dia=0.4), p.origin())
    r2 = cam.isolate(q.layers[2].gerber.polys, cam.IsolationParams(tool_dia=0.4), q.origin())
    assert abs(r1.length - r2.length) < 0.01 and r1.gcode.splitlines()[1] == r2.gcode.splitlines()[1]


def test_transforms_replay_with_their_own_centres_whatever_the_loading_order(tmp_path):
    """A layer loaded after a mirror is not mirrored: each layer remembers the transforms (and centres) it really had."""
    src = tmp_path / "o"
    src.mkdir()
    for f in FILES[:3]:
        shutil.copyfile(os.path.join(EX, f), src / f)
    p = Project()
    p.load(str(src / FILES[0]))
    p.load(str(src / FILES[1]))
    p.transform("mirror_x")
    p.load(str(src / FILES[2]))                                                               # loaded AFTER the mirror
    p.transform("rot90")
    out = tmp_path / "p"
    projectfile.save_project(p, str(out))
    q = projectfile.load_project(str(out))[0]
    assert signature(q) == signature(p)
    assert [len(l.ops) for l in q.data_layers()] == [2, 2, 1]


def test_save_again_into_the_same_folder_and_name_collisions(tmp_path):
    p, _ = board(tmp_path)
    out = tmp_path / "proj"
    projectfile.save_project(p, str(out))
    projectfile.save_project(p, str(out))                                                     # the layers already live there: nothing breaks
    assert sorted(os.listdir(out / "files")) == sorted(FILES)
    other = tmp_path / "other"
    other.mkdir()
    shutil.copyfile(os.path.join(EX, FILES[1]), other / FILES[1])                              # same file name, another folder
    q = Project()
    q.load(str(tmp_path / "originals" / FILES[1]))
    q.load(str(other / FILES[1]))
    out2 = tmp_path / "proj2"
    projectfile.save_project(q, str(out2))
    assert sorted(os.listdir(out2 / "files")) == ["TrafficLight_copperTop.gtl", "TrafficLight_copperTop_2.gtl"]
    assert len(projectfile.load_project(str(out2))[0].data_layers()) == 2


def test_errors_are_readable(tmp_path):
    with pytest.raises(ProjectError, match="nothing to save"):
        projectfile.save_project(Project(), str(tmp_path / "x"))
    p, src = board(tmp_path)
    os.remove(src / FILES[1])
    with pytest.raises(ProjectError, match="no longer on disk"):
        projectfile.save_project(p, str(tmp_path / "x"))
    assert not os.path.exists(tmp_path / "x" / projectfile.FILE)                              # nothing half-written
    with pytest.raises(ProjectError, match="not a FlatDeck project"):
        projectfile.load_project(str(tmp_path))


def test_a_project_folder_is_untrusted(tmp_path):
    p, _ = board(tmp_path)
    out = tmp_path / "proj"
    projectfile.save_project(p, str(out))
    path = out / projectfile.FILE
    good = json.loads(path.read_text())

    def with_(**kw):
        d = json.loads(json.dumps(good))
        for k, v in kw.items():
            d[k] = v
        path.write_text(json.dumps(d))
        return str(out)

    d = json.loads(json.dumps(good))
    d["layers"][0]["file"] = "../../etc/passwd"
    path.write_text(json.dumps(d))
    with pytest.raises(ProjectError, match="outside its folder"):
        projectfile.load_project(str(out))
    d["layers"][0]["file"] = "/etc/passwd"
    path.write_text(json.dumps(d))
    with pytest.raises(ProjectError, match="outside its folder"):
        projectfile.load_project(str(out))
    d["layers"][0]["file"] = "files/missing.gbr"
    path.write_text(json.dumps(d))
    with pytest.raises(ProjectError, match="is missing"):
        projectfile.load_project(str(out))
    with pytest.raises(ProjectError, match="different version"):
        projectfile.load_project(with_(format=99))
    path.write_text("{ not json")
    with pytest.raises(ProjectError, match="Cannot read"):
        projectfile.load_project(str(out))
    d = json.loads(json.dumps(good))
    d["layers"][1]["ops"] = [["explode", 0, 0]]
    path.write_text(json.dumps(d))
    with pytest.raises(ProjectError, match="damaged"):
        projectfile.load_project(str(out))
    d = json.loads(json.dumps(good))
    d["slots"] = [{"nope": 1}]
    path.write_text(json.dumps(d))
    with pytest.raises(ProjectError, match="damaged"):
        projectfile.load_project(str(out))


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
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path / "settings"))
    w = MainWindow()
    w.resize(1360, 820)
    w.show()
    yield w
    w.close()


def test_save_and_open_through_the_window_restore_the_settings_too(win, tmp_path, monkeypatch):
    for f in FILES:
        win.load_file(os.path.join(EX, f))
    win.iso_dia.setValue(0.4)
    win.iso_margin.setValue(0.1)
    win.iso_passes.setValue(2)
    win.iso_mode.setCurrentIndex(win.iso_mode.findData("ext"))
    win.iso_tool.setCurrentIndex(win.iso_tool.findData("vbit"))
    win.iso_angle.setValue(20)
    win.iso_rect.setChecked(True)
    win.slot_dia.setValue(3.0)
    win.fr_keep.setValue(2.0)
    win.hv_mirror.setChecked(True)
    win.dr_table.item(0, 0).setCheckState(Qt_Unchecked())
    lay = next(l for l in win.project.layers if l.name.endswith(".gbl"))
    isl = lay.islands()
    win.project.toggle_hv(lay, *isl[max(range(len(isl)), key=lambda i: geom.area(isl[i].outer))].outer[0])
    win.project.add_slot([[10, 10], [20, 10]])
    win.gen_isolation()
    win.transform("mirror_x") if False else None
    out = tmp_path / "myproject"
    out.mkdir()
    assert win.save_project_to(str(out)) and win.project_dir == str(out)
    assert "myproject" in win.windowTitle() and "Project saved" in win.messages.toPlainText()
    saved_default = win.qs.value("p/iso_dia")
    # a second window opens it
    from flatdeck.mainwindow import MainWindow
    w2 = MainWindow()
    w2.show()
    assert w2.open_project_from(str(out))
    assert [l.name for l in w2.project.data_layers()] == [l.name for l in win.project.data_layers()]
    assert not w2.project.jobs() and "generate the jobs again" in w2.statusBar().currentMessage()      # G-code is not part of the project
    assert w2.iso_dia.value() == 0.4 and w2.iso_passes.value() == 2 and w2.iso_mode.currentData() == "ext"
    assert w2.iso_tool.currentData() == "vbit" and w2.iso_angle.value() == 20 and w2.iso_rect.isChecked() and not w2.iso_clear.isChecked()
    assert w2.slot_dia.value() == 3.0 and w2.fr_keep.value() == 2.0 and w2.hv_mirror.isChecked()
    assert w2.dr_table.item(0, 0).checkState() == Qt_Unchecked() and w2.dr_table.item(1, 0).checkState() != Qt_Unchecked()
    assert len(w2.project.slots) == 1 and w2.slot_list.count() == 1
    lay2 = next(l for l in w2.project.layers if l.name.endswith(".gbl"))
    assert len(w2.project.hv_indices(lay2)) == 1 and "1 HV" in w2.layer_list.item(w2.project.layers.index(lay2)).text()
    assert "myproject" in w2.windowTitle()
    # opening a project does not touch the user's own defaults
    assert w2.qs.value("p/iso_dia") == saved_default
    w2.view.grab()
    w2.gen_isolation()
    assert w2.project.jobs()[-1].job.kind == "isolation"
    w2.close()


def Qt_Unchecked():
    from PySide6.QtCore import Qt
    return Qt.Unchecked


from flatdeck import geom  # noqa: E402


def test_dialogs_ask_for_a_folder_and_confirm_before_overwriting(win, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QFileDialog
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    win.save_project_dialog()                                                  # nothing loaded yet
    assert shown and "nothing to save" in shown[0]
    for f in FILES:
        win.load_file(os.path.join(EX, f))
    asked = []
    folder = tmp_path / "f1"
    folder.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: asked.append(a[1]) or str(folder))
    win.save_project_dialog()
    assert asked and "folder" in asked[0].lower() and projectfile.is_project(str(folder))
    # the same folder again: saved without asking; another folder that holds a project: asks first
    questions = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: questions.append(a[2]) or QMessageBox.No)
    win.save_project_dialog()
    assert not questions
    folder2 = tmp_path / "f2"
    folder2.mkdir()
    (folder2 / flatname()).write_text("x")
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(folder2))
    win.save_project_dialog()
    assert questions and "not empty" in questions[0] and not projectfile.is_project(str(folder2))         # said No: nothing written
    shutil.copyfile(folder / projectfile.FILE, folder2 / projectfile.FILE)
    questions.clear()
    win.save_project_dialog()
    assert questions and "already contains a FlatDeck project" in questions[0]
    # opening replaces the current project only after a confirmation
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(folder))
    n = len(win.project.layers)
    questions.clear()
    win.open_project_dialog()
    assert questions and "Replace the current project" in questions[0] and len(win.project.layers) == n
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    win.open_project_dialog()
    assert win.project_dir == str(folder)


def flatname():
    return "readme.txt"


def test_a_damaged_project_shows_a_message_and_leaves_the_session_alone(win, tmp_path, monkeypatch):
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    win.load_file(os.path.join(EX, FILES[1]))
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / projectfile.FILE).write_text('{"format": 1, "layers": [{"file": "../x"}]}')
    assert not win.open_project_from(str(bad))
    assert warned and "outside its folder" in warned[0] and len(win.project.layers) == 1 and win.project_dir is None
    assert all(a.toolTip() for a in win.findChildren(type(win.save_act)) if a.text() in ("Open project…", "Save project…"))


def test_toolbar_has_the_project_actions(win):
    texts = {a.text(): a for a in win.findChildren(type(win.save_act))}
    assert "Open project…" in texts and "Save project…" in texts
    assert "copy of the loaded files" in texts["Save project…"].toolTip() and "G-code is not saved" in texts["Save project…"].toolTip()


def test_drill_bit_choice_survives_a_refresh(win):
    for f in FILES:
        win.load_file(os.path.join(EX, f))
    win.dr_table.item(1, 0).setCheckState(Qt_Unchecked())
    win.iso_layer.setCurrentIndex(0)
    win.gen_isolation()                                                        # rebuilds the lists
    assert win.dr_table.item(1, 0).checkState() == Qt_Unchecked() and win.dr_table.item(0, 0).checkState() != Qt_Unchecked()
    win.gen_drill()
    assert len(win.project.jobs()[-1].job.holes) == 56 - len(win.project.layers[3].drill.tools[sorted(win.project.layers[3].drill.tools,
        key=lambda n: win.project.layers[3].drill.tools[n].dia)[1]].holes)

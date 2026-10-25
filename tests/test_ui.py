import os

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from flatdeck import __version__, theme
from flatdeck.mainwindow import MainWindow
from flatdeck.splash import SplashScreen

EX = os.path.join(os.path.dirname(__file__), "..", "examples")


@pytest.fixture(scope="module")
def app():
    a = QApplication.instance() or QApplication([])
    a.setStyleSheet(theme.STYLESHEET)
    return a


@pytest.fixture()
def win(app, tmp_path):
    from PySide6.QtCore import QSettings
    QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, str(tmp_path))
    w = MainWindow()
    w.resize(1360, 820)
    w.show()
    yield w
    w.close()


def load_all(w):
    for f in ("TrafficLight_copperTop.gtl", "TrafficLight_copperBottom.gbl", "TrafficLight_contour.gm1", "TrafficLight_drill.txt"):
        w.load_file(os.path.join(EX, f))


def test_load_and_generate_three_jobs(win):
    load_all(win)
    kinds = [l.kind for l in win.project.layers]
    assert kinds == ["copper", "copper", "outline", "drill"]
    win.iso_layer.setCurrentIndex(0)
    win.gen_isolation()
    win.gen_drill()
    win.gen_cutout()
    jobs = win.project.jobs()
    assert [j.job.kind for j in jobs] == ["isolation", "drill", "cutout"]
    assert win.layer_list.count() == 7
    win.layer_list.setCurrentRow(6)
    assert "G21 G90 G94" in win.code.toPlainText() and win.save_act.isEnabled()
    win.layer_list.setCurrentRow(0)
    assert not win.save_act.isEnabled() and win.code.toPlainText() == ""


def test_drill_tool_selection_from_table(win):
    load_all(win)
    assert win.dr_table.rowCount() == 4
    win.dr_table.item(0, 0).setCheckState(Qt.Unchecked)           # smallest bit (0.8 mm) off
    win.gen_drill()
    res = win.project.jobs()[-1].job
    assert len(res.holes) == 56 - 10


def test_transform_removes_jobs_after_confirm(win, monkeypatch):
    load_all(win)
    win.gen_isolation()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    win.transform("mirror_x")
    assert not win.project.jobs()
    win.gen_isolation()
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    win.transform("rot90")
    assert win.project.jobs()


def test_vbit_effective_diameter(win):
    win.iso_tool.setCurrentIndex(win.iso_tool.findData("vbit"))
    win.iso_tip.setValue(0.1)
    win.iso_angle.setValue(30)
    win.iso_depth.setValue(0.1)
    assert abs(win._iso_eff - 0.1 - 0.2 * 0.2679) < 1e-3
    assert not win.iso_dia.isEnabled()


def test_validation_error_shows_dialog_not_crash(win, monkeypatch):
    load_all(win)
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    win.dr_depth.setValue(0.1)
    win.dr_change.setValue(1)
    win.dr_travel.setValue(5)                                      # change Z below travel Z
    win.gen_drill()
    assert shown and not win.project.jobs()


def test_layer_kind_override_and_origin_mode(win):
    load_all(win)
    win.project.layers[2].kind = "copper"
    win._refresh()
    assert win.iso_layer.count() == 3
    win.origin_combo.setCurrentIndex(win.origin_combo.findData("file"))
    assert win.project.origin() == (0.0, 0.0)


def test_view_paints_and_fits(win, tmp_path):
    load_all(win)
    win.gen_isolation()
    win.gen_drill()
    win.gen_cutout()
    win.view.fit()
    img = win.view.grab().toImage()
    assert not img.isNull() and img.width() > 300
    assert win.view.scale > 1
    win.grab().save(str(tmp_path / "w.png"))


def test_splash_renders_every_stage(app, tmp_path):
    s = SplashScreen(__version__)
    for ms in (0, 400, 1200, 2000, 2600, 2999):
        s.set_time(ms)
        img = s.grab().toImage()
        assert img.width() == 780 and img.height() == 440
    assert s.total > 100


def test_mirror_is_off_by_default_and_names_job(win):
    load_all(win)
    win.iso_layer.setCurrentIndex(1)                               # copperBottom: still not mirrored unless asked
    assert not win.iso_mirror.isChecked() and not win.dr_mirror.isChecked()
    win.gen_isolation()
    assert "(mirrored)" not in win.project.jobs()[-1].name
    win.iso_mirror.setChecked(True)
    win.gen_isolation()
    assert "(mirrored)" in win.project.jobs()[-1].name
    win.dr_mirror.setChecked(True)
    win.gen_drill()
    assert "(mirrored)" in win.project.jobs()[-1].name


def test_app_icon_file_loads(app):
    from PySide6.QtGui import QIcon
    path = os.path.join(os.path.dirname(__file__), "..", "flatdeck", "flatdeck.svg")
    pm = QIcon(path).pixmap(64, 64)
    assert not pm.isNull() and pm.toImage().pixelColor(32, 8).alpha() > 0


def test_vbit_angle_presets_and_free_value(win):
    import math
    win.iso_tool.setCurrentIndex(win.iso_tool.findData("vbit"))
    assert win.iso_angle.isEnabled()
    items = [win.iso_angle.itemText(i) for i in range(win.iso_angle.count())]
    assert "20°" in items and "30°" in items
    win.iso_tip.setValue(0.1)
    win.iso_depth.setValue(0.1)
    win.iso_angle.setCurrentIndex(items.index("20°"))
    win.iso_angle.activated.emit(items.index("20°"))
    assert win.iso_angle.value() == 20 and abs(win._iso_eff - (0.1 + 0.2 * math.tan(math.radians(10)))) < 1e-4
    win.iso_angle.lineEdit().setText("22,5")                                # any other angle, typed
    win.iso_angle.lineEdit().editingFinished.emit()
    assert win.iso_angle.value() == 22.5 and abs(win._iso_eff - (0.1 + 0.2 * math.tan(math.radians(11.25)))) < 1e-4
    win.iso_angle.lineEdit().setText("abc")
    assert win.iso_angle.value() == 30                                      # invalid text falls back to 30


def test_measured_width_overrides_computed_one_and_margin_is_passed(win):
    load_all(win)
    win.iso_tool.setCurrentIndex(win.iso_tool.findData("vbit"))
    win.iso_tip.setValue(0.1)
    win.iso_depth.setValue(0.2)
    win.iso_angle.setValue(20)
    assert abs(win._iso_eff - 0.171) < 1e-3
    win.iso_measured.setValue(0.25)
    assert win._iso_eff == 0.25 and "measured" in win.iso_eff.text()
    win.iso_margin.setValue(0.03)
    win.iso_layer.setCurrentIndex(0)
    win.gen_isolation()
    job = win.project.jobs()[-1].job
    assert job.tool_dia == 0.25 and "Isolation" in job.gcode.splitlines()[0]
    win.iso_measured.setValue(0)
    assert abs(win._iso_eff - 0.171) < 1e-3


def test_sharp_vbit_tip_of_zero_is_allowed(win):
    import math
    win.iso_tool.setCurrentIndex(win.iso_tool.findData("vbit"))
    win.iso_tip.setValue(0.0)
    win.iso_angle.setValue(20)
    win.iso_depth.setValue(0.1)
    assert win.iso_tip.value() == 0.0
    assert abs(win._iso_eff - 2 * 0.1 * math.tan(math.radians(10))) < 1e-6      # 0.0353 mm: only depth and angle

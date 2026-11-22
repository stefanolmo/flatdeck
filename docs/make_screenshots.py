"""Regenerates the README images: QT_QPA_PLATFORM=offscreen python3 docs/make_screenshots.py"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "..", "examples")
sys.path.insert(0, os.path.join(HERE, ".."))

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from flatdeck import __version__, theme  # noqa: E402
from flatdeck.mainwindow import MainWindow  # noqa: E402
from flatdeck.splash import SplashScreen  # noqa: E402

QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, tempfile.mkdtemp())
app = QApplication([])
app.setStyleSheet(theme.STYLESHEET)
f = QFont(app.font())
f.setPointSize(10)
app.setFont(f)


def shot(w, name):
    w.view.fit()
    app.processEvents()
    w.grab().save(os.path.join(HERE, name))
    w.close()


def window(files, height=860):
    w = MainWindow()
    w.resize(1400, height)
    w.show()
    for n in files:
        w.load_file(os.path.join(EX, n))
    return w


# splash screen
s = SplashScreen(__version__)
s.set_time(2300)
s.grab().save(os.path.join(HERE, "splash.png"))

# main window: isolation, drilling and cut-out of the double-sided example board
w = window(("TrafficLight_copperTop.gtl", "TrafficLight_copperBottom.gbl", "TrafficLight_contour.gm1", "TrafficLight_drill.txt"))
w.iso_layer.setCurrentIndex(0)
w.gen_isolation()
w.gen_drill()
w.gen_cutout()
w.layer_list.setCurrentRow(4)            # the isolation job
shot(w, "main.png")

# high-voltage band and slots: the upper part of the Fancoil board (three rows of pads and their traces) is the HV zone
w = window(("BoardFancoil_copperBottom.gbl",), 900)
lay = w.project.layers[0]
for isl in lay.islands():
    if isl.outer[:, 1].mean() > 33:
        w.project.toggle_hv(lay, *isl.outer[0])
w.gen_hv()                                # 3 mm flat end mill, 3 mm clearance
w.tabs.setCurrentWidget(w._slot_tab)
w.slot_dia.setValue(2.0)
w.suggest_slots()                         # offset and reach: automatic
w.gen_slots()
w.layer_list.setCurrentRow(len(w.project.layers) - 1)
shot(w, "hv-slots.png")

# rectangle clearing, frame clearing and cut-out
w = window(("TrafficLight_contour.gm1", "TrafficLight_copperBottom.gbl"), 1000)
w.iso_layer.setCurrentIndex(0)
w.iso_dia.setValue(0.8)
w.iso_margin.setValue(0.1)
w.iso_rect.setChecked(True)
w.gen_isolation()
w.gen_frame()
w.gen_cutout()
w.tabs.setCurrentIndex(w.tabs.count() - 1)          # Cut-out (with the frame card)
w.layer_list.setCurrentRow(len(w.project.layers) - 2)
shot(w, "clearing.png")

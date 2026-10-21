"""Regenerates the README images: QT_QPA_PLATFORM=offscreen python3 docs/make_screenshots.py"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
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

s = SplashScreen(__version__)
s.set_time(2300)
s.grab().save(os.path.join(HERE, "splash.png"))

w = MainWindow()
w.resize(1400, 860)
w.show()
for name in ("demo_copper.gbr", "demo_outline.gbr", "TrafficLight_drill.txt"):
    w.load_file(os.path.join(HERE, "..", "examples", name))
w.gen_isolation()
w.gen_drill()
w.gen_cutout()
w.layer_list.setCurrentRow(3)            # isolation job
w.view.fit()
app.processEvents()
w.grab().save(os.path.join(HERE, "main.png"))

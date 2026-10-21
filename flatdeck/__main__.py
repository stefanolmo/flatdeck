import argparse
import os
import signal
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from . import __version__, theme
from .mainwindow import APP_NAME, MainWindow
from .splash import SplashScreen


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="flatdeck", description="Gerber / Excellon → G-code GRBL per fresare circuiti stampati")
    ap.add_argument("files", nargs="*", help="file Gerber / Excellon da aprire")
    ap.add_argument("--screenshot", metavar="PNG", help="(sviluppo) salva uno screenshot ed esci")
    ap.add_argument("--no-splash", action="store_true", help="non mostrare la schermata iniziale")
    args = ap.parse_args(argv)

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setDesktopFileName("flatdeck")
    app.setStyleSheet(theme.STYLESHEET)
    f = QFont(app.font())
    f.setPointSize(10)
    app.setFont(f)
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    splash = None
    if not (args.no_splash or args.screenshot or os.environ.get("FLATDECK_NO_SPLASH")):
        splash = SplashScreen(__version__)     # 3 s; the main window is prepared behind it
        splash.start()
        app.processEvents()
    w = MainWindow()
    if splash is None:
        w.show()
    else:
        splash.finished.connect(w.show)
        splash.finished.connect(w.raise_)
    for path in args.files:
        w.load_file(os.path.abspath(path))
    if args.screenshot:
        QTimer.singleShot(int(os.environ.get("FLATDECK_SHOT_DELAY", "800")),
                          lambda: (w.grab().save(args.screenshot), w.close(), app.quit()))
        w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

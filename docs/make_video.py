"""Renders a ~60 s promo video of the real application (no audio, English captions).

    QT_QPA_PLATFORM=offscreen python3 docs/make_video.py OUT_DIR      # writes OUT_DIR/frames/*.jpg and OUT_DIR/flatdeck.mp4

The program is driven by a script (virtual time: 30 frames per second, whatever the real speed), each frame is a grab of the window.
Needs ffmpeg for the final encoding.
"""
import os
import subprocess
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "..", "examples")
sys.path.insert(0, os.path.join(HERE, ".."))

import numpy as np  # noqa: E402
from PySide6.QtCore import QPoint, QRectF, QSettings, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication, QToolBar  # noqa: E402

from flatdeck import __version__, theme, viewer  # noqa: E402
from flatdeck.mainwindow import MainWindow  # noqa: E402

W, H, FPS, TOTAL = 1920, 1080, 30, 60.0
viewer.HEAVY_POINTS = 10 ** 9              # always antialiased: every frame is drawn in full

out = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
frames = os.path.join(out, "frames")
os.makedirs(frames, exist_ok=True)

QSettings.setPath(QSettings.NativeFormat, QSettings.UserScope, tempfile.mkdtemp())
app = QApplication([])
app.setStyleSheet(theme.STYLESHEET)
f = QFont(app.font())
f.setPointSize(11)
app.setFont(f)


def make_window() -> MainWindow:
    w = MainWindow()
    w.resize(W, H)
    w.show()
    app.processEvents()
    return w


def load(w, *names):
    for n in names:
        w.load_file(os.path.join(EX, n))


def card(lines, sub="") -> QPixmap:
    pm = QPixmap(W, H)
    pm.fill(QColor("#0b0f14"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QColor(theme.ACCENT))
    p.setFont(QFont("sans", 84, QFont.Bold))
    p.drawText(QRectF(0, 250, W, 140), Qt.AlignCenter, "FlatDeck")
    p.setPen(QColor("#e5e7eb"))
    p.setFont(QFont("sans", 34))
    for i, t in enumerate(lines):
        p.drawText(QRectF(0, 420 + i * 62, W, 60), Qt.AlignCenter, t)
    p.setPen(QColor(theme.MUTED))
    p.setFont(QFont("sans", 24))
    p.drawText(QRectF(0, 760, W, 50), Qt.AlignCenter, sub)
    p.end()
    return pm


INTRO = card(["Gerber and Excellon files in,", "GRBL 1.1 G-code out"], f"Replaces FlatCAM for PCB milling on GRBL machines · v{__version__}")
OUTRO = card(["Isolation · drilling · cut-out", "high-voltage clearance and slots"], "Free and open source · Linux · github.com/stefanolmo/flatdeck")

main = make_window()
other = make_window()                      # second board for the high-voltage part
cur = {"w": main}
captions: dict = {}                        # time -> text
hl = {"widget": None, "until": 0.0}


def caption(t, text):
    captions[t] = text


def highlight(widget, t, secs=1.6):
    hl["widget"], hl["until"] = widget, t + secs


# ------------------------------------------------------------------ the script (time in seconds)
cams = []                                  # (t0, t1, centre, scale or None for fit-relative) camera moves of the main view
actions = []                               # (t, callable)


def at(t):
    def deco(fn):
        actions.append((t, fn))
        return fn
    return deco


def boardpt(w, dx, dy):
    b = w.project.board_bounds()
    return (b[0] + dx, b[1] + dy)


caption(3.5, "Load your Gerber and drill files: layer types are detected")
for i, (t, n) in enumerate(((4.4, "TrafficLight_contour.gm1"), (5.1, "TrafficLight_copperTop.gtl"),
                            (5.8, "TrafficLight_copperBottom.gbl"), (6.5, "TrafficLight_drill.txt"))):
    actions.append((t, lambda n=n, t=t: (load(main, n), highlight(main.layer_list, t, 1.2))))
actions.append((7.4, lambda: (main.view.fit(), main.iso_layer.setCurrentIndex(0))))

caption(9.5, "Choose the bit: a V-bit's cutting width follows tip, angle and depth")
actions.append((10.0, lambda: (main.iso_tool.setCurrentIndex(main.iso_tool.findData("vbit")), highlight(main.iso_tool, 10.0, 2.5))))
actions.append((11.5, lambda: (main.iso_margin.setValue(0.1), highlight(main.iso_eff, 11.5, 2.0))))
caption(14.0, "Generate: the toolpaths and the real cutting width are drawn on the board")
actions.append((14.4, lambda: (main.gen_isolation(), highlight(main.layer_list, 14.4, 1.5))))
cams.append((15.5, 17.5, "corner", 3.2))
cams.append((18.5, 19.5, "fit", None))

caption(19.8, "Clear all the useless copper in a rectangle around the circuit")
actions.append((20.2, lambda: (main.iso_tool.setCurrentIndex(main.iso_tool.findData("flat")), main.iso_dia.setValue(0.8),
                               main.iso_layer.setCurrentIndex(0), highlight(main.iso_tool, 20.2, 1.6))))
actions.append((21.6, lambda: (main.iso_rect.setChecked(True), highlight(main.iso_rect, 21.6, 1.8))))
actions.append((23.0, lambda: (main.gen_isolation(), highlight(main.layer_list, 23.0, 1.5))))

caption(25.5, "Drilling: one program, automatic pauses to change the bit")
actions.append((26.0, lambda: (main.tabs.setCurrentIndex(1), main.dr_file.setCurrentIndex(0))))
actions.append((27.4, lambda: (main.gen_drill(), highlight(main.layer_list, 27.4, 1.5))))

caption(30.0, "Cut-out with tabs, and a clean frame around the circuit")
actions.append((30.4, lambda: main.tabs.setCurrentIndex(main.tabs.count() - 1)))
actions.append((31.6, lambda: (main.gen_cutout(), highlight(main.layer_list, 31.6, 1.5))))
actions.append((33.4, lambda: (main.gen_frame(), highlight(main.layer_list, 33.4, 1.5))))

caption(36.0, "Measure on the board, fade any layer to see what is under it")
actions.append((36.4, lambda: (main.measure_act.setChecked(True), highlight(main.measure_act_widget, 36.4, 1.4))))


def _measure():
    main.view.measure_pts = [boardpt(main, 10, 8), boardpt(main, 38, 24)]
    main.view.update()


actions.append((37.6, _measure))
actions.append((39.6, lambda: (main.measure_act.setChecked(False), main.layer_list.setCurrentRow(len(main.project.layers) - 1))))
steps = list(range(100, 29, -10))
for k, v in enumerate(steps):
    actions.append((40.0 + k * 0.12, lambda v=v: main.opacity.setValue(v)))
actions.append((41.2, lambda: highlight(main.opacity, 41.2, 1.6)))

caption(43.0, "Clean GRBL 1.1 G-code for every tool, ready to send")
actions.append((43.4, lambda: (main.layer_list.setCurrentRow(4), main.opacity.setValue(100))))
actions.append((44.2, lambda: highlight(main.code, 44.2, 2.2)))
actions.append((46.4, lambda: highlight(main.save_act_widget, 46.4, 1.8)))

caption(48.2, "High-voltage clearance and creepage slots: mark, suggest, generate")


def _prep_hv():
    load(other, "BoardFancoil_copperBottom.gbl")
    lay = other.project.layers[0]
    for isl in lay.islands():
        if isl.outer[:, 1].mean() > 33:
            other.project.toggle_hv(lay, *isl.outer[0])
    other.view.fit()
    other.tabs.setCurrentWidget(other._hv_tab)
    other.view.update()


actions.append((47.6, lambda: (cur.__setitem__("w", other), _prep_hv())))
actions.append((50.0, lambda: (other.gen_hv(), highlight(other.layer_list, 50.0, 1.5))))
actions.append((52.0, lambda: (other.tabs.setCurrentWidget(other._slot_tab), other.slot_dia.setValue(2.0), other.suggest_slots())))
actions.append((54.0, lambda: (other.gen_slots(), other.layer_list.setCurrentRow(len(other.project.layers) - 1),
                               highlight(other.layer_list, 54.0, 1.5))))

# widgets of the toolbar actions (for the highlight)
tbar = main.findChildren(QToolBar)[0]
main.measure_act_widget = tbar.widgetForAction(main.measure_act)
main.save_act_widget = tbar.widgetForAction(main.save_act)

CAPTION_END = [3.5, 9.5, 14.0, 19.8, 25.5, 30.0, 36.0, 43.0, 48.2]


def ease(a):
    return a * a * (3 - 2 * a)


def render(t) -> QPixmap:
    w = cur["w"]
    pm = w.grab()
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    wd = hl["widget"]
    if wd is not None and t < hl["until"] and wd.isVisible():
        tl = wd.mapTo(w, QPoint(0, 0))
        a = 0.5 + 0.5 * np.sin(t * 9)
        pen = QPen(QColor(251, 191, 36, int(140 + 100 * a)), 3.5)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(QRectF(tl.x() - 5, tl.y() - 5, wd.width() + 10, wd.height() + 10), 9, 9)
    # caption: the last one started and not replaced
    keys = [k for k in captions if k <= t]
    if keys:
        k = max(keys)
        text = captions[k]
        a = min((t - k) / 0.35, 1.0)
        p.setFont(QFont("sans", 25, QFont.DemiBold))
        r = p.fontMetrics().boundingRect(text).adjusted(-34, -18, 34, 18)
        r.moveCenter(QPoint(W // 2, H - 118))
        p.setOpacity(a)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(5, 8, 12, 232))
        p.drawRoundedRect(r, 16, 16)
        p.setBrush(QColor(theme.ACCENT))
        p.drawRoundedRect(QRectF(r.left(), r.bottom() - 5, r.width(), 5), 2, 2)
        p.setPen(QColor("#f8fafc"))
        p.drawText(r.adjusted(0, -3, 0, -3), Qt.AlignCenter, text)
    p.end()
    return pm


def blend(a: QPixmap, b: QPixmap, k: float) -> QPixmap:
    pm = QPixmap(a)
    p = QPainter(pm)
    p.setOpacity(k)
    p.drawPixmap(0, 0, b)
    p.end()
    return pm


# ------------------------------------------------------------------ render
actions.sort(key=lambda x: x[0])
ai = 0
cam_start: dict = {}
n = int(TOTAL * FPS)
ONLY = {int(x) for x in os.environ["ONLY"].split(",")} if os.environ.get("ONLY") else None      # a few frames, to preview
for i in range(n):
    t = i / FPS
    while ai < len(actions) and actions[ai][0] <= t:
        actions[ai][1]()
        ai += 1
        app.processEvents()
    for c in cams:
        t0, t1, where, sc = c
        if t0 <= t <= t1 + 1e-9:
            v = main.view
            if c not in cam_start:
                cam_start[c] = (v.centre.copy(), v.scale)
                if where == "fit":
                    v.fit()
                    cam_start[c] = (cam_start[c][0], cam_start[c][1], v.centre.copy(), v.scale)
                    v.centre, v.scale = cam_start[c][0].copy(), cam_start[c][1]
                else:
                    fit_scale = v.scale
                    cam_start[c] = (cam_start[c][0], cam_start[c][1], np.array(boardpt(main, 14, 12)), fit_scale * sc)
            c0, s0, c1, s1 = cam_start[c]
            k = ease(min((t - t0) / (t1 - t0), 1.0))
            v.centre = c0 + (c1 - c0) * k
            v.scale = float(np.exp(np.log(s0) + (np.log(s1) - np.log(s0)) * k))
            v.update()
    app.processEvents()
    if ONLY is not None and i not in ONLY:
        continue
    if t < 3.0:
        pm = INTRO
    elif t < 3.5:
        pm = blend(INTRO, render(t), ease((t - 3.0) / 0.5))
    elif t >= 56.0:
        pm = blend(render(t), OUTRO, ease(min((t - 56.0) / 0.6, 1.0)))
    else:
        pm = render(t)
    pm.save(os.path.join(frames, f"{i:05d}.jpg"), "JPG", 94)
    if i % 150 == 0:
        print(f"frame {i}/{n}", flush=True)

if ONLY is None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(FPS), "-i", os.path.join(frames, "%05d.jpg"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart", os.path.join(out, "flatdeck.mp4")], check=True)
print("done", os.path.join(out, "flatdeck.mp4"))

"""Dark theme (same look as CNC Deck) and procedurally drawn vector icons."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

BG = "#0e1217"
PANEL = "#141a21"
CARD = "#1a222c"
CARD_HI = "#222c38"
BORDER = "#2a3544"
TEXT = "#d9e1ea"
MUTED = "#7f8d9f"
ACCENT = "#2dd4bf"
ACCENT_DIM = "#14766b"
OK = "#34d399"
RUN = "#4aa8ff"
WARN = "#fbbf24"
DANGER = "#f87171"
AXIS = {"X": "#f87171", "Y": "#4ade80", "Z": "#60a5fa"}
MONO = "'JetBrains Mono','Ubuntu Mono','DejaVu Sans Mono','Liberation Mono',monospace"

STATE_COLORS = {
    "Idle": OK, "Run": RUN, "Jog": RUN, "Home": RUN, "Check": "#a78bfa", "Hold": WARN,
    "Door": WARN, "Alarm": DANGER, "Sleep": MUTED, "Disconnected": MUTED, "Connecting": WARN,
}

STYLESHEET = f"""
* {{ font-size: 13px; }}
QWidget {{ background: {BG}; color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {CARD_HI}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#card QLabel, QFrame#card QCheckBox, QFrame#card QSlider {{ background: transparent; }}
QLabel#cardTitle {{ color: {MUTED}; font-size: 11px; font-weight: 700; letter-spacing: 1.5px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#mono {{ font-family: {MONO}; }}
QPushButton, QToolButton {{
    background: {CARD_HI}; border: 1px solid {BORDER}; border-radius: 7px; padding: 6px 12px; color: {TEXT};
}}
QPushButton:hover, QToolButton:hover {{ background: #2c3847; border-color: #3c4b5f; }}
QPushButton:pressed, QToolButton:pressed {{ background: {ACCENT_DIM}; border-color: {ACCENT}; }}
QPushButton:disabled, QToolButton:disabled {{ color: #4d5866; background: #171e26; border-color: #202a36; }}
QPushButton#primary {{ background: {ACCENT}; color: #04211d; border: none; font-weight: 700; }}
QPushButton#primary:hover {{ background: #5eead4; }}
QPushButton#primary:disabled {{ background: #1c3a37; color: #4b7a74; }}
QPushButton#danger {{ background: #5b1f24; border-color: #8f3038; }}
QPushButton#danger:hover {{ background: #7a2a31; }}
QPushButton:checked, QToolButton:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT}; }}
QToolButton#run {{ background: #14532d; border-color: #1f7a45; }}
QToolButton#run:hover {{ background: #1a6b3b; }}
QToolButton#run:disabled {{ background: #17221d; border-color: #1f2d26; }}
QToolButton#stopb {{ background: #5b1f24; border-color: #8f3038; }}
QToolButton#stopb:hover {{ background: #7a2a31; }}
QToolButton#stopb:disabled {{ background: #231a1c; border-color: #31232a; }}
QToolButton#jog {{ padding: 0; border-radius: 9px; background: #232e3b; }}
QToolButton#jog:pressed {{ background: {ACCENT}; }}
QComboBox, QLineEdit, QDoubleSpinBox, QSpinBox {{
    background: #10161d; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 8px;
    selection-background-color: {ACCENT_DIM};
}}
QComboBox:focus, QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {CARD}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_DIM}; outline: 0; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px; }}
QPlainTextEdit, QTextEdit, QTableWidget {{
    background: #0b0f14; border: 1px solid {BORDER}; border-radius: 8px;
    font-family: {MONO}; font-size: 12px; selection-background-color: {ACCENT_DIM};
}}
QListWidget {{ background: #0b0f14; border: 1px solid {BORDER}; border-radius: 8px; padding: 3px; outline: 0; }}
QListWidget::item {{ padding: 4px 6px; border-radius: 5px; }}
QListWidget::item:selected {{ background: {ACCENT_DIM}; color: {TEXT}; }}
QListWidget::item:hover {{ background: #18212b; }}
QListWidget::indicator {{ width: 15px; height: 15px; border-radius: 4px; border: 1px solid {BORDER}; background: #10161d; }}
QListWidget::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 7px 16px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QHeaderView::section {{ background: {CARD}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER}; padding: 5px; }}
QTableWidget {{ gridline-color: #1c2530; alternate-background-color: #0e141a; }}
QProgressBar {{ background: #10161d; border: 1px solid {BORDER}; border-radius: 6px; text-align: center; height: 16px; color: {TEXT}; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QStatusBar {{ background: {PANEL}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
QToolBar {{ background: {PANEL}; border: none; border-bottom: 1px solid {BORDER}; spacing: 6px; padding: 6px 8px; }}
QToolBar QToolButton {{ padding: 6px 10px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #2c3847; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3d4d60; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #2c3847; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid {BORDER}; background: #10161d; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QMenu {{ background: {CARD}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_DIM}; }}
QSplitter::handle {{ background: {BG}; }}
QMessageBox {{ background: {CARD}; }}
"""


LAYER_COLORS = ["#d9822b", "#4aa8ff", "#a78bfa", "#34d399", "#f472b6", "#fbbf24"]
DRILL_COLORS = ["#f1f5f9", "#fbbf24", "#34d399", "#60a5fa", "#f472b6", "#a78bfa", "#fb923c"]
JOB_COLORS = {"isolation": ACCENT, "drill": OK, "cutout": WARN, "hv": "#fb923c"}


def _icon(draw, color: str = TEXT, size: int = 64) -> QIcon:
    icon = QIcon()
    for col, mode in ((color, QIcon.Normal), ("#4d5866", QIcon.Disabled)):
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.scale(size / 24, size / 24)
        p.setPen(QPen(QColor(col), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        draw(p, QColor(col))
        p.end()
        icon.addPixmap(pm, mode)
    return icon


def _poly(p, pts, close=False, fill=None):
    path = QPainterPath(QPointF(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(*pt)
    if close:
        path.closeSubpath()
    if fill is not None:
        p.fillPath(path, fill)
    else:
        p.drawPath(path)


def _open(p, c): _poly(p, [(3, 7), (3, 19), (19, 19), (21, 11), (8, 11), (6, 19)]); _poly(p, [(3, 7), (9, 7), (11, 9), (18, 9), (18, 11)])
def _save(p, c): _poly(p, [(5, 4), (17, 4), (20, 7), (20, 20), (5, 20)], True); _poly(p, [(8, 4), (8, 9), (16, 9), (16, 4)]); _poly(p, [(8, 20), (8, 14), (16, 14), (16, 20)])
def _fit(p, c): _poly(p, [(4, 9), (4, 4), (9, 4)]); _poly(p, [(15, 4), (20, 4), (20, 9)]); _poly(p, [(20, 15), (20, 20), (15, 20)]); _poly(p, [(9, 20), (4, 20), (4, 15)])
def _mirror_x(p, c): _poly(p, [(12, 3), (12, 21)]); _poly(p, [(9, 7), (4, 12), (9, 17)], True); _poly(p, [(15, 7), (20, 12), (15, 17)], True)
def _mirror_y(p, c): _poly(p, [(3, 12), (21, 12)]); _poly(p, [(7, 9), (12, 4), (17, 9)], True); _poly(p, [(7, 15), (12, 20), (17, 15)], True)
def _rotate(p, c): p.drawArc(QRectF(4, 4, 16, 16), 40 * 16, 280 * 16); _poly(p, [(17, 2.5), (18.5, 7), (14, 7.5)], True, c)
def _play(p, c): _poly(p, [(7, 4.5), (19, 12), (7, 19.5)], True, c)
def _trash(p, c): _poly(p, [(5, 7), (19, 7)]); _poly(p, [(9, 7), (9, 4), (15, 4), (15, 7)]); _poly(p, [(6.5, 7), (7.5, 20), (16.5, 20), (17.5, 7)])
def _drill(p, c): p.drawEllipse(QPointF(12, 12), 7, 7); p.drawEllipse(QPointF(12, 12), 2, 2)
def _target(p, c): p.drawEllipse(QPointF(12, 12), 6, 6); _poly(p, [(12, 2), (12, 7)]); _poly(p, [(12, 17), (12, 22)]); _poly(p, [(2, 12), (7, 12)]); _poly(p, [(17, 12), (22, 12)])


_NAMES = "open save fit mirror_x mirror_y rotate play trash drill target".split()
_FUNCS = {n: globals()["_" + n] for n in _NAMES}


def icon(name: str, color: str = TEXT) -> QIcon:
    return _icon(_FUNCS[name], color)

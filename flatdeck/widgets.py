"""Small reusable widgets."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QDoubleValidator, QFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QComboBox, QDoubleSpinBox, QFrame, QLabel, QPlainTextEdit, QSpinBox, QVBoxLayout

from . import theme


class Card(QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("card")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(14, 12, 14, 14)
        self._lay.setSpacing(10)
        if title:
            t = QLabel(title.upper())
            t.setObjectName("cardTitle")
            self._lay.addWidget(t)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        self._lay.addLayout(self.body)


def dspin(lo: float, hi: float, val: float, dec: int = 2, suffix: str = "", step: float | None = None) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    if step:
        s.setSingleStep(step)
    s.setKeyboardTracking(False)
    return s


def ispin(lo: int, hi: int, val: int, suffix: str = "") -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(val)
    if suffix:
        s.setSuffix(suffix)
    s.setKeyboardTracking(False)
    return s


class AngleBox(QComboBox):
    """V-bit included angle: pick a common value from the list or type any other (1-170 degrees)."""
    valueChanged = Signal(float)
    PRESETS = (10, 15, 20, 30, 45, 60, 90)

    def __init__(self, value: float = 30.0, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        self.lineEdit().setValidator(QDoubleValidator(1.0, 170.0, 1, self))
        for a in self.PRESETS:
            self.addItem(f"{a}°", float(a))
        self.setToolTip("Included angle of the V-bit. Choose a common one or type your own value.")
        self.setValue(value)
        self.activated.connect(lambda *_: self.valueChanged.emit(self.value()))
        self.lineEdit().editingFinished.connect(lambda: self.valueChanged.emit(self.value()))

    def value(self) -> float:
        try:
            v = float(self.currentText().replace("°", "").replace(",", ".").strip())
        except ValueError:
            return float(self.PRESETS[3])
        return min(max(v, 1.0), 170.0)

    def setValue(self, v: float) -> None:
        self.setCurrentText(f"{v:g}°")
        self.valueChanged.emit(self.value())


class GcodeHighlighter(QSyntaxHighlighter):
    RULES = [(r"^\(.*\)$|;.*$", "#6b7a8c"), (r"\b[GM]\d+(\.\d)?\b", "#60a5fa"), (r"[XY]-?\d+\.?\d*", "#4ade80"),
             (r"Z-?\d+\.?\d*", "#fbbf24"), (r"[FS]\d+\.?\d*", "#f472b6")]

    def __init__(self, doc):
        super().__init__(doc)
        self._rules = []
        for pat, col in self.RULES:
            f = QTextCharFormat()
            f.setForeground(QColor(col))
            self._rules.append((re.compile(pat), f))

    def highlightBlock(self, text: str) -> None:
        for rx, f in self._rules:
            for m in rx.finditer(text):
                self.setFormat(m.start(), m.end() - m.start(), f)


class CodeView(QPlainTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        f = QFont("monospace")
        f.setStyleHint(QFont.Monospace)
        f.setPointSize(10)
        self.setFont(f)
        self._hl = GcodeHighlighter(self.document())

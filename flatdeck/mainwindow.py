"""Main window: layer list, operation panels (isolation / drilling / cut-out), 2D view and G-code preview."""
from __future__ import annotations

import os

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPlainTextEdit,
                               QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
                               QToolBar, QVBoxLayout, QWidget, QHeaderView)

from . import __version__, cam, theme
from .model import Layer, Project
from .viewer import BoardView
from .widgets import AngleBox, Card, CodeView, dspin, ispin

APP_NAME = "FlatDeck"
KIND_LABEL = {"copper": "Copper", "outline": "Outline", "silk": "Silkscreen", "mask": "Mask", "drill": "Drill", "job": "G-code"}


def fmt_time(s: float) -> str:
    s = int(round(s))
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1360, 820)
        self.setAcceptDrops(True)
        self.qs = QSettings("flatdeck", "flatdeck")
        self.project = Project()
        self._fields: dict = {}
        self._building = False
        self._build_toolbar()
        self._build_body()
        self.view.set_project(self.project)
        self.statusBar().showMessage("Ready. Open a Gerber or Excellon file.")
        self._refresh()

    # ------------------------------------------------------------------ construction
    def _act(self, tb: QToolBar, text: str, icon: str, slot, tip: str = "") -> QAction:
        a = QAction(theme.icon(icon), text, self)
        a.setToolTip(tip or text)
        a.triggered.connect(slot)
        tb.addAction(a)
        return a

    def _build_toolbar(self) -> None:
        tb = QToolBar()
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)
        self._act(tb, "Open…", "open", self.open_files, "Open Gerber / Excellon files (several at once)")
        self.save_act = self._act(tb, "Save G-code…", "save", self.save_gcode, "Save the G-code of the selected job")
        tb.addSeparator()
        self._act(tb, "Mirror X", "mirror_x", lambda: self.transform("mirror_x"), "Mirror the whole project about a vertical axis")
        self._act(tb, "Mirror Y", "mirror_y", lambda: self.transform("mirror_y"), "Mirror the whole project about a horizontal axis")
        self._act(tb, "Rotate 90°", "rotate", lambda: self.transform("rot90"), "Rotate the whole project 90° counter-clockwise")
        tb.addSeparator()
        self._act(tb, "Fit", "fit", lambda: self.view.fit(), "Zoom to the whole board (or double-click the view)")

    def _build_body(self) -> None:
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)

        # ---- left column
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(10, 10, 6, 10)
        lv.setSpacing(10)
        c = Card("Layers")
        self.layer_list = QListWidget()
        self.layer_list.setMinimumHeight(120)
        self.layer_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.layer_list.customContextMenuRequested.connect(self._layer_menu)
        self.layer_list.itemChanged.connect(self._item_changed)
        self.layer_list.currentRowChanged.connect(self._selection_changed)
        c.body.addWidget(self.layer_list)
        row = QHBoxLayout()
        rm = QPushButton("Remove")
        rm.setIcon(theme.icon("trash"))
        rm.clicked.connect(self.remove_selected)
        row.addWidget(rm)
        row.addStretch(1)
        c.body.addLayout(row)
        lv.addWidget(c)

        c = Card("Project")
        g = QGridLayout()
        g.addWidget(QLabel("G-code origin"), 0, 0)
        self.origin_combo = QComboBox()
        self.origin_combo.addItem("Lower-left corner of the board", "board")
        self.origin_combo.addItem("Coordinates as in the files", "file")
        self.origin_combo.currentIndexChanged.connect(self._origin_changed)
        g.addWidget(self.origin_combo, 0, 1)
        c.body.addLayout(g)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        c.body.addWidget(self.info)
        lv.addWidget(c)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._iso_panel(), "Isolation")
        self.tabs.addTab(self._drill_panel(), "Drilling")
        self._hv_tab = self._hv_panel()
        self.tabs.addTab(self._hv_tab, "High voltage")
        self.tabs.addTab(self._cutout_panel(), "Cut-out")
        self.tabs.currentChanged.connect(lambda *_: self._update_pick())
        lv.addWidget(self.tabs)
        lv.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QScrollArea.NoFrame)
        sc.setWidget(left)
        sc.setMinimumWidth(400)
        split.addWidget(sc)

        # ---- right column
        right = QSplitter(Qt.Vertical)
        right.setChildrenCollapsible(False)
        self.view = BoardView()
        self.view.picked.connect(self._picked)
        self.view.cursorMoved.connect(lambda x, y: self.statusBar().showMessage(f"X {x:8.3f}   Y {y:8.3f}   mm from origin"))
        right.addWidget(self.view)
        bottom = QTabWidget()
        self.code = CodeView()
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        bottom.addTab(self.code, "G-code")
        bottom.addTab(self.messages, "Messages")
        self.bottom = bottom
        self.job_info = QLabel("")
        self.job_info.setObjectName("muted")
        bottom.setCornerWidget(self.job_info)
        right.addWidget(bottom)
        right.setStretchFactor(0, 4)
        right.setStretchFactor(1, 1)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([430, 930])

    # ----- parameter helpers (remembered between sessions)
    def _field(self, key: str, widget, default=None):
        self._fields[key] = widget
        v = self.qs.value("p/" + key)
        try:
            if v is not None:
                if hasattr(widget, "setValue"):
                    widget.setValue(type(widget.value())(float(v)))
                elif isinstance(widget, QComboBox):
                    i = widget.findData(v)
                    if i >= 0:
                        widget.setCurrentIndex(i)
        except (TypeError, ValueError):
            pass
        sig = widget.valueChanged if hasattr(widget, "valueChanged") else widget.currentIndexChanged
        sig.connect(lambda *_: self.qs.setValue("p/" + key, widget.value() if hasattr(widget, "value") else widget.currentData()))
        return widget

    def _form(self, card: Card, rows: list) -> QGridLayout:
        g = QGridLayout()
        g.setColumnStretch(1, 1)
        for i, (label, w) in enumerate(rows):
            g.addWidget(QLabel(label), i, 0)
            g.addWidget(w, i, 1)
        card.body.addLayout(g)
        return g

    def _gen_button(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("primary")
        b.setIcon(theme.icon("play", "#04211d"))
        b.clicked.connect(slot)
        return b

    def _iso_panel(self) -> QWidget:
        c = Card("Copper isolation")
        self.iso_layer = QComboBox()
        self.iso_tool = self._field("iso_tool", QComboBox())
        self.iso_tool.addItem("Flat end mill", "flat")
        self.iso_tool.addItem("V-bit", "vbit")
        self.iso_dia = self._field("iso_dia", dspin(0.01, 10, 0.2, 3, " mm", 0.05))
        self.iso_tip = self._field("iso_tip", dspin(0, 5, 0.1, 3, " mm", 0.05))
        self.iso_tip.setToolTip("Width of the flat at the very end of the V-bit (as declared by the maker, e.g. 0.1 mm).\n"
                                "0 = perfectly sharp point: the groove width then depends only on depth and angle.")
        self.iso_angle = self._field("iso_angle", AngleBox(30))
        self.iso_eff = QLabel("")
        self.iso_eff.setObjectName("muted")
        self.iso_measured = self._field("iso_measured", dspin(0, 5, 0, 3, " mm", 0.01))
        self.iso_measured.setSpecialValueText("not used")
        self.iso_measured.setToolTip("Width of a groove you actually measured with this bit at this depth (caliper / microscope).\n"
                                     "If set, it replaces the computed cutting width: bits are rarely exactly as specified.")
        self.iso_margin = self._field("iso_margin", dspin(0, 2, 0, 3, " mm", 0.01))
        self.iso_margin.setToolTip("Extra distance between the copper edge and the groove: traces stay this much wider on each side.\n"
                                   "Use it if traces come out thinner than the design.")
        self.iso_passes = self._field("iso_passes", ispin(1, 50, 1))
        self.iso_overlap = self._field("iso_overlap", dspin(0, 90, 20, 0, " %"))
        self.iso_mode = self._field("iso_mode", QComboBox())
        for k, t in (("all", "Outside and inside"), ("ext", "Outside only"), ("int", "Inside only (holes in the copper)")):
            self.iso_mode.addItem(t, k)
        self.iso_depth = self._field("iso_depth", dspin(0.005, 5, 0.05, 3, " mm", 0.01))
        self.iso_travel = self._field("iso_travel", dspin(0.5, 50, 2, 1, " mm"))
        self.iso_feed = self._field("iso_feed", dspin(1, 5000, 120, 0, " mm/min"))
        self.iso_plunge = self._field("iso_plunge", dspin(1, 2000, 40, 0, " mm/min"))
        self.iso_spindle = self._field("iso_spindle", ispin(0, 30000, 1000))
        self.iso_spindle.setToolTip("Spindle S value. 0 = do not emit M3/M5")
        self.iso_mirror = QCheckBox("Bottom side: mirror in X")
        self.iso_mirror.setToolTip("For the bottom copper: the board is turned over about its vertical axis.\n"
                                   "Zero X0 Y0 at the lower-left corner of the turned-over board.")
        self._form(c, [("Copper layer", self.iso_layer), ("Tool", self.iso_tool), ("Diameter", self.iso_dia),
                       ("Bit: tip diameter", self.iso_tip), ("Bit: angle", self.iso_angle), ("Measured width", self.iso_measured),
                       ("Cutting width", self.iso_eff), ("Safety margin", self.iso_margin),
                       ("Passes", self.iso_passes), ("Overlap", self.iso_overlap), ("Isolate", self.iso_mode),
                       ("Depth", self.iso_depth), ("Safe Z", self.iso_travel), ("Feed rate", self.iso_feed),
                       ("Plunge rate", self.iso_plunge), ("Spindle S", self.iso_spindle)])
        for w in (self.iso_tool, self.iso_dia, self.iso_tip, self.iso_angle, self.iso_depth, self.iso_measured):
            (w.valueChanged if hasattr(w, "valueChanged") else w.currentIndexChanged).connect(lambda *_: self._iso_tool_changed())
        self.iso_skip_hv = QCheckBox("Skip islands marked high-voltage")
        self.iso_skip_hv.setChecked(True)
        self.iso_skip_hv.setToolTip("Those islands are isolated by the wide band of the High voltage tab instead")
        c.body.addWidget(self.iso_mirror)
        c.body.addWidget(self.iso_skip_hv)
        c.body.addWidget(self._gen_button("Generate isolation", self.gen_isolation))
        self._iso_tool_changed()
        return c

    def _iso_tool_changed(self) -> None:
        vbit = self.iso_tool.currentData() == "vbit"
        self.iso_dia.setEnabled(not vbit)
        self.iso_tip.setEnabled(vbit)
        self.iso_angle.setEnabled(vbit)
        eff = cam.vbit_diameter(self.iso_tip.value(), self.iso_angle.value(), self.iso_depth.value()) if vbit else self.iso_dia.value()
        note = " (at the given depth)" if vbit else ""
        if self.iso_measured.value() > 0:
            eff, note = self.iso_measured.value(), " (measured)"
        self.iso_eff.setText(f"{eff:.3f} mm" + note)
        self._iso_eff = eff

    def _drill_panel(self) -> QWidget:
        c = Card("Drilling")
        self.dr_file = QComboBox()
        self.dr_file.currentIndexChanged.connect(self._fill_drill_tools)
        self.dr_table = QTableWidget(0, 4)
        self.dr_table.setHorizontalHeaderLabels(["", "Tool", "Ø mm", "Holes"])
        self.dr_table.verticalHeader().setVisible(False)
        self.dr_table.setSelectionMode(QTableWidget.NoSelection)
        self.dr_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.dr_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.dr_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.dr_table.setMinimumHeight(130)
        self.dr_depth = self._field("dr_depth", dspin(0.1, 20, 1.8, 2, " mm", 0.1))
        self.dr_travel = self._field("dr_travel", dspin(0.5, 50, 2, 1, " mm"))
        self.dr_change = self._field("dr_change", dspin(1, 100, 15, 1, " mm"))
        self.dr_plunge = self._field("dr_plunge", dspin(1, 2000, 40, 0, " mm/min"))
        self.dr_peck = self._field("dr_peck", dspin(0, 10, 0, 2, " mm", 0.1))
        self.dr_peck.setToolTip("0 = drill each hole in one plunge; otherwise step down by this depth")
        self.dr_spindle = self._field("dr_spindle", ispin(0, 30000, 1000))
        self.dr_mirror = QCheckBox("Drilling from the bottom side: mirror in X")
        c.body.addWidget(self.dr_file)
        c.body.addWidget(self.dr_table)
        self._form(c, [("Depth", self.dr_depth), ("Safe Z", self.dr_travel), ("Tool-change Z", self.dr_change),
                       ("Plunge rate", self.dr_plunge), ("Peck drilling", self.dr_peck), ("Spindle S", self.dr_spindle)])
        c.body.addWidget(self.dr_mirror)
        n = QLabel("The program pauses (M0) at every bit change: fit the bit and zero Z on the surface again.")
        n.setObjectName("muted")
        n.setWordWrap(True)
        c.body.addWidget(n)
        c.body.addWidget(self._gen_button("Generate drilling", self.gen_drill))
        return c

    def _hv_panel(self) -> QWidget:
        c = Card("High-voltage clearance")
        n = QLabel("Mark the copper islands that carry mains voltage; they get a wide copper-free band cut with a large flat end mill. "
                   "An island is the copper connected together on one layer: mark the same net again on the other layer.")
        n.setObjectName("muted")
        n.setWordWrap(True)
        c.body.addWidget(n)
        self.hv_layer = QComboBox()
        self.hv_layer.currentIndexChanged.connect(lambda *_: (self._update_pick(), self._hv_count()))
        self.hv_pick = QPushButton("Pick islands in the view")
        self.hv_pick.setCheckable(True)
        self.hv_pick.setToolTip("Click an island to mark it, click it again to unmark it")
        self.hv_pick.toggled.connect(lambda *_: self._update_pick())
        clear = QPushButton("Clear marks")
        clear.clicked.connect(self._hv_clear)
        row = QHBoxLayout()
        row.addWidget(self.hv_pick, 1)
        row.addWidget(clear)
        self.hv_count = QLabel("")
        self.hv_count.setObjectName("muted")
        self.hv_clear_w = self._field("hv_clearance", dspin(0.1, 50, 3.0, 1, " mm", 0.5))
        self.hv_clear_w.setToolTip("Width of the copper-free band, measured from the edge of the HV copper")
        self.hv_dia = self._field("hv_dia", dspin(0.5, 10, 3.0, 2, " mm", 0.5))
        self.hv_overlap = self._field("hv_overlap", dspin(0, 90, 20, 0, " %"))
        self.hv_depth = self._field("hv_depth", dspin(0.01, 5, 0.1, 3, " mm", 0.01))
        self.hv_travel = self._field("hv_travel", dspin(0.5, 50, 2, 1, " mm"))
        self.hv_feed = self._field("hv_feed", dspin(1, 5000, 100, 0, " mm/min"))
        self.hv_plunge = self._field("hv_plunge", dspin(1, 2000, 40, 0, " mm/min"))
        self.hv_spindle = self._field("hv_spindle", ispin(0, 30000, 1000))
        self.hv_mirror = QCheckBox("Bottom side: mirror in X")
        self._form(c, [("Copper layer", self.hv_layer)])
        c.body.addLayout(row)
        c.body.addWidget(self.hv_count)
        self._form(c, [("Clearance", self.hv_clear_w), ("Flat end mill Ø", self.hv_dia), ("Overlap", self.hv_overlap),
                       ("Depth", self.hv_depth), ("Safe Z", self.hv_travel), ("Feed rate", self.hv_feed),
                       ("Plunge rate", self.hv_plunge), ("Spindle S", self.hv_spindle)])
        c.body.addWidget(self.hv_mirror)
        n2 = QLabel("Uses a different bit from the fine isolation: zero Z again after the tool change. "
                    "Pick the clearance from the applicable standard for your voltage; the program does not check it.")
        n2.setObjectName("muted")
        n2.setWordWrap(True)
        c.body.addWidget(n2)
        c.body.addWidget(self._gen_button("Generate HV clearance", self.gen_hv))
        return c

    def _hv_layer(self):
        return self.hv_layer.currentData()

    def _update_pick(self) -> None:
        on = self.hv_pick.isChecked() and self.tabs.currentWidget() is self._hv_tab and self._hv_layer() is not None
        self.view.set_pick_layer(self._hv_layer() if on else None)

    def _hv_count(self) -> None:
        lay = self._hv_layer()
        if lay is None:
            self.hv_count.setText("No copper layer loaded.")
        else:
            self.hv_count.setText(f"{len(self.project.hv_indices(lay))} of {len(lay.islands())} islands marked")

    def _picked(self, x: float, y: float) -> None:
        lay = self._hv_layer()
        if lay is None:
            return
        r = self.project.toggle_hv(lay, x, y, tol=6 / self.view.scale)
        if r is None:
            self.statusBar().showMessage("No copper island at that point", 3000)
        self.view.update()
        self._hv_count()
        self._refresh_list_only()

    def _hv_clear(self) -> None:
        lay = self._hv_layer()
        if lay is not None:
            lay.hv_points = []
            self.view.update()
            self._hv_count()
            self._refresh_list_only()

    def _cutout_panel(self) -> QWidget:
        c = Card("Board cut-out")
        self.co_note = QLabel("")
        self.co_note.setObjectName("muted")
        self.co_note.setWordWrap(True)
        self.co_margin = self._field("co_margin", dspin(0, 50, 1.0, 1, " mm"))
        self.co_margin.setToolTip("Only used when there is no outline layer: bounding box of the data + margin")
        self.co_dia = self._field("co_dia", dspin(0.1, 10, 1.0, 2, " mm", 0.1))
        self.co_depth = self._field("co_depth", dspin(0.1, 20, 1.8, 2, " mm", 0.1))
        self.co_step = self._field("co_step", dspin(0.05, 10, 0.6, 2, " mm", 0.1))
        self.co_gaps = self._field("co_gaps", QComboBox())
        for k, t in (("none", "None (full cut)"), ("tb", "2: top and bottom"), ("lr", "2: left and right"), ("four", "4: one per side")):
            self.co_gaps.addItem(t, k)
        self.co_gapw = self._field("co_gapw", dspin(0.5, 20, 3, 1, " mm"))
        self.co_travel = self._field("co_travel", dspin(0.5, 50, 2, 1, " mm"))
        self.co_feed = self._field("co_feed", dspin(1, 5000, 100, 0, " mm/min"))
        self.co_plunge = self._field("co_plunge", dspin(1, 2000, 30, 0, " mm/min"))
        self.co_spindle = self._field("co_spindle", ispin(0, 30000, 1000))
        c.body.addWidget(self.co_note)
        self._form(c, [("Margin", self.co_margin), ("Tool Ø", self.co_dia), ("Depth", self.co_depth), ("Step down", self.co_step),
                       ("Tabs", self.co_gaps), ("Tab width", self.co_gapw), ("Safe Z", self.co_travel),
                       ("Feed rate", self.co_feed), ("Plunge rate", self.co_plunge), ("Spindle S", self.co_spindle)])
        c.body.addWidget(self._gen_button("Generate cut-out", self.gen_cutout))
        return c

    # ------------------------------------------------------------------ project actions
    def open_files(self) -> None:
        last = self.qs.value("last_dir", os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(self, "Open Gerber / Excellon files", last,
                                                "Gerber and Excellon (*.gbr *.ger *.gtl *.gbl *.gko *.gm1 *.gml *.cmp *.sol *.drl *.xln *.exc *.txt *.nc);;All files (*)")
        for p in paths:
            self.load_file(p)

    def load_file(self, path: str) -> None:
        try:
            lay = self.project.load(path)
        except Exception as e:  # noqa: BLE001 - show any parser failure to the user
            QMessageBox.warning(self, APP_NAME, f"Cannot read {os.path.basename(path)}:\n{e}")
            return
        self.qs.setValue("last_dir", os.path.dirname(path))
        self._log(f"Loaded {lay.name} ({KIND_LABEL[lay.kind]})")
        for w in lay.warnings:
            self._log(f"  ⚠ {w}")
        self._refresh()
        self.layer_list.setCurrentRow(len(self.project.layers) - 1)
        self.view.fit()

    def remove_selected(self) -> None:
        lay = self._current()
        if lay is not None:
            self.project.remove(lay)
            self.view.invalidate()
            self._refresh()

    def transform(self, op: str) -> None:
        if not self.project.data_layers():
            return
        if self.project.jobs() and QMessageBox.question(
                self, APP_NAME, "The G-code already generated would no longer match the transformed project and will be removed. Continue?") != QMessageBox.Yes:
            return
        self.project.transform(op)
        self.view.invalidate()
        self._refresh()
        self._log(f"Transform: {op}")

    def _origin_changed(self) -> None:
        mode = self.origin_combo.currentData()
        if mode == self.project.origin_mode:
            return
        if self.project.jobs():                          # their G-code was written with the old origin
            if QMessageBox.question(self, APP_NAME, "The G-code already generated uses the old origin and will be removed. Continue?") != QMessageBox.Yes:
                self.origin_combo.blockSignals(True)
                self.origin_combo.setCurrentIndex(self.origin_combo.findData(self.project.origin_mode))
                self.origin_combo.blockSignals(False)
                return
            self.project.layers = [l for l in self.project.layers if l.kind != "job"]
        self.project.origin_mode = mode
        self.view.invalidate()
        self._refresh()

    def _log(self, msg: str) -> None:
        self.messages.appendPlainText(msg)

    # ------------------------------------------------------------------ layer list
    def _current(self) -> Layer | None:
        r = self.layer_list.currentRow()
        return self.project.layers[r] if 0 <= r < len(self.project.layers) else None

    def _refresh(self) -> None:
        cur = self._current()
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        for l in self.project.layers:
            it = QListWidgetItem(self._item_text(l))
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if l.visible else Qt.Unchecked)
            self.layer_list.addItem(it)
        if cur in self.project.layers:
            self.layer_list.setCurrentRow(self.project.layers.index(cur))
        self.layer_list.blockSignals(False)
        for combo, kinds in ((self.iso_layer, ("copper",)), (self.hv_layer, ("copper",))):
            keep = combo.currentData()
            combo.clear()
            for l in self.project.layers:
                if l.kind in kinds:
                    combo.addItem(l.name, l)
            i = combo.findData(keep)
            combo.setCurrentIndex(max(i, 0))
        keep = self.dr_file.currentData()
        self.dr_file.blockSignals(True)
        self.dr_file.clear()
        for l in self.project.layers:
            if l.kind == "drill":
                self.dr_file.addItem(l.name, l)
        self.dr_file.setCurrentIndex(max(self.dr_file.findData(keep), 0))
        self.dr_file.blockSignals(False)
        self._fill_drill_tools()
        self._selection_changed()
        self._refresh_info()
        self._hv_count()
        self._update_pick()

    def _item_text(self, l: Layer) -> str:
        hv = f"   ·   {len(self.project.hv_indices(l))} HV" if l.kind == "copper" and l.hv_points else ""
        return f"{'⚙ ' if l.kind == 'job' else ''}{l.name}   ·   {KIND_LABEL[l.kind]}{hv}"

    def _refresh_list_only(self) -> None:
        for i, l in enumerate(self.project.layers):
            self.layer_list.item(i).setText(self._item_text(l))

    def _refresh_info(self) -> None:
        b = self.project.bounds()
        if b is None:
            self.info.setText("No file loaded.")
        else:
            ox, oy = self.project.origin()
            holes = sum(l.drill.hole_count() for l in self.project.layers if l.drill)
            bb = self.project.board_bounds()
            self.info.setText(f"Board: {bb[2] - bb[0]:.2f} × {bb[3] - bb[1]:.2f} mm · holes: {holes}\n"
                              f"Origin in the file: X {ox:.3f}  Y {oy:.3f}")
        try:
            outer, inner, note = self.project.board_outline(self.co_margin.value())
            self.co_note.setText(note or f"Outline from layer: {1 + len(inner)} profiles ({len(inner)} inner)")
        except ValueError:
            self.co_note.setText("Load at least one layer.")

    def _item_changed(self, item: QListWidgetItem) -> None:
        r = self.layer_list.row(item)
        if 0 <= r < len(self.project.layers):
            self.project.layers[r].visible = item.checkState() == Qt.Checked
            self.view.update()

    def _selection_changed(self, *_) -> None:
        lay = self._current()
        self.view.selected = lay
        self.view.update()
        job = lay.job if lay is not None and lay.kind == "job" else None
        self.save_act.setEnabled(job is not None)
        if job is not None:
            self.code.setPlainText(job.gcode)
            self.job_info.setText(f"{job.lines} lines · cutting {job.length / 1000:.2f} m · estimated time ~{fmt_time(job.time_s)}   ")
            self.bottom.setCurrentIndex(0)
            for w in job.warnings:
                self._log(f"⚠ {job.name}: {w}")
        else:
            self.code.setPlainText("")
            self.job_info.setText("")

    def _layer_menu(self, pos) -> None:
        lay = self._current()
        if lay is None or lay.kind not in ("copper", "outline", "silk", "mask"):
            return
        m = QMenu(self)
        for kind in ("copper", "outline", "silk", "mask"):
            a = m.addAction(f"Treat as: {KIND_LABEL[kind]}")
            a.setEnabled(lay.kind != kind)
            a.triggered.connect(lambda _=False, k=kind: self._set_kind(lay, k))
        m.exec(self.layer_list.mapToGlobal(pos))

    def _set_kind(self, lay: Layer, kind: str) -> None:
        lay.kind = kind
        self._refresh()
        self.view.invalidate()

    def _fill_drill_tools(self) -> None:
        lay = self.dr_file.currentData()
        self.dr_table.setRowCount(0)
        if lay is None:
            return
        for t in sorted(lay.drill.tools.values(), key=lambda t: t.dia):
            r = self.dr_table.rowCount()
            self.dr_table.insertRow(r)
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked)
            chk.setData(Qt.UserRole, t.number)
            self.dr_table.setItem(r, 0, chk)
            self.dr_table.setItem(r, 1, QTableWidgetItem(f"T{t.number}"))
            self.dr_table.setItem(r, 2, QTableWidgetItem(f"{t.dia:.3f}"))
            self.dr_table.setItem(r, 3, QTableWidgetItem(f"{len(t.holes)}" + (f" + {len(t.slots)} slots" if t.slots else "")))

    # ------------------------------------------------------------------ generation
    def _run(self, name: str, fn) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            res = fn()
        except ValueError as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, APP_NAME, str(e))
            return
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, APP_NAME, f"Unexpected error while generating:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        lay = self.project.add_job(res)
        self._refresh()
        self.layer_list.setCurrentRow(self.project.layers.index(lay))
        self._log(f"Generated {res.name}: {res.lines} lines, time ~{fmt_time(res.time_s)}")

    def _next_name(self, base: str) -> str:
        n = sum(1 for l in self.project.jobs() if l.name.startswith(base))
        return f"{base} {n + 1}" if n else base

    def gen_isolation(self) -> None:
        lay = self.iso_layer.currentData()
        if lay is None:
            QMessageBox.information(self, APP_NAME, "Load a copper layer.")
            return
        p = cam.IsolationParams(tool_dia=self._iso_eff, passes=self.iso_passes.value(), overlap=self.iso_overlap.value() / 100,
                                mode=self.iso_mode.currentData(), margin=self.iso_margin.value(), cut_z=-self.iso_depth.value(), travel_z=self.iso_travel.value(),
                                feed=self.iso_feed.value(), plunge=self.iso_plunge.value(), spindle=self.iso_spindle.value(),
                                mirror_x=self.project.mirror_axis() if self.iso_mirror.isChecked() else None)
        name = self._next_name(f"Isolation {lay.name}" + (" (mirrored)" if p.mirror_x is not None else ""))
        skip = self.iso_skip_hv.isChecked() and lay.hv_points
        copper = self.project.hv_paths(lay, False) if skip else lay.gerber.polys
        self._run("iso", lambda: cam.isolate(copper, p, self.project.origin(), name))

    def gen_hv(self) -> None:
        lay = self._hv_layer()
        if lay is None:
            QMessageBox.information(self, APP_NAME, "Load a copper layer.")
            return
        if not lay.hv_points:
            QMessageBox.information(self, APP_NAME, "Mark at least one island: turn on \"Pick islands in the view\" and click the HV copper.")
            return
        p = cam.HVParams(clearance=self.hv_clear_w.value(), tool_dia=self.hv_dia.value(), overlap=self.hv_overlap.value() / 100,
                         cut_z=-self.hv_depth.value(), travel_z=self.hv_travel.value(), feed=self.hv_feed.value(),
                         plunge=self.hv_plunge.value(), spindle=self.hv_spindle.value(),
                         mirror_x=self.project.mirror_axis() if self.hv_mirror.isChecked() else None)
        name = self._next_name(f"HV clearance {lay.name}" + (" (mirrored)" if p.mirror_x is not None else ""))
        hv, others = self.project.hv_paths(lay, True), self.project.hv_paths(lay, False)
        self._run("hv", lambda: cam.hv_clearance(hv, others, p, self.project.origin(), name))

    def gen_drill(self) -> None:
        lay = self.dr_file.currentData()
        if lay is None:
            QMessageBox.information(self, APP_NAME, "Load a drill file.")
            return
        tools = {self.dr_table.item(r, 0).data(Qt.UserRole): self.dr_table.item(r, 0).checkState() == Qt.Checked
                 for r in range(self.dr_table.rowCount())}
        p = cam.DrillParams(drill_z=-self.dr_depth.value(), travel_z=self.dr_travel.value(), change_z=self.dr_change.value(),
                            plunge=self.dr_plunge.value(), peck=self.dr_peck.value(), spindle=self.dr_spindle.value(), tools=tools,
                            mirror_x=self.project.mirror_axis() if self.dr_mirror.isChecked() else None)
        name = self._next_name(f"Drilling {lay.name}" + (" (mirrored)" if p.mirror_x is not None else ""))
        self._run("drill", lambda: cam.drill(lay.drill, p, self.project.origin(), name))

    def gen_cutout(self) -> None:
        def go():
            outer, inner, note = self.project.board_outline(self.co_margin.value())
            p = cam.CutoutParams(tool_dia=self.co_dia.value(), cut_z=-self.co_depth.value(), step=self.co_step.value(),
                                 travel_z=self.co_travel.value(), feed=self.co_feed.value(), plunge=self.co_plunge.value(),
                                 gaps=self.co_gaps.currentData(), gap_width=self.co_gapw.value(), spindle=self.co_spindle.value())
            res = cam.cutout(outer, inner, p, self.project.origin(), self._next_name("Cut-out"))
            if note:
                res.warnings.append(note)
            return res
        self._run("cutout", go)

    def save_gcode(self) -> None:
        lay = self._current()
        if lay is None or lay.kind != "job":
            return
        last = self.qs.value("save_dir", os.path.expanduser("~"))
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in lay.name)
        path, _ = QFileDialog.getSaveFileName(self, "Save G-code", os.path.join(last, safe + ".nc"), "G-code (*.nc *.gcode *.ngc)")
        if path:
            with open(path, "w") as f:
                f.write(lay.job.gcode)
            self.qs.setValue("save_dir", os.path.dirname(path))
            self.statusBar().showMessage(f"Saved {path}", 5000)

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        for u in ev.mimeData().urls():
            if u.isLocalFile():
                self.load_file(u.toLocalFile())

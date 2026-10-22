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
from .widgets import Card, CodeView, dspin, ispin

APP_NAME = "FlatDeck"
KIND_LABEL = {"copper": "Rame", "outline": "Contorno", "silk": "Serigrafia", "mask": "Maschera", "drill": "Fori", "job": "G-code"}


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
        self.statusBar().showMessage("Pronto. Apri un file Gerber o Excellon.")
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
        self._act(tb, "Apri…", "open", self.open_files, "Apri file Gerber / Excellon (anche più file insieme)")
        self.save_act = self._act(tb, "Salva G-code…", "save", self.save_gcode, "Salva il G-code del lavoro selezionato")
        tb.addSeparator()
        self._act(tb, "Specchia X", "mirror_x", lambda: self.transform("mirror_x"), "Specchia tutto il progetto rispetto a un asse verticale (rame inferiore)")
        self._act(tb, "Specchia Y", "mirror_y", lambda: self.transform("mirror_y"), "Specchia tutto il progetto rispetto a un asse orizzontale")
        self._act(tb, "Ruota 90°", "rotate", lambda: self.transform("rot90"), "Ruota tutto il progetto di 90° in senso antiorario")
        tb.addSeparator()
        self._act(tb, "Adatta", "fit", lambda: self.view.fit(), "Inquadra tutto (doppio clic sulla vista)")

    def _build_body(self) -> None:
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        self.setCentralWidget(split)

        # ---- left column
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(10, 10, 6, 10)
        lv.setSpacing(10)
        c = Card("Livelli")
        self.layer_list = QListWidget()
        self.layer_list.setMinimumHeight(120)
        self.layer_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.layer_list.customContextMenuRequested.connect(self._layer_menu)
        self.layer_list.itemChanged.connect(self._item_changed)
        self.layer_list.currentRowChanged.connect(self._selection_changed)
        c.body.addWidget(self.layer_list)
        row = QHBoxLayout()
        rm = QPushButton("Rimuovi")
        rm.setIcon(theme.icon("trash"))
        rm.clicked.connect(self.remove_selected)
        row.addWidget(rm)
        row.addStretch(1)
        c.body.addLayout(row)
        lv.addWidget(c)

        c = Card("Progetto")
        g = QGridLayout()
        g.addWidget(QLabel("Origine G-code"), 0, 0)
        self.origin_combo = QComboBox()
        self.origin_combo.addItem("Angolo basso-sinistro dei dati", "board")
        self.origin_combo.addItem("Coordinate dei file", "file")
        self.origin_combo.currentIndexChanged.connect(self._origin_changed)
        g.addWidget(self.origin_combo, 0, 1)
        c.body.addLayout(g)
        self.info = QLabel("")
        self.info.setObjectName("muted")
        self.info.setWordWrap(True)
        c.body.addWidget(self.info)
        lv.addWidget(c)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._iso_panel(), "Isolamento")
        self.tabs.addTab(self._drill_panel(), "Foratura")
        self.tabs.addTab(self._cutout_panel(), "Contorno")
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
        self.view.cursorMoved.connect(lambda x, y: self.statusBar().showMessage(f"X {x:8.3f}   Y {y:8.3f}   mm dall'origine"))
        right.addWidget(self.view)
        bottom = QTabWidget()
        self.code = CodeView()
        self.messages = QPlainTextEdit()
        self.messages.setReadOnly(True)
        bottom.addTab(self.code, "G-code")
        bottom.addTab(self.messages, "Messaggi")
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
        c = Card("Isolamento del rame")
        self.iso_layer = QComboBox()
        self.iso_tool = self._field("iso_tool", QComboBox())
        self.iso_tool.addItem("Fresa cilindrica", "flat")
        self.iso_tool.addItem("Punta a V", "vbit")
        self.iso_dia = self._field("iso_dia", dspin(0.01, 10, 0.2, 3, " mm", 0.05))
        self.iso_tip = self._field("iso_tip", dspin(0.01, 5, 0.1, 3, " mm", 0.05))
        self.iso_angle = self._field("iso_angle", dspin(5, 120, 30, 0, "°"))
        self.iso_eff = QLabel("")
        self.iso_eff.setObjectName("muted")
        self.iso_passes = self._field("iso_passes", ispin(1, 50, 1))
        self.iso_overlap = self._field("iso_overlap", dspin(0, 90, 20, 0, " %"))
        self.iso_mode = self._field("iso_mode", QComboBox())
        for k, t in (("all", "Esterna e interna"), ("ext", "Solo esterna"), ("int", "Solo interna (fori nel rame)")):
            self.iso_mode.addItem(t, k)
        self.iso_depth = self._field("iso_depth", dspin(0.005, 5, 0.05, 3, " mm", 0.01))
        self.iso_travel = self._field("iso_travel", dspin(0.5, 50, 2, 1, " mm"))
        self.iso_feed = self._field("iso_feed", dspin(1, 5000, 120, 0, " mm/min"))
        self.iso_plunge = self._field("iso_plunge", dspin(1, 2000, 40, 0, " mm/min"))
        self.iso_spindle = self._field("iso_spindle", ispin(0, 30000, 1000))
        self.iso_spindle.setToolTip("Valore S del mandrino. 0 = non emettere M3/M5")
        self.iso_mirror = QCheckBox("Faccia inferiore: specchia in X")
        self.iso_mirror.setToolTip("Per il rame inferiore: la scheda viene girata attorno al suo asse verticale.\n"
                                   "Azzera X0 Y0 nell'angolo basso-sinistro della scheda girata.")
        self._form(c, [("Livello rame", self.iso_layer), ("Utensile", self.iso_tool), ("Diametro", self.iso_dia),
                       ("Punta: diametro", self.iso_tip), ("Punta: angolo", self.iso_angle), ("Larghezza taglio", self.iso_eff),
                       ("Passate", self.iso_passes), ("Sovrapposizione", self.iso_overlap), ("Isola", self.iso_mode),
                       ("Profondità", self.iso_depth), ("Z di sicurezza", self.iso_travel), ("Avanzamento", self.iso_feed),
                       ("Tuffo", self.iso_plunge), ("Mandrino S", self.iso_spindle)])
        for w in (self.iso_tool, self.iso_dia, self.iso_tip, self.iso_angle, self.iso_depth):
            (w.currentIndexChanged if isinstance(w, QComboBox) else w.valueChanged).connect(lambda *_: self._iso_tool_changed())
        c.body.addWidget(self.iso_mirror)
        c.body.addWidget(self._gen_button("Genera isolamento", self.gen_isolation))
        self._iso_tool_changed()
        return c

    def _iso_tool_changed(self) -> None:
        vbit = self.iso_tool.currentData() == "vbit"
        self.iso_dia.setEnabled(not vbit)
        self.iso_tip.setEnabled(vbit)
        self.iso_angle.setEnabled(vbit)
        eff = cam.vbit_diameter(self.iso_tip.value(), self.iso_angle.value(), self.iso_depth.value()) if vbit else self.iso_dia.value()
        self.iso_eff.setText(f"{eff:.3f} mm" + (" (alla profondità indicata)" if vbit else ""))
        self._iso_eff = eff

    def _drill_panel(self) -> QWidget:
        c = Card("Foratura")
        self.dr_file = QComboBox()
        self.dr_file.currentIndexChanged.connect(self._fill_drill_tools)
        self.dr_table = QTableWidget(0, 4)
        self.dr_table.setHorizontalHeaderLabels(["", "Utensile", "Ø mm", "Fori"])
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
        self.dr_peck.setToolTip("0 = foro con un'unica discesa; altrimenti scende a passi di questa profondità")
        self.dr_spindle = self._field("dr_spindle", ispin(0, 30000, 1000))
        self.dr_mirror = QCheckBox("Foratura dal lato inferiore: specchia in X")
        c.body.addWidget(self.dr_file)
        c.body.addWidget(self.dr_table)
        self._form(c, [("Profondità", self.dr_depth), ("Z di sicurezza", self.dr_travel), ("Z cambio punta", self.dr_change),
                       ("Discesa", self.dr_plunge), ("Foratura a passi", self.dr_peck), ("Mandrino S", self.dr_spindle)])
        c.body.addWidget(self.dr_mirror)
        n = QLabel("Ad ogni cambio punta il programma si ferma (M0): monta la punta e azzera di nuovo Z sulla superficie.")
        n.setObjectName("muted")
        n.setWordWrap(True)
        c.body.addWidget(n)
        c.body.addWidget(self._gen_button("Genera foratura", self.gen_drill))
        return c

    def _cutout_panel(self) -> QWidget:
        c = Card("Taglio del contorno")
        self.co_note = QLabel("")
        self.co_note.setObjectName("muted")
        self.co_note.setWordWrap(True)
        self.co_margin = self._field("co_margin", dspin(0, 50, 1.0, 1, " mm"))
        self.co_margin.setToolTip("Usato solo se non c'è un livello contorno: rettangolo dei dati + margine")
        self.co_dia = self._field("co_dia", dspin(0.1, 10, 1.0, 2, " mm", 0.1))
        self.co_depth = self._field("co_depth", dspin(0.1, 20, 1.8, 2, " mm", 0.1))
        self.co_step = self._field("co_step", dspin(0.05, 10, 0.6, 2, " mm", 0.1))
        self.co_gaps = self._field("co_gaps", QComboBox())
        for k, t in (("none", "Nessuna (taglio completo)"), ("tb", "2: sopra e sotto"), ("lr", "2: sinistra e destra"), ("four", "4: una per lato")):
            self.co_gaps.addItem(t, k)
        self.co_gapw = self._field("co_gapw", dspin(0.5, 20, 3, 1, " mm"))
        self.co_travel = self._field("co_travel", dspin(0.5, 50, 2, 1, " mm"))
        self.co_feed = self._field("co_feed", dspin(1, 5000, 100, 0, " mm/min"))
        self.co_plunge = self._field("co_plunge", dspin(1, 2000, 30, 0, " mm/min"))
        self.co_spindle = self._field("co_spindle", ispin(0, 30000, 1000))
        c.body.addWidget(self.co_note)
        self._form(c, [("Margine", self.co_margin), ("Ø fresa", self.co_dia), ("Profondità", self.co_depth), ("Passo discesa", self.co_step),
                       ("Linguette", self.co_gaps), ("Larghezza linguetta", self.co_gapw), ("Z di sicurezza", self.co_travel),
                       ("Avanzamento", self.co_feed), ("Tuffo", self.co_plunge), ("Mandrino S", self.co_spindle)])
        c.body.addWidget(self._gen_button("Genera contorno", self.gen_cutout))
        return c

    # ------------------------------------------------------------------ project actions
    def open_files(self) -> None:
        last = self.qs.value("last_dir", os.path.expanduser("~"))
        paths, _ = QFileDialog.getOpenFileNames(self, "Apri file Gerber / Excellon", last,
                                                "Gerber ed Excellon (*.gbr *.ger *.gtl *.gbl *.gko *.gm1 *.gml *.cmp *.sol *.drl *.xln *.exc *.txt *.nc);;Tutti i file (*)")
        for p in paths:
            self.load_file(p)

    def load_file(self, path: str) -> None:
        try:
            lay = self.project.load(path)
        except Exception as e:  # noqa: BLE001 - show any parser failure to the user
            QMessageBox.warning(self, APP_NAME, f"Impossibile leggere {os.path.basename(path)}:\n{e}")
            return
        self.qs.setValue("last_dir", os.path.dirname(path))
        self._log(f"Caricato {lay.name} ({KIND_LABEL[lay.kind]})")
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
                self, APP_NAME, "Il G-code già generato non corrisponderebbe più al progetto trasformato e verrà rimosso. Continuare?") != QMessageBox.Yes:
            return
        self.project.transform(op)
        self.view.invalidate()
        self._refresh()
        self._log(f"Trasformazione: {op}")

    def _origin_changed(self) -> None:
        mode = self.origin_combo.currentData()
        if mode == self.project.origin_mode:
            return
        if self.project.jobs():                          # their G-code was written with the old origin
            if QMessageBox.question(self, APP_NAME, "Il G-code già generato usa la vecchia origine e verrà rimosso. Continuare?") != QMessageBox.Yes:
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
            it = QListWidgetItem(f"{'⚙ ' if l.kind == 'job' else ''}{l.name}   ·   {KIND_LABEL[l.kind]}")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if l.visible else Qt.Unchecked)
            self.layer_list.addItem(it)
        if cur in self.project.layers:
            self.layer_list.setCurrentRow(self.project.layers.index(cur))
        self.layer_list.blockSignals(False)
        for combo, kinds in ((self.iso_layer, ("copper",)),):
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

    def _refresh_info(self) -> None:
        b = self.project.bounds()
        if b is None:
            self.info.setText("Nessun file caricato.")
        else:
            ox, oy = self.project.origin()
            holes = sum(l.drill.hole_count() for l in self.project.layers if l.drill)
            bb = self.project.board_bounds()
            self.info.setText(f"Scheda: {bb[2] - bb[0]:.2f} × {bb[3] - bb[1]:.2f} mm · fori: {holes}\n"
                              f"Origine nel file: X {ox:.3f}  Y {oy:.3f}")
        try:
            outer, inner, note = self.project.board_outline(self.co_margin.value())
            self.co_note.setText(note or f"Contorno dal livello: {1 + len(inner)} profili ({len(inner)} interni)")
        except ValueError:
            self.co_note.setText("Carica almeno un livello.")

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
            self.job_info.setText(f"{job.lines} righe · taglio {job.length / 1000:.2f} m · tempo stimato ~{fmt_time(job.time_s)}   ")
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
            a = m.addAction(f"Tratta come: {KIND_LABEL[kind]}")
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
            self.dr_table.setItem(r, 3, QTableWidgetItem(f"{len(t.holes)}" + (f" + {len(t.slots)} asole" if t.slots else "")))

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
            QMessageBox.critical(self, APP_NAME, f"Errore inatteso durante la generazione:\n{e}")
            return
        QApplication.restoreOverrideCursor()
        lay = self.project.add_job(res)
        self._refresh()
        self.layer_list.setCurrentRow(self.project.layers.index(lay))
        self._log(f"Generato {res.name}: {res.lines} righe, tempo ~{fmt_time(res.time_s)}")

    def _next_name(self, base: str) -> str:
        n = sum(1 for l in self.project.jobs() if l.name.startswith(base))
        return f"{base} {n + 1}" if n else base

    def gen_isolation(self) -> None:
        lay = self.iso_layer.currentData()
        if lay is None:
            QMessageBox.information(self, APP_NAME, "Carica un livello di rame.")
            return
        p = cam.IsolationParams(tool_dia=self._iso_eff, passes=self.iso_passes.value(), overlap=self.iso_overlap.value() / 100,
                                mode=self.iso_mode.currentData(), cut_z=-self.iso_depth.value(), travel_z=self.iso_travel.value(),
                                feed=self.iso_feed.value(), plunge=self.iso_plunge.value(), spindle=self.iso_spindle.value(),
                                mirror_x=self.project.mirror_axis() if self.iso_mirror.isChecked() else None)
        name = self._next_name(f"Isolamento {lay.name}" + (" (specchiato)" if p.mirror_x is not None else ""))
        self._run("iso", lambda: cam.isolate(lay.gerber.polys, p, self.project.origin(), name))

    def gen_drill(self) -> None:
        lay = self.dr_file.currentData()
        if lay is None:
            QMessageBox.information(self, APP_NAME, "Carica un file di foratura.")
            return
        tools = {self.dr_table.item(r, 0).data(Qt.UserRole): self.dr_table.item(r, 0).checkState() == Qt.Checked
                 for r in range(self.dr_table.rowCount())}
        p = cam.DrillParams(drill_z=-self.dr_depth.value(), travel_z=self.dr_travel.value(), change_z=self.dr_change.value(),
                            plunge=self.dr_plunge.value(), peck=self.dr_peck.value(), spindle=self.dr_spindle.value(), tools=tools,
                            mirror_x=self.project.mirror_axis() if self.dr_mirror.isChecked() else None)
        name = self._next_name(f"Foratura {lay.name}" + (" (specchiato)" if p.mirror_x is not None else ""))
        self._run("drill", lambda: cam.drill(lay.drill, p, self.project.origin(), name))

    def gen_cutout(self) -> None:
        def go():
            outer, inner, note = self.project.board_outline(self.co_margin.value())
            p = cam.CutoutParams(tool_dia=self.co_dia.value(), cut_z=-self.co_depth.value(), step=self.co_step.value(),
                                 travel_z=self.co_travel.value(), feed=self.co_feed.value(), plunge=self.co_plunge.value(),
                                 gaps=self.co_gaps.currentData(), gap_width=self.co_gapw.value(), spindle=self.co_spindle.value())
            res = cam.cutout(outer, inner, p, self.project.origin(), self._next_name("Contorno"))
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
        path, _ = QFileDialog.getSaveFileName(self, "Salva G-code", os.path.join(last, safe + ".nc"), "G-code (*.nc *.gcode *.ngc)")
        if path:
            with open(path, "w") as f:
                f.write(lay.job.gcode)
            self.qs.setValue("save_dir", os.path.dirname(path))
            self.statusBar().showMessage(f"Salvato {path}", 5000)

    # ------------------------------------------------------------------ drag & drop
    def dragEnterEvent(self, ev) -> None:
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dropEvent(self, ev) -> None:
        for u in ev.mimeData().urls():
            if u.isLocalFile():
                self.load_file(u.toLocalFile())

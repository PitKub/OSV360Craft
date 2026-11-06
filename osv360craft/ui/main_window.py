"""Main window: sidebar navigation, pages, theme switch, Help menu."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                               QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from osv360craft import APP_NAME, __version__
from osv360craft.hardware import benchmark
from osv360craft.ui import help as help_ui
from osv360craft.ui.app_context import AppContext
from osv360craft.ui.batch_stitching import BatchStitchingPage
from osv360craft.ui.dashboard import DashboardPage
from osv360craft.ui.export_settings_page import ExportSettingsPage
from osv360craft.ui.hardware_analyzer import HardwareAnalyzerPage
from osv360craft.ui.live_stitching import LiveStitchingPage
from osv360craft.ui.pages_misc import JobHistoryPage, LogsPage, SettingsPage
from osv360craft.ui.stitching_controls import StitchingControlsPage
from osv360craft.ui.theme import apply_theme

NAV = ["Dashboard", "Live Stitching", "Batch Stitching", "Hardware Analyzer", "Stitching Controls",
       "Export Settings", "Job History", "Logs and Diagnostics", "Settings"]


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, first_run_detect: bool = True) -> None:
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.resize(1440, 900)

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(220)
        sv = QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 10)
        t = QLabel(APP_NAME)
        t.setObjectName("AppTitle")
        v = QLabel(f"version {__version__}")
        v.setObjectName("AppVersion")
        sv.addWidget(t)
        sv.addWidget(v)
        self.stack = QStackedWidget()
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        for i, name in enumerate(NAV):
            b = QPushButton(name)
            b.setObjectName("Nav")
            b.setCheckable(True)
            self.nav.addButton(b, i)
            sv.addWidget(b)
        self.nav.idClicked.connect(self.go)
        sv.addStretch(1)
        self.theme_btn = QPushButton()
        self.theme_btn.setObjectName("Nav")
        self.theme_btn.clicked.connect(self.toggle_theme)
        sv.addWidget(self.theme_btn)
        hb = QPushButton("Help")
        hb.setObjectName("Nav")
        hb.clicked.connect(self.show_help)
        db = QPushButton("Donate")
        db.setObjectName("Nav")
        db.clicked.connect(lambda: help_ui.donate_dialog(self))
        sv.addWidget(hb)
        sv.addWidget(db)
        h.addWidget(side)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.dashboard = DashboardPage(ctx, self.go)
        self.live = LiveStitchingPage(ctx)
        self.batch = BatchStitchingPage(ctx)
        self.analyzer = HardwareAnalyzerPage(ctx)
        self.stitch = StitchingControlsPage(ctx)
        self.export_page = ExportSettingsPage(ctx)
        self.history = JobHistoryPage(ctx)
        self.logs = LogsPage(ctx)
        self.settings_page = SettingsPage(ctx, self.set_theme)
        for p in (self.dashboard, self.live, self.batch, self.analyzer, self.stitch, self.export_page, self.history,
                  self.logs, self.settings_page):
            self.stack.addWidget(p.widget)

        self._menus()
        ctx.status.connect(lambda m: self.statusBar().showMessage(m, 12000))
        ctx.hardwareChanged.connect(self._after_detection)
        self.set_theme(ctx.settings.get("ui.theme", "dark"), save=False)
        self.go(0)
        if first_run_detect:
            QTimer.singleShot(200, self.analyzer.detect)

    def _menus(self) -> None:
        mb = self.menuBar()
        fm = mb.addMenu("&File")
        a = QAction("Open recording for live preview...", self)
        a.setShortcut(QKeySequence.Open)
        a.triggered.connect(lambda: (self.go(1), self.live.open_dialog()))
        fm.addAction(a)
        a = QAction("Add recordings to batch...", self)
        a.setShortcut("Ctrl+Shift+O")
        a.triggered.connect(lambda: (self.go(2), self.batch.add_files()))
        fm.addAction(a)
        fm.addSeparator()
        a = QAction("Quit", self)
        a.setShortcut(QKeySequence.Quit)
        a.triggered.connect(self.close)
        fm.addAction(a)
        vm = mb.addMenu("&View")
        a = QAction("Switch dark / light theme", self)
        a.setShortcut("Ctrl+T")
        a.triggered.connect(self.toggle_theme)
        vm.addAction(a)
        hm = mb.addMenu("&Help")
        a = QAction("User guide", self)
        a.setShortcut(QKeySequence.HelpContents)
        a.triggered.connect(self.show_help)
        hm.addAction(a)
        a = QAction("Donate...", self)
        a.triggered.connect(lambda: help_ui.donate_dialog(self))
        hm.addAction(a)
        a = QAction("About", self)
        a.triggered.connect(lambda: help_ui.about_dialog(self))
        hm.addAction(a)

    def go(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        b = self.nav.button(i)
        if b:
            b.setChecked(True)
        if i == 6:
            self.history.refresh()
        if i == 0:
            self.dashboard.refresh()

    def set_theme(self, name: str, save: bool = True) -> None:
        apply_theme(QApplication.instance(), name)
        self.theme_btn.setText("Light theme" if name == "dark" else "Dark theme")
        self._theme = name
        if save:
            self.ctx.settings.set("ui.theme", name)
        self.ctx.themeChanged.emit(name)

    def toggle_theme(self) -> None:
        self.set_theme("light" if getattr(self, "_theme", "dark") == "dark" else "dark")

    def show_help(self) -> None:
        help_ui.HelpDialog(self).exec()

    def _after_detection(self) -> None:
        rep = self.ctx.hardware
        if not rep or getattr(self, "_offered", False):
            return
        self._offered = True
        if not rep.openosv.found:
            QMessageBox.information(self, "OpenOSV needed",
                                    "The OpenOSV stitching engine (osvtool) was not found.\n\n"
                                    "The Windows installer includes it. Otherwise download the OpenOSV release from "
                                    "github.com/Kemerd/OpenOSV/releases, put its 'cli' folder in "
                                    "external\\OpenOSV\\cli next to OSV360Craft, or set its path in Settings.")
            return
        if self.ctx.settings.get("benchmark.offer", True) and benchmark.needs_rerun(self.ctx.bench, rep.fingerprint()):
            why = "first start" if self.ctx.bench is None else "hardware, driver, OpenOSV or FFmpeg changed"
            r = QMessageBox.question(self, "Benchmark",
                                     f"Run a short benchmark now ({why})?\n\nIt uses one of your own .OSV clips, takes "
                                     "about 1-3 minutes on a GPU and makes the automatic settings fit this computer. "
                                     "You can skip it and run it later from Hardware Analyzer.",
                                     QMessageBox.Yes | QMessageBox.No)
            if r == QMessageBox.Yes:
                self.go(3)
                self.analyzer.run_benchmark()

    def closeEvent(self, e) -> None:
        if self.batch.is_running():
            r = QMessageBox.question(self, "Batch running", "A batch is running. Stop it (the unfinished file is "
                                                            "deleted) and quit? Pending jobs are kept for next time.")
            if r != QMessageBox.Yes:
                e.ignore()
                return
            self.batch.shutdown()
        self.live.shutdown()
        e.accept()

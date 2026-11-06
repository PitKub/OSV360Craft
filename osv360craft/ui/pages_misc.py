"""Job History, Logs and Diagnostics, and Settings pages."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem)

from osv360craft import APP_NAME, __version__
from osv360craft.ui.widgets import PathRow, fmt_seconds, page
from osv360craft.utils.logging import ring
from osv360craft.utils.paths import logs_dir, user_data_dir


def open_path(p: str) -> None:
    if sys.platform == "win32":
        os.startfile(p)  # noqa: S606 - opening a user's own file or folder
    else:
        subprocess.Popen(["xdg-open", p])


class JobHistoryPage:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.widget, lay = page("Job History", "Every finished, failed or cancelled job, with its validation report.")
        row = QHBoxLayout()
        b = QPushButton("Refresh")
        b.clicked.connect(self.refresh)
        rep = QPushButton("Open report")
        rep.clicked.connect(lambda: self._open("report"))
        out = QPushButton("Open output folder")
        out.clicked.connect(lambda: self._open("output"))
        lg = QPushButton("Open log")
        lg.clicked.connect(lambda: self._open("log_file"))
        for w in (b, rep, out, lg):
            row.addWidget(w)
        row.addStretch(1)
        lay.addLayout(row)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Finished", "File", "Status", "Duration", "Settings", "Output", "Message"])
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setMinimumHeight(500)
        lay.addWidget(self.table)
        self.jobs = []
        self.refresh()

    def refresh(self) -> None:
        self.jobs = list(reversed(self.ctx.queue.history()))
        self.table.setRowCount(len(self.jobs))
        for r, j in enumerate(self.jobs):
            dur = (j.finished - j.started) if (j.finished and j.started) else None
            s = j.settings_summary or {}
            vals = [time.strftime("%Y-%m-%d %H:%M", time.localtime(j.finished or j.added)), j.name, j.status,
                    fmt_seconds(dur), f"{s.get('size', '')} {s.get('pipeline', '')} CRF {s.get('crf', '')}".strip(),
                    j.output or "", j.message]
            for c, v in enumerate(vals):
                self.table.setItem(r, c, QTableWidgetItem(str(v)))

    def _open(self, attr: str) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self.jobs):
            p = getattr(self.jobs[r], attr)
            if p:
                open_path(str(Path(p).parent) if attr == "output" else p)


class LogsPage:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.widget, lay = page("Logs and Diagnostics", f"Application log ({logs_dir()}). Per-job tool logs are in "
                                                         "each project's Logs folder.")
        row = QHBoxLayout()
        b1 = QPushButton("Open log folder")
        b1.clicked.connect(lambda: open_path(str(logs_dir())))
        b2 = QPushButton("Save diagnostic bundle...")
        b2.setToolTip("A zip with the hardware report, the application log and settings (no media)")
        b2.clicked.connect(self.bundle)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        lay.addLayout(row)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(5000)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        self.text.setFont(f)
        self.text.setMinimumHeight(560)
        lay.addWidget(self.text)
        for line in list(ring.records):
            self.text.appendPlainText(line)
        self._pending: list[str] = []
        ring.add_listener(self._pending.append)
        from PySide6.QtCore import QTimer
        self.timer = QTimer(self.widget)
        self.timer.timeout.connect(self._flush)
        self.timer.start(500)

    def _flush(self) -> None:
        while self._pending:
            self.text.appendPlainText(self._pending.pop(0))

    def bundle(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self.widget, "Save diagnostic bundle",
                                              f"osv360craft_diagnostics_{time.strftime('%Y%m%d_%H%M')}.zip", "Zip (*.zip)")
        if not path:
            return
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            if self.ctx.hardware:
                z.writestr("hardware_report.txt", self.ctx.hardware.to_text())
                z.writestr("hardware_report.json", json.dumps(self.ctx.hardware.to_dict(), indent=2, default=str))
            for f in logs_dir().glob("*.log*"):
                z.write(f, f"logs/{f.name}")
            for name in ("settings.json", "benchmark.json", "queue.json"):
                p = user_data_dir() / name
                if p.exists():
                    z.write(p, name)
            z.writestr("version.txt", f"{APP_NAME} {__version__}\n")
        self.ctx.status.emit(f"Diagnostic bundle saved: {path}")


class SettingsPage:
    def __init__(self, ctx, apply_theme) -> None:
        self.ctx = ctx
        s = ctx.settings
        self.widget, lay = page("Settings", "Paths are optional: OSV360Craft looks for the bundled OpenOSV and FFmpeg "
                                            "first, then the usual install locations and PATH.")
        g = QGroupBox("Tools")
        f = QFormLayout(g)
        self.osvtool = PathRow("file", "osvtool (osvtool.exe osvtool)", "automatic")
        self.osvtool.setText(s.get("paths.osvtool") or "")
        self.osvtool.changed.connect(lambda p: s.set("paths.osvtool", p))
        self.ffmpeg = PathRow("file", "ffmpeg (ffmpeg.exe ffmpeg)", "automatic")
        self.ffmpeg.setText(s.get("paths.ffmpeg") or "")
        self.ffmpeg.changed.connect(lambda p: s.set("paths.ffmpeg", p))
        f.addRow("OpenOSV osvtool:", self.osvtool)
        f.addRow("FFmpeg:", self.ffmpeg)
        hint = QLabel("After changing a path, press 'Detect again' in Hardware Analyzer.")
        hint.setObjectName("Muted")
        f.addRow("", hint)
        lay.addWidget(g)

        g2 = QGroupBox("Appearance")
        f2 = QFormLayout(g2)
        self.theme = QComboBox()
        self.theme.addItem("Dark", "dark")
        self.theme.addItem("Light", "light")
        self.theme.setCurrentIndex(max(0, self.theme.findData(s.get("ui.theme", "dark"))))
        self.theme.currentIndexChanged.connect(lambda: apply_theme(self.theme.currentData()))
        ctx.themeChanged.connect(lambda t: (self.theme.blockSignals(True),
                                            self.theme.setCurrentIndex(self.theme.findData(t)),
                                            self.theme.blockSignals(False)))
        f2.addRow("Theme:", self.theme)
        lay.addWidget(g2)

        g3 = QGroupBox("Processing")
        f3 = QFormLayout(g3)
        self.conc = QCheckBox("Allow two jobs at once on powerful systems (>= 64 GB RAM, >= 16 GB VRAM)")
        self.conc.setChecked(bool(s.get("processing.allow_concurrency")))
        self.conc.toggled.connect(lambda v: (s.set("processing.allow_concurrency", v), ctx.recompute_plan()))
        self.decode = QCheckBox("Decode-test the start and end of every output (recommended)")
        self.decode.setChecked(bool(s.get("batch.decode_check", True)))
        self.decode.toggled.connect(lambda v: s.set("batch.decode_check", v))
        self.bench = QCheckBox("Offer the benchmark when hardware or drivers change")
        self.bench.setChecked(bool(s.get("benchmark.offer", True)))
        self.bench.toggled.connect(lambda v: s.set("benchmark.offer", v))
        self.keep = QCheckBox("Keep files that fail validation (renamed .partial) for inspection")
        self.keep.setChecked(bool(s.get("batch.keep_failed", False)))
        self.keep.toggled.connect(lambda v: s.set("batch.keep_failed", v))
        for w in (self.conc, self.decode, self.keep, self.bench):
            f3.addRow("", w)
        lay.addWidget(g3)
        lay.addStretch(1)

"""Batch Stitching: queue, output folder, progress, logs."""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from osv360craft.batch.batch_processor import BatchProcessor, ProcessorConfig
from osv360craft.batch.job_manager import ACTIVE, DONE, FAILED, PENDING, Job
from osv360craft.ui.widgets import PathRow, fmt_seconds
from osv360craft.utils.file_manager import scan_sources

COLS = ["File", "Status", "Progress", "Speed", "Time left", "Renderer", "Output / message"]


class _Bridge(QObject):
    job = Signal(object)
    log = Signal(str, str)
    state = Signal(str)


class BatchStitchingPage:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.processors: list[BatchProcessor] = []
        self.bridge = _Bridge()
        self.widget = QWidget()
        root = QVBoxLayout(self.widget)
        root.setContentsMargins(18, 12, 18, 12)
        t = QLabel("Batch Stitching")
        t.setObjectName("PageTitle")
        root.addWidget(t)

        add = QHBoxLayout()
        for text, fn in (("Add files...", self.add_files), ("Add folder...", self.add_folder)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            add.addWidget(b)
        self.recursive = QCheckBox("Include subfolders")
        self.recursive.setChecked(True)
        add.addWidget(self.recursive)
        add.addStretch(1)
        for text, fn in (("Up", lambda: self.move(-1)), ("Down", lambda: self.move(1)), ("Remove", self.remove),
                         ("Retry failed", self.retry), ("Clear finished", self.clear_finished)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            add.addWidget(b)
        root.addLayout(add)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Stretch)
        self.table.setAcceptDrops(True)
        self.table.cellDoubleClicked.connect(self.open_output)
        root.addWidget(self.table, 3)

        g = QGroupBox("Output")
        gl = QVBoxLayout(g)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("Project folder:"))
        self.project = PathRow("dir", placeholder="Where Stitched/, Reports/, Logs/ ... are created")
        self.project.setText(ctx.settings.get("paths.project_root") or str(Path.home() / "Videos" / "OSV360Craft_Project"))
        self.project.changed.connect(lambda p: ctx.settings.set("paths.project_root", p))
        r1.addWidget(self.project, 1)
        r1.addWidget(QLabel("If the output exists:"))
        self.existing = QComboBox()
        self.existing.addItem("Keep both (new name)", "unique")
        self.existing.addItem("Skip the clip", "skip")
        self.existing.addItem("Replace (asks first)", "replace")
        i = self.existing.findData(ctx.settings.get("batch.existing", "unique"))
        self.existing.setCurrentIndex(max(0, i))
        self.existing.currentIndexChanged.connect(lambda: ctx.settings.set("batch.existing", self.existing.currentData()))
        r1.addWidget(self.existing)
        gl.addLayout(r1)
        r2 = QHBoxLayout()
        self.audio_sep = QCheckBox("Also save the camera's audio track to Audio/")
        self.audio_sep.setChecked(bool(ctx.settings.get("batch.save_audio")))
        self.audio_sep.toggled.connect(lambda v: ctx.settings.set("batch.save_audio", v))
        r2.addWidget(self.audio_sep)
        self.summary = QLabel()
        self.summary.setObjectName("Muted")
        self.summary.setWordWrap(True)
        r2.addWidget(self.summary, 1)
        gl.addLayout(r2)
        root.addWidget(g)

        ctl = QHBoxLayout()
        self.b_start = QPushButton("Start")
        self.b_start.setObjectName("Primary")
        self.b_start.clicked.connect(self.start)
        self.b_pause = QPushButton("Pause after current")
        self.b_pause.clicked.connect(self.pause)
        self.b_stop = QPushButton("Stop")
        self.b_stop.setObjectName("Danger")
        self.b_stop.clicked.connect(self.stop)
        self.overall = QProgressBar()
        self.overall.setRange(0, 1000)
        self.overall_lbl = QLabel()
        for w in (self.b_start, self.b_pause, self.b_stop):
            ctl.addWidget(w)
        ctl.addWidget(self.overall, 1)
        ctl.addWidget(self.overall_lbl)
        root.addLayout(ctl)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(4000)
        root.addWidget(self.log, 2)

        self.bridge.job.connect(lambda j: self.refresh())
        self.bridge.log.connect(lambda jid, line: self.log.appendPlainText(line))
        self.bridge.state.connect(self._state)
        ctx.planChanged.connect(self._summary)
        self.widget.setAcceptDrops(True)
        self.widget.dragEnterEvent = lambda e: e.acceptProposedAction() if e.mimeData().hasUrls() else None
        self.widget.dropEvent = lambda e: self.add_paths([u.toLocalFile() for u in e.mimeData().urls()])
        self._buttons(False)
        self.refresh()
        self._summary()

    # ---- queue edits ----------------------------------------------------
    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self.widget, "Add recordings", "", "DJI Osmo 360 (*.OSV *.osv)")
        self.add_paths(files)

    def add_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self.widget, "Add a folder of recordings")
        if d:
            self.add_paths([d])

    def add_paths(self, paths: list[str]) -> None:
        found, skipped = scan_sources(paths, recursive=self.recursive.isChecked())
        added = sum(1 for s in found if self.ctx.queue.add(str(s.path), str(s.proxy) if s.proxy else None))
        for m in skipped:
            self.log.appendPlainText(m)
        self.log.appendPlainText(f"Added {added} recording(s); {len(found) - added} already queued.")
        self.refresh()

    def _selected(self) -> Job | None:
        r = self.table.currentRow()
        jobs = self.ctx.queue.snapshot()
        return jobs[r] if 0 <= r < len(jobs) else None

    def move(self, d: int) -> None:
        j = self._selected()
        if j:
            idx = [x.id for x in self.ctx.queue.jobs].index(j.id)
            self.ctx.queue.move(j.id, idx + d)
            self.refresh()
            self.table.selectRow(max(0, min(idx + d, self.table.rowCount() - 1)))

    def remove(self) -> None:
        j = self._selected()
        if j and not self.ctx.queue.remove(j.id):
            self.log.appendPlainText("A running job cannot be removed; stop it first.")
        self.refresh()

    def retry(self) -> None:
        n = self.ctx.queue.retry_all_failed()
        self.log.appendPlainText(f"{n} job(s) queued again.")
        self.refresh()

    def clear_finished(self) -> None:
        self.ctx.queue.clear_finished()
        self.refresh()

    def open_output(self, row: int, _col: int) -> None:
        jobs = self.ctx.queue.snapshot()
        if row < len(jobs) and jobs[row].status == DONE and jobs[row].output:
            target = jobs[row].output
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", target])
            else:
                subprocess.Popen(["xdg-open", str(Path(target).parent)])

    # ---- running --------------------------------------------------------
    def _config(self) -> ProcessorConfig:
        rep, plan = self.ctx.hardware, self.ctx.plan
        if not rep or not rep.openosv.found:
            raise RuntimeError("OpenOSV (osvtool) is not available")
        if not plan:
            raise RuntimeError("no processing plan yet")
        root = Path(self.project.text())
        return ProcessorConfig(
            osvtool=str(rep.openosv.path), ffmpeg=rep.ffmpeg.ffmpeg, ffprobe=rep.ffmpeg.ffprobe,
            supported_options=rep.openosv.render_options, export=self.ctx.export, device=plan.device,
            device_fallbacks=list(plan.device_fallbacks), encoder=plan.encoder, crf=plan.crf, project_root=root,
            existing=self.existing.currentData(), save_audio_separately=self.audio_sep.isChecked(),
            initial_yaw=float(self.ctx.settings.get("orientation.initial_yaw", 0.0) or 0.0),
            decode_check=bool(self.ctx.settings.get("batch.decode_check", True)),
            keep_failed_partial=bool(self.ctx.settings.get("batch.keep_failed", False)),
            plan_rows=plan.summary_rows())

    def start(self) -> None:
        if any(p.running for p in self.processors):
            return
        if not self.ctx.queue.next_pending():
            self.log.appendPlainText("Nothing pending in the queue.")
            return
        if not self.project.text():
            QMessageBox.warning(self.widget, "Batch", "Choose a project folder first.")
            return
        if self.existing.currentData() == "replace":
            if QMessageBox.question(self.widget, "Replace outputs",
                                    "Existing stitched files with the same name will be replaced after the new file "
                                    "is validated. Original recordings are never touched. Continue?") != QMessageBox.Yes:
                return
        try:
            cfg = self._config()
        except RuntimeError as e:
            QMessageBox.warning(self.widget, "Batch", str(e))
            return
        if cfg.encoder is None and self.ctx.export.codec_family != "tiff":
            QMessageBox.warning(self.widget, "Batch", "No working HEVC encoder was found. See Hardware Analyzer.")
            return
        recent = [str(cfg.project_root)] + [p for p in (self.ctx.settings.get("recent.projects") or [])
                                            if p != str(cfg.project_root)]
        self.ctx.settings.set("recent.projects", recent[:10])
        n = self.ctx.plan.concurrency if self.ctx.plan else 1
        self.processors = []
        for _ in range(n):
            bp = BatchProcessor(self.ctx.queue, self._config, on_job=self.bridge.job.emit,
                                on_log=self.bridge.log.emit, on_state=self.bridge.state.emit)
            self.processors.append(bp)
            bp.start()
        self._t0 = time.time()
        self._buttons(True)
        self.log.appendPlainText(f"Batch started with {n} worker(s): {cfg.device.upper()} renderer, "
                                 f"{cfg.encoder or 'no'} encoder, CRF {cfg.crf}.")

    def pause(self) -> None:
        for p in self.processors:
            p.pause_after_current()

    def stop(self) -> None:
        if QMessageBox.question(self.widget, "Stop", "Stop now? The unfinished output will be deleted.") == QMessageBox.Yes:
            for p in self.processors:
                p.stop()

    def _state(self, s: str) -> None:
        if s.startswith("error"):
            self.log.appendPlainText(f"Batch could not start: {s}")
        active = any(p.state in ("running", "pausing") for p in self.processors)
        self._buttons(active)
        if not active:
            self.log.appendPlainText(f"Batch {s}.")
        self.refresh()

    def _buttons(self, running: bool) -> None:
        self.b_start.setEnabled(not running)
        self.b_pause.setEnabled(running)
        self.b_stop.setEnabled(running)

    def is_running(self) -> bool:
        return any(p.running for p in self.processors)

    def shutdown(self) -> None:
        for p in self.processors:
            p.stop()
        for p in self.processors:
            p.wait(10)

    # ---- display --------------------------------------------------------
    def refresh(self) -> None:
        jobs = self.ctx.queue.snapshot()
        self.table.setRowCount(len(jobs))
        total_frames = done_frames = 0
        speed = 0.0
        for r, j in enumerate(jobs):
            prog = f"{j.frames_done}/{j.frames_total}" if j.frames_total else "-"
            if j.frames_total:
                prog += f"  ({100 * j.progress:.0f} %)"
            vals = [j.name, j.status, prog, f"{j.fps:.1f} fps" if j.status in ACTIVE and j.fps else "-",
                    fmt_seconds(j.eta_seconds) if j.status in ACTIVE else "-",
                    (j.device_used or j.device_requested or "-").upper(),
                    j.output if j.status == DONE else j.message]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(str(v))
                if c == 1 and j.status == FAILED:
                    it.setForeground(Qt.red)
                self.table.setItem(r, c, it)
            if j.status in (PENDING,) or j.status in ACTIVE:
                total_frames += j.frames_total or (j.clip.get("frame_count") or 0)
                done_frames += j.frames_done
                if j.status in ACTIVE:
                    speed += j.fps
        if total_frames:
            self.overall.setValue(int(1000 * done_frames / total_frames))
            eta = (total_frames - done_frames) / speed if speed else None
            self.overall_lbl.setText(f"{done_frames}/{total_frames} frames known, about {fmt_seconds(eta)} left "
                                     "(clips not yet analysed are not counted)")
        else:
            self.overall.setValue(0)
            self.overall_lbl.setText(f"{sum(1 for j in jobs if j.status == PENDING)} pending")

    def _summary(self) -> None:
        plan = self.ctx.plan
        if plan:
            self.summary.setText(f"Renderer {plan.device.upper()}, encoder {plan.encoder or 'none'}, CRF {plan.crf}, "
                                 f"{plan.concurrency} job(s) at a time - details in Hardware Analyzer. "
                                 f"Colour: {self.ctx.export.colour.output}.")

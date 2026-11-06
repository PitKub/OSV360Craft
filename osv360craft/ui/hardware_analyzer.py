"""Hardware Analyzer: detection report, benchmark, recommended configuration."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
                               QProgressBar, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QHeaderView)

from osv360craft.core import osv_probe
from osv360craft.hardware import benchmark, hardware_report, storage_detector
from osv360craft.hardware.auto_optimizer import PROFILES
from osv360craft.hardware.codec_detector import HEVC_CANDIDATES
from osv360craft.ui.widgets import page
from osv360craft.utils.file_manager import human_bytes


class _BenchSignals(QObject):
    progress = Signal(str, float)


class HardwareAnalyzerPage:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._bench: benchmark.Benchmark | None = None
        self.sig = _BenchSignals()
        self.widget, lay = page("Hardware Analyzer", "Everything below is detected or measured on this computer. "
                                                     "Settings are chosen from these results, never assumed.")
        top = QHBoxLayout()
        self.btn_detect = QPushButton("Detect again")
        self.btn_detect.clicked.connect(self.detect)
        self.btn_txt = QPushButton("Export TXT...")
        self.btn_json = QPushButton("Export JSON...")
        self.btn_txt.clicked.connect(lambda: self.export("txt"))
        self.btn_json.clicked.connect(lambda: self.export("json"))
        self.btn_disk = QPushButton("Test output drive speed")
        self.btn_disk.clicked.connect(self.disk_test)
        top.addWidget(self.btn_detect)
        top.addWidget(self.btn_txt)
        top.addWidget(self.btn_json)
        top.addWidget(self.btn_disk)
        top.addStretch(1)
        lay.addLayout(top)

        g = QGroupBox("Recommended configuration")
        gl = QVBoxLayout(g)
        pr = QHBoxLayout()
        pr.addWidget(QLabel("Processing profile:"))
        self.profile = QComboBox()
        for k, v in PROFILES.items():
            self.profile.addItem(v, k)
        self.profile.setCurrentIndex(max(0, self.profile.findData(ctx.profile)))
        self.profile.currentIndexChanged.connect(self._profile_changed)
        pr.addWidget(self.profile)
        pr.addStretch(1)
        gl.addLayout(pr)
        self.plan_table = QTableWidget(0, 3)
        self.plan_table.setHorizontalHeaderLabels(["Setting", "Selected", "Why"])
        self.plan_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.plan_table.verticalHeader().setVisible(False)
        self.plan_table.setMinimumHeight(330)
        gl.addWidget(self.plan_table)
        self.plan_warn = QLabel()
        self.plan_warn.setObjectName("Warn")
        self.plan_warn.setWordWrap(True)
        gl.addWidget(self.plan_warn)
        lay.addWidget(g)

        b = QGroupBox("Benchmark (about 1-3 minutes on a GPU; uses one of your own clips)")
        bl = QVBoxLayout(b)
        br = QHBoxLayout()
        self.btn_bench = QPushButton("Run benchmark...")
        self.btn_bench.setObjectName("Primary")
        self.btn_bench.clicked.connect(self.run_benchmark)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_benchmark)
        self.bench_bar = QProgressBar()
        self.bench_bar.setRange(0, 1000)
        self.bench_msg = QLabel()
        br.addWidget(self.btn_bench)
        br.addWidget(self.btn_cancel)
        br.addWidget(self.bench_bar, 1)
        bl.addLayout(br)
        bl.addWidget(self.bench_msg)
        self.bench_text = QPlainTextEdit()
        self.bench_text.setReadOnly(True)
        self.bench_text.setMaximumHeight(200)
        bl.addWidget(self.bench_text)
        lay.addWidget(b)

        r = QGroupBox("Detection report")
        rl = QVBoxLayout(r)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        f = QFont("Consolas")
        f.setStyleHint(QFont.Monospace)
        self.report.setFont(f)
        self.report.setMinimumHeight(420)
        rl.addWidget(self.report)
        lay.addWidget(r)

        self.sig.progress.connect(self._bench_progress)
        ctx.hardwareChanged.connect(self.refresh)
        ctx.planChanged.connect(self.refresh_plan)
        ctx.benchmarkChanged.connect(self.refresh_bench)
        self.refresh_bench()

    # ---- detection ------------------------------------------------------
    def detect(self) -> None:
        self.btn_detect.setEnabled(False)
        self.report.setPlainText("Detecting hardware, OpenOSV and FFmpeg; testing each encoder ...")
        s = self.ctx.settings
        drives = [p for p in (s.get("paths.project_root"), str(Path.home())) if p]

        def work():
            return hardware_report.build_report(s.get("paths.osvtool") or None, s.get("paths.ffmpeg") or None, drives)

        def done(rep):
            self.btn_detect.setEnabled(True)
            self.ctx.set_hardware(rep)

        self.ctx.run_task(work, done, lambda e: self.btn_detect.setEnabled(True))

    def refresh(self) -> None:
        if self.ctx.hardware:
            self.report.setPlainText(self.ctx.hardware.to_text())
        self.refresh_plan()

    def refresh_plan(self) -> None:
        plan = self.ctx.plan
        if not plan:
            return
        rows = plan.summary_rows()
        self.plan_table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                self.plan_table.setItem(i, j, QTableWidgetItem(v))
        self.plan_table.resizeColumnsToContents()
        self.plan_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.plan_warn.setText("\n".join(plan.warnings))

    def _profile_changed(self) -> None:
        self.ctx.settings.set("processing.profile", self.profile.currentData())
        self.ctx.recompute_plan()

    def export(self, kind: str) -> None:
        if not self.ctx.hardware:
            return
        path, _ = QFileDialog.getSaveFileName(self.widget, "Export report", f"osv360craft_hardware.{kind}",
                                              "JSON (*.json)" if kind == "json" else "Text (*.txt)")
        if path:
            self.ctx.hardware.export(Path(path))
            self.ctx.status.emit(f"Report saved to {path}")

    def disk_test(self) -> None:
        folder = self.ctx.settings.get("paths.project_root") or str(Path.home())
        self.btn_disk.setEnabled(False)

        def done(r):
            self.btn_disk.setEnabled(True)
            w, rd, note = r
            msg = f"{folder}: write {w:.0f} MB/s, read {rd:.0f} MB/s ({note})" if w else f"{folder}: {note}"
            self.ctx.status.emit(msg)
            self.report.appendPlainText("\nDrive test: " + msg)

        self.ctx.run_task(lambda: storage_detector.benchmark(folder), done,
                          lambda e: self.btn_disk.setEnabled(True))

    # ---- benchmark ------------------------------------------------------
    def run_benchmark(self, clip: str | None = None) -> None:
        rep = self.ctx.hardware
        if not rep or not rep.openosv.found:
            QMessageBox.warning(self.widget, "Benchmark", "OpenOSV (osvtool) was not found. Install it first.")
            return
        if not clip:
            clip, _ = QFileDialog.getOpenFileName(self.widget, "Choose a short .OSV clip for the benchmark", "",
                                                  "DJI Osmo 360 (*.OSV *.osv)")
        if not clip:
            return
        renderers = [n for n in ("cuda", "opencl", "metal") if rep.renderer_available(n)] + ["cpu"]
        encoders = rep.ffmpeg.verified(HEVC_CANDIDATES)
        osvtool, ff = str(rep.openosv.path), rep.ffmpeg.ffmpeg
        self.btn_bench.setEnabled(False)
        self.btn_cancel.setEnabled(True)

        def work():
            info = osv_probe.probe(Path(osvtool), Path(clip), rep.ffmpeg.ffprobe)
            if not info.ok:
                raise RuntimeError(f"clip could not be analysed: {info.error}")
            native = "{}x{}".format(*info.native_equirect) if info.native_equirect else None
            self._bench = benchmark.Benchmark(osvtool, ff, clip, renderers, encoders, native, rep.fingerprint(),
                                              progress=lambda m, f: self.sig.progress.emit(m, f))
            return self._bench.run()

        def done(res):
            self.btn_bench.setEnabled(True)
            self.btn_cancel.setEnabled(False)
            if not res.cancelled:
                benchmark.save(res)
                self.ctx.bench = res
                self.ctx.benchmarkChanged.emit()
                self.ctx.recompute_plan()

        def fail(e):
            self.btn_bench.setEnabled(True)
            self.btn_cancel.setEnabled(False)
            self.bench_msg.setText(f"Benchmark failed: {e}")

        self.ctx.run_task(work, done, fail)

    def cancel_benchmark(self) -> None:
        if self._bench:
            self._bench.cancel()

    def _bench_progress(self, msg: str, frac: float) -> None:
        self.bench_msg.setText(msg)
        self.bench_bar.setValue(int(frac * 1000))

    def refresh_bench(self) -> None:
        b = self.ctx.bench
        if not b:
            self.bench_text.setPlainText("No benchmark yet. Settings are chosen from detected hardware until one is run.")
            return
        L = [f"Run {b.created} on {Path(b.clip).name}"]
        if self.ctx.hardware and benchmark.needs_rerun(b, self.ctx.hardware.fingerprint()):
            L.append("Hardware, driver, OpenOSV or FFmpeg changed since this run: results are ignored until re-run.")
        L += [f"Decode ({k}, one lens): {v} fps" for k, v in b.decode_fps.items()]
        L += [f"Stitch + colour on {k.upper()} at 1920x960: {v} fps" for k, v in b.device_fps.items()]
        L += [f"{k.upper()} failed: {v}" for k, v in b.device_errors.items()]
        if b.fullres_fps:
            L.append(f"Full resolution {b.fullres_size}: {b.fullres_fps} fps")
        L += [f"Encode {k} 7680x3840 10-bit: {v} fps" for k, v in b.encode_fps.items()]
        L.append(f"Peaks: CPU {b.peak_cpu_percent or '-'} %, RAM {human_bytes(b.peak_ram_bytes)}, "
                 f"GPU {b.peak_gpu_percent if b.peak_gpu_percent is not None else 'n/a'} %, "
                 f"VRAM {human_bytes(b.peak_vram_used_bytes) if b.peak_vram_used_bytes else 'n/a'}")
        L += b.notes
        self.bench_text.setPlainText("\n".join(L))

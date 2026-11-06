"""Dashboard: engine and hardware status at a glance, recent jobs and projects."""

from __future__ import annotations

import time

from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QListWidget, QPushButton, QVBoxLayout

from osv360craft.ui.widgets import KeyValueGrid, page
from osv360craft.utils.file_manager import human_bytes


class DashboardPage:
    def __init__(self, ctx, navigate) -> None:
        self.ctx = ctx
        self.widget, lay = page("Dashboard", "OSV360Craft stitches original DJI Osmo 360 .OSV recordings with the "
                                             "OpenOSV engine. Nothing is ever written to your original files.")
        row = QHBoxLayout()
        g1 = QGroupBox("Engine")
        self.engine = KeyValueGrid()
        QVBoxLayout(g1).addWidget(self.engine)
        g2 = QGroupBox("This computer")
        self.hw = KeyValueGrid()
        QVBoxLayout(g2).addWidget(self.hw)
        row.addWidget(g1, 1)
        row.addWidget(g2, 1)
        lay.addLayout(row)

        self.warn = QLabel()
        self.warn.setObjectName("Warn")
        self.warn.setWordWrap(True)
        lay.addWidget(self.warn)

        actions = QHBoxLayout()
        for text, target in (("Open a recording for live preview", 1), ("Batch stitch recordings", 2),
                             ("Hardware Analyzer", 3)):
            b = QPushButton(text)
            if target == 2:
                b.setObjectName("Primary")
            b.clicked.connect(lambda _=False, t=target: navigate(t))
            actions.addWidget(b)
        actions.addStretch(1)
        lay.addLayout(actions)

        row2 = QHBoxLayout()
        g3 = QGroupBox("Recent jobs")
        self.recent = QListWidget()
        QVBoxLayout(g3).addWidget(self.recent)
        g4 = QGroupBox("Recent project folders")
        self.projects = QListWidget()
        QVBoxLayout(g4).addWidget(self.projects)
        row2.addWidget(g3, 1)
        row2.addWidget(g4, 1)
        lay.addLayout(row2, 1)
        ctx.hardwareChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        rep = self.ctx.hardware
        if rep is None:
            self.engine.set("Status", "Detecting hardware and tools ...")
            return
        o, f = rep.openosv, rep.ffmpeg
        self.engine.set("Status", f"Detected {rep.created}", "ok")
        self.engine.set("OpenOSV", f"{o.version} ({o.path})" if o.found else (o.error or "not found"),
                        "ok" if o.found else "bad")
        r = ", ".join(x.upper() for x in o.gpu_renderers) or "CPU only"
        self.engine.set("Renderers", r, "ok" if o.gpu_renderers else "warn")
        self.engine.set("FFmpeg", f"{f.version}" if f.found else (f.error or "not found"), "ok" if f.found else "bad")
        best = f.best_hevc()
        self.engine.set("HEVC 10-bit encoder", best or "none verified", "ok" if best else "bad")
        plan = self.ctx.plan
        if plan:
            self.engine.set("Selected backend", f"{plan.device.upper()} + {plan.encoder or '-'} "
                                                f"({self.ctx.profile.upper()} profile)")
        c = rep.cpu
        self.hw.set("CPU", f"{c.model} - {c.physical_cores or '?'} cores / {c.logical_processors} threads")
        self.hw.set("RAM", f"{human_bytes(rep.ram.available_bytes)} free of {human_bytes(rep.ram.total_bytes)}")
        g = rep.primary_gpu
        self.hw.set("GPU", f"{g.name} ({g.vendor})" if g else "none identified", None if g else "warn")
        self.hw.set("GPU memory", f"{human_bytes(g.vram_total_bytes)} (free {human_bytes(g.vram_free_bytes)})" if g else "-")
        self.hw.set("NVDEC / NVENC", f"{rep.nvdec_status()[0]} / "
                    f"{'verified' if f.tests.get('hevc_nvenc') and f.tests['hevc_nvenc'].verified else 'not available'}")
        self.hw.set("OS", f"{rep.os_name} {rep.os_version}")
        msgs = list(rep.warnings) + [f"{d.name}: {d.detail} - {d.remedy}" for d in rep.dependencies if d.ok is False]
        self.warn.setText("\n".join("- " + m for m in msgs))
        self.recent.clear()
        for j in reversed(self.ctx.queue.history()[-15:]):
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(j.finished or j.added))
            self.recent.addItem(f"{when}  {j.status.upper():<9} {j.name}  {j.message[:80]}")
        self.projects.clear()
        for p in self.ctx.settings.get("recent.projects", []) or []:
            self.projects.addItem(p)

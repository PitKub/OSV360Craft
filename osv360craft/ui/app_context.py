"""Shared application state and background-task helper for the UI."""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from osv360craft.batch.job_manager import JobQueue
from osv360craft.core.osv_probe import ClipInfo
from osv360craft.export.export_settings import ExportSettings
from osv360craft.hardware import auto_optimizer, benchmark
from osv360craft.hardware.hardware_report import HardwareReport
from osv360craft.utils.logging import get_logger
from osv360craft.utils.settings_store import SettingsStore

log = get_logger(__name__)


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _Signals()

    def run(self) -> None:
        try:
            r = self.fn()
        except Exception as e:
            log.error("Background task failed: %s\n%s", e, traceback.format_exc())
            self.signals.failed.emit(str(e))
            return
        self.signals.done.emit(r)


class AppContext(QObject):
    hardwareChanged = Signal()
    benchmarkChanged = Signal()
    exportChanged = Signal()
    planChanged = Signal()
    themeChanged = Signal(str)
    clipOpened = Signal(object)     # ClipInfo
    status = Signal(str)
    applyHeading = Signal(float)    # viewer heading -> stitching controls

    def __init__(self, settings: SettingsStore) -> None:
        super().__init__()
        self.settings = settings
        self.pool = QThreadPool.globalInstance()
        self._tasks: set[_Task] = set()
        self.hardware: HardwareReport | None = None
        self.bench: benchmark.BenchmarkResult | None = benchmark.load_saved()
        self.export = ExportSettings.from_dict(settings.get("export"))
        self.queue = JobQueue()
        self.current_clip: ClipInfo | None = None
        self.plan: auto_optimizer.RenderPlan | None = None

    # ---- background tasks --------------------------------------------
    def run_task(self, fn: Callable[[], Any], on_done: Callable[[Any], None] | None = None,
                 on_error: Callable[[str], None] | None = None) -> None:
        t = _Task(fn)
        self._tasks.add(t)

        def finish(r: Any) -> None:
            self._tasks.discard(t)
            if on_done:
                on_done(r)

        def fail(msg: str) -> None:
            self._tasks.discard(t)
            self.status.emit(f"Error: {msg}")
            if on_error:
                on_error(msg)

        t.signals.done.connect(finish)
        t.signals.failed.connect(fail)
        self.pool.start(t)

    # ---- state helpers -----------------------------------------------
    @property
    def profile(self) -> str:
        return self.settings.get("processing.profile", "auto")

    def osvtool_path(self) -> str | None:
        if self.hardware and self.hardware.openosv.path:
            return str(self.hardware.openosv.path)
        return self.settings.get("paths.osvtool") or None

    def save_export(self) -> None:
        self.settings.set("export", self.export.to_dict())
        self.exportChanged.emit()
        self.recompute_plan()

    def set_hardware(self, rep: HardwareReport) -> None:
        self.hardware = rep
        self.hardwareChanged.emit()
        if self.bench and benchmark.needs_rerun(self.bench, rep.fingerprint()):
            self.status.emit("Hardware, driver, OpenOSV or FFmpeg changed since the last benchmark: re-run recommended.")
        self.recompute_plan()

    def recompute_plan(self) -> None:
        if not self.hardware:
            return
        bench = self.bench if (self.bench and not benchmark.needs_rerun(self.bench, self.hardware.fingerprint())) else None
        self.plan = auto_optimizer.make_plan(self.hardware, self.export, self.profile, bench, self.current_clip,
                                             allow_concurrency=bool(self.settings.get("processing.allow_concurrency")))
        self.planChanged.emit()

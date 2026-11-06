"""Jobs, the persistent queue and the job history.

The queue survives restarts: a job that was running when the application
closed is put back to ``pending`` (its partial output is discarded, never
mistaken for a finished file) so the queue can resume.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from osv360craft.utils.logging import get_logger
from osv360craft.utils.paths import user_data_dir
from osv360craft.utils.settings_store import atomic_write_json

log = get_logger(__name__)

PENDING, ANALYSING, RUNNING, VALIDATING, DONE, FAILED, CANCELLED, SKIPPED = (
    "pending", "analysing", "running", "validating", "done", "failed", "cancelled", "skipped")
ACTIVE = {ANALYSING, RUNNING, VALIDATING}
FINISHED = {DONE, FAILED, CANCELLED, SKIPPED}


@dataclass
class Job:
    source: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: str = PENDING
    proxy: str | None = None
    clip: dict = field(default_factory=dict)
    output: str | None = None
    pipeline: str = ""
    frames_done: int = 0
    frames_total: int = 0
    fps: float = 0.0
    device_requested: str = ""
    device_used: str = ""
    encoder: str = ""
    message: str = ""
    command: str = ""
    report: str | None = None
    log_file: str | None = None
    attempts: int = 0
    added: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    settings_summary: dict = field(default_factory=dict)

    @property
    def name(self) -> str:
        return Path(self.source).name

    @property
    def progress(self) -> float:
        return self.frames_done / self.frames_total if self.frames_total else 0.0

    @property
    def eta_seconds(self) -> float | None:
        if self.fps > 0 and self.frames_total:
            return (self.frames_total - self.frames_done) / self.fps
        return None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Job":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


class JobQueue:
    """Thread-safe ordered job list with JSON persistence."""

    def __init__(self, path: Path | None = None, history_path: Path | None = None) -> None:
        self.path = path or user_data_dir() / "queue.json"
        self.history_path = history_path or user_data_dir() / "history.json"
        self._lock = threading.RLock()
        self.jobs: list[Job] = []
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.jobs = [Job.from_dict(d) for d in data.get("jobs", [])]
        except (OSError, ValueError, TypeError):
            self.jobs = []
        for j in self.jobs:
            if j.status in ACTIVE:
                j.status = PENDING
                j.message = "interrupted by an application exit; will restart from the beginning"
                j.frames_done = 0

    def save(self) -> None:
        with self._lock:
            try:
                atomic_write_json(self.path, {"version": 1, "jobs": [j.to_dict() for j in self.jobs]})
            except OSError as e:
                log.error("Could not save the queue: %s", e)

    # --- edits ---------------------------------------------------------
    def add(self, source: str, proxy: str | None = None) -> Job | None:
        with self._lock:
            key = str(Path(source).resolve()).lower()
            if any(str(Path(j.source).resolve()).lower() == key and j.status not in FINISHED for j in self.jobs):
                return None
            j = Job(source=str(source), proxy=proxy)
            self.jobs.append(j)
        self.save()
        return j

    def remove(self, job_id: str) -> bool:
        with self._lock:
            j = self.get(job_id)
            if not j or j.status in ACTIVE:
                return False
            self.jobs.remove(j)
        self.save()
        return True

    def move(self, job_id: str, new_index: int) -> None:
        with self._lock:
            j = self.get(job_id)
            if not j:
                return
            self.jobs.remove(j)
            self.jobs.insert(max(0, min(new_index, len(self.jobs))), j)
        self.save()

    def retry(self, job_id: str) -> bool:
        with self._lock:
            j = self.get(job_id)
            if not j or j.status not in (FAILED, CANCELLED):
                return False
            j.status, j.message, j.frames_done, j.fps = PENDING, "queued for retry", 0, 0.0
        self.save()
        return True

    def retry_all_failed(self) -> int:
        n = 0
        for j in list(self.jobs):
            if self.retry(j.id):
                n += 1
        return n

    def clear_finished(self) -> None:
        with self._lock:
            self.jobs = [j for j in self.jobs if j.status not in FINISHED]
        self.save()

    def get(self, job_id: str) -> Job | None:
        return next((j for j in self.jobs if j.id == job_id), None)

    def next_pending(self) -> Job | None:
        with self._lock:
            return next((j for j in self.jobs if j.status == PENDING), None)

    def claim_next(self) -> Job | None:
        """Atomically take the next pending job (safe with several processors)."""
        with self._lock:
            j = next((j for j in self.jobs if j.status == PENDING), None)
            if j:
                j.status = ANALYSING
            return j

    def snapshot(self) -> list[Job]:
        with self._lock:
            return [Job.from_dict(j.to_dict()) for j in self.jobs]

    # --- history -------------------------------------------------------
    def record_history(self, job: Job) -> None:
        try:
            hist = json.loads(self.history_path.read_text(encoding="utf-8")) if self.history_path.exists() else []
        except (OSError, ValueError):
            hist = []
        hist.append(job.to_dict())
        hist = hist[-500:]
        try:
            atomic_write_json(self.history_path, hist)
        except OSError as e:
            log.error("Could not save history: %s", e)

    def history(self) -> list[Job]:
        try:
            return [Job.from_dict(d) for d in json.loads(self.history_path.read_text(encoding="utf-8"))]
        except (OSError, ValueError, TypeError):
            return []

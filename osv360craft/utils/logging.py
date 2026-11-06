"""Application logging: a rotating file in the user data folder plus an
in-memory ring buffer the Logs page reads."""

from __future__ import annotations

import collections
import logging
import logging.handlers
import threading
from typing import Callable

from osv360craft.utils.paths import logs_dir

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


class RingBufferHandler(logging.Handler):
    """Keeps the last N formatted records and notifies listeners (thread-safe)."""

    def __init__(self, capacity: int = 5000) -> None:
        super().__init__()
        self.records: collections.deque[str] = collections.deque(maxlen=capacity)
        self._listeners: list[Callable[[str], None]] = []
        self._lock_l = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
        except Exception:  # pragma: no cover - formatting must never raise
            return
        self.records.append(line)
        with self._lock_l:
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(line)
            except Exception:
                pass

    def add_listener(self, cb: Callable[[str], None]) -> None:
        with self._lock_l:
            self._listeners.append(cb)


ring = RingBufferHandler()
_configured = False


def setup_logging(level: int = logging.INFO) -> None:
    global _configured
    if _configured:
        return
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(_FORMAT)
    fh = logging.handlers.RotatingFileHandler(
        logs_dir() / "osv360craft.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    ring.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(ring)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)

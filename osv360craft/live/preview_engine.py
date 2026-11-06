"""Stitched preview frames from OpenOSV, without exporting the clip.

Why this design: OpenOSV has no public frame-by-frame API. Its only C ABI
(plugins/common/OsvEngineAbi.h) is private to the Premiere plug-ins, exported
by OpenOSVImporter.prm, CUDA-only and returns device planes for the caller's
own kernel. So the preview drives osvtool directly:

* a parked frame (scrubbing, seam tuning) is one exact render:
  ``osvtool render --frame N --purpose exact --out f.png``;
* playback renders short segments ahead of the play head:
  ``osvtool render --range a-b --purpose play --out seg/f_%06d.png`` and the
  player shows frames as they are written. ``--purpose play`` is OpenOSV's
  own playback mode (it never waits for a stitch analysis).

Every frame shown is a real OpenOSV stitch at the preview size. Playback
speed is whatever this machine renders at that size; it is measured and
displayed, never assumed. The preview never touches the export settings or
the original file.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from osv360craft.export.export_settings import ExportSettings, build_render_command
from osv360craft.hardware.benchmark import parse_progress
from osv360craft.utils.logging import get_logger
from osv360craft.utils.process_manager import popen_flags

log = get_logger(__name__)


@dataclass
class PreviewConfig:
    osvtool: str
    source: str
    frame_count: int
    fps: float
    size: str               # e.g. "1920x960"; "native" allowed
    view_colour: str        # "709" (viewable) or "export" (as exported)
    device: str
    export: ExportSettings
    supported: set[str]
    cache_dir: Path
    cache_bytes: int
    source_colour: str | None = None
    segment_frames: int = 30

    def key(self) -> str:
        """Changes whenever anything that alters the picture changes."""
        d = {"src": self.source, "size": self.size, "view": self.view_colour,
             "stitch": self.export.stitch.changed(), "colour": self.export.colour.__dict__}
        return hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:12]


class FrameCache:
    """On-disk LRU of rendered preview frames, bounded in bytes."""

    def __init__(self, root: Path, budget: int) -> None:
        self.root, self.budget = root, budget
        self.root.mkdir(parents=True, exist_ok=True)
        self._lru: OrderedDict[tuple[str, int], Path] = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def get(self, key: str, n: int) -> Path | None:
        with self._lock:
            p = self._lru.get((key, n))
            if p and p.exists():
                self._lru.move_to_end((key, n))
                return p
            return None

    def put(self, key: str, n: int, src: Path) -> Path:
        dst = self.root / key / f"{n:07d}.png"
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src != dst:
            shutil.move(str(src), dst)
        with self._lock:
            if (key, n) not in self._lru:
                self._bytes += dst.stat().st_size
            self._lru[(key, n)] = dst
            self._lru.move_to_end((key, n))
            while self._bytes > self.budget and len(self._lru) > 1:
                _, old = self._lru.popitem(last=False)
                try:
                    self._bytes -= old.stat().st_size
                    old.unlink()
                except OSError:
                    pass
        return dst

    def clear(self) -> None:
        with self._lock:
            self._lru.clear()
            self._bytes = 0
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def used_bytes(self) -> int:
        return self._bytes


class PreviewEngine:
    """Thread-based frame producer. Callbacks run on worker threads; the UI
    layer forwards them to the GUI thread."""

    def __init__(self, cfg: PreviewConfig, on_frame: Callable[[int, Path], None],
                 on_status: Callable[[str], None] = lambda s: None,
                 on_stats: Callable[[dict], None] = lambda d: None) -> None:
        self.cfg = cfg
        self.on_frame, self.on_status, self.on_stats = on_frame, on_status, on_stats
        self.cache = FrameCache(cfg.cache_dir / "preview", cfg.cache_bytes)
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._job_id = 0
        self._worker: threading.Thread | None = None
        self.last_render_fps: float | None = None
        self.last_device: str = ""

    # ---- public -------------------------------------------------------
    def update_config(self, cfg: PreviewConfig) -> None:
        self.cancel()
        self.cfg = cfg
        self.cache.budget = cfg.cache_bytes

    def request_frame(self, n: int) -> None:
        """Exact stitched frame N (cached)."""
        n = max(0, min(n, self.cfg.frame_count - 1))
        key = self.cfg.key()
        hit = self.cache.get(key, n)
        if hit:
            self.on_frame(n, hit)
            return
        self._start(lambda jid: self._render_single(jid, key, n))

    def play_from(self, n: int) -> None:
        """Render segments ahead from frame N until cancelled or the clip ends."""
        self._start(lambda jid: self._render_segments(jid, self.cfg.key(), n))

    def cancel(self) -> None:
        with self._lock:
            self._job_id += 1
            p = self._proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    # ---- internals ----------------------------------------------------
    def _start(self, fn: Callable[[int], None]) -> None:
        self.cancel()
        with self._lock:
            jid = self._job_id
        t = threading.Thread(target=self._guard, args=(fn, jid), daemon=True, name="preview")
        self._worker = t
        t.start()

    def _guard(self, fn: Callable[[int], None], jid: int) -> None:
        try:
            fn(jid)
        except Exception as e:  # keep the GUI alive whatever happens
            log.exception("Preview failed")
            self.on_status(f"Preview error: {e}")

    def _alive(self, jid: int) -> bool:
        return jid == self._job_id

    def _args(self, out: str, frame: int | None = None, rng: str | None = None, purpose: str = "exact") -> list[str]:
        c = self.cfg
        force = None
        if c.view_colour == "709":
            force = "709"
        elif c.export.colour.resolved(c.source_colour) == "dlogm":
            force = "dlogm"
        args = build_render_command(c.osvtool, c.source, out, c.export, device=c.device, frame=frame,
                                    frame_range=rng, size_override=c.size, purpose=purpose, force_colour=force,
                                    supported=c.supported or None, source_colour=c.source_colour)
        if "--mode" in args:  # preview is always the full sphere
            args[args.index("--mode") + 1] = "equirect"
        return args

    def _spawn(self, args: list[str], jid: int) -> subprocess.Popen | None:
        with self._lock:
            if not self._alive(jid):
                return None
            self._proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL, **popen_flags())
            return self._proc

    def _render_single(self, jid: int, key: str, n: int) -> None:
        tmp = self.cfg.cache_dir / "work" / f"single_{jid}"
        tmp.mkdir(parents=True, exist_ok=True)
        out = tmp / "frame.png"
        self.on_status(f"Stitching frame {n} ...")
        t0 = time.perf_counter()
        p = self._spawn(self._args(str(out), frame=n, purpose="exact"), jid)
        if not p:
            return
        text = p.communicate()[0].decode("utf-8", "replace")
        if not self._alive(jid):
            shutil.rmtree(tmp, ignore_errors=True)
            return
        if p.returncode != 0 or not out.exists():
            err = next((l[7:] for l in text.splitlines() if l.startswith("error: ")), text.strip()[-300:])
            self.on_status(f"Frame {n} could not be stitched: {err}")
            shutil.rmtree(tmp, ignore_errors=True)
            return
        dt = time.perf_counter() - t0
        path = self.cache.put(key, n, out)
        shutil.rmtree(tmp, ignore_errors=True)
        self.on_stats({"single_frame_seconds": dt, "cache_bytes": self.cache.used_bytes})
        self.on_status(f"Frame {n} stitched in {dt:.2f} s (exact)")
        self.on_frame(n, path)

    def _render_segments(self, jid: int, key: str, start: int) -> None:
        c = self.cfg
        n = start
        while self._alive(jid) and n < c.frame_count:
            # skip frames already cached
            while n < c.frame_count and self.cache.get(key, n):
                if not self._alive(jid):
                    return
                self.on_frame(n, self.cache.get(key, n))
                n += 1
            if n >= c.frame_count:
                break
            a, b = n, min(c.frame_count - 1, n + c.segment_frames - 1)
            seg = c.cache_dir / "work" / f"seg_{jid}_{a}"
            seg.mkdir(parents=True, exist_ok=True)
            p = self._spawn(self._args(str(seg / "f_%06d.png"), rng=f"{a}-{b}", purpose="play"), jid)
            if not p:
                return
            delivered: set[int] = set()
            base: list[int | None] = [None]
            reader = threading.Thread(target=self._read_progress, args=(p,), daemon=True)
            reader.start()

            def harvest(final: bool) -> None:
                files = sorted((int(m.group(1)), q) for q in seg.glob("f_*.png")
                               if (m := re.match(r"f_(\d+)\.png$", q.name)))
                if not files:
                    return
                if base[0] is None:
                    base[0] = files[0][0]
                # the newest file may still be being written unless the process ended
                ready = files if final else files[:-1]
                for num, q in ready:
                    if num in delivered:
                        continue
                    delivered.add(num)
                    frame = a + (num - base[0])  # works for absolute or relative numbering
                    path = self.cache.put(key, frame, q)
                    if self._alive(jid):
                        self.on_frame(frame, path)

            while p.poll() is None and self._alive(jid):
                harvest(False)
                time.sleep(0.03)
            if not self._alive(jid):
                shutil.rmtree(seg, ignore_errors=True)
                return
            harvest(True)
            shutil.rmtree(seg, ignore_errors=True)
            if p.returncode != 0:
                self.on_status(f"Playback render stopped (osvtool exit {p.returncode}); see Logs")
                return
            n = b + 1
        if self._alive(jid):
            self.on_status("End of clip")

    def _read_progress(self, p: subprocess.Popen) -> None:
        assert p.stdout is not None
        for raw in iter(p.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            pr = parse_progress(line)
            if pr:
                self.last_render_fps, self.last_device = pr[2], pr[3]
                self.on_stats({"render_fps": pr[2], "device": pr[3], "cache_bytes": self.cache.used_bytes})
            elif line.startswith("error: "):
                self.on_status(line)

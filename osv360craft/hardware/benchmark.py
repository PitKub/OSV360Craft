"""Short, non-destructive first-run benchmark on one of the user's own clips.

Measured separately:
* decode   - FFmpeg decoding one lens stream (hardware hwaccel, then software);
* stitch   - osvtool rendering 60 equirect frames at 1920x960 on every renderer
             OpenOSV reports available (colour conversion runs in the same GPU
             kernel as the stitch, so the two cannot be timed apart; this is
             stated in the result);
* full-res - 30 frames at the clip's native equirect size on the fastest renderer;
* encode   - each verified HEVC encoder on 30 synthetic 8K 10-bit frames.

CPU, RAM, GPU and VRAM use are sampled while it runs. Every step has a
timeout and the whole run can be cancelled. Outputs go to a temp folder that
is removed afterwards. No DJI sample is bundled, so a clip must be chosen.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from osv360craft.hardware import gpu_detector
from osv360craft.utils.logging import get_logger
from osv360craft.utils.paths import user_data_dir
from osv360craft.utils.process_manager import popen_flags

log = get_logger(__name__)

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

PROGRESS_RE = re.compile(r"^\s*(\d+)/(\d+)\s+frames\s+([\d.]+)\s+fps\s+\(([^)]*)\)\s*$")


def parse_progress(line: str) -> tuple[int, int, float, str] | None:
    """osvtool's progress line: '  12/65 frames  14.2 fps  (cuda)'."""
    m = PROGRESS_RE.match(line.strip("\r\n"))
    if not m:
        return None
    done, total = int(m.group(1)), int(m.group(2))
    if total == 0 or done > total:
        return None
    return done, total, float(m.group(3)), m.group(4)


@dataclass
class BenchmarkResult:
    created: str = ""
    fingerprint: str = ""
    clip: str = ""
    decode_fps: dict[str, float] = field(default_factory=dict)   # method -> frames/s (one lens)
    device_fps: dict[str, float] = field(default_factory=dict)   # renderer -> frames/s at 1920x960
    device_errors: dict[str, str] = field(default_factory=dict)
    preview_fps: float | None = None
    fullres_fps: float | None = None
    fullres_size: str = ""
    encode_fps: dict[str, float] = field(default_factory=dict)   # encoder -> frames/s at 7680x3840
    peak_cpu_percent: float | None = None
    peak_ram_bytes: int | None = None
    peak_gpu_percent: float | None = None
    peak_vram_used_bytes: int | None = None
    notes: list[str] = field(default_factory=list)
    cancelled: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BenchmarkResult":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})


def results_path() -> Path:
    return user_data_dir() / "benchmark.json"


def load_saved() -> BenchmarkResult | None:
    p = results_path()
    if not p.exists():
        return None
    try:
        return BenchmarkResult.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def save(res: BenchmarkResult) -> None:
    results_path().write_text(json.dumps(res.to_dict(), indent=2), encoding="utf-8")


def needs_rerun(saved: BenchmarkResult | None, fingerprint: str) -> bool:
    return saved is None or saved.cancelled or saved.fingerprint != fingerprint


class _Sampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop_evt = threading.Event()
        self.cpu = 0.0
        self.ram = 0
        self.gpu = None
        self.vram = None
        self.pids: set[int] = set()

    def run(self) -> None:
        nvidia = bool(gpu_detector.query_nvidia_smi(with_cc=False))
        if psutil:
            psutil.cpu_percent(None)
        while not self.stop_evt.wait(1.0):
            if psutil:
                self.cpu = max(self.cpu, psutil.cpu_percent(None))
                rss = 0
                for pid in list(self.pids):
                    try:
                        p = psutil.Process(pid)
                        rss += p.memory_info().rss
                        rss += sum(c.memory_info().rss for c in p.children(recursive=True))
                    except (psutil.Error, OSError):
                        pass
                self.ram = max(self.ram, rss)
            if nvidia:
                u = gpu_detector.poll_utilisation()
                if u.get("gpu_percent") is not None:
                    self.gpu = max(self.gpu or 0, u["gpu_percent"])
                if u.get("vram_used_bytes") is not None:
                    self.vram = max(self.vram or 0, u["vram_used_bytes"])


class Benchmark:
    def __init__(self, osvtool: str | None, ffmpeg: str | None, clip: str, renderers: list[str],
                 encoders: list[str], native_size: str | None, fingerprint: str,
                 progress: Callable[[str, float], None] | None = None) -> None:
        self.osvtool, self.ffmpeg, self.clip = osvtool, ffmpeg, clip
        self.renderers, self.encoders, self.native_size = renderers, encoders, native_size
        self.fingerprint = fingerprint
        self.progress = progress or (lambda msg, frac: None)
        self._cancel = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._sampler = _Sampler()

    def cancel(self) -> None:
        self._cancel.set()
        p = self._proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    # ------------------------------------------------------------------
    def _run(self, args: list[str], timeout: float, on_line: Callable[[str], None] | None = None) -> tuple[int | None, float, str]:
        if self._cancel.is_set():
            return None, 0.0, "cancelled"
        t0 = time.perf_counter()
        tail: list[str] = []
        try:
            self._proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                          stdin=subprocess.DEVNULL, **popen_flags())
        except OSError as e:
            return None, 0.0, str(e)
        self._sampler.pids.add(self._proc.pid)
        killer = threading.Timer(timeout, self._proc.kill)
        killer.start()
        try:
            assert self._proc.stderr is not None
            buf = b""
            while True:
                chunk = self._proc.stderr.read1(4096) if hasattr(self._proc.stderr, "read1") else self._proc.stderr.read(4096)
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"\r\n|\n|\r", buf)
                buf = parts.pop()
                for raw in parts:
                    line = raw.decode("utf-8", "replace")
                    tail = (tail + [line])[-20:]
                    if on_line:
                        on_line(line)
            rc = self._proc.wait()
        finally:
            killer.cancel()
            self._sampler.pids.discard(self._proc.pid)
        if self._cancel.is_set():
            return None, time.perf_counter() - t0, "cancelled"
        return rc, time.perf_counter() - t0, "\n".join(tail)

    def _render_fps(self, device: str, size: str, frames: int, out: Path, encoder: str | None) -> tuple[float | None, str]:
        last: list = [None]

        def on_line(line: str) -> None:
            p = parse_progress(line)
            if p:
                last[0] = p

        args = [self.osvtool, "render", self.clip, "--range", f"0-{frames - 1}", "--mode", "equirect", "--size", size,
                "--color", "709", "--device", device, "--no-audio", "--no-spherical-metadata", "--out", str(out)]
        if encoder:
            args[-2:-2] = ["--codec", encoder]
        if self.ffmpeg:
            args[-2:-2] = ["--ffmpeg", self.ffmpeg]
        rc, secs, tail = self._run(args, timeout=600, on_line=on_line)
        if rc != 0:
            err = next((l[7:] for l in tail.splitlines() if l.startswith("error: ")), tail[-300:])
            return None, err or f"exit {rc}"
        if last[0] and last[0][3]:
            return last[0][2], f"{last[0][3]}"
        return frames / secs if secs > 0 else None, "wall clock"

    def run(self) -> BenchmarkResult:
        res = BenchmarkResult(time.strftime("%Y-%m-%d %H:%M:%S"), self.fingerprint, self.clip)
        res.notes.append("Colour conversion runs inside OpenOSV's stitching kernel; it is included in the stitch figures.")
        self._sampler.start()
        steps = 2 + len(self.renderers) + len(self.encoders) + (1 if self.native_size else 0)
        k = 0
        try:
            with tempfile.TemporaryDirectory(prefix="osv360craft_bench_") as td:
                tdp = Path(td)
                # 1. decode
                if self.ffmpeg:
                    for method, pre in (("hardware", ["-hwaccel", "auto"]), ("software", [])):
                        self.progress(f"Decode test ({method})", k / steps)
                        rc, secs, tail = self._run([self.ffmpeg, "-v", "error", *pre, "-i", self.clip, "-map", "0:v:0",
                                                    "-frames:v", "120", "-f", "null", "-"], timeout=300)
                        if rc == 0 and secs > 0:
                            res.decode_fps[method] = round(120 / secs, 1)
                        k += 1
                    res.notes.append("Decode figures are for one lens stream; OpenOSV decodes two per frame.")
                encoder = self.encoders[0] if self.encoders else None
                # 2. stitch per renderer
                for dev in self.renderers:
                    self.progress(f"Stitch test on {dev.upper()} (60 frames, 1920x960)", k / steps)
                    fps, detail = self._render_fps(dev, "1920x960", 60, tdp / f"s_{dev}.mp4", encoder)
                    if fps:
                        res.device_fps[dev] = round(fps, 2)
                    else:
                        res.device_errors[dev] = detail
                    k += 1
                if res.device_fps:
                    best = max(res.device_fps, key=res.device_fps.get)
                    res.preview_fps = res.device_fps[best]
                    # 3. full resolution
                    if self.native_size:
                        self.progress(f"Full-resolution test on {best.upper()} (30 frames, {self.native_size})", k / steps)
                        fps, detail = self._render_fps(best, self.native_size, 30, tdp / "full.mp4", encoder)
                        res.fullres_fps, res.fullres_size = (round(fps, 2) if fps else None), self.native_size
                        if not fps:
                            res.notes.append(f"Full-resolution test failed: {detail}")
                        k += 1
                # 4. encoders
                if self.ffmpeg:
                    for enc in self.encoders:
                        self.progress(f"Encode test {enc} (30 frames, 7680x3840 10-bit)", k / steps)
                        out = tdp / f"e_{enc}.mp4"
                        rc, secs, _ = self._run([self.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i",
                                                 "testsrc2=s=7680x3840:r=30", "-frames:v", "30", "-pix_fmt",
                                                 "yuv420p10le", "-c:v", enc, str(out)], timeout=600)
                        if rc == 0 and secs > 0:
                            res.encode_fps[enc] = round(30 / secs, 2)
                        k += 1
                    res.notes.append("Encode figures include generating the synthetic 8K test pattern.")
        finally:
            self._sampler.stop_evt.set()
            self._sampler.join(timeout=3)
        res.cancelled = self._cancel.is_set()
        res.peak_cpu_percent = self._sampler.cpu or None
        res.peak_ram_bytes = self._sampler.ram or None
        res.peak_gpu_percent = self._sampler.gpu
        res.peak_vram_used_bytes = self._sampler.vram
        if res.peak_gpu_percent is None:
            res.notes.append("GPU utilisation is sampled through nvidia-smi and is not available on this GPU.")
        self.progress("Benchmark cancelled" if res.cancelled else "Benchmark complete", 1.0)
        return res

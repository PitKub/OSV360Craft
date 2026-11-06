"""Real-time dual-fisheye frames for the live preview.

Source, in order of preference (nothing is ever written to disk, no proxy is created):

1. the camera's own ``.LRF`` proxy beside the ``.OSV`` (2048 x 1024, both lenses side
   by side; the Osmo 360 records it automatically) - decoded by FFmpeg in real time;
2. without an ``.LRF``: the ``.OSV``'s two lens streams decoded on the GPU (NVDEC) where
   available, each scaled to 1024 x 1024 on the GPU, placed side by side - the same layout.

Frames arrive as 8-bit RGBA ``QImage`` objects through a small bounded queue; when the
viewer falls behind, FFmpeg simply waits (the pipe fills), so memory stays constant.
The GPU viewer projects them onto the sphere with OpenOSV's calibration
(``fisheye_calib``). This is a quick look, not OpenOSV's stitch: a paused frame is
replaced by the exact OpenOSV render.
"""

from __future__ import annotations

import queue
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from osv360craft.utils.file_manager import find_proxy
from osv360craft.utils.logging import get_logger
from osv360craft.utils.process_manager import native_args, popen_flags

log = get_logger(__name__)

HALF = 1024
WIDTH, HEIGHT = 2 * HALF, HALF


@dataclass
class SourcePlan:
    kind: str            # "lrf" or "osv-gpu" or "osv-cpu"
    path: str
    description: str
    fps: float | None = None      # the source's own frame rate (an .LRF may differ from its .OSV)
    frames: int | None = None


def probe_video(ffprobe: str | None, path: str) -> tuple[float | None, int | None]:
    """Frame rate and frame count of the first video stream (ffprobe), or (None, None)."""
    if not ffprobe:
        return None, None
    import json
    from fractions import Fraction

    from osv360craft.utils import process_manager as pm
    r = pm.run([ffprobe, "-v", "error", "-select_streams", "v:0", "-show_entries",
                "stream=avg_frame_rate,r_frame_rate,nb_frames", "-of", "json", path], timeout=30)
    try:
        st = json.loads(r.stdout)["streams"][0]
        rate = st.get("avg_frame_rate") or st.get("r_frame_rate")
        fps = float(Fraction(rate)) if rate and rate != "0/0" else None
        n = int(st["nb_frames"]) if str(st.get("nb_frames", "")).isdigit() else None
        return fps, n
    except (ValueError, KeyError, IndexError, ZeroDivisionError):
        return None, None


def choose_source(osv: str, nvdec: bool, ffprobe: str | None = None) -> SourcePlan:
    lrf = find_proxy(Path(osv))
    if lrf is not None:
        fps, frames = probe_video(ffprobe, str(lrf))
        rate = f", {fps:.3f} fps" if fps else ""
        return SourcePlan("lrf", str(lrf), f"camera proxy {lrf.name} (decoded in real time{rate})", fps, frames)
    if nvdec:
        return SourcePlan("osv-gpu", osv, "no .LRF beside the clip: both lenses decoded on the GPU (NVDEC), scaled down")
    return SourcePlan("osv-cpu", osv, "no .LRF and no NVDEC: both 8K lenses decoded on the CPU (may not keep up)")


def ffmpeg_args(ffmpeg: str, plan: SourcePlan, start_s: float) -> list[str]:
    seek = ["-ss", f"{max(0.0, start_s):.3f}"] if start_s > 0 else []
    out = ["-f", "rawvideo", "-pix_fmt", "rgba", "-"]
    if plan.kind == "lrf":
        return [ffmpeg, "-v", "error", "-nostdin", *seek, "-i", plan.path, "-map", "0:v:0",
                "-vf", f"scale={WIDTH}:{HEIGHT}", *out]
    if plan.kind == "osv-gpu":
        # track 1 = slave lens (left), track 2 = master lens (right), as in the .LRF
        graph = (f"[0:v:0]scale_cuda={HALF}:{HALF},hwdownload,format=nv12[a];"
                 f"[0:v:1]scale_cuda={HALF}:{HALF},hwdownload,format=nv12[b];[a][b]hstack=inputs=2[v]")
        return [ffmpeg, "-v", "error", "-nostdin", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda", *seek,
                "-i", plan.path, "-filter_complex", graph, "-map", "[v]", *out]
    graph = f"[0:v:0]scale={HALF}:{HALF}[a];[0:v:1]scale={HALF}:{HALF}[b];[a][b]hstack=inputs=2[v]"
    return [ffmpeg, "-v", "error", "-nostdin", "-threads", "0", *seek, "-i", plan.path, "-filter_complex", graph,
            "-map", "[v]", *out]


class RealtimeSource:
    """Decodes from a start frame in a background thread; get() hands out frames in order."""

    def __init__(self, ffmpeg: str, plan: SourcePlan, fps: float, queue_frames: int = 8) -> None:
        """``fps`` is the CLIP's frame rate: frames are numbered in clip frames, even when the
        source (an .LRF) runs at another rate."""
        self.ffmpeg, self.plan, self.fps = ffmpeg, plan, fps or 30.0
        self.source_fps = plan.fps or self.fps
        self.stderr_tail: list[str] = []
        self.frames: queue.Queue = queue.Queue(maxsize=queue_frames)
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.error = ""
        self.ended = False
        self.next_index = 0

    def start(self, frame: int) -> None:
        self.stop()
        self._stop.clear()
        self.error, self.ended = "", False
        self.frames = queue.Queue(maxsize=self.frames.maxsize)
        self.next_index = frame
        args = ffmpeg_args(self.ffmpeg, self.plan, frame / self.fps)
        try:
            self._proc = subprocess.Popen(native_args(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          stdin=subprocess.DEVNULL, bufsize=WIDTH * HEIGHT * 4, **popen_flags())
        except OSError as e:
            self.error = str(e)
            return
        self._thread = threading.Thread(target=self._read, args=(self._proc, frame), daemon=True, name="realtime")
        self._thread.start()
        # FFmpeg's messages are read continuously: an unread pipe fills up and freezes the decoder.
        threading.Thread(target=self._drain, args=(self._proc,), daemon=True, name="realtime-log").start()

    def _drain(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for raw in iter(proc.stderr.readline, b""):
            line = raw.decode("utf-8", "replace").strip()
            if line:
                self.stderr_tail = (self.stderr_tail + [line])[-20:]

    def _read(self, proc: subprocess.Popen, first: int) -> None:
        from PySide6.QtGui import QImage

        size = WIDTH * HEIGHT * 4
        k = 0
        ratio = self.fps / self.source_fps   # clip frames per source frame (2.0 for a 30 fps proxy of a 60 fps clip)
        assert proc.stdout is not None
        while not self._stop.is_set():
            buf = proc.stdout.read(size)
            if not buf or len(buf) < size:
                break
            n = first + int(round(k * ratio))
            k += 1
            img = QImage(buf, WIDTH, HEIGHT, WIDTH * 4, QImage.Format_RGBA8888).copy()  # own the pixels
            while not self._stop.is_set():
                try:
                    self.frames.put((n, img), timeout=0.2)
                    break
                except queue.Full:
                    continue
        if not self._stop.is_set():
            rc = proc.wait()
            if rc not in (0, None):
                self.error = (self.stderr_tail[-1] if self.stderr_tail else f"FFmpeg exit code {rc}")[:300]
                log.warning("Real-time preview source stopped: %s", self.error)
            self.ended = True

    def get(self) -> tuple[int, object] | None:
        """The next decoded frame, or None if none is ready yet."""
        try:
            n, img = self.frames.get_nowait()
        except queue.Empty:
            return None
        self.next_index = n + 1
        return n, img

    def stop(self) -> None:
        self._stop.set()
        p = self._proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=2)
        self._proc = None
        self._thread = None


def grab_frame(ffmpeg: str, plan: SourcePlan, frame: int, fps: float, timeout: float = 30):
    """One frame at FRAME (for scrubbing in real-time mode)."""
    from PySide6.QtGui import QImage

    args = ffmpeg_args(ffmpeg, plan, frame / (fps or 30.0))
    args[-1:-1] = ["-frames:v", "1"]
    try:
        cp = subprocess.run(native_args(args), capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                            **popen_flags())
    except (OSError, subprocess.TimeoutExpired):
        return None
    if len(cp.stdout) < WIDTH * HEIGHT * 4:
        return None
    return QImage(cp.stdout[:WIDTH * HEIGHT * 4], WIDTH, HEIGHT, WIDTH * 4, QImage.Format_RGBA8888).copy()

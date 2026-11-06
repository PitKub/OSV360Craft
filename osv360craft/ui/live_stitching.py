"""Live Stitching: open an .OSV and look around the stitched sphere.

Frames are real OpenOSV stitches at the preview size (see live/preview_engine).
Playback shows frames as fast as this machine renders them; the measured
playback, stitching and decode speeds are displayed, never assumed.
"""

from __future__ import annotations

import tempfile
import time
from collections import OrderedDict, deque
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QSlider, QSplitter, QVBoxLayout, QWidget)

from osv360craft.core import osv_probe
from osv360craft.hardware import gpu_detector
from osv360craft.hardware.auto_optimizer import PREVIEW_MODES
from osv360craft.live.panorama_viewer import make_viewer
from osv360craft.live.preview_engine import PreviewConfig, PreviewEngine
from osv360craft.ui.widgets import KeyValueGrid
from osv360craft.utils import process_manager as pm
from osv360craft.utils.file_manager import find_proxy, human_bytes
from osv360craft.utils.paths import cache_dir

PREVIEW_LABELS = {"native": "Native", "4k": "4K preview (3840x1920)", "2k": "2K preview (1920x960)",
                  "performance": "Performance (1280x640)"}
MAX_DECODED = 90


class _Bridge(QObject):
    frame = Signal(int, object)     # n, QImage
    status = Signal(str)
    stats = Signal(dict)
    source = Signal(object)         # QImage


class LiveStitchingPage:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.engine: PreviewEngine | None = None
        self.clip: osv_probe.ClipInfo | None = None
        self.bridge = _Bridge()
        self.frames: OrderedDict[int, QImage] = OrderedDict()
        self.current = 0
        self.playing = False
        self._shown_times: deque[float] = deque(maxlen=30)
        self._waiting_since: float | None = None

        self.widget = QWidget()
        root = QVBoxLayout(self.widget)
        root.setContentsMargins(18, 12, 18, 12)
        title = QLabel("Live Stitching")
        title.setObjectName("PageTitle")
        root.addWidget(title)

        bar = QHBoxLayout()
        b_open = QPushButton("Open .OSV...")
        b_open.setObjectName("Primary")
        b_open.clicked.connect(self.open_dialog)
        bar.addWidget(b_open)
        bar.addWidget(QLabel("Preview:"))
        self.mode = QComboBox()
        for k, v in PREVIEW_LABELS.items():
            self.mode.addItem(v, k)
        self.mode.currentIndexChanged.connect(self._reconfigure)
        bar.addWidget(self.mode)
        self.view_colour = QComboBox()
        self.view_colour.addItem("View in Rec.709 (D-Log M shown graded for viewing)", "709")
        self.view_colour.addItem("View as exported", "export")
        self.view_colour.currentIndexChanged.connect(self._reconfigure)
        bar.addWidget(self.view_colour)
        self.flat = QCheckBox("Flat view")
        self.flat.toggled.connect(lambda v: self.viewer.set_flat(v))
        self.guide = QCheckBox("Horizon guide")
        self.guide.toggled.connect(lambda v: self.viewer.set_guide(v))
        bar.addWidget(self.flat)
        bar.addWidget(self.guide)
        bar.addStretch(1)
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.viewer = make_viewer()
        self.viewer.setMinimumSize(640, 360)
        self.viewer.viewChanged.connect(self._view_changed)
        ll.addWidget(self.viewer, 1)
        tl = QHBoxLayout()
        self.b_play = QPushButton("Play")
        self.b_play.clicked.connect(self.toggle_play)
        b_prev = QPushButton("<")
        b_prev.setToolTip("Previous frame")
        b_prev.clicked.connect(lambda: self.seek(self.current - 1))
        b_next = QPushButton(">")
        b_next.setToolTip("Next frame")
        b_next.clicked.connect(lambda: self.seek(self.current + 1))
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setEnabled(False)
        self.slider.sliderReleased.connect(lambda: self.seek(self.slider.value()))
        self.pos = QLabel("-")
        for w in (b_prev, self.b_play, b_next):
            tl.addWidget(w)
        tl.addWidget(self.slider, 1)
        tl.addWidget(self.pos)
        ll.addLayout(tl)
        self.status = QLabel("Open a recording. Frames are stitched by OpenOSV; nothing is exported.")
        self.status.setObjectName("Muted")
        self.status.setWordWrap(True)
        ll.addWidget(self.status)
        split.addWidget(left)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        g = QGroupBox("Clip")
        self.info = KeyValueGrid()
        QVBoxLayout(g).addWidget(self.info)
        rl.addWidget(g)
        g2 = QGroupBox("Source lenses")
        v2 = QVBoxLayout(g2)
        self.src_img = QLabel("-")
        self.src_img.setAlignment(Qt.AlignCenter)
        self.src_img.setMinimumHeight(120)
        v2.addWidget(self.src_img)
        rl.addWidget(g2)
        g3 = QGroupBox("Performance")
        self.perf = KeyValueGrid()
        QVBoxLayout(g3).addWidget(self.perf)
        rl.addWidget(g3)
        g4 = QGroupBox("View")
        v4 = QVBoxLayout(g4)
        self.view_lbl = QLabel("yaw 0, pitch 0, roll 0, FOV 90")
        v4.addWidget(self.view_lbl)
        b_head = QPushButton("Use this heading as the initial viewing direction")
        b_head.setToolTip("Lossless: stored as 360 metadata in exports")
        b_head.clicked.connect(lambda: self.ctx.applyHeading.emit(self.viewer.view.yaw))
        v4.addWidget(b_head)
        b_frame = QPushButton("Re-stitch this frame with current settings")
        b_frame.clicked.connect(lambda: self.seek(self.current, force=True))
        v4.addWidget(b_frame)
        rl.addWidget(g4)
        rl.addStretch(1)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        self.timer = QTimer(self.widget)
        self.timer.timeout.connect(self._tick)
        self.gpu_timer = QTimer(self.widget)
        self.gpu_timer.timeout.connect(self._poll_gpu)
        self.gpu_timer.start(3000)
        self.bridge.frame.connect(self._on_frame)
        self.bridge.status.connect(self.status.setText)
        self.bridge.stats.connect(self._on_stats)
        self.bridge.source.connect(self._on_source)
        ctx.exportChanged.connect(self._settings_changed)
        ctx.planChanged.connect(self._apply_plan_default)
        self.widget.setAcceptDrops(True)
        self.widget.dragEnterEvent = self._drag_enter
        self.widget.dropEvent = self._drop
        self._apply_plan_default()

    # ---- opening --------------------------------------------------------
    def _drag_enter(self, e) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def _drop(self, e) -> None:
        for u in e.mimeData().urls():
            p = u.toLocalFile()
            if p.lower().endswith(".osv"):
                self.open(p)
                return

    def open_dialog(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self.widget, "Open DJI Osmo 360 recording", "", "DJI Osmo 360 (*.OSV *.osv)")
        if p:
            self.open(p)

    def open(self, path: str) -> None:
        rep = self.ctx.hardware
        if not rep or not rep.openosv.found:
            self.status.setText("OpenOSV (osvtool) is not available; see Hardware Analyzer.")
            return
        self.stop()
        self.status.setText(f"Analysing {Path(path).name} ...")
        tool, ffprobe = Path(str(rep.openosv.path)), rep.ffmpeg.ffprobe

        def done(ci: osv_probe.ClipInfo):
            if not ci.ok:
                self.status.setText(f"Cannot open: {ci.error}")
                return
            self.clip = ci
            self.ctx.current_clip = ci
            self.ctx.clipOpened.emit(ci)
            self.ctx.recompute_plan()
            self._show_info(ci)
            self.slider.setRange(0, max(0, (ci.frame_count or 1) - 1))
            self.slider.setEnabled(True)
            self.frames.clear()
            self._reconfigure()

        self.ctx.run_task(lambda: osv_probe.probe(tool, Path(path), ffprobe), done)

    def _show_info(self, ci: osv_probe.ClipInfo) -> None:
        self.info.set("File", Path(ci.path).name)
        self.info.set("Duration", f"{ci.duration_s:.2f} s, {ci.frame_count} frames" if ci.duration_s else "unknown")
        lbl = ci.recording_mode_label
        self.info.set("Recording", lbl if "per lens" in lbl else f"{lbl} ({ci.stream_w}x{ci.stream_h} per lens)")
        ne = ci.native_equirect
        self.info.set("Native equirect", f"{ne[0]}x{ne[1]}" if ne else "unknown")
        self.info.set("Frame rate", ci.fps_label)
        self.info.set("Codec / depth", f"{ci.codec or '?'} {ci.bit_depth or '?'}-bit {ci.pix_fmt or ''}")
        self.info.set("Colour", ci.color_label)
        self.info.set("Lens streams", str(ci.video_streams) if ci.video_streams is not None else "unknown",
                      "ok" if (ci.video_streams or 0) >= 2 else "warn")
        self.info.set("Audio", f"{ci.audio.get('sampleRate')} Hz, {ci.audio.get('channels')} ch"
                      if ci.has_audio and ci.audio else ("yes" if ci.has_audio else "none"))
        self.info.set("Camera", ci.camera or "unknown")
        self.info.set("Proxy (.LRF)", "found" if find_proxy(Path(ci.path)) else "none")
        if ci.warnings:
            self.info.set("Notes", "; ".join(ci.warnings[:3]), "warn")

    # ---- configuration --------------------------------------------------
    def _apply_plan_default(self) -> None:
        plan = self.ctx.plan
        if plan and not getattr(self, "_mode_set", False):
            i = self.mode.findData(plan.preview_mode)
            if i >= 0:
                self.mode.blockSignals(True)
                self.mode.setCurrentIndex(i)
                self.mode.blockSignals(False)
                self._mode_set = True

    def _config(self) -> PreviewConfig | None:
        rep, ci = self.ctx.hardware, self.clip
        if not (rep and ci and rep.openosv.found):
            return None
        mode = self.mode.currentData()
        size = PREVIEW_MODES.get(mode) or ("{}x{}".format(*ci.native_equirect) if ci.native_equirect else "1920x960")
        plan = self.ctx.plan
        return PreviewConfig(osvtool=str(rep.openosv.path), source=ci.path, frame_count=ci.frame_count or 1,
                             fps=ci.fps or 30.0, size=size, view_colour=self.view_colour.currentData(),
                             device=plan.device if plan else "auto", export=self.ctx.export,
                             supported=rep.openosv.render_options, cache_dir=cache_dir(),
                             cache_bytes=plan.cache_bytes if plan else 1024 ** 3, source_colour=ci.color_mode)

    def _reconfigure(self) -> None:
        cfg = self._config()
        if not cfg:
            return
        was_playing = self.playing
        self.stop()
        self.frames.clear()
        if self.engine is None:
            self.engine = PreviewEngine(cfg, self._engine_frame, self.bridge.status.emit, self.bridge.stats.emit)
        else:
            self.engine.update_config(cfg)
        self.perf.set("Preview size", cfg.size + " (does not change the export)")
        self.seek(self.current, force=True)
        if was_playing:
            self.toggle_play()

    def _settings_changed(self) -> None:
        if self.clip:
            self._reconfigure()

    # ---- frames ---------------------------------------------------------
    def _engine_frame(self, n: int, path: Path) -> None:
        img = QImage(str(path))  # decoded on the engine thread, not the GUI thread
        if not img.isNull():
            self.bridge.frame.emit(n, img)

    def _on_frame(self, n: int, img: QImage) -> None:
        self.frames[n] = img
        self.frames.move_to_end(n)
        while len(self.frames) > MAX_DECODED:
            k, _ = next(iter(self.frames.items()))
            if k == self.current:
                break
            self.frames.popitem(last=False)
        if not self.playing and n == self.current:
            self._display(n)

    def _display(self, n: int) -> None:
        img = self.frames.get(n)
        if img is None:
            return
        self.viewer.set_image(img)
        self.current = n
        self.slider.blockSignals(True)
        self.slider.setValue(n)
        self.slider.blockSignals(False)
        fps = self.clip.fps or 30 if self.clip else 30
        self.pos.setText(f"{n} / {(self.clip.frame_count or 1) - 1}  ({n / fps:.2f} s)")
        now = time.perf_counter()
        self._shown_times.append(now)
        if len(self._shown_times) > 2:
            span = self._shown_times[-1] - self._shown_times[0]
            if span > 0 and self.playing:
                self.perf.set("Playback", f"{(len(self._shown_times) - 1) / span:.1f} fps "
                              f"(source {self.clip.fps_label if self.clip else '?'})")

    def seek(self, n: int, force: bool = False) -> None:
        if not (self.engine and self.clip):
            return
        n = max(0, min(n, (self.clip.frame_count or 1) - 1))
        self.current = n
        if self.playing:
            self.stop()
        if n in self.frames and not force:
            self._display(n)
        else:
            self.engine.request_frame(n)
        self._request_source(n)

    def toggle_play(self) -> None:
        if not (self.engine and self.clip):
            return
        if self.playing:
            self.stop()
            return
        self.playing = True
        self.b_play.setText("Pause")
        self._shown_times.clear()
        self.engine.play_from(self.current)
        self.timer.start(max(1, int(1000 / (self.clip.fps or 30))))

    def stop(self) -> None:
        if self.playing:
            self.playing = False
            self.b_play.setText("Play")
            self.timer.stop()
            if self.engine:
                self.engine.cancel()
        self.perf.set("Playback", "paused")

    def _tick(self) -> None:
        nxt = self.current + 1
        if self.clip and nxt >= (self.clip.frame_count or 1):
            self.stop()
            return
        if nxt in self.frames:
            self._display(nxt)
            self._waiting_since = None
        else:
            self._waiting_since = self._waiting_since or time.perf_counter()
            self.perf.set("Playback", "waiting for the stitcher (rendering slower than real time)", "warn")

    # ---- side panels ----------------------------------------------------
    def _on_stats(self, d: dict) -> None:
        if "render_fps" in d:
            self.perf.set("Stitching", f"{d['render_fps']:.1f} fps on {str(d.get('device', '?')).upper()}")
        if "single_frame_seconds" in d:
            self.perf.set("Exact frame", f"{d['single_frame_seconds']:.2f} s")
        if "cache_bytes" in d:
            self.perf.set("Frame cache", human_bytes(d["cache_bytes"]))
        b = self.ctx.bench
        if b and b.decode_fps:
            self.perf.set("Decoding (benchmark)", ", ".join(f"{k} {v} fps/lens" for k, v in b.decode_fps.items()))

    def _poll_gpu(self) -> None:
        if not self.widget.isVisible():
            return
        self.ctx.run_task(gpu_detector.poll_utilisation, self._on_gpu)

    def _on_gpu(self, d: dict) -> None:
        if d.get("gpu_percent") is not None:
            self.perf.set("GPU", f"{d['gpu_percent']:.0f} %, VRAM {human_bytes(d.get('vram_used_bytes'))} used")
        else:
            self.perf.set("GPU", "utilisation not available for this GPU (nvidia-smi only)")

    def _request_source(self, n: int) -> None:
        rep, ci = self.ctx.hardware, self.clip
        if not (rep and ci and rep.ffmpeg.ffmpeg):
            return
        t = n / (ci.fps or 30)
        ff = rep.ffmpeg.ffmpeg
        lrf = find_proxy(Path(ci.path))

        def work():
            out = Path(tempfile.gettempdir()) / "osv360craft_src.png"
            if lrf:
                args = [ff, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(lrf), "-frames:v", "1",
                        "-vf", "scale=640:-2", str(out)]
            else:
                args = [ff, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", ci.path, "-filter_complex",
                        "[0:v:0]scale=320:-2[a];[0:v:1]scale=320:-2[b];[a][b]hstack", "-frames:v", "1", str(out)]
            r = pm.run(args, timeout=60)
            return QImage(str(out)) if r.ok else None

        self.ctx.run_task(work, lambda img: self.bridge.source.emit(img))

    def _on_source(self, img) -> None:
        if img is not None and not img.isNull():
            self.src_img.setPixmap(QPixmap.fromImage(img).scaledToWidth(min(420, self.src_img.width() or 420),
                                                                         Qt.SmoothTransformation))

    def _view_changed(self, yaw: float, pitch: float, roll: float, fov: float) -> None:
        self.view_lbl.setText(f"yaw {yaw:.1f}, pitch {pitch:.1f}, roll {roll:.1f}, FOV {fov:.0f}")

    def shutdown(self) -> None:
        self.stop()
        if self.engine:
            self.engine.cancel()

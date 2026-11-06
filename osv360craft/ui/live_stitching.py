"""Live Stitching: open an .OSV and look around the stitched sphere.

Frames are real OpenOSV stitches at the preview size (see live/preview_engine).
Playback shows frames as fast as this machine renders them; the measured
playback, stitching and decode speeds are displayed, never assumed.
"""

from __future__ import annotations

import tempfile
import time
from collections import deque
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QSizePolicy, QSlider, QSplitter, QVBoxLayout, QWidget)

from osv360craft.core import osv_probe
from osv360craft.hardware import gpu_detector
from osv360craft.hardware.auto_optimizer import PREVIEW_MODES
from osv360craft.live import attitude as attitude_mod
from osv360craft.live import fisheye_calib, realtime_source
from osv360craft.live.panorama_viewer import SoftwarePanoramaViewer, make_viewer
from osv360craft.live.preview_engine import PreviewConfig, PreviewEngine
from osv360craft.ui.widgets import KeyValueGrid
from osv360craft.utils import process_manager as pm
from osv360craft.utils.file_manager import find_proxy, human_bytes
from osv360craft.utils.paths import cache_dir

PREVIEW_LABELS = {"realtime": "Real-time (camera proxy, quick stitch)", "native": "Native", "4k": "4K preview (3840x1920)",
                  "2k": "2K preview (1920x960)", "performance": "Performance (1280x640)"}
REALTIME_EXACT_SIZE = "1920x960"  # size of the exact OpenOSV frame shown when real-time playback pauses
MAX_DECODED_BYTES = 768 * 1024 ** 2  # decoded frames kept in memory; older ones reload from the disk cache


class _Bridge(QObject):
    frame = Signal(int, object, str)  # n, QImage, path in the frame cache
    status = Signal(str)
    stats = Signal(dict)
    source = Signal(object)         # QImage
    quick = Signal(int, object)     # n, dual-fisheye QImage (real-time mode, while scrubbing)


class LiveStitchingPage:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.engine: PreviewEngine | None = None
        self.clip: osv_probe.ClipInfo | None = None
        self.bridge = _Bridge()
        self.frames: dict[int, QImage] = {}
        self.paths: dict[int, str] = {}
        self.current = 0
        self.playing = False
        self._shown_times: deque[float] = deque(maxlen=30)
        self._waiting_since: float | None = None
        self.rt: realtime_source.RealtimeSource | None = None
        self.rt_plan: realtime_source.SourcePlan | None = None
        self.fisheye: fisheye_calib.DualFisheye | None = None
        self.attitude: attitude_mod.Attitude | None = None
        self._exact_shown = -1

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
        self.mode.currentIndexChanged.connect(self._mode_chosen)
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
        self.viewer_box = QVBoxLayout()
        self.viewer_box.setContentsMargins(0, 0, 0, 0)
        self.viewer = None
        self._install_viewer(make_viewer())
        ll.addLayout(self.viewer_box, 1)
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
        self.src_img.setMinimumWidth(10)
        # the picture follows the panel's width instead of forcing the panel wider than the window
        self.src_img.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._src_image = None
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
        b_head = QPushButton("Use as initial heading")
        b_head.setToolTip("Use this viewing direction as the direction players open on.\n"
                          "Lossless: stored as 360 metadata in exports (set in Stitching Controls).")
        b_head.clicked.connect(lambda: self.ctx.applyHeading.emit(self.viewer.view.yaw))
        v4.addWidget(b_head)
        b_frame = QPushButton("Re-stitch frame")
        b_frame.setToolTip("Stitch the current frame again with OpenOSV, using the current Stitching Controls")
        b_frame.clicked.connect(lambda: self.seek(self.current, force=True))
        v4.addWidget(b_frame)
        rl.addWidget(g4)
        rl.addStretch(1)
        side = QScrollArea()
        self.side = side
        side.setWidgetResizable(True)
        side.setFrameShape(QScrollArea.NoFrame)
        side.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        side.setWidget(right)
        side.setMinimumWidth(340)
        split.addWidget(side)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.splitterMoved.connect(lambda *_: self._fit_source())
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
        self.bridge.quick.connect(self._on_quick)
        ctx.exportChanged.connect(self._settings_changed)
        ctx.planChanged.connect(self._apply_plan_default)
        self.widget.setAcceptDrops(True)
        self.widget.dragEnterEvent = self._drag_enter
        self.widget.dropEvent = self._drop
        self._apply_plan_default()

    # ---- viewer -----------------------------------------------------------
    def _install_viewer(self, v) -> None:
        old = self.viewer
        v.setMinimumSize(480, 270)
        v.viewChanged.connect(self._view_changed)
        if hasattr(v, "glFailed"):
            v.glFailed.connect(self._gl_failed)
        if old is not None:
            img = old.current_image()
            v.view.flat, v.view.guide = old.view.flat, old.view.guide
            self.viewer_box.removeWidget(old)
            old.hide()
            old.deleteLater()
            if img is not None:
                v.set_image(img, old.current_fisheye(), getattr(old, "_level", None))
        self.viewer = v
        self.viewer_box.addWidget(v, 1)

    def _gl_failed(self, msg: str) -> None:
        self.status.setText(msg + " - switched to the flat software viewer (sphere view needs OpenGL).")
        self._install_viewer(SoftwarePanoramaViewer())

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
        path = str(Path(path))  # native form, see utils.process_manager.native_args
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
            self._setup_realtime(ci)
            self.slider.setRange(0, max(0, (ci.frame_count or 1) - 1))
            self.slider.setEnabled(True)
            self.frames.clear()
            self.paths.clear()
            self._reconfigure()

        self.ctx.run_task(lambda: osv_probe.probe(tool, Path(path), ffprobe), done)

    def _setup_realtime(self, ci: osv_probe.ClipInfo) -> None:
        rep = self.ctx.hardware
        nvdec = bool(rep and rep.nvdec_status()[0] == "available" and "cuda" in rep.ffmpeg.hwaccels)
        self.rt_plan = realtime_source.choose_source(ci.path, nvdec, rep.ffmpeg.ffprobe if rep else None)
        self.fisheye = fisheye_calib.from_probe(ci.calibration, ci.stream_h)
        realtime_ok = self.rt_plan.kind != "osv-cpu" and bool(rep and rep.ffmpeg.ffmpeg)
        if realtime_ok and not getattr(self, "_user_mode", False):
            self.mode.blockSignals(True)
            self.mode.setCurrentIndex(self.mode.findData("realtime"))
            self.mode.blockSignals(False)
        self.perf.set("Real-time source", self.rt_plan.description + ("" if self.fisheye.calibrated else
                                                                        f"; {self.fisheye.note}"))
        # gyro orientation, so the quick stitch is levelled like OpenOSV's (loaded in the background)
        self.attitude = None
        self.perf.set("Levelling", "reading the camera's gyro data ...")
        tool, clip, avata = str(rep.openosv.path), ci.path, ci.djmd_schema == "Avata 360"

        def loaded(att) -> None:
            if self.clip is None or self.clip.path != clip:
                return
            self.attitude = att
            self.perf.set("Levelling", "gyro, as OpenOSV (mode from Stitching Controls)" if att else
                          "no gyro data in this clip: shown as the camera was held", None if att else "warn")
            if not self.playing and self.viewer.current_fisheye() is not None:
                self.viewer.set_image(self.viewer.current_image(), self.fisheye, self._level(self.current))

        self.ctx.run_task(lambda: attitude_mod.load(tool, clip, avata), loaded)

    def _level(self, n: int):
        """bodyFromView for frame N under the chosen stabilisation (None without gyro data)."""
        if self.attitude is None:
            return None
        return self.attitude.body_from_view(n, self.ctx.export.stitch.stab)

    @property
    def realtime(self) -> bool:
        return self.mode.currentData() == "realtime" and self.rt_plan is not None

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
            self.info.set("OpenOSV notes", f"{len(ci.warnings)} note(s) from OpenOSV's file reader - hover for details",
                          "warn", tooltip="\n".join(ci.warnings))

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

    def _mode_chosen(self) -> None:
        self._user_mode = True
        self._reconfigure()

    def _config(self) -> PreviewConfig | None:
        rep, ci = self.ctx.hardware, self.clip
        if not (rep and ci and rep.openosv.found):
            return None
        mode = self.mode.currentData()
        if mode == "realtime":
            size = REALTIME_EXACT_SIZE
        else:
            size = PREVIEW_MODES.get(mode) or ("{}x{}".format(*ci.native_equirect) if ci.native_equirect else "1920x960")
        plan = self.ctx.plan
        return PreviewConfig(osvtool=str(rep.openosv.path), source=ci.path, frame_count=ci.frame_count or 1,
                             fps=ci.fps or 30.0, size=size, view_colour=self.view_colour.currentData(),
                             device=plan.device if plan else "auto", export=self.ctx.export,
                             supported=rep.openosv.render_options, cache_dir=cache_dir(),
                             cache_bytes=plan.cache_bytes if plan else 1024 ** 3, source_colour=ci.color_mode,
                             quick_first=mode != "realtime")  # real-time mode shows the proxy frame first

    def _reconfigure(self) -> None:
        cfg = self._config()
        if not cfg:
            return
        was_playing = self.playing
        self.stop()
        self.frames.clear()
        self.paths.clear()
        if self.engine is None:
            self.engine = PreviewEngine(cfg, self._engine_frame, self.bridge.status.emit, self.bridge.stats.emit)
        else:
            self.engine.update_config(cfg)
        self.perf.set("Preview size", ("real-time proxy while playing; exact OpenOSV frame " + cfg.size + " when paused"
                                       if self.realtime else cfg.size) + " (does not change the export)")
        self.seek(self.current, force=True)
        if was_playing:
            self.toggle_play()

    def _settings_changed(self) -> None:
        if self.clip:
            self._reconfigure()

    # ---- frames ---------------------------------------------------------
    @staticmethod
    def _load(path: str) -> QImage | None:
        """Decode a frame as 8-bit RGBA (what the screen shows; a third of the 16-bit size)."""
        img = QImage(path)
        return None if img.isNull() else img.convertToFormat(QImage.Format_RGBA8888)

    def _engine_frame(self, n: int, path: Path) -> None:
        img = self._load(str(path))  # decoded on the engine thread, not the GUI thread
        if img is not None:
            self.bridge.frame.emit(n, img, str(path))

    def _on_frame(self, n: int, img: QImage, path: str) -> None:
        self.paths[n] = path
        self.frames[n] = img
        self._trim()
        if not self.playing and n == self.current:
            self._display(n)

    def _trim(self) -> None:
        """Keep decoded frames within MAX_DECODED_BYTES: frames already shown go first, then
        those furthest ahead. A dropped frame is reloaded from the frame cache if needed."""
        def size(im: QImage) -> int:
            return im.sizeInBytes()
        total = sum(size(i) for i in self.frames.values())
        if total <= MAX_DECODED_BYTES:
            return
        order = sorted(self.frames, key=lambda k: (k >= self.current, -k if k < self.current else k))
        for k in order:
            if total <= MAX_DECODED_BYTES:
                break
            if k == self.current:
                continue
            total -= size(self.frames.pop(k))

    def _frame(self, n: int) -> QImage | None:
        img = self.frames.get(n)
        if img is None and n in self.paths and Path(self.paths[n]).exists():
            img = self._load(self.paths[n])
            if img is not None:
                self.frames[n] = img
        return img

    def _display(self, n: int) -> None:
        img = self._frame(n)
        if img is None:
            return
        self.viewer.set_image(img)
        self.current = n
        self._exact_shown = n
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
        if self.playing and self.rt is not None and not force:
            self.rt.start(n)            # jump and keep playing in real time
            return
        if self.playing:
            self.stop()
        self._exact_shown = -1
        if not force and self._frame(n) is not None:
            self._display(n)
        else:
            if self.realtime:
                self._request_quick(n)
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
        if self.realtime:
            rep = self.ctx.hardware
            self.engine.cancel()
            self.rt = realtime_source.RealtimeSource(rep.ffmpeg.ffmpeg, self.rt_plan, self.clip.fps or 30.0)
            self.rt.start(self.current)
            self.timer.start(max(1, int(1000 / self.rt.source_fps)))  # paced by the proxy's own frame rate
            self.status.setText("Real-time preview from " + self.rt_plan.description +
                                ". Quick stitch while playing; pause for OpenOSV's exact stitch.")
        else:
            self.engine.play_from(self.current)
            self.timer.start(max(1, int(1000 / (self.clip.fps or 30))))

    def stop(self, exact: bool = True) -> None:
        if self.playing:
            self.playing = False
            self.b_play.setText("Play")
            self.timer.stop()
            if self.engine:
                self.engine.cancel()
            if self.rt is not None:
                self.rt.stop()
                self.rt = None
                if exact and self.engine:
                    self._exact_shown = -1
                    self.engine.request_frame(self.current)  # replace the quick frame with OpenOSV's stitch
        self.perf.set("Playback", "paused")

    def _tick(self) -> None:
        if self.rt is not None:
            self._tick_realtime()
            return
        nxt = self.current + 1
        if self.clip and nxt >= (self.clip.frame_count or 1):
            self.stop()
            return
        if nxt in self.frames or (nxt in self.paths and self._frame(nxt) is not None):
            self._display(nxt)
            self._waiting_since = None
        else:
            self._waiting_since = self._waiting_since or time.perf_counter()
            self.perf.set("Playback", "waiting for the stitcher (rendering slower than real time)", "warn")

    def _tick_realtime(self) -> None:
        item = self.rt.get()
        if item is None:
            if self.rt.ended:
                last = (self.clip.frame_count or 1) - 1
                if self.rt.error:
                    self.status.setText(f"Real-time preview stopped at frame {self.current}: {self.rt.error}")
                elif self.current < last - max(2, int((self.clip.fps or 30) / max(1.0, self.rt.source_fps))) - 1:
                    self.status.setText(f"The proxy ({Path(self.rt_plan.path).name}) ends at frame {self.current}, "
                                        f"before the clip's last frame ({last}). Choose a 2K or 4K preview to "
                                        "watch the rest with OpenOSV's exact stitch.")
                self.stop()
            return
        n, img = item
        self.viewer.set_image(img, self.fisheye, self._level(n))
        self.current = n
        self.slider.blockSignals(True)
        self.slider.setValue(n)
        self.slider.blockSignals(False)
        fps = self.clip.fps or 30
        self.pos.setText(f"{n} / {(self.clip.frame_count or 1) - 1}  ({n / fps:.2f} s)")
        self._shown_times.append(time.perf_counter())
        if len(self._shown_times) > 2:
            span = self._shown_times[-1] - self._shown_times[0]
            if span > 0:
                self.perf.set("Playback", f"{(len(self._shown_times) - 1) / span:.1f} fps real-time "
                              f"(source {self.clip.fps_label})", "ok")
        if n >= (self.clip.frame_count or 1) - 1:
            self.stop()

    def _request_quick(self, n: int) -> None:
        """Real-time mode while scrubbing: the proxy frame at N straight away."""
        rep = self.ctx.hardware
        if not (rep and rep.ffmpeg.ffmpeg and self.rt_plan):
            return
        ff, plan, fps = rep.ffmpeg.ffmpeg, self.rt_plan, self.clip.fps or 30.0
        self.ctx.run_task(lambda: realtime_source.grab_frame(ff, plan, n, fps), lambda img: self.bridge.quick.emit(n, img))

    def _on_quick(self, n: int, img) -> None:
        # only if the exact OpenOSV frame for this position has not arrived already
        if img is not None and not img.isNull() and n == self.current and self._exact_shown != n and not self.playing:
            self.viewer.set_image(img, self.fisheye, self._level(n))
            self.pos.setText(f"{n} / {(self.clip.frame_count or 1) - 1}  ({n / (self.clip.fps or 30):.2f} s)")

    # ---- side panels ----------------------------------------------------
    def _on_stats(self, d: dict) -> None:
        if "render_fps" in d:
            self.perf.set("Stitching", f"{d['render_fps']:.1f} fps on {str(d.get('device', '?')).upper()}")
        if "quick_frame_seconds" in d:
            self.perf.set("Quick frame", f"{d['quick_frame_seconds']:.2f} s (shown first)")
        if "single_frame_seconds" in d:
            self.perf.set("Exact frame", f"{d['single_frame_seconds']:.2f} s (includes OpenOSV's seam analyses)")
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
            self._src_image = img
            self._fit_source()

    def _fit_source(self) -> None:
        if self._src_image is None:
            return
        width = max(120, self.side.viewport().width() - 64)  # group box margins and padding
        self.src_img.setPixmap(QPixmap.fromImage(self._src_image).scaledToWidth(width, Qt.SmoothTransformation))

    def _view_changed(self, yaw: float, pitch: float, roll: float, fov: float) -> None:
        self.view_lbl.setText(f"yaw {yaw:.1f}, pitch {pitch:.1f}, roll {roll:.1f}, FOV {fov:.0f}")

    def shutdown(self) -> None:
        self.stop(exact=False)
        if self.engine:
            self.engine.cancel()

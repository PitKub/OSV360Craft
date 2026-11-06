"""Export Settings: resolution, frame rate, codec, encoder, quality, colour,
spherical metadata - with a plain statement of what happens to the image."""

from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QLabel, QPushButton,
                               QSpinBox, QVBoxLayout)

from osv360craft.export.export_settings import (CODEC_FAMILIES, COLOUR_OUTPUTS, HDR_PEAKS, HDR_TONES, SIZE_PRESETS,
                                                ColourSettings, pipeline_for, validate_for_video)
from osv360craft.ui.widgets import page

SIZE_LABELS = {"native": "Native (camera resolution - recommended)", "7680x3840": "7680 x 3840 (8K)",
               "5760x2880": "5760 x 2880", "3840x1920": "3840 x 1920 (4K)", "2560x1280": "2560 x 1280",
               "1920x960": "1920 x 960"}
QUALITY = [("auto", "Automatic - CRF 16 (visually lossless)"), ("high", "High - CRF 14"),
           ("maximum", "Maximum - CRF 10 (very large files)"), ("custom", "Custom CRF")]


class ExportSettingsPage:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._loading = False
        self.widget, lay = page("Export Settings", "Defaults are a pure stitch: source colour, 10-bit, native "
                                                   "resolution, source frame rate, original audio.")
        pure = QPushButton("Restore pure-stitch defaults")
        pure.setObjectName("Primary")
        pure.clicked.connect(self.pure_stitch)
        lay.addWidget(pure)

        g = QGroupBox("Picture")
        f = QFormLayout(g)
        self.mode = QComboBox()
        self.mode.addItem("360 equirectangular (2:1)", "equirect")
        self.mode.addItem("Flat reframed view", "reframe")
        self.size = QComboBox()
        for s in SIZE_PRESETS:
            self.size.addItem(SIZE_LABELS[s], s)
        self.fps = QLabel("Source frame rate, always (no frame interpolation)")
        f.addRow("Output:", self.mode)
        f.addRow("Resolution:", self.size)
        f.addRow("Frame rate:", self.fps)
        self.yaw, self.pitch, self.roll, self.fov = (QDoubleSpinBox() for _ in range(4))
        for w, lo, hi in ((self.yaw, -180, 180), (self.pitch, -90, 90), (self.roll, -180, 180), (self.fov, 30, 170)):
            w.setRange(lo, hi)
            w.setSuffix(" deg")
        f.addRow("Reframe pan / tilt:", self._pair(self.yaw, self.pitch))
        f.addRow("Reframe roll / FOV:", self._pair(self.roll, self.fov))
        lay.addWidget(g)

        g2 = QGroupBox("Colour")
        f2 = QFormLayout(g2)
        self.colour = QComboBox()
        for k, v in COLOUR_OUTPUTS.items():
            self.colour.addItem(v, k)
        self.tone = QComboBox()
        self.tone.addItem("OpenOSV default (ACES bright)", None)
        for t in HDR_TONES:
            self.tone.addItem(t, t)
        self.peak = QComboBox()
        self.peak.addItem("OpenOSV default (1000 nits)", None)
        for p in HDR_PEAKS:
            self.peak.addItem(f"{p} nits", p)
        f2.addRow("Colour output:", self.colour)
        f2.addRow("HDR tone scale:", self.tone)
        f2.addRow("PQ peak brightness:", self.peak)
        self.colour_note = QLabel()
        self.colour_note.setWordWrap(True)
        self.colour_note.setObjectName("Muted")
        f2.addRow("", self.colour_note)
        lay.addWidget(g2)

        g3 = QGroupBox("Encoding")
        f3 = QFormLayout(g3)
        self.family = QComboBox()
        for k, v in CODEC_FAMILIES.items():
            self.family.addItem(v, k)
        self.encoder = QComboBox()
        self.quality = QComboBox()
        for k, v in QUALITY:
            self.quality.addItem(v, k)
        self.crf = QSpinBox()
        self.crf.setRange(0, 51)
        self.bitrate = QDoubleSpinBox()
        self.bitrate.setRange(0, 2000)
        self.bitrate.setSuffix(" Mbit/s")
        self.bitrate.setSpecialValueText("off (constant quality)")
        self.audio = QCheckBox("Keep the original audio (copied, not re-encoded)")
        self.spherical = QCheckBox("Write 360 metadata (Spherical Video V1 + V2, YouTube / VR players)")
        f3.addRow("Format:", self.family)
        f3.addRow("Encoder:", self.encoder)
        f3.addRow("Quality:", self.quality)
        f3.addRow("Custom CRF:", self.crf)
        f3.addRow("Bitrate (H.264/ProRes only):", self.bitrate)
        f3.addRow("", self.audio)
        f3.addRow("", self.spherical)
        lay.addWidget(g3)

        g4 = QGroupBox("What happens to the image")
        v4 = QVBoxLayout(g4)
        self.chain = QLabel()
        self.chain.setWordWrap(True)
        self.errors = QLabel()
        self.errors.setObjectName("Bad")
        self.errors.setWordWrap(True)
        v4.addWidget(self.chain)
        v4.addWidget(self.errors)
        lay.addWidget(g4)
        lay.addStretch(1)

        for w in (self.mode, self.size, self.colour, self.tone, self.peak, self.family, self.encoder, self.quality):
            w.currentIndexChanged.connect(lambda *_: self._changed())
        for w in (self.crf, self.bitrate, self.yaw, self.pitch, self.roll, self.fov):
            w.valueChanged.connect(lambda *_: self._changed())
        for w in (self.audio, self.spherical):
            w.toggled.connect(lambda *_: self._changed())
        ctx.hardwareChanged.connect(self._fill_encoders)
        ctx.clipOpened.connect(lambda _c: self._describe())
        self._fill_encoders()
        self.load()

    @staticmethod
    def _pair(a, b):
        from PySide6.QtWidgets import QHBoxLayout, QWidget
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(a)
        h.addWidget(b)
        return w

    def _fill_encoders(self) -> None:
        cur = self.ctx.export.encoder
        self._loading = True
        self.encoder.clear()
        self.encoder.addItem("Automatic (fastest verified)", "auto")
        rep = self.ctx.hardware
        if rep:
            for name, t in rep.ffmpeg.tests.items():
                if t.listed:
                    state = "verified" if t.verified else ("failed test" if t.verified is False else "untested")
                    self.encoder.addItem(f"{name} - {t.label} ({state})", name)
        i = self.encoder.findData(cur)
        self.encoder.setCurrentIndex(i if i >= 0 else 0)
        self._loading = False

    def load(self) -> None:
        self._loading = True
        e = self.ctx.export
        for combo, val in ((self.mode, e.mode), (self.size, e.size), (self.colour, e.colour.output),
                           (self.tone, e.colour.tone), (self.peak, e.colour.hdr_peak), (self.family, e.codec_family),
                           (self.encoder, e.encoder), (self.quality, e.quality)):
            i = combo.findData(val)
            combo.setCurrentIndex(i if i >= 0 else 0)
        self.crf.setValue(e.custom_crf)
        self.bitrate.setValue(e.custom_bitrate_mbps or 0)
        self.audio.setChecked(e.keep_audio)
        self.spherical.setChecked(e.spherical_metadata)
        self.yaw.setValue(e.yaw)
        self.pitch.setValue(e.pitch)
        self.roll.setValue(e.roll)
        self.fov.setValue(e.fov)
        self._loading = False
        self._describe()

    def _changed(self) -> None:
        if self._loading:
            return
        e = self.ctx.export
        e.mode, e.size = self.mode.currentData(), self.size.currentData()
        e.colour = ColourSettings(self.colour.currentData(), self.tone.currentData(), self.peak.currentData())
        e.codec_family, e.encoder, e.quality = self.family.currentData(), self.encoder.currentData(), self.quality.currentData()
        e.custom_crf = self.crf.value()
        e.custom_bitrate_mbps = self.bitrate.value() or None
        e.keep_audio, e.spherical_metadata = self.audio.isChecked(), self.spherical.isChecked()
        e.yaw, e.pitch, e.roll, e.fov = self.yaw.value(), self.pitch.value(), self.roll.value(), self.fov.value()
        self.ctx.save_export()
        self._describe()

    def _describe(self) -> None:
        e = self.ctx.export
        clip = self.ctx.current_clip
        src = clip.color_mode if clip else None
        pipe = pipeline_for(e, src if src else "DLogM")
        self.crf.setEnabled(e.quality == "custom")
        self.bitrate.setEnabled(e.codec_family in ("h264", "prores") and e.codec_family != "prores")
        self.colour_note.setText("Example for a D-Log M clip: " + e.colour.describe("DLogM") if not clip else
                                 "This clip: " + e.colour.describe(src))
        steps = ["1. OpenOSV decodes both 10-bit HEVC lens streams."]
        steps.append("2. The two fisheye images are resampled into one sphere in high-precision floating point "
                     "(16-bit/32-bit). This resampling is the stitch itself and the only unavoidable change to pixels.")
        if pipe == "passthrough":
            steps.append("3. D-Log M is kept: OpenOSV writes lossless 16-bit TIFF chunks (deleted afterwards).")
            steps.append(f"4. FFmpeg encodes them to 10-bit {e.codec_family.upper()} and joins the chunks without "
                         "re-encoding; the original audio is copied in.")
        elif pipe == "direct":
            steps.append("3. OpenOSV converts colour as selected and encodes HEVC Main10 (10-bit) directly; "
                         "the original audio is copied in.")
        elif pipe == "transcode":
            steps.append("3. An HEVC 10-bit master is rendered at CRF 10, then converted to the chosen format "
                         "(one extra generation).")
        else:
            steps.append("3. Frames are written as lossless 16-bit TIFF files. No encoding, no audio.")
        if pipe != "tiff":
            steps.append(f"Encoding: CRF {e.crf if e.quality != 'auto' else 'from profile (16 by default)'}. "
                         "Lower CRF = closer to lossless, larger files.")
        if e.size != "native" and clip and clip.native_equirect:
            nw = clip.native_equirect[0]
            if int(e.size.split("x")[0]) > nw:
                steps.append(f"Warning: {e.size} is larger than this clip's native {nw} px width: it is "
                             "upscaled, which adds pixels but no original detail.")
        self.chain.setText("\n".join(steps))
        self.errors.setText("\n".join(validate_for_video(e)))

    def pure_stitch(self) -> None:
        e = self.ctx.export
        e.mode, e.size, e.codec_family, e.encoder, e.quality = "equirect", "native", "hevc", "auto", "auto"
        e.colour = ColourSettings("source")
        e.keep_audio = e.spherical_metadata = True
        e.custom_bitrate_mbps = None
        e.stitch.exposure = None
        self.ctx.save_export()
        self.load()

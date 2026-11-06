"""Stitching Controls: orientation (horizon levelling, direction lock,
RockSteady), lens calibration and OpenOSV's seam controls.

Every control starts at "OpenOSV default", which means the option is not
passed and OpenOSV's own Source Settings default applies.
"""

from __future__ import annotations

from PySide6.QtWidgets import (QButtonGroup, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QRadioButton, QVBoxLayout)

from osv360craft.export.export_settings import StitchSettings
from osv360craft.ui.widgets import OptionalCombo, OptionalFloat, page

STAB_CHOICES = [
    (None, "OpenOSV default", "Leaves stabilisation to OpenOSV's Source Settings default."),
    ("off", "Off - as recorded", "No gyro correction: the camera's own motion and tilt are kept."),
    ("horizon", "Horizon levelling", "Keeps the horizon level using the gyro; heading follows the camera "
                                     "(DJI Studio: Horizon Leveling)."),
    ("full", "Direction lock", "Keeps the first frame's heading and level horizon: the view does not turn "
                               "with the camera (DJI Studio: direction lock)."),
    ("smooth", "RockSteady (smooth)", "Smooths the camera's rotation, removing shake but following turns "
                                      "(DJI Studio: RockSteady)."),
    ("smooth-horizon", "RockSteady + horizon levelling", "Smooths rotation and keeps the horizon level."),
]

ON_OFF = [("On", True), ("Off", False)]


class StitchingControlsPage:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self._loading = False
        self.widget, lay = page(
            "Stitching Controls",
            "Orientation is corrected inside the stitch from the camera's gyroscope, so it costs no extra quality. "
            "Controls left at 'OpenOSV default' are not passed to the engine.")

        # ---- orientation -------------------------------------------------
        g = QGroupBox("Horizon, levelling and direction")
        gl = QVBoxLayout(g)
        self.stab_group = QButtonGroup(self.widget)
        for i, (val, label, tip) in enumerate(STAB_CHOICES):
            rb = QRadioButton(label)
            rb.setToolTip(tip)
            rb.setProperty("value", val)
            self.stab_group.addButton(rb, i)
            row = QHBoxLayout()
            row.addWidget(rb)
            d = QLabel(tip)
            d.setObjectName("Muted")
            d.setWordWrap(True)
            row.addWidget(d, 1)
            gl.addLayout(row)
        self.stab_group.buttonToggled.connect(lambda *_: self._changed())

        hd = QHBoxLayout()
        hd.addWidget(QLabel("Initial viewing direction (heading):"))
        self.heading = QDoubleSpinBox()
        self.heading.setRange(-180, 180)
        self.heading.setSuffix(" deg")
        self.heading.setDecimals(1)
        self.heading.valueChanged.connect(self._heading_changed)
        hd.addWidget(self.heading)
        b0 = QPushButton("Reset")
        b0.clicked.connect(lambda: self.heading.setValue(0))
        hd.addWidget(b0)
        hd.addStretch(1)
        gl.addLayout(hd)
        note = QLabel("The heading is written as Spherical Video V2 metadata (the initial view players open on). "
                      "It is lossless: no pixel is re-rendered or re-encoded. In Live Stitching, 'Use this "
                      "heading' copies the viewer's direction here.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        gl.addWidget(note)
        lay.addWidget(g)

        # ---- lens / colour neutral --------------------------------------
        g2 = QGroupBox("Lens calibration and exposure")
        f2 = QFormLayout(g2)
        self.calib = OptionalCombo([("Automatic (as recorded)", "auto"), ("Bare lenses", "native"),
                                    ("Lens protectors / ND filters", "lens-guards"), ("Underwater", "underwater")])
        self.lens_align = OptionalCombo([("Auto (measure per clip)", "auto"), ("Off", "off")])
        self.exposure = OptionalFloat(-6, 6, 0.1, 0.0, " stops")
        self.fit = OptionalCombo([("Osmo 360", "osmo360"), ("Avata 360", "avata360")])
        f2.addRow("Calibration:", self.calib)
        f2.addRow("Lens alignment:", self.lens_align)
        f2.addRow("Exposure offset:", self.exposure)
        f2.addRow("Camera colour model:", self.fit)
        en = QLabel("Exposure changes the picture. Leave it at the default for a pure stitch.")
        en.setObjectName("Muted")
        f2.addRow("", en)
        lay.addWidget(g2)

        # ---- seam --------------------------------------------------------
        g3 = QGroupBox("Seam")
        f3 = QFormLayout(g3)
        self.seam_blend = OptionalFloat(0.2, 8, 0.1, 1.5, " deg")
        self.parallax_blend = OptionalFloat(0, 4, 0.05, 0.35, " deg")
        self.seam_smoothing = OptionalFloat(0, 8, 0.1, 0.0, " deg")
        self.near_offset = OptionalFloat(-3, 3, 0.05, 0.0, " deg")
        self.far_offset = OptionalFloat(-3, 3, 0.05, 0.0, " deg")
        self.parallax_grid = OptionalCombo([("Auto", "auto"), ("Follows the scene", "follows"), ("Steady", "steady")])
        self.hide_mount = OptionalCombo([("Auto", "auto"), ("On", "on"), ("Off", "off")])
        f3.addRow("Seam blend:", self.seam_blend)
        f3.addRow("Parallax blend:", self.parallax_blend)
        f3.addRow("Seam smoothing:", self.seam_smoothing)
        f3.addRow("Near offset:", self.near_offset)
        f3.addRow("Far offset:", self.far_offset)
        f3.addRow("Parallax grid:", self.parallax_grid)
        f3.addRow("Hide mount:", self.hide_mount)
        lay.addWidget(g3)

        # ---- photometric -------------------------------------------------
        g4 = QGroupBox("Seam brightness and lens artefacts")
        f4 = QFormLayout(g4)
        self.gain = OptionalCombo(ON_OFF)
        self.photo = OptionalCombo([("Off", "off"), ("Rim", "rim"), ("Full", "full")])
        self.shading = OptionalCombo([("Auto", "auto"), ("Off", "off")])
        self.flare = OptionalCombo(ON_OFF)
        self.seam_search = OptionalCombo(ON_OFF)
        self.parallax = OptionalCombo(ON_OFF)
        self.scene_light = OptionalCombo([("Auto", "auto"), ("Day", "day"), ("Night", "night")])
        f4.addRow("Exposure match between lenses:", self.gain)
        f4.addRow("Sky seam field:", self.photo)
        f4.addRow("Lens shading correction:", self.shading)
        f4.addRow("Sun ghost removal:", self.flare)
        f4.addRow("Seam search:", self.seam_search)
        f4.addRow("Parallax correction:", self.parallax)
        f4.addRow("Scene light:", self.scene_light)
        lay.addWidget(g4)

        reset = QPushButton("Reset everything to OpenOSV defaults")
        reset.clicked.connect(self.reset)
        lay.addWidget(reset)
        lay.addStretch(1)

        for w in (self.calib, self.lens_align, self.fit, self.parallax_grid, self.hide_mount, self.gain, self.photo,
                  self.shading, self.flare, self.seam_search, self.parallax, self.scene_light):
            w.currentIndexChanged.connect(lambda *_: self._changed())
        for w in (self.exposure, self.seam_blend, self.parallax_blend, self.seam_smoothing, self.near_offset,
                  self.far_offset):
            w.changed.connect(self._changed)
        ctx.applyHeading.connect(lambda yaw: self.heading.setValue(round(yaw, 1)))
        self.load()

    def load(self) -> None:
        self._loading = True
        s = self.ctx.export.stitch
        for b in self.stab_group.buttons():
            if b.property("value") == s.stab:
                b.setChecked(True)
        self.calib.set_value(s.calib)
        self.lens_align.set_value(s.lens_align)
        self.exposure.set_value(s.exposure)
        self.fit.set_value(s.fit)
        for name in ("seam_blend", "parallax_blend", "seam_smoothing", "near_offset", "far_offset"):
            getattr(self, name).set_value(getattr(s, name))
        for name in ("parallax_grid", "hide_mount", "gain", "photo", "shading", "flare", "seam_search", "parallax",
                     "scene_light"):
            getattr(self, name).set_value(getattr(s, name))
        self.heading.setValue(float(self.ctx.settings.get("orientation.initial_yaw", 0.0) or 0.0))
        self._loading = False

    def _changed(self) -> None:
        if self._loading:
            return
        b = self.stab_group.checkedButton()
        s = StitchSettings(
            stab=b.property("value") if b else None, calib=self.calib.value(), lens_align=self.lens_align.value(),
            exposure=self.exposure.value(), fit=self.fit.value(), seam_blend=self.seam_blend.value(),
            parallax_blend=self.parallax_blend.value(), seam_smoothing=self.seam_smoothing.value(),
            near_offset=self.near_offset.value(), far_offset=self.far_offset.value(),
            parallax_grid=self.parallax_grid.value(), hide_mount=self.hide_mount.value(), gain=self.gain.value(),
            photo=self.photo.value(), shading=self.shading.value(), flare=self.flare.value(),
            seam_search=self.seam_search.value(), parallax=self.parallax.value(), scene_light=self.scene_light.value())
        self.ctx.export.stitch = s
        self.ctx.save_export()

    def _heading_changed(self, v: float) -> None:
        if not self._loading:
            self.ctx.settings.set("orientation.initial_yaw", float(v))

    def reset(self) -> None:
        self.ctx.export.stitch = StitchSettings()
        self.ctx.settings.set("orientation.initial_yaw", 0.0)
        self.ctx.save_export()
        self.load()

"""Export, colour and stitching settings, and their translation into an
``osvtool render`` command line.

Every stitching control is optional: ``None`` means "leave it to OpenOSV",
whose plug-in engine then applies its own Source Settings default (the
defaults Premiere users get). Only values the user actually changed are
passed. An option the installed osvtool does not accept raises
:class:`UnsupportedOption` instead of being dropped silently.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

QUALITY_CRF = {"auto": 16, "high": 14, "maximum": 10}
SIZE_PRESETS = ["native", "7680x3840", "5760x2880", "3840x1920", "2560x1280", "1920x960"]
COLOUR_OUTPUTS = {
    "source": "Keep source colour - pure stitch (D-Log M stays D-Log M, HLG stays HLG, Rec.709 stays Rec.709)",
    "auto": "Automatic (D-Log M -> Rec.2100 PQ HDR10, SDR stays Rec.709)",
    "709-dji": "Rec.709 SDR, DJI-style look",
    "709-standard": "Rec.709 SDR, neutral (OpenOSV standard)",
    "pq": "Rec.2100 PQ HDR (HDR10)",
    "hlg": "Rec.2100 HLG HDR",
    "dlogm": "Original D-Log M (passthrough, 10-bit)",
}
HDR_TONES = ["aces-bright", "aces-detailed", "bt2408-natural", "bt2408-punchy", "bt2408-neutral"]
HDR_PEAKS = ["1000", "600", "400", "203"]
STAB_MODES = ["off", "horizon", "full", "smooth", "smooth-horizon"]
CODEC_FAMILIES = {"hevc": "H.265 / HEVC 10-bit (direct from OpenOSV)",
                  "h264": "H.264 (transcoded from an HEVC master)",
                  "prores": "Apple ProRes 422 HQ 10-bit (transcoded from an HEVC master)",
                  "tiff": "16-bit TIFF image sequence (lossless, very large; no audio)"}


class UnsupportedOption(ValueError):
    pass


@dataclass
class StitchSettings:
    """None = OpenOSV default. Ranges are osvtool's own (validated there too)."""
    stab: str | None = None
    calib: str | None = None              # auto|native|lens-guards|underwater
    exposure: float | None = None         # -6..6 stops
    fit: str | None = None                # osmo360|avata360|dji|pocket3
    seam_blend: float | None = None       # 0.2..8
    parallax_blend: float | None = None   # 0..4
    seam_smoothing: float | None = None   # 0..8
    near_offset: float | None = None      # -3..3
    far_offset: float | None = None       # -3..3
    flare: bool | None = None
    lens_align: str | None = None         # off|auto
    parallax_grid: str | None = None      # follows|steady|auto
    shading: str | None = None            # off|auto
    shading_strength: float | None = None
    photo: str | None = None              # off|rim|full
    photo_strength: float | None = None
    gain: bool | None = None
    seam_search: bool | None = None
    parallax: bool | None = None
    hide_mount: str | None = None         # on|off|auto
    scene_light: str | None = None        # auto|day|night

    RANGES = {"exposure": (-6, 6), "seam_blend": (0.2, 8), "parallax_blend": (0, 4), "seam_smoothing": (0, 8),
              "near_offset": (-3, 3), "far_offset": (-3, 3), "shading_strength": (0, 1), "photo_strength": (0, 1)}

    def validate(self) -> list[str]:
        errs = []
        for name, (lo, hi) in self.RANGES.items():
            v = getattr(self, name)
            if v is not None and not (lo <= v <= hi):
                errs.append(f"{name.replace('_', ' ')} must be within {lo}..{hi}")
        return errs

    def to_args(self) -> list[str]:
        a: list[str] = []
        for f in fields(self):
            v = getattr(self, f.name)
            if v is None:
                continue
            opt = "--" + f.name.replace("_", "-")
            if isinstance(v, bool):
                a.append(opt if v else "--no-" + f.name.replace("_", "-"))
            elif isinstance(v, float):
                a += [opt, f"{v:g}"]
            else:
                a += [opt, str(v)]
        return a

    def changed(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


@dataclass
class ColourSettings:
    output: str = "source"
    tone: str | None = None       # HDR tone scale (PQ/HLG from D-Log M)
    hdr_peak: str | None = None   # PQ only

    def resolved(self, source_colour: str | None) -> str:
        """The concrete output for a clip. 'source' keeps the recorded encoding."""
        if self.output != "source":
            return self.output
        return {"DLogM": "dlogm", "HLG": "hlg", "Normal": "709-standard"}.get(source_colour or "", "dlogm")

    def to_args(self, source_colour: str | None = None) -> list[str]:
        a: list[str] = []
        out = self.resolved(source_colour)
        if out != self.output:
            return ColourSettings(out, self.tone, self.hdr_peak).to_args()
        if self.output in ("709-dji", "709-standard"):
            a += ["--color", "709", "--look", self.output.split("-")[1]]
        elif self.output in ("pq", "hlg", "dlogm"):
            a += ["--color", self.output]
        if self.tone and self.output in ("auto", "pq", "hlg"):
            a += ["--tone", self.tone]
        if self.hdr_peak and self.output in ("auto", "pq"):
            a += ["--hdr-peak", self.hdr_peak]
        return a

    def describe(self, source_colour: str | None) -> str:
        src = {"DLogM": "D-Log M", "HLG": "HLG", "Normal": "Rec.709"}.get(source_colour or "", "unknown")
        if self.output == "source":
            return f"{src} -> {src} (unchanged, pure stitch)"
        if self.output == "auto":
            out = "Rec.2100 PQ (HDR10)" if source_colour == "DLogM" else (
                "Rec.709" if source_colour == "Normal" else "OpenOSV automatic choice")
        else:
            out = COLOUR_OUTPUTS[self.output]
        return f"{src} -> {out}"


@dataclass
class ExportSettings:
    mode: str = "equirect"               # equirect | reframe
    size: str = "native"                 # native or WxH
    codec_family: str = "hevc"
    encoder: str = "auto"                # auto or an ffmpeg encoder name
    quality: str = "auto"                # auto|high|maximum|custom
    custom_crf: int = 18
    custom_bitrate_mbps: float | None = None  # transcoded formats only
    keep_audio: bool = True
    spherical_metadata: bool = True
    frame_range: str | None = None       # "a-b" timeline frames, None = all
    # reframe-only view
    yaw: float = 0.0
    pitch: float = 0.0
    roll: float = 0.0
    fov: float = 100.0
    colour: ColourSettings = field(default_factory=ColourSettings)
    stitch: StitchSettings = field(default_factory=StitchSettings)

    @property
    def crf(self) -> int:
        return self.custom_crf if self.quality == "custom" else QUALITY_CRF.get(self.quality, 18)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict | None) -> "ExportSettings":
        d = dict(d or {})
        colour = ColourSettings(**{k: v for k, v in (d.pop("colour", None) or {}).items()
                                   if k in {f.name for f in fields(ColourSettings)}})
        stitch = StitchSettings(**{k: v for k, v in (d.pop("stitch", None) or {}).items()
                                   if k in {f.name for f in fields(StitchSettings)}})
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known}, colour=colour, stitch=stitch)


def parse_size(size: str) -> tuple[int, int] | None:
    if size == "native":
        return None
    w, h = size.lower().split("x")
    return int(w), int(h)


def build_render_command(osvtool: str | Path, source: str | Path, out: str | Path, ex: ExportSettings, *,
                         device: str | None = None, codec: str | None = None, crf: int | None = None,
                         ffmpeg: str | None = None, supported: set[str] | None = None,
                         frame: int | None = None, frame_range: str | None = None,
                         size_override: str | None = None, purpose: str | None = None,
                         force_colour: str | None = None, no_audio: bool | None = None,
                         spherical: bool | None = None, source_colour: str | None = None) -> list[str]:
    """The exact argument list for one osvtool render.

    ``supported`` is the option set read from ``osvtool render --help``; when
    given, every option used must be in it.
    """
    args: list[str] = [str(osvtool), "render", str(source)]
    if frame is not None:
        args += ["--frame", str(frame)]
    elif frame_range or ex.frame_range:
        args += ["--range", frame_range or ex.frame_range]
    else:
        args.append("--all")
    args += ["--mode", ex.mode]
    size = size_override or ex.size
    if size != "native":
        args += ["--size", size]
    elif ex.mode == "reframe":
        args += ["--size", "1920x1080"]
    if ex.mode == "reframe":
        args += ["--yaw", f"{ex.yaw:g}", "--pitch", f"{ex.pitch:g}", "--roll", f"{ex.roll:g}", "--fov", f"{ex.fov:g}"]
    if force_colour:
        args += ["--color", force_colour]
    else:
        args += ex.colour.to_args(source_colour)
    args += ex.stitch.to_args()
    if purpose:
        args += ["--purpose", purpose]
    if device and device != "auto":
        args += ["--device", device]
    out_s = str(out)
    is_video = out_s.lower().endswith((".mp4", ".mov"))
    if is_video:
        if codec:
            args += ["--codec", codec]
        args += ["--crf", str(crf if crf is not None else ex.crf)]
        if ffmpeg:
            args += ["--ffmpeg", str(ffmpeg)]
        if (no_audio if no_audio is not None else not ex.keep_audio):
            args.append("--no-audio")
        if ex.mode == "equirect" and not (spherical if spherical is not None else ex.spherical_metadata):
            args.append("--no-spherical-metadata")
    args += ["--out", out_s]

    if supported:
        used = {a for a in args if a.startswith("--")}
        missing = sorted(u for u in used if u not in supported)
        if missing:
            raise UnsupportedOption("The installed osvtool does not accept: " + ", ".join(missing) +
                                    ". Update OpenOSV or reset these settings to their defaults.")
    return args


def pipeline_for(ex: ExportSettings, source_colour: str | None) -> str:
    """How a job is produced:

    * ``direct``      osvtool renders and encodes HEVC 10-bit itself (PQ / HLG / Rec.709 outputs);
    * ``passthrough`` osvtool cannot pipe D-Log M to video, so it writes lossless 16-bit TIFF
                      chunks that FFmpeg encodes to HEVC 10-bit and joins without re-encoding;
    * ``transcode``   H.264 / ProRes: an HEVC master is rendered first, then converted;
    * ``tiff``        a 16-bit TIFF image sequence, no encoding at all.
    """
    if ex.codec_family == "tiff":
        return "tiff"
    out = ex.colour.resolved(source_colour)
    if out == "dlogm":
        return "passthrough"  # H.264 / ProRes are encoded straight from the TIFF chunks too
    if ex.codec_family in ("h264", "prores"):
        return "transcode"
    return "direct"


def validate_for_video(ex: ExportSettings) -> list[str]:
    errs = ex.stitch.validate()
    if ex.custom_bitrate_mbps and ex.codec_family == "hevc":
        errs.append("A custom bitrate is not available for direct HEVC output (osvtool uses constant quality). "
                    "Use a quality preset or custom CRF.")
    sz = parse_size(ex.size) if ex.size != "native" else None
    if sz and ex.mode == "equirect" and sz[0] != 2 * sz[1]:
        errs.append(f"Equirectangular output must be 2:1; {ex.size} is not.")
    return errs

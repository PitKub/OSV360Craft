"""Clip analysis through ``osvtool probe --json -`` (schema openosv.probe/1),
cross-checked with ffprobe for codec, bit depth and audio.

Only the keys osvtool documents as stable are relied on; anything else is
read opportunistically and shown as "unknown" when absent.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from osv360craft.utils import process_manager as pm
from osv360craft.utils.logging import get_logger

log = get_logger(__name__)

COLOR_MODE_LABELS = {"DLogM": "D-Log M", "HLG": "HLG", "Normal": "Normal (Rec.709)"}
LENS_MODE_LABELS = {1920: "4K mode", 3000: "6K mode", 3840: "8K mode"}


@dataclass
class ClipInfo:
    path: str
    ok: bool = False
    error: str = ""
    frame_count: int | None = None
    fps_num: int | None = None
    fps_den: int | None = None
    fps: float | None = None
    duration_s: float | None = None
    stream_w: int | None = None
    stream_h: int | None = None
    mode: str | None = None
    color_mode: str | None = None
    has_audio: bool = False
    audio: dict | None = None
    is_lrf: bool = False
    variable_frame_rate: bool = False
    camera: str | None = None
    firmware: str | None = None
    has_calibration: bool | None = None
    codec: str | None = None
    bit_depth: int | None = None
    pix_fmt: str | None = None
    video_streams: int | None = None
    warnings: list[str] = field(default_factory=list)
    calibration: dict | None = None   # probe "calibration" (slave / master lens records), for the live preview
    djmd_schema: str | None = None    # metadata numbering OpenOSV read the clip in ("Osmo 360", "Avata 360")

    # ---- derived values -------------------------------------------------
    @property
    def native_equirect(self) -> tuple[int, int] | None:
        """OpenOSV's 'Native' equirect size: 2 x lens height by lens height."""
        if self.stream_h:
            return 2 * self.stream_h, self.stream_h
        return None

    @property
    def recording_mode_label(self) -> str:
        if self.stream_h in LENS_MODE_LABELS:
            return LENS_MODE_LABELS[self.stream_h]
        return f"{self.stream_w}x{self.stream_h} per lens" if self.stream_h else "unknown"

    @property
    def color_label(self) -> str:
        if not self.color_mode:
            return "unknown"
        return COLOR_MODE_LABELS.get(self.color_mode, self.color_mode)

    @property
    def fps_label(self) -> str:
        if self.fps_num and self.fps_den:
            f = self.fps_num / self.fps_den
            return f"{f:.3f}".rstrip("0").rstrip(".") + (f" ({self.fps_num}/{self.fps_den})" if self.fps_den != 1 else "")
        return "unknown"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["native_equirect"] = self.native_equirect
        return d


def _find_key(obj: Any, names: set[str], depth: int = 0) -> Any:
    if depth > 6:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in names and v not in (None, "", []):
                return v
        for v in obj.values():
            r = _find_key(v, names, depth + 1)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj[:50]:
            r = _find_key(v, names, depth + 1)
            if r is not None:
                return r
    return None


def parse_probe_json(doc: dict, path: str) -> ClipInfo:
    ci = ClipInfo(path=path)
    if doc.get("schema") != "openosv.probe/1":
        ci.warnings.append(f"unexpected probe schema {doc.get('schema')!r}")
    ci.frame_count = doc.get("frameCount") or None
    fps = doc.get("fps")
    if isinstance(fps, dict):
        ci.fps_num, ci.fps_den, ci.fps = fps.get("num"), fps.get("den"), fps.get("value")
    ci.duration_s = doc.get("durationSeconds")
    ci.stream_w, ci.stream_h = doc.get("streamW"), doc.get("streamH")
    ci.mode = doc.get("mode")
    ci.color_mode = doc.get("colorModeName")
    ci.has_audio = bool(doc.get("hasAudio"))
    ci.djmd_schema = doc.get("djmdSchema") if isinstance(doc.get("djmdSchema"), str) else None
    ci.audio = doc.get("audio")
    ci.is_lrf = bool(doc.get("isLrf"))
    tl = doc.get("timeline")
    if isinstance(tl, dict):
        ci.variable_frame_rate = bool(tl.get("variableFrameRate") or tl.get("vfr") or tl.get("heldFrames"))
    ci.camera = _find_key(doc.get("clip"), {"productName", "product_name", "modelName", "model_name"})
    ci.firmware = _find_key(doc.get("clip"), {"firmware", "firmwareVersion", "fwVersion"})
    cal = doc.get("calibration")
    ci.has_calibration = None if "calibration" not in doc else bool(cal)
    if isinstance(cal, dict) and isinstance(cal.get("slave"), dict) and isinstance(cal.get("master"), dict):
        keep = ("fx", "fy", "cx", "cy", "k", "width", "height", "camExtriQ")
        ci.calibration = {lens: {k: cal[lens].get(k) for k in keep} for lens in ("slave", "master")}
    ci.warnings += [str(w) for w in doc.get("warnings") or []]
    ci.ok = bool(ci.frame_count and ci.stream_w and ci.stream_h)
    if not ci.ok:
        ci.error = "osvtool could not identify a dual-fisheye recording in this file"
    return ci


def apply_ffprobe(ci: ClipInfo, ffprobe: str | None) -> None:
    """Fill codec / bit depth / stream count from ffprobe (an independent reader)."""
    if not ffprobe:
        return
    r = pm.run([ffprobe, "-v", "error", "-print_format", "json", "-show_streams", ci.path], timeout=60)
    if not r.ok:
        return
    try:
        streams = json.loads(r.stdout).get("streams", [])
    except ValueError:
        return
    # Cover pictures and thumbnails are "video" streams too; only real video tracks are lenses.
    video = [s for s in streams if s.get("codec_type") == "video"
             and not (s.get("disposition") or {}).get("attached_pic")
             and s.get("codec_name") not in ("mjpeg", "png", "bmp")]
    ci.video_streams = len(video)
    if video:
        v = video[0]
        ci.codec = v.get("codec_name")
        ci.pix_fmt = v.get("pix_fmt")
        bpr = v.get("bits_per_raw_sample")
        if bpr and str(bpr).isdigit():
            ci.bit_depth = int(bpr)
        elif ci.pix_fmt:
            ci.bit_depth = 10 if "10" in ci.pix_fmt else 12 if "12" in ci.pix_fmt else 8
        if not ci.fps_num and v.get("avg_frame_rate", "0/0") != "0/0":
            fr = Fraction(v["avg_frame_rate"])
            ci.fps_num, ci.fps_den, ci.fps = fr.numerator, fr.denominator, float(fr)
    if not ci.is_lrf and ci.video_streams is not None and ci.video_streams < 2:
        ci.warnings.append(f"only {ci.video_streams} video stream(s) found; an .OSV should carry one per lens")


def probe(osvtool: Path, clip: Path, ffprobe: str | None = None, timeout: float = 120) -> ClipInfo:
    r = pm.run([osvtool, "probe", clip, "--json", "-"], timeout=timeout)
    if r.timed_out:
        return ClipInfo(str(clip), error="osvtool probe timed out")
    if r.returncode != 0 or not r.stdout.strip():
        msg = next((l[7:] for l in r.stderr.splitlines() if l.startswith("error: ")), "") or r.error \
            or f"osvtool probe failed (exit {r.returncode})"
        return ClipInfo(str(clip), error=msg)
    try:
        doc = json.loads(r.stdout)
    except ValueError as e:
        return ClipInfo(str(clip), error=f"probe output was not JSON: {e}")
    ci = parse_probe_json(doc, str(clip))
    apply_ffprobe(ci, ffprobe)
    return ci

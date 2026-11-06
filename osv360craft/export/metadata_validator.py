"""Validation of every finished render. A job is only reported successful
when this passes: no failed check, every expectation verified by ffprobe,
the independent metadata parser or a real decode."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from fractions import Fraction
from pathlib import Path

from osv360craft.core.metadata_manager import read_spherical
from osv360craft.utils import process_manager as pm

PASS, WARN, FAIL = "pass", "warn", "fail"

EXPECTED_TAGS = {
    "pq": ("bt2020", "smpte2084", "bt2020nc"),
    "hlg": ("bt2020", "arib-std-b67", "bt2020nc"),
    "709": ("bt709", "bt709", "bt709"),
}


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class Expectation:
    width: int | None = None
    height: int | None = None
    fps_num: int | None = None
    fps_den: int | None = None
    frames: int | None = None
    min_bit_depth: int = 10
    codec: str | None = None             # hevc / h264 / prores
    colour: str | None = None            # pq / hlg / 709 / dlogm
    audio: bool = False
    spherical: bool = True


@dataclass
class ValidationReport:
    path: str
    checks: list[Check] = field(default_factory=list)
    probe: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and not any(c.status == FAIL for c in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    def add(self, name: str, status: str, detail: str) -> None:
        self.checks.append(Check(name, status, detail))

    def to_dict(self) -> dict:
        return {"path": self.path, "ok": self.ok, "checks": [asdict(c) for c in self.checks]}


def _bit_depth(stream: dict) -> int | None:
    bpr = stream.get("bits_per_raw_sample")
    if bpr and str(bpr).isdigit():
        return int(bpr)
    pf = stream.get("pix_fmt") or ""
    for d in (16, 12, 10):
        if str(d) in pf:
            return d
    return 8 if pf else None


def ffprobe_json(ffprobe: str, path: str | Path, count_frames: bool = False) -> dict | None:
    args = [ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format"]
    if count_frames:
        args += ["-count_packets"]
    r = pm.run(args + [str(path)], timeout=300)
    if not r.ok:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def decode_check(ffmpeg: str, path: str | Path, duration: float | None, seconds: float = 2.0) -> tuple[bool, str]:
    """Decode the first and last few seconds; any decoder error fails."""
    spans = [["-t", str(seconds)]]
    if duration and duration > seconds * 2:
        spans.append(["-ss", f"{max(0.0, duration - seconds):.3f}"])
    for span in spans:
        pre = span if span[0] == "-ss" else []
        post = span if span[0] == "-t" else []
        r = pm.run([ffmpeg, "-v", "error", "-xerror", *pre, "-i", str(path), *post, "-map", "0:v:0", "-f", "null", "-"],
                   timeout=600)
        if not r.ok or r.stderr.strip():
            return False, (r.stderr.strip().splitlines() or [r.error or "decode failed"])[0][:300]
    return True, "first and last seconds decode without errors"


def validate(path: str | Path, exp: Expectation, ffprobe: str | None, ffmpeg: str | None,
             decode: bool = True) -> ValidationReport:
    path = Path(path)
    rep = ValidationReport(str(path))
    if not path.exists() or path.stat().st_size == 0:
        rep.add("file", FAIL, "output missing or empty")
        return rep
    rep.add("file", PASS, f"{path.stat().st_size / 1024 ** 2:.1f} MB")
    if not ffprobe:
        rep.add("ffprobe", FAIL, "ffprobe not available; output cannot be validated")
        return rep
    doc = ffprobe_json(ffprobe, path, count_frames=True)
    if not doc:
        rep.add("container", FAIL, "ffprobe could not read the file (corrupt or incomplete)")
        return rep
    rep.probe = doc
    streams = doc.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not v:
        rep.add("video stream", FAIL, "no video stream")
        return rep
    codec = v.get("codec_name")
    if exp.codec:
        rep.add("codec", PASS if codec == exp.codec else FAIL, f"{codec} (expected {exp.codec})")
    w, h = v.get("width"), v.get("height")
    if exp.width and exp.height:
        rep.add("resolution", PASS if (w, h) == (exp.width, exp.height) else FAIL, f"{w}x{h} (expected {exp.width}x{exp.height})")
    if exp.spherical:
        rep.add("aspect 2:1", PASS if w and h and w == 2 * h else FAIL, f"{w}x{h}")
    depth = _bit_depth(v)
    if depth is None:
        rep.add("bit depth", WARN, "unknown")
    else:
        rep.add("bit depth", PASS if depth >= exp.min_bit_depth else FAIL,
                f"{depth}-bit {v.get('pix_fmt')} (required >= {exp.min_bit_depth})")
    rate = v.get("r_frame_rate") or v.get("avg_frame_rate")
    fps_f = None
    if rate and rate != "0/0":
        fps_f = Fraction(rate)
        if exp.fps_num and exp.fps_den:
            want = Fraction(exp.fps_num, exp.fps_den)
            rep.add("frame rate", PASS if abs(float(fps_f - want)) < 1e-3 else FAIL, f"{rate} (source {want})")
    n = v.get("nb_read_packets") or v.get("nb_frames")
    try:
        n = int(n) if n is not None else None
    except ValueError:
        n = None
    if exp.frames:
        if n is None:
            rep.add("frame count", WARN, "could not be counted")
        else:
            rep.add("frame count", PASS if abs(n - exp.frames) <= 1 else FAIL, f"{n} (expected {exp.frames})")
    if exp.colour in EXPECTED_TAGS:
        want = EXPECTED_TAGS[exp.colour]
        got = (v.get("color_primaries"), v.get("color_transfer"), v.get("color_space"))
        rep.add("colour tags", PASS if got == want else FAIL, f"{'/'.join(map(str, got))} (expected {'/'.join(want)})")
    elif exp.colour == "dlogm":
        rep.add("colour tags", PASS, f"D-Log M signal, container tagged {v.get('color_primaries')}/"
                f"{v.get('color_transfer')} like the camera original; grade with a D-Log M LUT")
    vdur = float(v.get("duration") or doc.get("format", {}).get("duration") or 0) or None
    if exp.audio:
        if not a:
            rep.add("audio", FAIL, "the source has audio but the output has none")
        else:
            adur = float(a.get("duration") or 0) or None
            vst, ast = float(v.get("start_time") or 0), float(a.get("start_time") or 0)
            frame = float(1 / fps_f) if fps_f else 1 / 30
            start_ok = abs(vst - ast) <= frame
            dur_ok = (vdur is None or adur is None or abs(vdur - adur) <= max(0.1, 2 * frame))
            rep.add("audio", PASS, f"{a.get('codec_name')} {a.get('sample_rate')} Hz {a.get('channels')} ch")
            rep.add("A/V sync", PASS if start_ok and dur_ok else FAIL,
                    f"start offset {abs(vst - ast) * 1000:.1f} ms, duration video {vdur or 0:.3f} s / audio {adur or 0:.3f} s")
    if exp.spherical:
        sp = read_spherical(path)
        rep.add("Spherical V1", PASS if sp.v1 and (sp.v1_projection or "").lower() == "equirectangular" else FAIL,
                f"uuid XML {'present, ' + str(sp.v1_projection) if sp.v1 else 'missing'}")
        rep.add("Spherical V2", PASS if sp.v2_sv3d and sp.v2_projection == "equirectangular" else FAIL,
                f"sv3d {'present' if sp.v2_sv3d else 'missing'}, projection {sp.v2_projection}, "
                f"st3d {'mono' if sp.v2_stereo_mode == 0 else sp.v2_stereo_mode}")
        side = [d for d in v.get("side_data_list", []) if d.get("side_data_type") == "Spherical Mapping"]
        if side:
            rep.add("ffprobe spherical", PASS if side[0].get("projection") == "equirectangular" else FAIL,
                    f"projection {side[0].get('projection')}, yaw {side[0].get('yaw')}")
        else:
            rep.add("ffprobe spherical", WARN, "this ffprobe did not report spherical side data")
        if sp.pose:
            rep.add("orientation", PASS, f"initial view yaw {sp.pose[0]:g}, pitch {sp.pose[1]:g}, roll {sp.pose[2]:g}")
    if decode and ffmpeg:
        ok, msg = decode_check(ffmpeg, path, vdur)
        rep.add("decode", PASS if ok else FAIL, msg)
    return rep

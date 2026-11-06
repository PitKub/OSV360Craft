"""FFmpeg discovery and verified encoder / decoder capabilities.

An encoder listed by ``ffmpeg -encoders`` is only a candidate: NVENC, AMF
and Quick Sync appear in most Windows builds whether or not the GPU and
driver can run them. Every candidate is therefore verified with a tiny
10-bit Main10 test encode using the same rate-control options osvtool uses.
"""

from __future__ import annotations

import glob
import os
import re
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from osv360craft.utils import process_manager as pm
from osv360craft.utils.logging import get_logger
from osv360craft.utils.paths import install_dir, is_windows

log = get_logger(__name__)

# Order matters: the first verified encoder is the hardware default.
HEVC_CANDIDATES = ["hevc_nvenc", "hevc_amf", "hevc_qsv", "hevc_videotoolbox", "libx265"]
H264_CANDIDATES = ["h264_nvenc", "h264_amf", "h264_qsv", "libx264"]
PRORES_CANDIDATES = ["prores_ks", "prores"]
HWACCEL_INTEREST = ["cuda", "d3d11va", "dxva2", "qsv", "vulkan", "opencl", "videotoolbox"]

ENCODER_VENDOR = {"nvenc": "NVIDIA NVENC", "amf": "AMD AMF", "qsv": "Intel Quick Sync",
                  "videotoolbox": "Apple VideoToolbox", "libx265": "x265 (CPU)", "libx264": "x264 (CPU)",
                  "prores": "ProRes (CPU)"}


@dataclass
class EncoderTest:
    name: str
    listed: bool
    verified: bool | None = None  # None = not tested
    main10: bool | None = None
    detail: str = ""

    @property
    def label(self) -> str:
        for k, v in ENCODER_VENDOR.items():
            if k in self.name:
                return v
        return self.name


@dataclass
class FfmpegInfo:
    ffmpeg: str | None = None
    ffprobe: str | None = None
    version: str = ""
    gpl: bool | None = None
    encoders: set[str] = field(default_factory=set)
    hwaccels: list[str] = field(default_factory=list)
    tests: dict[str, EncoderTest] = field(default_factory=dict)
    error: str = ""

    @property
    def found(self) -> bool:
        return bool(self.ffmpeg)

    def verified(self, family: list[str]) -> list[str]:
        return [n for n in family if self.tests.get(n) and self.tests[n].verified]

    def best_hevc(self) -> str | None:
        v = self.verified(HEVC_CANDIDATES)
        return v[0] if v else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["encoders"] = sorted(self.encoders)
        return d


def _ffmpeg_candidates(configured: str | None) -> list[Path]:
    c: list[Path] = []
    for v in (configured, os.environ.get("OSV_FFMPEG_EXE")):
        if v:
            c.append(Path(v))
    exe = "ffmpeg.exe" if is_windows() else "ffmpeg"
    base = install_dir()
    c += [base / "external" / "ffmpeg" / "bin" / exe, base / "external" / "ffmpeg" / exe,
          base / "vendor" / "ffmpeg" / exe]  # a source checkout after scripts/get_ffmpeg.ps1
    w = pm.which("ffmpeg")
    if w:
        c.append(Path(w))
    if is_windows():
        la = os.environ.get("LOCALAPPDATA", "")
        patterns = [
            rf"{la}\Microsoft\WinGet\Packages\Gyan.FFmpeg*\*\bin\ffmpeg.exe",
            rf"{la}\Microsoft\WinGet\Links\ffmpeg.exe",
            rf"{os.path.expanduser('~')}\scoop\shims\ffmpeg.exe",
            r"C:\ProgramData\chocolatey\bin\ffmpeg.exe",
            r"C:\ffmpeg\bin\ffmpeg.exe",
        ]
        for pat in patterns:
            c += [Path(p) for p in sorted(glob.glob(pat))]
    return c


def locate_ffmpeg(configured: str | None = None) -> tuple[str | None, str | None]:
    for p in _ffmpeg_candidates(configured):
        if p.is_file():
            probe = p.with_name("ffprobe.exe" if p.suffix.lower() == ".exe" else "ffprobe")
            return str(p), str(probe) if probe.is_file() else pm.which("ffprobe")
    return None, pm.which("ffprobe")


def parse_encoders(text: str) -> set[str]:
    out = set()
    for line in text.splitlines():
        m = re.match(r"^\s*([VAS])[\.FSXBD]{5}\s+(\S+)", line)
        if m and m.group(1) == "V":
            out.add(m.group(2))
    return out


def parse_hwaccels(text: str) -> list[str]:
    lines = text.splitlines()
    try:
        i = next(k for k, l in enumerate(lines) if "Hardware acceleration methods" in l)
    except StopIteration:
        return []
    return [l.strip() for l in lines[i + 1:] if l.strip()]


def quality_args(codec: str) -> list[str]:
    """Mirror of osvtool's FfmpegPipe rate control, so a pass here means osvtool's call works too."""
    if "nvenc" in codec:
        return ["-preset", "p5", "-rc", "vbr", "-cq", "18", "-b:v", "0", "-profile:v", "main10"]
    if "amf" in codec:
        return ["-rc", "cqp", "-qp_i", "18", "-qp_p", "18"]
    if codec == "libx265":
        return ["-crf", "18", "-preset", "medium", "-x265-params", "profile=main10:log-level=error"]
    return []


def test_encoder(ffmpeg: str, codec: str, ten_bit: bool = True, size: str = "1280x640",
                 timeout: float = 45) -> EncoderTest:
    t = EncoderTest(codec, True)
    with tempfile.TemporaryDirectory(prefix="osv360craft_enc_") as td:
        if codec.startswith("prores"):
            out = Path(td) / "t.mov"
            args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s={size}:r=30", "-frames:v", "8", "-c:v", codec, "-profile:v", "3",
                    "-pix_fmt", "yuv422p10le", str(out)]
        else:
            out = Path(td) / "t.mp4"
            pix = "yuv420p10le" if ten_bit else "yuv420p"
            args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc2=s={size}:r=30", "-frames:v", "8", "-c:v", codec, "-pix_fmt", pix]
            if codec.startswith("hevc") or codec == "libx265":
                args += quality_args(codec)
            args.append(str(out))
        r = pm.run(args, timeout=timeout)
        ok = r.ok and out.exists() and out.stat().st_size > 0
        t.verified = ok
        t.main10 = ok if ten_bit and not codec.startswith("prores") else None
        if not ok:
            lines = [l.strip() for l in r.stderr.splitlines() if l.strip()]
            key = [l for l in lines if re.search(r"error|cannot|could not|failed|not supported|unsupported|no device",
                                                 l, re.I) and "Nothing was written" not in l]
            t.detail = (key or lines or [r.error or ("timed out" if r.timed_out else "failed")])[0][:300]
    return t


def detect(configured_ffmpeg: str | None = None, run_tests: bool = True) -> FfmpegInfo:
    ff, fp = locate_ffmpeg(configured_ffmpeg)
    info = FfmpegInfo(ff, fp)
    if not ff:
        info.error = "FFmpeg was not found (needed for video output)."
        return info
    v = pm.run([ff, "-hide_banner", "-version"], timeout=20)
    if v.ok:
        first = v.stdout.splitlines()[0] if v.stdout else ""
        m = re.search(r"ffmpeg version (\S+)", first)
        info.version = m.group(1) if m else first
        info.gpl = "--enable-gpl" in v.stdout
    else:
        info.error = f"ffmpeg -version failed: {v.error or v.stderr[:200]}"
        return info
    e = pm.run([ff, "-hide_banner", "-encoders"], timeout=20)
    info.encoders = parse_encoders(e.stdout)
    h = pm.run([ff, "-hide_banner", "-hwaccels"], timeout=20)
    info.hwaccels = parse_hwaccels(h.stdout)
    for name in HEVC_CANDIDATES + H264_CANDIDATES + PRORES_CANDIDATES:
        listed = name in info.encoders
        if not listed:
            info.tests[name] = EncoderTest(name, False, False, detail="not in this FFmpeg build")
            continue
        if run_tests:
            ten = not name.startswith("h264_")  # hardware H.264 is tested 8-bit; libx264 as High 10
            info.tests[name] = test_encoder(ff, name, ten_bit=ten)
            log.info("Encoder %s: %s", name, "verified" if info.tests[name].verified else
                     f"FAILED ({info.tests[name].detail})")
        else:
            info.tests[name] = EncoderTest(name, True)
    return info

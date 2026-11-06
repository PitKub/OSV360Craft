"""AUTO / QUALITY / PERFORMANCE processing plans.

A plan is a list of decisions, each with the value chosen and the reason,
built only from what was detected or measured on this machine. Nothing in a
plan reduces the export quality the user selected: profiles only fill in
values the user left on "Automatic", and every value is shown.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from osv360craft.core.osv_probe import ClipInfo
from osv360craft.export.export_settings import ExportSettings, parse_size
from osv360craft.hardware.benchmark import BenchmarkResult
from osv360craft.hardware.codec_detector import HEVC_CANDIDATES
from osv360craft.hardware.hardware_report import HardwareReport
from osv360craft.utils.file_manager import human_bytes

GIB = 1024 ** 3
PROFILES = {"auto": "AUTO - recommended", "quality": "QUALITY", "performance": "PERFORMANCE"}
PROFILE_CRF = {"auto": 16, "quality": 12, "performance": 20}


def _load_profiles() -> None:
    """config/hardware_profiles.json may adjust labels and CRF targets (validated; bad values ignored)."""
    import json
    from osv360craft.utils.paths import config_defaults_dir
    try:
        data = json.loads((config_defaults_dir() / "hardware_profiles.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    for k in PROFILES:
        d = data.get(k) or {}
        if isinstance(d.get("label"), str):
            PROFILES[k] = d["label"]
        if isinstance(d.get("crf"), int) and 0 <= d["crf"] <= 51:
            PROFILE_CRF[k] = d["crf"]


_load_profiles()
PREVIEW_MODES = {"native": None, "4k": "3840x1920", "2k": "1920x960", "performance": "1280x640"}


@dataclass
class Decision:
    key: str
    label: str
    value: str
    reason: str


@dataclass
class RenderPlan:
    profile: str
    device: str
    device_fallbacks: list[str]
    encoder: str | None
    crf: int
    preview_mode: str
    preview_size: str | None
    cache_bytes: int
    concurrency: int
    worker_threads: int
    decisions: list[Decision] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary_rows(self) -> list[tuple[str, str, str]]:
        return [(d.label, d.value, d.reason) for d in self.decisions]


def _available_renderers(hw: HardwareReport) -> list[str]:
    out = [n for n in ("cuda", "opencl", "metal") if hw.renderer_available(n)]
    if hw.openosv.found:
        out.append("cpu")
    return out


def choose_device(hw: HardwareReport, bench: BenchmarkResult | None) -> tuple[str, list[str], str]:
    avail = _available_renderers(hw)
    if not avail:
        return "cpu", [], "OpenOSV not found; CPU assumed until it is installed"
    if bench and bench.device_fps:
        measured = {d: f for d, f in bench.device_fps.items() if f and d in avail}
        if measured:
            best = max(measured, key=measured.get)
            rest = [d for d in sorted(measured, key=measured.get, reverse=True) if d != best]
            rest += [d for d in avail if d not in rest and d != best]
            return best, rest, f"fastest in this machine's benchmark ({measured[best]:.1f} frames/s at the test size)"
    pg = hw.primary_gpu
    if "cuda" in avail and (pg is None or pg.cuda_capable_for_openosv is not False):
        return "cuda", [d for d in avail if d != "cuda"], "OpenOSV reports a working CUDA device (NVDEC decode + CUDA stitch)"
    for d in ("opencl", "metal"):
        if d in avail:
            dev = "; ".join(hw.openosv.backends[d].devices) or d
            return d, [x for x in avail if x != d], f"OpenOSV reports a working {d.upper()} device: {dev}"
    return "cpu", [], "no GPU renderer is usable by OpenOSV on this machine"


def choose_encoder(hw: HardwareReport, ex: ExportSettings, profile: str) -> tuple[str | None, str]:
    f = hw.ffmpeg
    if ex.encoder != "auto":
        t = f.tests.get(ex.encoder)
        if t and t.verified is False:
            return ex.encoder, f"chosen by you, but it FAILED the test encode: {t.detail}"
        return ex.encoder, "chosen by you"
    verified = f.verified(HEVC_CANDIDATES)
    if not verified:
        return None, "no HEVC encoder passed the 10-bit test encode"
    hw_enc = [v for v in verified if v != "libx265"]
    if hw_enc:
        return hw_enc[0], f"first hardware HEVC Main10 encoder that passed a real test encode ({', '.join(verified)} passed)"
    return "libx265", "no hardware HEVC encoder works here; x265 on the CPU (slow at 8K, best compression)"


def choose_preview(hw: HardwareReport, device: str, profile: str, bench: BenchmarkResult | None) -> tuple[str, str]:
    if profile == "performance":
        return "performance", "PERFORMANCE profile"
    pg = hw.primary_gpu
    vram = (pg.vram_total_bytes or 0) if pg else 0
    if bench and bench.preview_fps:
        # The benchmark rendered at 1920x960; pick the largest mode that should still stream >= 10 fps.
        fps = bench.preview_fps
        if fps >= 40 and device != "cpu":
            return "4k", f"benchmark preview speed {fps:.0f} fps at 2K leaves headroom for 4K"
        if fps >= 10:
            return "2k", f"benchmark preview speed {fps:.0f} fps at 2K"
        return "performance", f"benchmark preview speed only {fps:.1f} fps at 2K"
    if device == "cuda" and vram >= 8 * GIB:
        return ("4k" if profile != "quality" or vram < 16 * GIB else "4k"), \
            f"CUDA with {human_bytes(vram)} VRAM (not yet benchmarked)"
    if device in ("cuda", "opencl", "metal"):
        return "2k", f"GPU renderer {device} (not yet benchmarked)"
    return "performance", "CPU renderer"


def make_plan(hw: HardwareReport, ex: ExportSettings, profile: str = "auto",
              bench: BenchmarkResult | None = None, clip: ClipInfo | None = None,
              allow_concurrency: bool = False) -> RenderPlan:
    D: list[Decision] = []
    warnings: list[str] = []

    device, fallbacks, why = choose_device(hw, bench)
    D.append(Decision("device", "Stitching renderer", device.upper(), why))
    D.append(Decision("fallback", "Fallback order", " > ".join(x.upper() for x in fallbacks) or "none",
                      "tried in this order if the renderer fails at start-up; every change is logged"))

    pg = hw.primary_gpu
    if device == "cuda":
        dec, dec_why = "NVDEC (hardware)", "OpenOSV's CUDA path decodes both lenses on NVDEC"
    elif device in ("opencl", "metal"):
        dec, dec_why = "managed by OpenOSV", "OpenOSV picks hardware decoding where its build supports it (D3D11VA on Windows)"
    else:
        dec, dec_why = "software (CPU)", "no GPU path"
    D.append(Decision("decode", "Video decoding", dec, dec_why))

    encoder, enc_why = choose_encoder(hw, ex, profile)
    D.append(Decision("encoder", "Video encoder", encoder or "none", enc_why))
    if encoder is None:
        warnings.append("Video export is blocked: no working HEVC encoder.")

    if ex.quality == "auto":
        crf = PROFILE_CRF[profile]
        crf_why = f"{PROFILES[profile]} profile default (lower is higher quality; 16 and below is visually transparent)"
    else:
        crf = ex.crf
        crf_why = f"your quality setting '{ex.quality}'"
    D.append(Decision("crf", "Quality target", f"CRF/CQ {crf}", crf_why))

    if clip and clip.native_equirect:
        nw, nh = clip.native_equirect
        sz = parse_size(ex.size)
        if sz is None:
            D.append(Decision("size", "Export resolution", f"{nw}x{nh} (native)", f"{clip.recording_mode_label}: 2 x lens height"))
        else:
            label = f"{sz[0]}x{sz[1]}"
            if sz[0] > nw:
                D.append(Decision("size", "Export resolution", f"{label} (UPSCALED from native {nw}x{nh})",
                                  "your choice; upscaling adds pixels, not original detail"))
            else:
                D.append(Decision("size", "Export resolution", label, "your choice" + (" (native)" if sz[0] == nw else "")))
    else:
        D.append(Decision("size", "Export resolution", ex.size, "your choice (clip not analysed yet)"))
    if clip and clip.fps_num:
        D.append(Decision("fps", "Frame rate", clip.fps_label, "source frame rate preserved; no interpolation"))

    preview_mode, pv_why = choose_preview(hw, device, profile, bench)
    D.append(Decision("preview", "Preview resolution", f"{preview_mode} ({PREVIEW_MODES[preview_mode] or 'native'})",
                      pv_why + "; never affects the export"))

    cache = hw.ram.safe_cache_bytes()
    D.append(Decision("cache", "Preview frame cache", human_bytes(cache),
                      "a quarter of free RAM, capped at 4 GB and 10 % of installed RAM"))
    vram_txt = "managed by OpenOSV (its decoder uses min(1.5 GB, 20 % of free VRAM))" if device != "cpu" else "not used"
    D.append(Decision("vram", "GPU memory", vram_txt, "OpenOSV sizes its own VRAM frame cache"))

    phys = hw.cpu.physical_cores or max(1, (hw.cpu.logical_processors or 2) // 2)
    workers = max(1, min(4, phys - 1))
    D.append(Decision("threads", "Processing threads",
                      f"OpenOSV: managed internally; this app: {workers} worker thread(s)",
                      "osvtool's plug-in engine manages its own thread pool; the GPU path needs few CPU threads"))

    concurrency = 1
    why_c = "one 8K job at a time avoids exhausting GPU memory and disk bandwidth"
    ram = hw.ram.total_bytes or 0
    vram = (pg.vram_total_bytes or 0) if pg else 0
    if allow_concurrency:
        if ram >= 64 * GIB and vram >= 16 * GIB and (hw.cpu.logical_processors or 0) >= 16 and device != "cpu":
            concurrency = 2
            why_c = "you allowed concurrency and this machine has >= 64 GB RAM, >= 16 GB VRAM and >= 16 threads"
        else:
            why_c = "you allowed concurrency, but this machine is below 64 GB RAM / 16 GB VRAM / 16 threads"
    D.append(Decision("concurrency", "Concurrent jobs", str(concurrency), why_c))

    if device == "cpu" and hw.openosv.found:
        warnings.append("CPU rendering at 8K is very slow; expect well under 1 frame per second on most CPUs.")
    return RenderPlan(profile, device, fallbacks, encoder, crf, preview_mode, PREVIEW_MODES[preview_mode], cache,
                      concurrency, workers, D, warnings)

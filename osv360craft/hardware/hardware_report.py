"""The Hardware Analyzer's data: every detector, the OpenOSV and FFmpeg
inspection and the Windows dependency checks, in one exportable report."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from osv360craft import APP_NAME, __version__
from osv360craft.core import openosv_manager
from osv360craft.hardware import codec_detector, cpu_detector, gpu_detector, ram_detector, storage_detector
from osv360craft.utils.file_manager import human_bytes
from osv360craft.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class DependencyCheck:
    name: str
    ok: bool | None  # None = could not be determined
    detail: str
    remedy: str = ""


@dataclass
class HardwareReport:
    created: str
    os_name: str
    os_version: str
    cpu: cpu_detector.CpuInfo
    ram: ram_detector.RamInfo
    gpus: list[gpu_detector.GpuInfo]
    drives: list[storage_detector.DriveInfo]
    openosv: openosv_manager.OpenOsvInfo
    ffmpeg: codec_detector.FfmpegInfo
    dependencies: list[DependencyCheck] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    @property
    def real_gpus(self) -> list[gpu_detector.GpuInfo]:
        return [g for g in self.gpus if not g.is_virtual and g.vendor != "Microsoft"]

    @property
    def primary_gpu(self) -> gpu_detector.GpuInfo | None:
        gs = self.real_gpus
        if not gs:
            return None
        return max(gs, key=lambda g: (g.vendor == "NVIDIA" and bool(g.cuda_capable_for_openosv),
                                      g.vram_total_bytes or 0))

    def renderer_available(self, name: str) -> bool:
        if name == "cpu":
            return self.openosv.found
        b = self.openosv.backends.get(name)
        return bool(b and b.available)

    def fingerprint(self) -> str:
        """Changes when the CPU, RAM size, a GPU, a GPU driver, OpenOSV or FFmpeg changes."""
        parts = [self.cpu.model, str((self.ram.total_bytes or 0) // (1024 ** 3)), self.openosv.version,
                 self.ffmpeg.version]
        parts += sorted(f"{g.name}|{g.driver_version}" for g in self.gpus)
        return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]

    def nvdec_status(self) -> tuple[str, str]:
        g = self.primary_gpu
        if not g or g.vendor != "NVIDIA":
            return "not applicable", "no NVIDIA GPU"
        cuda = self.openosv.backends.get("cuda")
        if cuda and cuda.available:
            return "available", "OpenOSV decodes on NVDEC through its CUDA path"
        return "unavailable", (cuda.reason if cuda else "OpenOSV CUDA backend not present")

    def to_dict(self) -> dict:
        return {
            "application": f"{APP_NAME} {__version__}",
            "created": self.created,
            "fingerprint": self.fingerprint(),
            "os": {"name": self.os_name, "version": self.os_version},
            "cpu": self.cpu.to_dict(),
            "ram": self.ram.to_dict(),
            "gpus": [g.to_dict() for g in self.gpus],
            "drives": [d.to_dict() for d in self.drives],
            "openosv": {
                "found": self.openosv.found, "path": str(self.openosv.path) if self.openosv.path else None,
                "version": self.openosv.version, "modules": self.openosv.modules,
                "ffmpeg_libraries": self.openosv.ffmpeg_libs,
                "backends": {k: vars(v) for k, v in self.openosv.backends.items()},
                "error": self.openosv.error,
            },
            "ffmpeg": self.ffmpeg.to_dict(),
            "nvdec": self.nvdec_status(),
            "dependencies": [vars(d) for d in self.dependencies],
            "warnings": self.warnings,
        }

    def to_text(self) -> str:
        L: list[str] = []
        add = L.append
        add(f"{APP_NAME} {__version__} - hardware and dependency report")
        add(f"Created: {self.created}    Fingerprint: {self.fingerprint()}")
        add(f"Operating system: {self.os_name} {self.os_version}")
        add("")
        add("CPU")
        add(f"  Model:        {self.cpu.model} ({self.cpu.vendor}, {self.cpu.architecture})")
        add(f"  Cores:        {self.cpu.physical_cores or 'unknown'} physical / {self.cpu.logical_processors} logical")
        add(f"  Instructions: {', '.join(self.cpu.instruction_sets) or 'unknown'}")
        add(f"  Load:         {self.cpu.load_percent if self.cpu.load_percent is not None else 'unknown'} %")
        add("RAM")
        add(f"  Installed:    {human_bytes(self.ram.total_bytes)}   Available: {human_bytes(self.ram.available_bytes)}"
            f"   Used: {self.ram.percent_used} %")
        add(f"  Frame cache budget: {human_bytes(self.ram.safe_cache_bytes())}")
        add("GPU")
        if not self.gpus:
            add("  No graphics adapter identified.")
        for g in self.gpus:
            cc = f", compute {g.compute_capability}" if g.compute_capability else ""
            add(f"  {g.name} [{g.vendor}]  VRAM {human_bytes(g.vram_total_bytes)}"
                f" (free {human_bytes(g.vram_free_bytes)})  driver {g.driver_version or 'unknown'}{cc}"
                f"{'  (virtual adapter)' if g.is_virtual else ''}")
        add("OpenOSV")
        o = self.openosv
        add(f"  osvtool:      {o.path or 'not found'}  version {o.version or '-'}")
        if o.error:
            add(f"  Error:        {o.error}")
        for name, b in o.backends.items():
            state = ("available: " + "; ".join(b.devices)) if b.available else \
                ("not compiled in" if not b.compiled else f"unavailable ({b.reason})")
            add(f"  {name.upper():<7}       {state}")
        add(f"  CPU renderer: {'available' if o.found else 'unavailable'}")
        add(f"  NVDEC:        {': '.join(self.nvdec_status())}")
        add(f"  Linked FFmpeg libraries: {o.ffmpeg_libs or 'unknown'}")
        add("FFmpeg (encoder)")
        f = self.ffmpeg
        add(f"  ffmpeg:       {f.ffmpeg or 'not found'}  version {f.version or '-'}"
            f"{'  (GPL build)' if f.gpl else ''}")
        add(f"  ffprobe:      {f.ffprobe or 'not found'}")
        add(f"  Hardware decoding methods: {', '.join(f.hwaccels) or 'none reported'}")
        for name, t in f.tests.items():
            if not t.listed:
                continue
            state = "verified" if t.verified else ("not tested" if t.verified is None else f"FAILED - {t.detail}")
            add(f"  {name:<18} {t.label:<20} {state}{'  Main10' if t.main10 else ''}")
        add("Storage")
        for d in self.drives:
            speed = f"  write {d.write_mb_s:.0f} MB/s, read {d.read_mb_s:.0f} MB/s" if d.write_mb_s else ""
            add(f"  {d.root:<6} {d.media_type:<8} free {human_bytes(d.free_bytes)} of {human_bytes(d.total_bytes)}{speed}")
        add("Dependencies")
        for dep in self.dependencies:
            mark = "OK " if dep.ok else ("?? " if dep.ok is None else "MISSING ")
            add(f"  [{mark.strip()}] {dep.name}: {dep.detail}" + (f"  -> {dep.remedy}" if dep.remedy and not dep.ok else ""))
        if self.warnings:
            add("Warnings")
            for w in self.warnings:
                add(f"  - {w}")
        return "\n".join(L) + "\n"

    def export(self, path: Path) -> None:
        path = Path(path)
        if path.suffix.lower() == ".json":
            path.write_text(json.dumps(self.to_dict(), indent=2, default=str), encoding="utf-8")
        else:
            path.write_text(self.to_text(), encoding="utf-8")


# ----------------------------------------------------------------------
def _windows_version() -> tuple[str, str]:
    if sys.platform != "win32":
        return platform.system(), platform.release()
    v = sys.getwindowsversion()
    name = "Windows 11" if v.major == 10 and v.build >= 22000 else f"Windows {platform.release()}"
    return name, f"{v.major}.{v.minor}.{v.build}"


def _vcredist() -> DependencyCheck:
    name = "Microsoft Visual C++ 2015-2022 Redistributable (x64)"
    remedy = "Install it from https://aka.ms/vs/17/release/vc_redist.x64.exe (needed by osvtool.exe)"
    if sys.platform != "win32":
        return DependencyCheck(name, None, "not applicable on this platform")
    try:
        import winreg

        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64")
        installed = winreg.QueryValueEx(k, "Installed")[0]
        ver = winreg.QueryValueEx(k, "Version")[0]
        return DependencyCheck(name, bool(installed), f"version {ver}", remedy)
    except OSError:
        sysdir = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
        present = (sysdir / "vcruntime140.dll").exists() and (sysdir / "msvcp140.dll").exists()
        return DependencyCheck(name, present or None,
                               "runtime DLLs present" if present else "registry key not found", remedy)


def _opencl_icd() -> DependencyCheck:
    if sys.platform != "win32":
        return DependencyCheck("OpenCL loader", None, "not checked on this platform")
    p = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenCL.dll"
    return DependencyCheck("OpenCL loader (OpenCL.dll)", p.exists(),
                           "present" if p.exists() else "not found",
                           "Installed by AMD, Intel and NVIDIA graphics drivers; update the GPU driver")


def build_report(osvtool_path: str | None = None, ffmpeg_path: str | None = None,
                 drive_paths: list[str] | None = None, test_encoders: bool = True,
                 reuse_encoder_tests: bool = True) -> HardwareReport:
    t0 = time.time()
    os_name, os_ver = _windows_version()
    cpu = cpu_detector.detect()
    ram = ram_detector.detect()
    gpus = gpu_detector.detect()
    tool = openosv_manager.locate_osvtool(osvtool_path)
    oinfo = openosv_manager.inspect(tool)
    finfo = codec_detector.detect(ffmpeg_path, run_tests=test_encoders, use_cache=reuse_encoder_tests)
    roots: list[str] = []
    drives = []
    for p in drive_paths or [str(Path.home())]:
        r = storage_detector.drive_root(p)
        if r not in roots:
            roots.append(r)
            drives.append(storage_detector.detect(p))
    rep = HardwareReport(time.strftime("%Y-%m-%d %H:%M:%S"), os_name, os_ver, cpu, ram, gpus, drives, oinfo, finfo)

    deps = rep.dependencies
    deps.append(DependencyCheck("OpenOSV osvtool", oinfo.found,
                                f"{oinfo.version} at {oinfo.path}" if oinfo.found else (oinfo.error or "not found"),
                                "Download the OpenOSV release zip (github.com/Kemerd/OpenOSV/releases) and place its "
                                "'cli' folder in external\\OpenOSV\\cli, or set its path in Settings"))
    deps.append(DependencyCheck("FFmpeg", finfo.found, finfo.version or finfo.error,
                                "Install FFmpeg (for example: winget install Gyan.FFmpeg) or set its path in Settings"))
    deps.append(DependencyCheck("ffprobe", bool(finfo.ffprobe), finfo.ffprobe or "not found",
                                "ffprobe ships with FFmpeg; it is needed to validate outputs"))
    deps.append(_vcredist())
    deps.append(_opencl_icd())
    if sys.platform == "win32":
        name, ver = os_name, os_ver
        ok = sys.getwindowsversion().major >= 10
        deps.append(DependencyCheck("Windows 10/11 64-bit", ok and platform.machine().endswith("64"),
                                    f"{name} {ver} {platform.machine()}"))

    w = rep.warnings
    pg = rep.primary_gpu
    if pg and pg.vendor == "NVIDIA" and pg.cuda_capable_for_openosv is False:
        w.append(f"{pg.name} (compute {pg.compute_capability}) is older than OpenOSV's CUDA target (7.5, GTX 16/RTX 20); "
                 "OpenCL or the CPU renderer will be used.")
    if not rep.real_gpus:
        w.append("No physical GPU identified: rendering will use OpenOSV's CPU renderer and will be slow at 8K.")
    if oinfo.found and not oinfo.gpu_renderers:
        w.append("OpenOSV reports no usable GPU renderer on this machine; the CPU renderer will be used.")
    if finfo.found and not finfo.best_hevc():
        w.append("No HEVC encoder passed the 10-bit test encode; video export is not possible until FFmpeg is fixed.")
    if ram.total_bytes and ram.total_bytes < 15 * 1024 ** 3:
        w.append("Less than 16 GB RAM: 8K rendering may be slow; concurrency is limited to one job.")
    for d in drives:
        if d.free_bytes is not None and d.free_bytes < 50 * 1024 ** 3:
            w.append(f"Drive {d.root} has only {human_bytes(d.free_bytes)} free; an 8K render can need tens of GB.")
    log.info("Hardware report built in %.1f s (fingerprint %s)", time.time() - t0, rep.fingerprint())
    return rep

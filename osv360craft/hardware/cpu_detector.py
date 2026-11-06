"""CPU identification: vendor, model, cores, threads, instruction sets, load."""

from __future__ import annotations

import os
import platform
import re
from dataclasses import asdict, dataclass, field

from osv360craft.utils.logging import get_logger

log = get_logger(__name__)

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is a declared dependency
    psutil = None

# Windows IsProcessorFeaturePresent ids (winnt.h).
_WIN_FEATURES = {"sse3": 13, "sse4_1": 37, "sse4_2": 38, "avx": 39, "avx2": 40, "avx512f": 41}


@dataclass
class CpuInfo:
    vendor: str = "unknown"
    model: str = "unknown"
    architecture: str = ""
    physical_cores: int | None = None
    logical_processors: int | None = None
    instruction_sets: list[str] = field(default_factory=list)
    load_percent: float | None = None
    sources: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _windows_model() -> tuple[str | None, str | None]:
    try:
        import winreg

        k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0")
        name = winreg.QueryValueEx(k, "ProcessorNameString")[0].strip()
        vendor = winreg.QueryValueEx(k, "VendorIdentifier")[0].strip()
        return name, vendor
    except OSError:
        return None, None


def _windows_features() -> list[str]:
    import ctypes

    out = []
    try:
        fn = ctypes.windll.kernel32.IsProcessorFeaturePresent
        for name, fid in _WIN_FEATURES.items():
            if fn(fid):
                out.append(name)
    except (AttributeError, OSError):
        pass
    return out


def _linux_cpuinfo() -> tuple[str | None, str | None, list[str]]:
    try:
        text = open("/proc/cpuinfo", encoding="utf-8", errors="replace").read()
    except OSError:
        return None, None, []
    model = re.search(r"^model name\s*:\s*(.+)$", text, re.M)
    vendor = re.search(r"^vendor_id\s*:\s*(.+)$", text, re.M)
    flags = re.search(r"^flags\s*:\s*(.+)$", text, re.M)
    want = {"sse3": "pni", "sse4_1": "sse4_1", "sse4_2": "sse4_2", "avx": "avx", "avx2": "avx2",
            "avx512f": "avx512f", "fma": "fma"}
    fl = set(flags.group(1).split()) if flags else set()
    return (model.group(1).strip() if model else None, vendor.group(1).strip() if vendor else None,
            [k for k, v in want.items() if v in fl])


def normalise_vendor(v: str | None, model: str) -> str:
    s = f"{v or ''} {model}".lower()
    if "intel" in s:
        return "Intel"
    if "amd" in s:
        return "AMD"
    if "arm" in s or "qualcomm" in s or "snapdragon" in s:
        return "ARM"
    return v or "unknown"


def detect() -> CpuInfo:
    ci = CpuInfo(architecture=platform.machine())
    model = vendor = None
    if platform.system() == "Windows":
        model, vendor = _windows_model()
        ci.instruction_sets = _windows_features()
        ci.sources.append("registry/IsProcessorFeaturePresent")
    elif os.path.exists("/proc/cpuinfo"):
        model, vendor, ci.instruction_sets = _linux_cpuinfo()
        ci.sources.append("/proc/cpuinfo")
    ci.model = model or platform.processor() or "unknown"
    ci.vendor = normalise_vendor(vendor, ci.model)
    ci.logical_processors = os.cpu_count()
    if psutil:
        ci.physical_cores = psutil.cpu_count(logical=False)
        ci.logical_processors = psutil.cpu_count(logical=True) or ci.logical_processors
        try:
            ci.load_percent = psutil.cpu_percent(interval=0.3)
        except Exception:
            pass
        ci.sources.append("psutil")
    if not ci.physical_cores and ci.logical_processors:
        ci.physical_cores = None  # unknown rather than guessed
    return ci

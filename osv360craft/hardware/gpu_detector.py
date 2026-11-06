"""Graphics adapters, from several independent sources:

1. ``nvidia-smi`` (NVIDIA only): name, VRAM total/free, driver, compute capability.
2. Windows CIM (``Win32_VideoController``): every adapter, driver version.
3. The display-class registry key: 64-bit VRAM size (CIM's AdapterRAM is a
   32-bit field and reports at most 4 GiB).
4. osvtool's own backend diagnostics (CUDA / OpenCL device names) - the
   authority on what OpenOSV can actually render with; merged in by
   hardware_report.

No vendor is assumed: a machine without a GPU, or with a GPU nothing can
identify, is reported as such.
"""

from __future__ import annotations

import re
import sys
from dataclasses import asdict, dataclass, field

from osv360craft.utils import process_manager as pm
from osv360craft.utils.logging import get_logger

log = get_logger(__name__)

# OpenOSV's CUDA build targets compute capability 7.5 and newer (Turing+), plus PTX.
OPENOSV_MIN_CUDA_CC = 7.5


@dataclass
class GpuInfo:
    name: str
    vendor: str
    vram_total_bytes: int | None = None
    vram_free_bytes: int | None = None
    driver_version: str | None = None
    compute_capability: float | None = None
    utilization_percent: float | None = None
    pnp_id: str | None = None
    sources: list[str] = field(default_factory=list)

    @property
    def is_virtual(self) -> bool:
        n = self.name.lower()
        return any(k in n for k in ("basic display", "basic render", "remote", "virtual", "parsec"))

    @property
    def cuda_capable_for_openosv(self) -> bool | None:
        if self.vendor != "NVIDIA":
            return False
        if self.compute_capability is None:
            return None
        return self.compute_capability >= OPENOSV_MIN_CUDA_CC

    def to_dict(self) -> dict:
        d = asdict(self)
        d["cuda_capable_for_openosv"] = self.cuda_capable_for_openosv
        return d


def vendor_of(name: str, pnp: str | None = None) -> str:
    s = f"{name} {pnp or ''}".lower()
    if "nvidia" in s or "ven_10de" in s or "geforce" in s or "quadro" in s or "rtx" in s:
        return "NVIDIA"
    if "amd" in s or "radeon" in s or "ven_1002" in s or "ven_1022" in s:
        return "AMD"
    if "intel" in s or "ven_8086" in s or " arc" in s or "iris" in s or "uhd graphics" in s:
        return "Intel"
    if "microsoft" in s or "ven_1414" in s:
        return "Microsoft"
    return "unknown"


def parse_nvidia_smi(text: str) -> list[GpuInfo]:
    out = []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue

        def num(i: int) -> float | None:
            try:
                return float(parts[i])
            except (IndexError, ValueError):
                return None

        g = GpuInfo(parts[0], "NVIDIA", sources=["nvidia-smi"])
        tot, free = num(1), num(2)
        g.vram_total_bytes = int(tot * 1024 ** 2) if tot is not None else None
        g.vram_free_bytes = int(free * 1024 ** 2) if free is not None else None
        g.driver_version = parts[3] or None
        g.compute_capability = num(4)
        g.utilization_percent = num(5)
        out.append(g)
    return out


def query_nvidia_smi(with_cc: bool = True) -> list[GpuInfo]:
    exe = pm.which("nvidia-smi")
    if not exe and sys.platform == "win32":
        cand = r"C:\Windows\System32\nvidia-smi.exe"
        exe = cand if __import__("os").path.exists(cand) else None
    if not exe:
        return []
    fields = "name,memory.total,memory.free,driver_version,compute_cap,utilization.gpu"
    r = pm.run([exe, f"--query-gpu={fields}", "--format=csv,noheader,nounits"], timeout=15)
    if not r.ok:  # older drivers do not know compute_cap
        fields = "name,memory.total,memory.free,driver_version"
        r = pm.run([exe, f"--query-gpu={fields}", "--format=csv,noheader,nounits"], timeout=15)
    return parse_nvidia_smi(r.stdout) if r.ok else []


def _cim_adapters() -> list[GpuInfo]:
    data = pm.powershell_json(
        "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion,PNPDeviceID "
        "| ConvertTo-Json -Compress")
    if data is None:
        return []
    if isinstance(data, dict):
        data = [data]
    out = []
    for d in data:
        name = (d.get("Name") or "").strip()
        if not name:
            continue
        g = GpuInfo(name, vendor_of(name, d.get("PNPDeviceID")), driver_version=d.get("DriverVersion"),
                    pnp_id=d.get("PNPDeviceID"), sources=["CIM"])
        ram = d.get("AdapterRAM")
        if isinstance(ram, int) and ram > 0:
            g.vram_total_bytes = ram  # may be capped at 4 GiB; registry value preferred below
        out.append(g)
    return out


def _registry_vram() -> dict[str, int]:
    """Adapter description -> qwMemorySize from the display class key."""
    sizes: dict[str, int] = {}
    if sys.platform != "win32":
        return sizes
    try:
        import winreg

        cls = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, cls)
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            if not re.fullmatch(r"\d{4}", sub):
                continue
            try:
                k = winreg.OpenKey(root, sub)
                desc = winreg.QueryValueEx(k, "DriverDesc")[0]
                mem = winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")[0]
                if isinstance(mem, bytes):
                    mem = int.from_bytes(mem[:8], "little")
                if isinstance(mem, int) and mem > 0:
                    sizes[desc.strip()] = mem
            except OSError:
                continue
    except OSError:
        pass
    return sizes


def detect() -> list[GpuInfo]:
    gpus: list[GpuInfo] = []
    if sys.platform == "win32":
        gpus = _cim_adapters()
        reg = _registry_vram()
        for g in gpus:
            if g.name in reg:
                g.vram_total_bytes = reg[g.name]
                g.sources.append("registry")
    nv = query_nvidia_smi()
    for n in nv:
        match = next((g for g in gpus if g.vendor == "NVIDIA" and
                      (g.name.lower() in n.name.lower() or n.name.lower() in g.name.lower())), None)
        if match:
            match.vram_total_bytes = n.vram_total_bytes or match.vram_total_bytes
            match.vram_free_bytes = n.vram_free_bytes
            match.compute_capability = n.compute_capability
            match.utilization_percent = n.utilization_percent
            match.driver_version = n.driver_version or match.driver_version  # NVIDIA's own numbering
            match.sources.append("nvidia-smi")
        else:
            gpus.append(n)
    if not gpus:
        log.info("No graphics adapter could be identified by CIM or nvidia-smi")
    return gpus


def poll_utilisation() -> dict:
    """Light-weight live sample for monitors: NVIDIA via nvidia-smi only.
    Returns {} when no source is available (shown as 'not available')."""
    nv = query_nvidia_smi(with_cc=False)
    if not nv:
        return {}
    g = nv[0]
    used = (g.vram_total_bytes - g.vram_free_bytes) if g.vram_total_bytes and g.vram_free_bytes is not None else None
    return {"gpu_percent": g.utilization_percent, "vram_used_bytes": used, "vram_total_bytes": g.vram_total_bytes,
            "vram_free_bytes": g.vram_free_bytes, "name": g.name}

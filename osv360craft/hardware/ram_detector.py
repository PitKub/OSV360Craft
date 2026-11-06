"""System memory: installed, available, utilisation and a safe frame budget."""

from __future__ import annotations

import sys
from dataclasses import asdict, dataclass

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

GIB = 1024 ** 3


@dataclass
class RamInfo:
    total_bytes: int | None = None
    available_bytes: int | None = None
    percent_used: float | None = None
    source: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["safe_cache_bytes"] = self.safe_cache_bytes()
        return d

    def safe_cache_bytes(self) -> int:
        """Memory this application may use for frame caches: a quarter of what is free,
        between 256 MiB and 4 GiB, never more than 10 % of installed RAM."""
        if not self.available_bytes or not self.total_bytes:
            return 512 * 1024 ** 2
        budget = min(self.available_bytes // 4, 4 * GIB, self.total_bytes // 10)
        return max(256 * 1024 ** 2, budget)


def _win_global_memory() -> RamInfo | None:
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
        return RamInfo(st.ullTotalPhys, st.ullAvailPhys, float(st.dwMemoryLoad), "GlobalMemoryStatusEx")
    return None


def detect() -> RamInfo:
    if psutil:
        vm = psutil.virtual_memory()
        return RamInfo(vm.total, vm.available, vm.percent, "psutil")
    if sys.platform == "win32":
        r = _win_global_memory()
        if r:
            return r
    return RamInfo(source="unavailable")

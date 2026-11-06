"""Drives: free space, SSD/HDD where Windows reports it, an optional short
sequential write/read benchmark (user-triggered, 256 MiB, temp file removed)."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from osv360craft.utils import process_manager as pm
from osv360craft.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class DriveInfo:
    path: str
    root: str
    total_bytes: int | None = None
    free_bytes: int | None = None
    media_type: str = "unknown"  # SSD / HDD / unknown
    bus_type: str = ""
    write_mb_s: float | None = None
    read_mb_s: float | None = None
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def drive_root(path: str | Path) -> str:
    p = Path(path).resolve()
    if sys.platform == "win32":
        return p.drive + "\\" if p.drive else str(p.anchor)
    return str(p.anchor)


def _windows_media_type(root: str) -> tuple[str, str]:
    letter = root[:1]
    if not letter.isalpha():
        return "unknown", ""
    data = pm.powershell_json(
        f"$d = Get-Partition -DriveLetter {letter} -ErrorAction Stop | Get-Disk | Get-PhysicalDisk; "
        "$d | Select-Object MediaType,BusType | ConvertTo-Json -Compress", timeout=20)
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return "unknown", ""
    mt = str(data.get("MediaType", ""))
    bus = str(data.get("BusType", ""))
    mapping = {"3": "HDD", "4": "SSD", "HDD": "HDD", "SSD": "SSD"}
    media = mapping.get(mt, "unknown")
    if media == "unknown" and bus in ("17", "NVMe"):
        media = "SSD"
    bus_names = {"17": "NVMe", "11": "SATA", "7": "USB", "8": "RAID"}
    return media, bus_names.get(bus, bus)


def detect(path: str | Path, media_type: bool = True) -> DriveInfo:
    root = drive_root(path)
    d = DriveInfo(str(path), root)
    try:
        u = shutil.disk_usage(root)
        d.total_bytes, d.free_bytes = u.total, u.free
    except OSError as e:
        d.note = f"disk usage unavailable: {e}"
    if media_type and sys.platform == "win32":
        d.media_type, d.bus_type = _windows_media_type(root)
    return d


def benchmark(folder: str | Path, size_mb: int = 256) -> tuple[float | None, float | None, str]:
    """Sequential write (with fsync) then read of a temp file. The read figure may be
    inflated by the OS cache; that is reported in the note."""
    folder = Path(folder)
    try:
        if shutil.disk_usage(folder).free < (size_mb * 3) * 1024 ** 2:
            return None, None, "skipped: not enough free space for a safe test"
    except OSError as e:
        return None, None, f"skipped: {e}"
    block = os.urandom(4 * 1024 ** 2)
    fd, name = tempfile.mkstemp(prefix="osv360craft_disk_", dir=folder)
    try:
        t0 = time.perf_counter()
        with os.fdopen(fd, "wb", buffering=0) as f:
            for _ in range(size_mb // 4):
                f.write(block)
            os.fsync(f.fileno())
        w = size_mb / max(time.perf_counter() - t0, 1e-6)
        t0 = time.perf_counter()
        with open(name, "rb", buffering=0) as f:
            while f.read(8 * 1024 ** 2):
                pass
        r = size_mb / max(time.perf_counter() - t0, 1e-6)
        return w, r, "read speed may include operating-system caching"
    except OSError as e:
        return None, None, f"failed: {e}"
    finally:
        try:
            os.remove(name)
        except OSError:
            pass


def estimate_output_bytes(width: int, height: int, fps: float, seconds: float, crf: int) -> int:
    """Rough HEVC 10-bit size estimate for the storage warning (deliberately generous).
    About 0.12 bits/pixel at CRF 18, roughly doubling every 6 CRF steps down."""
    bpp = 0.12 * (2 ** ((18 - crf) / 6.0))
    return int(width * height * fps * seconds * bpp / 8)

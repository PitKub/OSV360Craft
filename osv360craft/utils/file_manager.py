"""File discovery, project folders and output-path safety.

Rules enforced here (the rest of the application relies on them):
* original recordings are only ever opened for reading;
* no output path may equal, or carry the extension of, a camera original;
* existing outputs are never overwritten silently: a unique name is chosen
  unless the caller explicitly asked to replace.
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

SOURCE_EXTS = {".osv"}
PROXY_EXTS = {".lrf"}
PROTECTED_EXTS = SOURCE_EXTS | PROXY_EXTS
PROJECT_SUBDIRS = ("Original", "Stitched", "Preview", "Audio", "Logs", "Reports")


class UnsafeOutputError(ValueError):
    """An output path would touch an original recording."""


@dataclass
class SourceFile:
    path: Path
    size: int
    proxy: Path | None  # the camera's .LRF beside it, if any


def find_proxy(osv: Path) -> Path | None:
    for ext in (".LRF", ".lrf"):
        cand = osv.with_suffix(ext)
        if cand.exists():
            return cand
    # DJI sometimes names the proxy with the same stem in the same folder but other case
    try:
        for p in osv.parent.iterdir():
            if p.suffix.lower() == ".lrf" and p.stem.lower() == osv.stem.lower():
                return p
    except OSError:
        pass
    return None


def scan_sources(paths: Iterable[str | Path], recursive: bool = True) -> tuple[list[SourceFile], list[str]]:
    """Expand files and folders into .OSV recordings.

    Returns (sources, skipped-messages). Empty files, proxies and macOS
    ``._`` metadata twins are skipped; duplicates are removed.
    """
    found: list[SourceFile] = []
    skipped: list[str] = []
    seen: set[str] = set()

    def consider(p: Path) -> None:
        if p.name.startswith("._"):
            return
        ext = p.suffix.lower()
        if ext in PROXY_EXTS:
            return
        if ext not in SOURCE_EXTS:
            return
        try:
            size = p.stat().st_size
        except OSError as e:
            skipped.append(f"{p}: unreadable ({e})")
            return
        if size == 0:
            skipped.append(f"{p}: empty file skipped")
            return
        key = os.path.normcase(str(p.resolve()))
        if key in seen:
            return
        seen.add(key)
        found.append(SourceFile(p, size, find_proxy(p)))

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            for child in sorted(it):
                if child.is_file():
                    consider(child)
        elif p.is_file():
            if p.suffix.lower() not in SOURCE_EXTS:
                skipped.append(f"{p}: not an .OSV recording")
            else:
                consider(p)
        else:
            skipped.append(f"{p}: not found")
    return found, skipped


def same_file(a: Path, b: Path) -> bool:
    try:
        if a.exists() and b.exists():
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def assert_safe_output(out: Path, sources: Iterable[Path] = ()) -> None:
    if out.suffix.lower() in PROTECTED_EXTS:
        raise UnsafeOutputError(f"Refusing to write a file with a camera-original extension: {out}")
    for s in sources:
        if same_file(out, Path(s)):
            raise UnsafeOutputError(f"Output would overwrite the original recording {s}")


def unique_path(path: Path) -> Path:
    """``name.mp4`` -> ``name (2).mp4`` ... if the name is taken."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    m = re.match(r"^(.*) \((\d+)\)$", stem)
    if m:
        stem = m.group(1)
    n = 2
    while True:
        cand = path.with_name(f"{stem} ({n}){suffix}")
        if not cand.exists():
            return cand
        n += 1


def sanitize_name(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return name or "output"


@dataclass
class ProjectLayout:
    root: Path

    def ensure(self) -> "ProjectLayout":
        for sub in PROJECT_SUBDIRS:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        return self

    def __getattr__(self, item: str) -> Path:  # stitched, preview, audio, logs, reports, original
        cap = item.capitalize()
        if cap in PROJECT_SUBDIRS:
            return self.root / cap
        raise AttributeError(item)


def free_bytes(path: Path) -> int | None:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    try:
        return shutil.disk_usage(p).free
    except OSError:
        return None


def human_bytes(n: float | None) -> str:
    if n is None:
        return "unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"

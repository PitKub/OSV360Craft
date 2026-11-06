"""Locating and interrogating OpenOSV's ``osvtool``.

Nothing about the installed OpenOSV is assumed: the version, the compiled-in
backends (CUDA / OpenCL / Metal), the FFmpeg it links and the options its
``render`` command accepts are all read from the binary itself.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from osv360craft.utils import process_manager as pm
from osv360craft.utils.logging import get_logger
from osv360craft.utils.paths import install_dir, is_windows

log = get_logger(__name__)

EXE = "osvtool.exe" if is_windows() else "osvtool"

# Exit codes documented by osvtool.
EXIT_OK, EXIT_USAGE, EXIT_INPUT, EXIT_RUNTIME = 0, 1, 2, 3
EXIT_MEANING = {
    EXIT_OK: "success",
    EXIT_USAGE: "usage error (an option was rejected)",
    EXIT_INPUT: "input error (the recording could not be read)",
    EXIT_RUNTIME: "runtime error (decoding, rendering or encoding failed)",
}


@dataclass
class BackendStatus:
    name: str           # cuda / opencl / metal
    compiled: bool      # the module is in this osvtool build
    available: bool     # the backend initialised on this machine
    devices: list[str] = field(default_factory=list)
    reason: str = ""    # why it is unavailable


@dataclass
class OpenOsvInfo:
    path: Path | None
    found: bool
    version: str = ""
    modules: list[str] = field(default_factory=list)
    ffmpeg_libs: str = ""
    ffmpeg_is_gpl: bool | None = None
    backends: dict[str, BackendStatus] = field(default_factory=dict)
    render_options: set[str] = field(default_factory=set)
    error: str = ""
    raw_version: str = ""

    def supports(self, option: str) -> bool:
        return option in self.render_options

    @property
    def gpu_renderers(self) -> list[str]:
        return [n for n, b in self.backends.items() if b.available]


def candidate_paths(configured: str | None = None) -> list[Path]:
    c: list[Path] = []
    if configured:
        c.append(Path(configured))
    env = os.environ.get("OSV360CRAFT_OSVTOOL")
    if env:
        c.append(Path(env))
    base = install_dir()
    c += [
        base / "external" / "OpenOSV" / "cli" / EXE,
        base / "external" / "OpenOSV" / EXE,
        base / "cli" / EXE,
        base / "vendor" / "OpenOSV" / "cli" / EXE,   # a source checkout after scripts/get_openosv.ps1
    ]
    if is_windows():
        c += [Path(r"C:\OpenOSV\cli") / EXE, Path(r"C:\OpenOSV") / EXE]
    w = pm.which("osvtool")
    if w:
        c.append(Path(w))
    return c


def locate_osvtool(configured: str | None = None) -> Path | None:
    for p in candidate_paths(configured):
        if p.is_file():
            return p.resolve()
    return None


_BACKEND_RE = re.compile(r"^\s*(cuda|opencl|metal)\s*:\s*(.*)$")


def parse_version_output(text: str) -> tuple[str, list[str], str, bool | None, dict[str, BackendStatus]]:
    version = ""
    modules: list[str] = []
    ffmpeg = ""
    gpl: bool | None = None
    backends: dict[str, BackendStatus] = {}
    for line in text.splitlines():
        if not version:
            m = re.match(r"^\s*OpenOSV\s+(\S+)", line)
            if m:
                version = m.group(1)
                continue
        m = re.match(r"^\s*modules\s*:\s*(.*)$", line)
        if m:
            modules = m.group(1).split()
            continue
        m = re.match(r"^\s*ffmpeg\s*:\s*(.*)$", line)
        if m:
            ffmpeg = m.group(1).strip()
            gpl = "GPL build" in ffmpeg if ("LGPL" in ffmpeg or "GPL" in ffmpeg) else None
            continue
        m = _BACKEND_RE.match(line)
        if m:
            name, rest = m.group(1), m.group(2).strip()
            b = backends.setdefault(name, BackendStatus(name, True, False))
            if rest.startswith("unavailable"):
                r = re.match(r"unavailable\s*\((.*)\)\s*$", rest)
                b.reason = r.group(1) if r else rest
            else:
                b.available = True
                b.devices.append(re.sub(r"^\[\d+\]\s*", "", rest))
    for mod, name in (("render_cuda", "cuda"), ("render_opencl", "opencl"), ("render_metal", "metal")):
        if name not in backends:
            backends[name] = BackendStatus(name, mod in modules, False,
                                           reason="" if mod in modules else "not compiled into this osvtool")
    return version, modules, ffmpeg, gpl, backends


def parse_render_options(help_text: str) -> set[str]:
    """Every ``--option`` (including ``--no-x`` negations) named in ``render --help``."""
    return set(re.findall(r"(?<![\w-])(--[a-z0-9][a-z0-9-]*)", help_text))


def inspect(path: Path | None) -> OpenOsvInfo:
    if path is None:
        return OpenOsvInfo(None, False, error="osvtool was not found")
    r = pm.run([path, "--version"], timeout=60)
    if not r.ok:
        msg = r.error or r.stderr.strip() or f"exit code {r.returncode}"
        if is_windows() and r.returncode in (-1073741515, 3221225781):
            msg = ("osvtool could not start: a required DLL is missing. Install the Microsoft Visual C++ "
                   "2015-2022 Redistributable (x64) and keep the FFmpeg DLLs from the OpenOSV zip beside osvtool.exe.")
        return OpenOsvInfo(path, False, error=f"osvtool --version failed: {msg}", raw_version=r.stdout + r.stderr)
    version, modules, ffmpeg, gpl, backends = parse_version_output(r.stdout)
    info = OpenOsvInfo(path, True, version, modules, ffmpeg, gpl, backends, raw_version=r.stdout)
    h = pm.run([path, "render", "--help"], timeout=30)
    if h.ok or h.stdout:
        info.render_options = parse_render_options(h.stdout)
    if not info.render_options:
        log.warning("Could not read osvtool render --help; option detection disabled")
    log.info("OpenOSV %s at %s; renderers available: %s", version, path, info.gpu_renderers or ["cpu"])
    return info

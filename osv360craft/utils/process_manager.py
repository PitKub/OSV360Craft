"""Small, safe wrappers around child processes.

* Arguments are always passed as a list (never through a shell).
* On Windows no console window flashes up.
* Every call has a timeout so a hung tool cannot hang the caller.
"""

from __future__ import annotations

import ntpath
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from osv360craft.utils.logging import get_logger

log = get_logger(__name__)

CREATE_NO_WINDOW = 0x08000000


def popen_flags() -> dict:
    if sys.platform == "win32":
        return {"creationflags": CREATE_NO_WINDOW}
    return {}


@dataclass
class RunResult:
    args: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out and not self.error


_WIN_PATH = re.compile(r"^(//|\\\\|[A-Za-z]:[/\\])")


def native_args(args: Sequence[str | Path], platform: str | None = None) -> list[str]:
    """Arguments for a child process, with Windows paths in native form.

    OpenOSV's osvtool parses its command line with CLI11, which on Windows
    treats any argument starting with '/' as a Windows-style option. Qt's file
    dialogs return forward-slash paths, so a recording on a network share,
    '//NAS/footage/CAM_0001.OSV', reached osvtool as an option and it failed
    with 'file is required'. Converting to '\\\\NAS\\footage\\CAM_0001.OSV'
    (and 'C:/x' to 'C:\\x') fixes this for every tool.
    """
    plat = platform or sys.platform
    out = [str(a) for a in args]
    if plat != "win32":
        return out
    return [ntpath.normpath(a) if _WIN_PATH.match(a) else a for a in out]


def run(args: Sequence[str | Path], timeout: float = 30.0, cwd: str | Path | None = None,
        env: dict | None = None) -> RunResult:
    argv = native_args(args)
    try:
        cp = subprocess.run(
            argv, capture_output=True, timeout=timeout, cwd=cwd, env=env,
            stdin=subprocess.DEVNULL, **popen_flags(),
        )
        return RunResult(argv, cp.returncode, cp.stdout.decode("utf-8", "replace"),
                         cp.stderr.decode("utf-8", "replace"))
    except subprocess.TimeoutExpired as e:
        log.warning("Timed out after %.0f s: %s", timeout, argv[0])
        out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else ""
        err = (e.stderr or b"").decode("utf-8", "replace") if isinstance(e.stderr, bytes) else ""
        return RunResult(argv, None, out, err, timed_out=True)
    except (OSError, ValueError) as e:
        return RunResult(argv, None, "", "", error=str(e))


def which(name: str) -> str | None:
    return shutil.which(name)


def powershell_json(command: str, timeout: float = 20.0):
    """Run a PowerShell snippet that ends in ConvertTo-Json; returns parsed JSON or None."""
    import json

    if sys.platform != "win32":
        return None
    exe = which("powershell.exe") or which("pwsh.exe")
    if not exe:
        return None
    r = run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            timeout=timeout)
    if not r.ok or not r.stdout.strip():
        log.debug("PowerShell query failed: %s %s", command[:60], r.stderr[:200])
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None

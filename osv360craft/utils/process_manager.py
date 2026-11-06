"""Small, safe wrappers around child processes.

* Arguments are always passed as a list (never through a shell).
* On Windows no console window flashes up.
* Every call has a timeout so a hung tool cannot hang the caller.
"""

from __future__ import annotations

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


def run(args: Sequence[str | Path], timeout: float = 30.0, cwd: str | Path | None = None,
        env: dict | None = None) -> RunResult:
    argv = [str(a) for a in args]
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

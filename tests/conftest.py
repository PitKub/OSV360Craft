import hashlib
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
DATA = Path(__file__).parent / "data"

# Real osvtool for integration tests: OSV360CRAFT_TEST_OSVTOOL, else none.
REAL_OSVTOOL = os.environ.get("OSV360CRAFT_TEST_OSVTOOL") or None
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("OSV360CRAFT_DATA_DIR", str(tmp_path / "appdata"))
    yield


@pytest.fixture
def fake_osvtool(tmp_path, monkeypatch) -> str:
    """An executable wrapper around tests/fake_osvtool.py (sh on POSIX, .cmd on Windows)."""
    script = Path(__file__).parent / "fake_osvtool.py"
    if sys.platform == "win32":
        w = tmp_path / "osvtool.cmd"
        w.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        w = tmp_path / "osvtool"
        w.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        w.chmod(w.stat().st_mode | stat.S_IEXEC)
    if REAL_OSVTOOL:
        monkeypatch.setenv("FAKE_REAL_OSVTOOL", REAL_OSVTOOL)
    return str(w)


@pytest.fixture
def fake_osv(tmp_path) -> Path:
    """A small MP4 shaped like an .OSV: two HEVC 10-bit video tracks (one per lens) and AAC audio."""
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg not available")
    p = tmp_path / "src" / "CAM_0001.OSV"
    p.parent.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x64:r=30", "-f", "lavfi", "-i",
                    "testsrc=s=64x64:r=30", "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000",
                    "-t", "0.4", "-map", "0:v", "-map", "1:v", "-map", "2:a", "-c:v", "libx265",
                    "-x265-params", "log-level=none", "-pix_fmt", "yuv420p10le", "-c:a", "aac", "-f", "mp4", str(p)],
                   check=True)
    return p


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()

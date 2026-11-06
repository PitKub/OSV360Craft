"""Per-user locations. Nothing is written next to the executable, so the
application runs without administrator rights."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from osv360craft import APP_ID


def is_windows() -> bool:
    return sys.platform == "win32"


def app_root() -> Path:
    """Folder holding main.py (source run) or the unpacked bundle (PyInstaller)."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def is_compiled() -> bool:
    """True in a Nuitka build (which sets __compiled__ in every module) or a PyInstaller build."""
    return "__compiled__" in globals() or bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """Folder of OSV360Craft.exe (or main.py), where the ``external`` folder sits.

    A Nuitka onefile build runs from a temporary extraction folder; sys.argv[0]
    still names the EXE the user started."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    if "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return app_root()


def user_data_dir() -> Path:
    override = os.environ.get("OSV360CRAFT_DATA_DIR")
    if override:
        base = Path(override)
    elif is_windows():
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / APP_ID
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / APP_ID
    base.mkdir(parents=True, exist_ok=True)
    return base


def logs_dir() -> Path:
    p = user_data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def cache_dir() -> Path:
    p = user_data_dir() / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_defaults_dir() -> Path:
    return app_root() / "config"

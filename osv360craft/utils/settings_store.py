"""Persistent user settings: defaults from config/settings.json, the user's
own values in the data folder. A damaged user file is replaced by the
defaults (and kept aside as .bad), never trusted."""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

from osv360craft.utils.logging import get_logger
from osv360craft.utils.paths import config_defaults_dir, user_data_dir

log = get_logger(__name__)


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class SettingsStore:
    def __init__(self, user_file: Path | None = None, defaults_file: Path | None = None) -> None:
        self.defaults_file = defaults_file or config_defaults_dir() / "settings.json"
        self.user_file = user_file or user_data_dir() / "settings.json"
        self._lock = threading.Lock()
        self.defaults = self._load(self.defaults_file) or {}
        user = self._load(self.user_file, quarantine=True) or {}
        self.data = _deep_merge(self.defaults, user)

    @staticmethod
    def _load(path: Path, quarantine: bool = False) -> dict | None:
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else None
        except (OSError, ValueError) as e:
            log.warning("Settings file %s unreadable (%s); using defaults", path, e)
            if quarantine:
                try:
                    path.replace(path.with_suffix(".bad"))
                except OSError:
                    pass
            return None

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return copy.deepcopy(node)

    def set(self, dotted: str, value: Any, save: bool = True) -> None:
        with self._lock:
            parts = dotted.split(".")
            node = self.data
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        if save:
            self.save()

    def save(self) -> None:
        with self._lock:
            try:
                atomic_write_json(self.user_file, self.data)
            except OSError as e:
                log.error("Could not save settings: %s", e)

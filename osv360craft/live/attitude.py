"""Gyro levelling for the real-time preview, reproducing OpenOSV 0.6.4's chain.

Source of the numbers (read in OpenOSV's source, not guessed):

* per-frame orientation: ``camera_attitude`` (equal to IMU batch entry 4, the one at
  the frame timestamp - AttitudeTrack.h ``batchAnchorIndex = 4``), exported by
  ``osvtool extract <clip> --imu out.csv`` as att_w, att_x, att_y, att_z = stored f0..f3;
* default reading XYZW / world->body / rig axes (AttitudeTrack.cpp worldFromBodyQuat):
  ``worldFromMounted = (f3; -f1, f2, f0)``;
* product mount (Stabilization.cpp productMount): identity for the Osmo 360, and for a clip
  read in the Avata 360 numbering (the Osmo 360 II, as OpenOSV reports) the frame with
  columns (-X, -Z, -Y); ``worldFromBody = worldFromMounted * productMount^T``;
* levelling mount (levellingMount): identity unless the body's +Y (lens axis) is closer
  to vertical than 45 degrees on average over the clip;
* world up +Z;
* HorizonLock (stabilizationBodyFromWorld): ``bodyFromView = mount * rWm^T * basis * Rz(yaw)``
  with ``basis`` the levelled frame; the yaw kept equals the body's own, so this is
  ``mount * rWm^T * basis`` with ``basis`` built from the current heading;
* Full (direction lock): ``bodyFromView = rBw * rRef * mount``, rRef = frame 0;
* Off: ``bodyFromView = mount``.

The vibration low-pass and RockSteady smoothing are not reproduced (a preview levels every
frame, so heading shake stays visible); the exact OpenOSV frame on pause has them.
"""

from __future__ import annotations

import csv
import math
import os
import tempfile
from pathlib import Path

from osv360craft.utils import process_manager as pm

Mat = list[float]  # 3x3 row-major
IDENTITY: Mat = [1, 0, 0, 0, 1, 0, 0, 0, 1]
AVATA_PRODUCT_MOUNT: Mat = [-1, 0, 0, 0, 0, -1, 0, -1, 0]   # columns (-X, -Z, -Y)
COS45 = 0.70710678118654752


def mul(a: Mat, b: Mat) -> Mat:
    return [sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3)) for r in range(3) for c in range(3)]


def transpose(a: Mat) -> Mat:
    return [a[c * 3 + r] for r in range(3) for c in range(3)]


def apply(a: Mat, v: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(a[r * 3] * v[0] + a[r * 3 + 1] * v[1] + a[r * 3 + 2] * v[2] for r in range(3))  # type: ignore[return-value]


def columns(c0, c1, c2) -> Mat:
    return [c0[0], c1[0], c2[0], c0[1], c1[1], c2[1], c0[2], c1[2], c2[2]]


def quat_matrix(w: float, x: float, y: float, z: float) -> Mat:
    n = math.sqrt(w * w + x * x + y * y + z * z)
    if not math.isfinite(n) or n < 1e-12:
        return list(IDENTITY)
    w, x, y, z = w / n, x / n, y / n, z / n
    return [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
            2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
            2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]


def world_from_mounted(f0: float, f1: float, f2: float, f3: float) -> Mat:
    """OpenOSV's default attitude reading of the stored floats f0..f3."""
    return quat_matrix(f3, -f1, f2, f0)


def _norm(v):
    n = math.sqrt(sum(c * c for c in v))
    return tuple(c / n for c in v) if n > 1e-12 else None


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def horizon_basis(up, forward_hint) -> Mat | None:
    """Columns (right, forward, up) with forward the horizontal part of the hint."""
    d = sum(f * u for f, u in zip(forward_hint, up))
    fwd = _norm(tuple(f - u * d for f, u in zip(forward_hint, up)))
    if fwd is None:
        return None
    right = _norm(_cross(fwd, up))
    return columns(right, fwd, up)


def levelling_mount(world_from_body: list[Mat], up=(0.0, 0.0, 1.0)) -> Mat:
    if not world_from_body:
        return list(IDENTITY)
    cs = [sum(a * b for a, b in zip(apply(w, (0.0, 1.0, 0.0)), up)) for w in world_from_body]
    if sum(abs(c) for c in cs) / len(cs) <= COS45:
        return list(IDENTITY)
    if sum(cs) >= 0:
        return columns((1, 0, 0), (0, 0, -1), (0, 1, 0))
    return columns((-1, 0, 0), (0, 0, -1), (0, -1, 0))


class Attitude:
    """Per-frame bodyFromView rotations for the preview shader."""

    def __init__(self, world_from_body: list[Mat], up=(0.0, 0.0, 1.0)) -> None:
        self.wfb = world_from_body
        self.up = up
        self.mount = levelling_mount(world_from_body, up)

    def __len__(self) -> int:
        return len(self.wfb)

    def body_from_view(self, frame: int, mode: str | None) -> Mat:
        """``mode`` as osvtool's --stab: off | horizon | full | smooth | smooth-horizon | None (OpenOSV
        default = RockSteady + horizon, levelled here per frame)."""
        if not self.wfb:
            return list(IDENTITY)
        i = max(0, min(frame, len(self.wfb) - 1))
        r_wb = self.wfb[i]
        if mode in ("off", "smooth"):
            return list(self.mount)
        if mode == "full":
            return mul(mul(transpose(r_wb), self.wfb[0]), self.mount)
        r_wm = mul(r_wb, self.mount)
        basis = horizon_basis(self.up, apply(r_wm, (0.0, 1.0, 0.0))) or \
            horizon_basis(self.up, apply(r_wm, (0.0, 0.0, 1.0)))
        if basis is None:
            return list(self.mount)
        return mul(self.mount, mul(transpose(r_wm), basis))


def load(osvtool: str, clip: str, avata_numbering: bool, timeout: float = 120) -> Attitude | None:
    """Read the clip's per-frame attitude through ``osvtool extract --imu`` (a small temporary CSV
    in the system temp folder, deleted straight away). None when the clip has no attitude."""
    fd, tmp = tempfile.mkstemp(prefix="osv360craft_imu_", suffix=".csv")
    os.close(fd)
    try:
        r = pm.run([osvtool, "extract", clip, "--imu", tmp], timeout=timeout)
        if not r.ok or not Path(tmp).exists():
            return None
        mounted_from_body = transpose(AVATA_PRODUCT_MOUNT) if avata_numbering else list(IDENTITY)
        by_frame: dict[int, Mat] = {}
        with open(tmp, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    n = int(row["frame"])
                    q = [float(row[k]) for k in ("att_w", "att_x", "att_y", "att_z")]
                except (KeyError, ValueError):
                    continue
                by_frame[n] = mul(world_from_mounted(*q), mounted_from_body)
        if not by_frame:
            return None
        # a frame without metadata repeats the previous one (as OpenOSV's perFrame does)
        wfb: list[Mat] = []
        last = by_frame[min(by_frame)]
        for n in range(max(by_frame) + 1):
            last = by_frame.get(n, last)
            wfb.append(last)
        return Attitude(wfb)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass

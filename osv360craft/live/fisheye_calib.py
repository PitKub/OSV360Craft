"""OpenOSV's lens calibration, turned into parameters for the GPU dual-fisheye preview.

Conventions are OpenOSV's (docs/GEOMETRY.md and src/osv/geom/StreamScaling.cpp, v0.6.4):

* lens frame: +z optical axis, +x image right, +y image down;
* body frame: X right, Y forward (master lens axis), Z up;
* ``camExtriQ`` = (w, x, y, z) and ``d_lens = R(q) * d_body``;
* Kannala-Brandt 5: ``r/f = theta (1 + k1 th^2 + ... + k5 th^10)``,
  ``u = cx + fx (r/f) cos(phi)``, ``v = cy + fy (r/f) sin(phi)``, on the 3840 px frame;
* a 1024 px proxy half (or a lens stream scaled to 1024) shows the full 3840 px frame
  for 8K recordings and the central 3776 px for 6K:
  ``scale = 1024 / 3840`` or ``1024 / 3776``, ``c = 512 + (c_cal - 1920) * scale``;
* usable field of view 195.18 degrees, feathered over its last 4 degrees;
* the proxy's left half is the slave lens, the right half the master lens.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

USABLE_FOV_DEG = 195.18
FEATHER_DEG = 4.0
SENSOR = 3840.0
CROP_6K = 3776.0
HALF = 1024.0


def quat_to_rows(q: dict) -> list[float]:
    """Rotation matrix of a unit quaternion (w, x, y, z), row-major."""
    w, x, y, z = (float(q.get(k, 0.0)) for k in ("w", "x", "y", "z"))
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    return [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
            2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
            2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]


# Ideal back-to-back lenses, used when a clip carries no calibration (approximate):
IDEAL_MASTER_ROWS = [1, 0, 0, 0, 0, -1, 0, 1, 0]     # lens x = body X, y = -Z (down), z = +Y
IDEAL_SLAVE_ROWS = [-1, 0, 0, 0, 0, -1, 0, -1, 0]    # facing -Y: image right is body -X


@dataclass
class LensParams:
    rows: list[float]                      # R, row-major: d_lens = R d_body
    fx: float
    fy: float
    cx: float
    cy: float
    k: list[float] = field(default_factory=lambda: [0.0] * 5)


@dataclass
class DualFisheye:
    slave: LensParams                      # left half
    master: LensParams                     # right half
    theta_max: float = math.radians(USABLE_FOV_DEG / 2)
    feather: float = math.radians(FEATHER_DEG)
    calibrated: bool = True
    note: str = ""


def proxy_scale(stream_h: int | None) -> float:
    return HALF / (SENSOR if stream_h == 3840 else CROP_6K)


def _lens(d: dict, scale: float) -> LensParams:
    k = [float(v) for v in (d.get("k") or [])][:5]
    k += [0.0] * (5 - len(k))
    return LensParams(quat_to_rows(d.get("camExtriQ") or {}), float(d["fx"]) * scale, float(d["fy"]) * scale,
                      HALF / 2 + (float(d["cx"]) - SENSOR / 2) * scale,
                      HALF / 2 + (float(d["cy"]) - SENSOR / 2) * scale, k)


def from_probe(calibration: dict | None, stream_h: int | None) -> DualFisheye:
    """Parameters from ``osvtool probe --json`` (its "calibration" object)."""
    scale = proxy_scale(stream_h)
    try:
        sl, ma = calibration["slave"], calibration["master"]
        if not (sl.get("camExtriQ") and ma.get("camExtriQ")):
            raise KeyError("camExtriQ")
        note = "8K: full 3840 px lens frame" if stream_h == 3840 else "6K/4K: 3776 px crop (OpenOSV's rule)"
        return DualFisheye(_lens(sl, scale), _lens(ma, scale), note=note)
    except (KeyError, TypeError, ValueError):
        # Equidistant lenses whose 195 degree circle fills each half: a rough but usable look.
        f = (HALF / 2) / math.radians(USABLE_FOV_DEG / 2)
        return DualFisheye(LensParams(IDEAL_SLAVE_ROWS, f, f, HALF / 2, HALF / 2),
                           LensParams(IDEAL_MASTER_ROWS, f, f, HALF / 2, HALF / 2),
                           calibrated=False, note="no calibration in the clip: ideal lenses (approximate)")


def project(lens: LensParams, d_body: tuple[float, float, float]) -> tuple[float, float, float] | None:
    """Pixel (u, v) in a 1024 px half and theta for a body direction; the shader's maths, for tests."""
    r = lens.rows
    x = r[0] * d_body[0] + r[1] * d_body[1] + r[2] * d_body[2]
    y = r[3] * d_body[0] + r[4] * d_body[1] + r[5] * d_body[2]
    z = r[6] * d_body[0] + r[7] * d_body[1] + r[8] * d_body[2]
    h = math.hypot(x, y)
    th = math.atan2(h, z)
    t2 = th * th
    k1, k2, k3, k4, k5 = lens.k
    rf = th * (1 + t2 * (k1 + t2 * (k2 + t2 * (k3 + t2 * (k4 + t2 * k5)))))
    cx, cy = (x / h, y / h) if h > 1e-9 else (0.0, 0.0)
    return lens.cx + lens.fx * rf * cx, lens.cy + lens.fy * rf * cy, th

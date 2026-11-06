"""Synthetic dual-fisheye frames with a known calibration, for testing the GPU projection.

Every pixel's colour encodes the body direction it sees (red = longitude, green = latitude
in OpenOSV's body frame), so a viewer pointed anywhere can be checked exactly."""

import math

import numpy as np

from osv360craft.live.fisheye_calib import DualFisheye


def rows_to_quat(r):
    """Rotation matrix (row-major) -> unit quaternion (Shepperd: largest-diagonal branch)."""
    m = np.array(r, float).reshape(3, 3)
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        q = ((s / 4), (m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s, (m[1, 0] - m[0, 1]) / s)
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2
        q = ((m[2, 1] - m[1, 2]) / s, s / 4, (m[0, 1] + m[1, 0]) / s, (m[0, 2] + m[2, 0]) / s)
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2
        q = ((m[0, 2] - m[2, 0]) / s, (m[0, 1] + m[1, 0]) / s, s / 4, (m[1, 2] + m[2, 1]) / s)
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2
        q = ((m[1, 0] - m[0, 1]) / s, (m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s, s / 4)
    return dict(zip("wxyz", q))


def rz(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])


def calibration():
    """Probe-style calibration on the 3840 px frame: KB5 with distortion, master turned 1 degree."""
    master = (np.array([1, 0, 0, 0, 0, -1, 0, 1, 0], float).reshape(3, 3) @ rz(1.0)).ravel()
    slave = np.array([-1, 0, 0, 0, 0, -1, 0, -1, 0], float)
    lens = {"fx": 1129.0, "fy": 1131.0, "cx": 1925.0, "cy": 1912.0, "k": [-0.012, 0.0025, -0.0004, 0.0, 0.0],
            "width": 3840, "height": 3840}
    return {"slave": {**lens, "camExtriQ": rows_to_quat(slave)},
            "master": {**lens, "cx": 1916.0, "cy": 1929.0, "camExtriQ": rows_to_quat(master)}}


def encode(d_body):
    lon = np.arctan2(d_body[..., 0], d_body[..., 1])           # 0 = forward (+Y), +90 = right (+X)
    lat = np.arcsin(np.clip(d_body[..., 2], -1, 1))
    return (lon + np.pi) / (2 * np.pi), (lat + np.pi / 2) / np.pi


def render(fe: DualFisheye, half=1024, world_from_body=None):
    """``world_from_body`` (3x3 row-major list): colours then encode WORLD directions, as if the
    camera body were held in that orientation."""
    out = np.zeros((half, 2 * half, 4), np.uint8)
    out[..., 3] = 255
    for i, lens in enumerate((fe.slave, fe.master)):
        ys, xs = np.mgrid[0:half, 0:half].astype(float) + 0.5
        dx, dy = (xs - lens.cx) / lens.fx, (ys - lens.cy) / lens.fy
        rf = np.hypot(dx, dy)
        th_tab = np.linspace(0, math.radians(110), 4000)
        t2 = th_tab ** 2
        k1, k2, k3, k4, k5 = lens.k
        rf_tab = th_tab * (1 + t2 * (k1 + t2 * (k2 + t2 * (k3 + t2 * (k4 + t2 * k5)))))
        th = np.interp(rf, rf_tab, th_tab)
        phi = np.arctan2(dy, dx)
        d_lens = np.stack([np.sin(th) * np.cos(phi), np.sin(th) * np.sin(phi), np.cos(th)], -1)
        R = np.array(lens.rows, float).reshape(3, 3)
        d_body = d_lens @ R                                      # R^T d_lens, row vectors
        if world_from_body is not None:
            d_body = d_body @ np.array(world_from_body, float).reshape(3, 3).T
        u, v = encode(d_body)
        ok = th < fe.theta_max
        out[..., i * half:(i + 1) * half, 0] = np.where(ok, (u * 255).round(), 0)
        out[..., i * half:(i + 1) * half, 1] = np.where(ok, (v * 255).round(), 0)
        out[..., i * half:(i + 1) * half, 2] = np.where(ok, 128, 0)
    return out


def view_dir_body(yaw, pitch):
    """Centre ray of the viewer at (yaw, pitch), in body axes (viewer: -Z forward, +Y up)."""
    y, p = math.radians(yaw), math.radians(pitch)
    d_view = np.array([-math.cos(p) * math.sin(y), math.sin(p), -math.cos(p) * math.cos(y)])
    return np.array([d_view[0], -d_view[2], d_view[1]])


def expected_colour(yaw, pitch):
    u, v = encode(view_dir_body(yaw, pitch))
    return float(u) * 255, float(v) * 255

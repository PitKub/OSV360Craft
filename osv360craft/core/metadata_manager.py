"""Independent reader for 360-degree metadata in MP4 / MOV files, and a
lossless in-place edit of the initial viewing direction.

This parser is deliberately separate from OpenOSV (which writes the tags)
so validation does not trust the writer. It reads:

* Spherical Video V1: the ``uuid`` box ffcc8263-f855-4a93-8814-587a02521fdd
  holding GSpherical XML;
* Spherical Video V2: ``st3d`` and ``sv3d`` (``proj`` -> ``prhd`` pose and
  ``equi`` projection) inside the video sample entry.

``set_initial_pose`` rewrites the three 16.16 fixed-point values of an
existing ``prhd`` box. The box keeps its size, so no offset in the file
moves and no media byte is touched: the video is not re-encoded.
"""

from __future__ import annotations

import os
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

V1_UUID = bytes.fromhex("ffcc8263f8554a938814587a02521fdd")
CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl", b"sv3d", b"proj", b"edts", b"udta"}
SAMPLE_ENTRIES = {b"hvc1", b"hev1", b"avc1", b"avc3", b"apch", b"apcn", b"apcs", b"apco", b"ap4h", b"mp4v"}


@dataclass
class Box:
    type: bytes
    offset: int      # of the box header
    size: int
    header: int      # header length (8 or 16)

    @property
    def payload(self) -> int:
        return self.offset + self.header

    @property
    def end(self) -> int:
        return self.offset + self.size


@dataclass
class SphericalInfo:
    v1: bool = False
    v1_projection: str | None = None
    v1_stitched: bool | None = None
    v1_xml: str = ""
    v2_st3d: bool = False
    v2_stereo_mode: int | None = None
    v2_sv3d: bool = False
    v2_projection: str | None = None   # equirectangular / cubemap / mesh
    pose: tuple[float, float, float] | None = None  # yaw, pitch, roll (degrees)
    prhd_offset: int | None = None
    video_entry: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def is_equirect_360(self) -> bool:
        return (self.v2_sv3d and self.v2_projection == "equirectangular") or \
            (self.v1 and (self.v1_projection or "").lower() == "equirectangular")


def _iter_boxes(f, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return
        size, typ = struct.unpack(">I4s", hdr)
        header = 8
        if size == 1:
            ext = f.read(8)
            if len(ext) < 8:
                return
            size = struct.unpack(">Q", ext)[0]
            header = 16
        elif size == 0:
            size = end - pos
        if typ == b"uuid":
            header += 0  # usertype is part of the payload for our purposes
        if size < header or pos + size > end:
            return
        yield Box(typ, pos, size, header)
        pos += size


def _walk(f, start: int, end: int, path: tuple, out: list):
    for b in _iter_boxes(f, start, end):
        out.append((path + (b.type,), b))
        if b.type in CONTAINERS:
            _walk(f, b.payload, b.end, path + (b.type,), out)
        elif b.type == b"stsd":
            # full box (4) + entry count (4), then sample entries
            _walk(f, b.payload + 8, b.end, path + (b.type,), out)
        elif b.type in SAMPLE_ENTRIES:
            # visual sample entry header is 78 bytes after the box header
            _walk(f, b.payload + 78, b.end, path + (b.type,), out)


def read_spherical(path: str | Path) -> SphericalInfo:
    info = SphericalInfo()
    path = Path(path)
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            boxes: list = []
            _walk(f, 0, size, (), boxes)
            for p, b in boxes:
                if b.type in SAMPLE_ENTRIES and b"trak" in p and info.video_entry is None:
                    info.video_entry = b.type.decode("ascii", "replace")
                if b.type == b"uuid":
                    f.seek(b.payload)
                    if f.read(16) == V1_UUID:
                        xml = f.read(min(b.end - b.payload - 16, 65536)).decode("utf-8", "replace")
                        info.v1, info.v1_xml = True, xml
                        m = re.search(r"<GSpherical:ProjectionType>\s*([^<]+?)\s*<", xml)
                        info.v1_projection = m.group(1) if m else None
                        m = re.search(r"<GSpherical:Stitched>\s*([^<]+?)\s*<", xml)
                        info.v1_stitched = (m.group(1).lower() == "true") if m else None
                elif b.type == b"st3d":
                    f.seek(b.payload + 4)
                    mode = f.read(1)
                    info.v2_st3d = True
                    info.v2_stereo_mode = mode[0] if mode else None
                elif b.type == b"sv3d":
                    info.v2_sv3d = True
                elif b.type == b"prhd":
                    f.seek(b.payload + 4)
                    vals = f.read(12)
                    if len(vals) == 12:
                        y, pch, r = struct.unpack(">iii", vals)
                        info.pose = (y / 65536.0, pch / 65536.0, r / 65536.0)
                        info.prhd_offset = b.payload + 4
                elif b.type == b"equi" and b"proj" in p:
                    info.v2_projection = "equirectangular"
                elif b.type in (b"cbmp",) and b"proj" in p:
                    info.v2_projection = "cubemap"
                elif b.type in (b"mshp",) and b"proj" in p:
                    info.v2_projection = "mesh"
    except OSError as e:
        info.errors.append(str(e))
    return info


def _fixed(deg: float) -> int:
    return int(round(deg * 65536.0))


def set_initial_pose(path: str | Path, yaw: float, pitch: float = 0.0, roll: float = 0.0) -> SphericalInfo:
    """Set the Spherical V2 initial viewing direction without re-encoding.

    yaw in -180..180, pitch in -90..90, roll in -180..180 (degrees), as in the
    Spherical Video V2 RFC. The file must already carry an sv3d/proj/prhd box
    (OpenOSV's spherical tagging writes one). Only those 12 bytes change.
    """
    if not (-180 <= yaw <= 180 and -90 <= pitch <= 90 and -180 <= roll <= 180):
        raise ValueError("pose out of range (yaw/roll -180..180, pitch -90..90)")
    info = read_spherical(path)
    if info.prhd_offset is None:
        raise ValueError("no Spherical V2 'prhd' box to edit; tag the file as 360 video first")
    with open(path, "r+b") as f:
        f.seek(info.prhd_offset)
        f.write(struct.pack(">iii", _fixed(yaw), _fixed(pitch), _fixed(roll)))
        f.flush()
        os.fsync(f.fileno())
    return read_spherical(path)

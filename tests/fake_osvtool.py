#!/usr/bin/env python3
"""A stand-in for OpenOSV's osvtool, for tests without real Osmo 360 footage.

It follows osvtool's documented contract - probe JSON schema openosv.probe/1,
the render progress line "  N/T frames  F fps  (device)" on stderr, "error:"
lines, exit codes 0/1/2/3, %06d image patterns - and produces real media with
FFmpeg (10-bit HEVC, 16-bit TIFF/PNG, source audio copied). 360 tagging is
delegated to the real osvtool given in FAKE_REAL_OSVTOOL; tests that need
the tags are skipped without it.

Environment switches:
  FAKE_FAIL_DEVICE=cuda      render with that device exits 3 before any frame
  FAKE_COLOR=DLogM           colour mode the probe reports (default DLogM)
  FAKE_FRAMES / FAKE_SIZE    frame count / lens size the probe reports
  FAKE_BAD_SIZE=1            render a wrong size (validation must fail)
  FAKE_SLOW=seconds          sleep per frame (for stop/cancel tests)
  FAKE_CALIB_JSON=path       probe reports this calibration (slave / master lens records)
  FAKE_STARTUP=seconds       sleep before rendering (clip open + GPU start-up)
"""

import json
import os
import subprocess
import sys
import time

FFMPEG = os.environ.get("FAKE_FFMPEG", "ffmpeg")
HELP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "osvtool-0.6.4-render-help.txt")


def err(msg, code):
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(code)


def opt(args, name, default=None):
    if name in args:
        return args[args.index(name) + 1]
    return default


def probe(args):
    path = args[0]
    if not os.path.isfile(path):
        err(f"cannot open {path}", 2)
    frames = int(os.environ.get("FAKE_FRAMES", "12"))
    lens = int(os.environ.get("FAKE_SIZE", "64"))
    doc = {"schema": "openosv.probe/1", "path": path, "frameCount": frames,
           "fps": {"num": 30, "den": 1, "value": 30.0}, "durationSeconds": frames / 30,
           "streamW": lens, "streamH": lens, "mode": "K6", "colorModeName": os.environ.get("FAKE_COLOR", "DLogM"),
           "hasAudio": True, "audio": {"sampleRate": 48000, "channels": 2, "sampleCount": 48000 * frames // 30},
           "isLrf": False, "clip": {"header": {"productName": "Osmo 360 (fake)"}}, "calibration": (json.loads(open(os.environ["FAKE_CALIB_JSON"]).read()) if os.environ.get("FAKE_CALIB_JSON")
                           else {"slots": 24}),
           "timeline": None, "warnings": []}
    if "--json" in args and opt(args, "--json") == "-":
        sys.stdout.write(json.dumps(doc))
    sys.exit(0)


def render(args):
    src = args[0]
    if not os.path.isfile(src):
        err("cannot open input", 2)
    out = opt(args, "--out")
    if not out:
        err("--out is required", 1)
    device = opt(args, "--device", "auto")
    if device == "auto":
        device = "cpu"
    if os.environ.get("FAKE_FAIL_DEVICE") == device:
        sys.stderr.write("[12:00:00.000] [osv] [error] CUDA: no device\n")
        err("renderer initialisation failed", 3)
    color = opt(args, "--color", "pq")
    time.sleep(float(os.environ.get("FAKE_STARTUP", "0")))  # osvtool opening an 8K clip takes seconds
    frames_total = int(os.environ.get("FAKE_FRAMES", "12"))
    if "--frame" in args:
        a = b = int(opt(args, "--frame"))
    elif "--range" in args:
        a, b = (int(x) for x in opt(args, "--range").split("-"))
    else:
        a, b = 0, frames_total - 1
    if b >= frames_total:
        err(f"frame range {a}-{b} outside 0-{frames_total - 1}", 1)
    lens = int(os.environ.get("FAKE_SIZE", "64"))
    size = opt(args, "--size") or f"{2 * lens}x{lens}"
    if os.environ.get("FAKE_BAD_SIZE"):
        w, h = (int(x) for x in size.split("x"))
        size = f"{w}x{h + 2}"
    n = b - a + 1
    is_video = out.lower().endswith((".mp4", ".mov"))
    if is_video and color not in ("pq", "hlg", "709"):
        err("video output needs --color pq|hlg|709", 1)
    slow = float(os.environ.get("FAKE_SLOW", "0"))
    t0 = time.time()
    for i in range(1, n + 1):
        if slow:
            time.sleep(slow)
        if i % 10 == 0 or i == n:
            fps = i / max(time.time() - t0, 1e-3)
            sys.stderr.write(f"  {i}/{n} frames  {fps:.1f} fps  ({device})\n")
            sys.stderr.flush()
    if is_video:
        codec = opt(args, "--codec", "hevc_nvenc")
        tags = {"709": ("bt709", "bt709", "bt709"), "pq": ("bt2020", "smpte2084", "bt2020nc"),
                "hlg": ("bt2020", "arib-std-b67", "bt2020nc")}[color]
        cmd = [opt(args, "--ffmpeg") or FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i",
               f"testsrc2=s={size}:r=30"]
        if "--no-audio" not in args:
            cmd += ["-i", src, "-map", "0:v", "-map", "1:a?", "-c:a", "copy", "-shortest"]
        cmd += ["-frames:v", str(n), "-c:v", codec, "-pix_fmt", "yuv420p10le", "-color_primaries", tags[0], "-color_trc", tags[1],
                "-colorspace", tags[2], "-tag:v", "hvc1"]
        if codec == "libx265":
            cmd += ["-x265-params", "log-level=none"]
        cmd.append(out)
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            err("ffmpeg: " + r.stderr.decode(errors="replace")[-200:], 3)
        if "--mode" in args and opt(args, "--mode") == "equirect" and "--no-spherical-metadata" not in args:
            real = os.environ.get("FAKE_REAL_OSVTOOL")
            if real:
                subprocess.run([real, "spherical", out], capture_output=True, check=True)
    else:
        ext = os.path.splitext(out)[1].lower()
        pix = "rgb48le" if ext in (".tif", ".tiff") else "rgb48be"
        if "%" in out:
            cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30", "-frames:v", str(n),
                   "-start_number", str(a), "-pix_fmt", pix, out]
        else:
            cmd = [FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30", "-frames:v", "1",
                   "-pix_fmt", pix, "-update", "1", out]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode != 0:
            err("image write failed: " + r.stderr.decode(errors="replace")[-200:], 3)
    sys.exit(0)


def main():
    argv = sys.argv[1:]
    if argv == ["--version"]:
        print("OpenOSV 0.6.4\n  modules : core container meta color video geom render_cpu render_cuda io\n"
              "  ffmpeg  : avcodec 62 (LGPL)\n  cuda    : Fake RTX 4090")
        return 0
    if not argv:
        return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "render" and rest == ["--help"]:
        sys.stdout.write(open(HELP, encoding="utf-8").read())
        return 0
    if cmd == "probe":
        probe(rest)
    if cmd == "render":
        render(rest)
    if cmd == "spherical":
        real = os.environ.get("FAKE_REAL_OSVTOOL")
        if real:
            return subprocess.run([real, "spherical", *rest]).returncode
        err("spherical tagging needs FAKE_REAL_OSVTOOL", 3)
    if cmd == "extract" and "--imu" in rest:
        # osvtool's per-frame CSV; stored floats (0, 0, 0, 1) read as the identity attitude.
        # Frame 3 is missing, as when a metadata sample fails to decode.
        with open(opt(rest, "--imu"), "w", encoding="utf-8") as f:
            f.write("frame,seq,timestamp_us,att_w,att_x,att_y,att_z,acc_x,acc_y,acc_z,iso,exposure_num,"
                    "exposure_den,wb_cct,sensor_temp\n")
            for i in range(int(os.environ.get("FAKE_FRAMES", "12"))):
                if i != 3:
                    f.write(f"{i},{2 * i},{i * 33333},0,0,0,1,0,0,9.8,100,1,60,5600,40\n")
        return 0
    if cmd == "extract":
        out = opt(rest, "--audio")
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", rest[0], "-map", "0:a:0", "-c", "copy", out])
        return r.returncode
    return 1


if __name__ == "__main__":
    sys.exit(main())

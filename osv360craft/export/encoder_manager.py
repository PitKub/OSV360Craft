"""FFmpeg command lines for the steps OpenOSV does not perform itself:

* encoding the 16-bit TIFF chunks of a D-Log M passthrough render;
* joining encoded chunks and copying the source audio in, without re-encoding;
* converting an HEVC master to H.264 or ProRes.

Rate control mirrors osvtool's own for each encoder family, and 10-bit is
kept end to end (yuv420p10le, or yuv422p10le for ProRes).
"""

from __future__ import annotations

from pathlib import Path

from osv360craft.export.metadata_validator import EXPECTED_TAGS


def video_quality_args(encoder: str, crf: int, family: str, bitrate_mbps: float | None = None) -> list[str]:
    if family == "prores":
        return ["-profile:v", "3", "-vendor", "apl0", "-pix_fmt", "yuv422p10le"]
    if bitrate_mbps and family == "h264":
        b = f"{bitrate_mbps:g}M"
        rc = ["-b:v", b, "-maxrate", b, "-bufsize", f"{bitrate_mbps * 2:g}M"]
    else:
        rc = None
    if "nvenc" in encoder:
        prof = "main10" if family == "hevc" else "high10"
        return ["-pix_fmt", "yuv420p10le", "-preset", "p5", "-profile:v", prof] + \
            (rc or ["-rc", "vbr", "-cq", str(crf), "-b:v", "0"])
    if "amf" in encoder:
        q = str(max(0, min(51, crf)))
        return ["-pix_fmt", "yuv420p10le"] + (rc or ["-rc", "cqp", "-qp_i", q, "-qp_p", q])
    if "qsv" in encoder:
        return ["-pix_fmt", "p010le"] + (rc or ["-global_quality", str(crf)])
    if encoder == "libx265":
        return ["-pix_fmt", "yuv420p10le", "-preset", "medium", "-x265-params", "profile=main10:log-level=error"] + \
            (rc or ["-crf", str(crf)])
    if encoder == "libx264":
        return ["-pix_fmt", "yuv420p10le", "-profile:v", "high10", "-preset", "medium"] + (rc or ["-crf", str(crf)])
    return ["-pix_fmt", "yuv420p10le"] + (rc or ["-crf", str(crf)])


def colour_args(colour: str) -> list[str]:
    """Container colour signalling. D-Log M is tagged BT.709 as the camera does in the original."""
    prim, trc, space = EXPECTED_TAGS.get(colour, EXPECTED_TAGS["709"])
    return ["-color_primaries", prim, "-color_trc", trc, "-colorspace", space, "-color_range", "tv"]


def tag_args(family: str) -> list[str]:
    if family == "hevc":
        return ["-tag:v", "hvc1"]
    if family == "h264":
        return ["-tag:v", "avc1"]
    return []


def encode_tiff_chunk(ffmpeg: str, pattern: str, start_number: int, count: int, fps_num: int, fps_den: int,
                      out: Path, encoder: str, crf: int, family: str, colour: str,
                      bitrate_mbps: float | None = None) -> list[str]:
    """RGB48 TIFF frames -> one encoded segment (no audio). The RGB -> YCbCr matrix is BT.709,
    matching the tags, computed with accurate rounding from the 16-bit source."""
    matrix = "bt2020nc" if colour in ("pq", "hlg") else "bt709"
    return [ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
            "-framerate", f"{fps_num}/{fps_den}", "-start_number", str(start_number), "-i", pattern,
            "-frames:v", str(count),
            "-vf", f"scale=out_color_matrix={matrix}:out_range=tv:flags=accurate_rnd+full_chroma_int",
            "-c:v", encoder, *video_quality_args(encoder, crf, family, bitrate_mbps), *colour_args(colour),
            *tag_args(family), "-an", str(out)]


def concat_and_mux(ffmpeg: str, list_file: Path, audio_source: str | None, out: Path, family: str) -> list[str]:
    """Join segments with stream copy and copy the source's first audio track (no re-encode)."""
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file)]
    if audio_source:
        args += ["-i", audio_source, "-map", "0:v:0", "-map", "1:a:0?", "-c:a", "copy", "-shortest"]
    else:
        args += ["-map", "0:v:0"]
    args += ["-c:v", "copy", *tag_args(family)]
    if out.suffix.lower() == ".mp4":
        args += ["-movflags", "+write_colr+faststart"]
    return args + [str(out)]


def transcode(ffmpeg: str, master: Path, out: Path, encoder: str, crf: int, family: str, colour: str,
              bitrate_mbps: float | None = None) -> list[str]:
    args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(master), "-map", "0:v:0", "-map", "0:a?",
            "-c:v", encoder, *video_quality_args(encoder, crf, family, bitrate_mbps), *colour_args(colour),
            *tag_args(family), "-c:a", "copy"]
    if out.suffix.lower() == ".mp4":
        args += ["-movflags", "+write_colr+faststart"]
    return args + [str(out)]


def write_concat_list(segments: list[Path], list_file: Path) -> None:
    def esc(p: Path) -> str:
        return str(p.resolve()).replace("\\", "/").replace("'", "'\\''")
    list_file.write_text("".join(f"file '{esc(s)}'\n" for s in segments), encoding="utf-8")


def default_encoder(family: str, verified: dict[str, bool]) -> str | None:
    order = {"hevc": ["hevc_nvenc", "hevc_amf", "hevc_qsv", "libx265"],
             "h264": ["libx264"],  # 10-bit H.264 (High 10) is a CPU-encoder feature on most GPUs
             "prores": ["prores_ks", "prores"]}[family]
    return next((e for e in order if verified.get(e)), None)

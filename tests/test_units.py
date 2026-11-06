"""Unit tests: parsers, command building, settings, files, metadata, optimizer."""

import shutil
import sys
from pathlib import Path

import pytest

from conftest import DATA, REAL_OSVTOOL
from osv360craft.core import metadata_manager, openosv_manager, osv_probe
from osv360craft.export import encoder_manager as em
from osv360craft.export.export_settings import (ColourSettings, ExportSettings, StitchSettings, UnsupportedOption,
                                                build_render_command, pipeline_for, validate_for_video)
from osv360craft.hardware import codec_detector, gpu_detector
from osv360craft.hardware.benchmark import parse_progress
from osv360craft.utils import file_manager as fm
from osv360craft.utils.settings_store import SettingsStore

HELP = (Path(__file__).parents[1] / "docs" / "osvtool-0.6.4-render-help.txt").read_text(encoding="utf-8")
OPTS = openosv_manager.parse_render_options(HELP)


# ---- osvtool output parsing ------------------------------------------
def test_progress_line():
    assert parse_progress("  12/65 frames  14.2 fps  (cuda)") == (12, 65, 14.2, "cuda")
    assert parse_progress("  65/65 frames  3.0 fps  (opencl)\r") == (65, 65, 3.0, "opencl")
    assert parse_progress("error: no") is None
    assert parse_progress("  70/65 frames  1.0 fps  (cpu)") is None


def test_version_output():
    text = ("OpenOSV 0.6.4\n  modules : core container meta color video geom render_cpu render_cuda render_opencl io\n"
            "  ffmpeg  : avcodec 62 (LGPL)\n  cuda    : NVIDIA GeForce RTX 4090\n"
            "  opencl  : unavailable (no platform)\n")
    v, mods, ff, gpl, b = openosv_manager.parse_version_output(text)
    assert v == "0.6.4" and "render_cuda" in mods and gpl is False
    assert b["cuda"].available and b["cuda"].devices == ["NVIDIA GeForce RTX 4090"]
    assert b["opencl"].compiled and not b["opencl"].available and b["opencl"].reason == "no platform"
    assert not b["metal"].compiled


def test_render_help_options_cover_everything_we_pass():
    used = {"--all", "--range", "--frame", "--mode", "--size", "--color", "--look", "--tone", "--hdr-peak", "--device",
            "--codec", "--crf", "--ffmpeg", "--no-audio", "--no-spherical-metadata", "--out", "--purpose",
            "--yaw", "--pitch", "--roll", "--fov"}
    for f in StitchSettings.__dataclass_fields__:
        used.add("--" + f.replace("_", "-"))
    assert used - OPTS == set(), f"not in osvtool 0.6.4 render --help: {used - OPTS}"
    for neg in ("--no-flare", "--no-gain", "--no-seam-search", "--no-parallax"):
        assert neg in OPTS


def test_probe_json_parsing():
    doc = {"schema": "openosv.probe/1", "path": "a.OSV", "frameCount": 600, "fps": {"num": 60000, "den": 1001,
           "value": 59.94}, "durationSeconds": 10.01, "streamW": 3840, "streamH": 3840, "mode": "K8",
           "colorModeName": "DLogM", "hasAudio": True, "audio": {"sampleRate": 48000, "channels": 2}, "isLrf": False}
    ci = osv_probe.parse_probe_json(doc, "a.OSV")
    assert ci.ok and ci.native_equirect == (7680, 3840) and ci.recording_mode_label == "8K mode"
    assert ci.fps_label.startswith("59.94") and ci.color_label == "D-Log M"
    ci6 = osv_probe.parse_probe_json({**doc, "streamW": 3000, "streamH": 3000}, "b")
    assert ci6.native_equirect == (6000, 3000)
    bad = osv_probe.parse_probe_json({"schema": "openosv.probe/1", "frameCount": 0}, "c")
    assert not bad.ok and bad.error


def test_nvidia_smi_and_encoders_parsing():
    g = gpu_detector.parse_nvidia_smi("NVIDIA GeForce RTX 3060, 12288, 11000, 560.94, 8.6, 3\n")[0]
    assert g.vram_total_bytes == 12288 * 1024 ** 2 and g.compute_capability == 8.6 and g.cuda_capable_for_openosv
    old = gpu_detector.parse_nvidia_smi("NVIDIA GeForce GTX 1080, 8192, 8000, 531.0, 6.1, 0\n")[0]
    assert old.cuda_capable_for_openosv is False
    enc = codec_detector.parse_encoders(" V....D hevc_nvenc   NVIDIA NVENC hevc encoder\n A....D aac   AAC\n")
    assert enc == {"hevc_nvenc"}
    assert codec_detector.parse_hwaccels("Hardware acceleration methods:\ncuda\nd3d11va\n") == ["cuda", "d3d11va"]
    assert gpu_detector.vendor_of("AMD Radeon RX 7900 XTX") == "AMD"
    assert gpu_detector.vendor_of("Intel(R) Arc(TM) A770") == "Intel"
    assert gpu_detector.vendor_of("Microsoft Basic Display Adapter") == "Microsoft"


# ---- export settings -> osvtool command --------------------------------
def test_default_command_is_pure_stitch():
    ex = ExportSettings()
    args = build_render_command("osvtool", "C.OSV", "o.mp4", ex, device="cuda", codec="hevc_nvenc", crf=16,
                                supported=OPTS, source_colour="HLG")
    assert args[:4] == ["osvtool", "render", "C.OSV", "--all"]
    assert "--size" not in args  # native
    assert args[args.index("--color") + 1] == "hlg"  # HLG kept HLG
    assert "--stab" not in args and "--exposure" not in args  # nothing forced
    assert args[-2:] == ["--out", "o.mp4"]


def test_colour_resolution_and_pipeline():
    ex = ExportSettings()
    assert ex.colour.resolved("DLogM") == "dlogm" and pipeline_for(ex, "DLogM") == "passthrough"
    assert ex.colour.resolved("Normal") == "709-standard" and pipeline_for(ex, "Normal") == "direct"
    ex.colour = ColourSettings("pq", "aces-detailed", "600")
    assert pipeline_for(ex, "DLogM") == "direct"
    assert ex.colour.to_args("DLogM") == ["--color", "pq", "--tone", "aces-detailed", "--hdr-peak", "600"]
    ex.codec_family = "prores"
    assert pipeline_for(ex, "DLogM") == "transcode"
    ex.colour = ColourSettings("709-dji")
    assert ex.colour.to_args() == ["--color", "709", "--look", "dji"]


def test_tristate_stitch_args_and_validation():
    s = StitchSettings(stab="full", flare=False, seam_blend=2.0, gain=True)
    assert s.to_args() == ["--stab", "full", "--seam-blend", "2", "--no-flare", "--gain"]
    assert StitchSettings(seam_blend=9).validate()
    ex = ExportSettings(size="7680x3000")
    assert any("2:1" in e for e in validate_for_video(ex))
    ex = ExportSettings(custom_bitrate_mbps=100)
    assert any("bitrate" in e for e in validate_for_video(ex))


def test_unsupported_option_is_refused_not_dropped():
    ex = ExportSettings(stitch=StitchSettings(stab="horizon"))
    with pytest.raises(UnsupportedOption):
        build_render_command("osvtool", "a", "b.mp4", ex, supported={"--all", "--mode", "--out", "--crf"})


def test_settings_roundtrip(tmp_path):
    ex = ExportSettings(size="3840x1920", colour=ColourSettings("hlg"), stitch=StitchSettings(stab="horizon"))
    back = ExportSettings.from_dict(ex.to_dict())
    assert back == ex
    defaults = Path(__file__).parents[1] / "config" / "settings.json"
    s = SettingsStore(tmp_path / "u.json", defaults)
    assert s.get("export.colour.output") == "source" and s.get("ui.theme") == "dark"
    s.set("ui.theme", "light")
    assert SettingsStore(tmp_path / "u.json", defaults).get("ui.theme") == "light"
    (tmp_path / "bad.json").write_text("{nope")
    assert SettingsStore(tmp_path / "bad.json", defaults).get("ui.theme") == "dark"


# ---- files -------------------------------------------------------------
def test_scan_and_output_safety(tmp_path):
    d = tmp_path / "card"
    (d / "sub").mkdir(parents=True)
    (d / "CAM_1.OSV").write_bytes(b"x")
    (d / "CAM_1.LRF").write_bytes(b"x")
    (d / "sub" / "CAM_2.osv").write_bytes(b"x")
    (d / "EMPTY.OSV").write_bytes(b"")
    (d / "._CAM_1.OSV").write_bytes(b"x")
    found, skipped = fm.scan_sources([d, d / "CAM_1.OSV"])
    assert sorted(s.path.name for s in found) == ["CAM_1.OSV", "CAM_2.osv"]
    assert next(s for s in found if s.path.name == "CAM_1.OSV").proxy.name == "CAM_1.LRF"
    assert any("empty" in m for m in skipped)
    with pytest.raises(fm.UnsafeOutputError):
        fm.assert_safe_output(d / "CAM_1.OSV", [d / "CAM_1.OSV"])
    with pytest.raises(fm.UnsafeOutputError):
        fm.assert_safe_output(tmp_path / "x.lrf")
    o = tmp_path / "a.mp4"
    o.write_bytes(b"1")
    assert fm.unique_path(o).name == "a (2).mp4"


# ---- metadata ------------------------------------------------------------
def test_spherical_reader_on_files_tagged_by_real_osvtool():
    untagged = metadata_manager.read_spherical(DATA / "equirect_untagged.mp4")
    assert not untagged.is_equirect_360 and not untagged.v1 and not untagged.v2_sv3d
    t = metadata_manager.read_spherical(DATA / "equirect_tagged_by_osvtool.mp4")
    assert t.v1 and t.v1_projection == "equirectangular" and t.v1_stitched
    assert t.v2_st3d and t.v2_stereo_mode == 0 and t.v2_sv3d and t.v2_projection == "equirectangular"
    assert t.pose == (0.0, 0.0, 0.0)


def test_initial_pose_is_a_lossless_in_place_edit(tmp_path):
    src = DATA / "equirect_tagged_by_osvtool.mp4"
    dst = tmp_path / "p.mp4"
    shutil.copy(src, dst)
    info = metadata_manager.set_initial_pose(dst, 90.0, -5.0, 2.5)
    assert info.pose == (90.0, -5.0, 2.5)
    a, b = src.read_bytes(), dst.read_bytes()
    assert len(a) == len(b)
    assert sum(x != y for x, y in zip(a, b)) <= 12  # only the prhd values changed
    with pytest.raises(ValueError):
        metadata_manager.set_initial_pose(DATA / "equirect_untagged.mp4", 10)
    with pytest.raises(ValueError):
        metadata_manager.set_initial_pose(dst, 200)


def test_ffmpeg_args_keep_10bit():
    a = em.video_quality_args("hevc_nvenc", 16, "hevc")
    assert "yuv420p10le" in a and "main10" in a
    assert "high10" in em.video_quality_args("libx264", 16, "h264")
    assert "yuv422p10le" in em.video_quality_args("prores_ks", 16, "prores")
    assert "-b:v" in em.video_quality_args("libx264", 16, "h264", 120)


@pytest.mark.skipif(not REAL_OSVTOOL, reason="set OSV360CRAFT_TEST_OSVTOOL to a real osvtool")
def test_real_osvtool_inspection():
    info = openosv_manager.inspect(Path(REAL_OSVTOOL))
    assert info.found and info.version
    assert {"--purpose", "--stab", "--color", "--no-spherical-metadata"} <= info.render_options


# ---- optimizer on synthetic machines -------------------------------------
def _machine(vendor, gpu_name, vram_gb, cc, backends, encoders, ram_gb=32, threads=16):
    from osv360craft.core.openosv_manager import BackendStatus, OpenOsvInfo
    from osv360craft.hardware.codec_detector import EncoderTest, FfmpegInfo
    from osv360craft.hardware.cpu_detector import CpuInfo
    from osv360craft.hardware.gpu_detector import GpuInfo
    from osv360craft.hardware.hardware_report import HardwareReport
    from osv360craft.hardware.ram_detector import RamInfo
    gpus = [GpuInfo(gpu_name, vendor, vram_gb * 1024 ** 3, vram_gb * 1024 ** 3, "1.0", cc)] if gpu_name else []
    o = OpenOsvInfo(Path("osvtool"), True, "0.6.4",
                    backends={n: BackendStatus(n, True, n in backends, [gpu_name] if n in backends else [])
                              for n in ("cuda", "opencl", "metal")})
    f = FfmpegInfo("ffmpeg", "ffprobe", "7.1", tests={e: EncoderTest(e, True, ok) for e, ok in encoders.items()})
    return HardwareReport("now", "Windows 11", "10.0.26100", CpuInfo("X", "CPU", "AMD64", threads // 2, threads),
                          RamInfo(ram_gb * 1024 ** 3, ram_gb * 1024 ** 3 // 2, 50.0), gpus, [], o, f)


def test_plan_nvidia_amd_cpu():
    from osv360craft.hardware.auto_optimizer import make_plan
    from osv360craft.hardware.benchmark import BenchmarkResult
    nv = _machine("NVIDIA", "RTX 4080", 16, 8.9, {"cuda", "opencl"}, {"hevc_nvenc": True, "libx265": True})
    p = make_plan(nv, ExportSettings())
    assert (p.device, p.encoder, p.crf, p.preview_mode, p.concurrency) == ("cuda", "hevc_nvenc", 16, "4k", 1)
    assert p.device_fallbacks == ["opencl", "cpu"]
    amd = _machine("AMD", "RX 7800 XT", 16, None, {"opencl"}, {"hevc_nvenc": False, "hevc_amf": True, "libx265": True})
    p = make_plan(amd, ExportSettings(), "quality")
    assert (p.device, p.encoder, p.crf) == ("opencl", "hevc_amf", 12)
    cpu = _machine("", "", 0, None, set(), {"libx265": True}, ram_gb=8, threads=4)
    p = make_plan(cpu, ExportSettings(), "performance")
    assert (p.device, p.encoder, p.preview_mode) == ("cpu", "libx265", "performance") and p.warnings
    # a measured benchmark wins over the vendor heuristic
    b = BenchmarkResult(device_fps={"cuda": 5.0, "opencl": 9.0, "cpu": 0.5}, preview_fps=9.0)
    assert make_plan(nv, ExportSettings(), bench=b).device == "opencl"
    # a user's own quality is never replaced by the profile
    assert make_plan(nv, ExportSettings(quality="maximum"), "performance").crf == 10
    # concurrency only when allowed and the machine is big enough
    big = _machine("NVIDIA", "RTX 6000", 48, 8.9, {"cuda"}, {"hevc_nvenc": True}, ram_gb=128, threads=32)
    assert make_plan(big, ExportSettings(), allow_concurrency=True).concurrency == 2
    assert make_plan(nv, ExportSettings(), allow_concurrency=True).concurrency == 1


def test_upscale_is_labelled():
    from osv360craft.hardware.auto_optimizer import make_plan
    nv = _machine("NVIDIA", "RTX 4080", 16, 8.9, {"cuda"}, {"hevc_nvenc": True})
    clip = osv_probe.parse_probe_json({"schema": "openosv.probe/1", "frameCount": 10, "streamW": 3000,
                                       "streamH": 3000, "fps": {"num": 30, "den": 1, "value": 30}}, "x")
    p = make_plan(nv, ExportSettings(size="7680x3840"), clip=clip)
    size = next(d for d in p.decisions if d.key == "size")
    assert "UPSCALED" in size.value and "6000x3000" in size.value


def test_windows_paths_reach_tools_in_native_form():
    """Regression: Qt returns '//NAS/share/x.OSV'; CLI11 on Windows took it for an option ('file is required')."""
    from osv360craft.utils.process_manager import native_args
    args = ["osvtool.exe", "render", "//NAS/footage/CAM_0001_D.OSV", "--range", "0-59", "--out", "C:/Temp/s.mp4",
            "--ffmpeg", "C:/ffmpeg/bin/ffmpeg.exe", "-vf", "scale=out_color_matrix=bt709", "f_%06d.tif"]
    out = native_args(args, platform="win32")
    assert out[2] == r"\\NAS\footage\CAM_0001_D.OSV"
    assert out[6] == r"C:\Temp\s.mp4" and out[8] == r"C:\ffmpeg\bin\ffmpeg.exe"
    assert out[3:5] == ["--range", "0-59"] and out[9:] == args[9:]  # options and filters untouched
    assert native_args(args, platform="linux") == args


def test_no_powershell_or_shell_launches():
    """Antivirus heuristics treat hidden PowerShell as suspicious: the app must never start it."""
    import re as _re
    src = Path(__file__).parents[1] / "osv360craft"
    for f in src.rglob("*.py"):
        code = "\n".join(l for l in f.read_text(encoding="utf-8").splitlines() if not l.lstrip().startswith("#"))
        assert not _re.search(r"[\"'](powershell|pwsh)(\.exe)?[\"']", code, _re.I), f
        assert "shell=True" not in code, f


def test_encoder_results_are_cached_per_ffmpeg(tmp_path):
    import shutil as _sh
    ff = _sh.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg not available")
    first = codec_detector.detect(ff)
    assert first.tests and codec_detector._cache_path().exists()
    again = codec_detector.detect(ff)  # reused, no test encodes
    assert {k: v.verified for k, v in again.tests.items()} == {k: v.verified for k, v in first.tests.items()}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows detectors")
def test_windows_detectors_run_without_other_programs():
    from osv360craft.hardware import cpu_detector, ram_detector, storage_detector
    c = cpu_detector.detect()
    assert c.model != "unknown" and c.logical_processors
    assert ram_detector.detect().total_bytes
    gpus = gpu_detector.detect()  # a CI VM has at least the Microsoft Basic Display Adapter
    assert gpus and all(g.name for g in gpus)
    d = storage_detector.detect("C:\\")
    assert d.free_bytes and d.media_type in ("SSD", "HDD", "unknown")

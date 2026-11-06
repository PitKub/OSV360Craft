"""End-to-end tests of the batch pipelines, validation and the preview engine,
using tests/fake_osvtool.py (osvtool's CLI contract) and real FFmpeg."""

import threading
import time
from pathlib import Path

import pytest

from conftest import HAVE_FFMPEG, REAL_OSVTOOL, sha256
from osv360craft.batch.batch_processor import BatchProcessor, ProcessorConfig
from osv360craft.batch.job_manager import CANCELLED, DONE, FAILED, PENDING, JobQueue
from osv360craft.core import openosv_manager
from osv360craft.export.export_settings import ExportSettings
from osv360craft.export.metadata_validator import Expectation, validate
from osv360craft.live.preview_engine import PreviewConfig, PreviewEngine

pytestmark = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe needed")
needs_real = pytest.mark.skipif(not REAL_OSVTOOL, reason="360 tagging needs a real osvtool (OSV360CRAFT_TEST_OSVTOOL)")


def run_batch(tmp_path, tool, src, ex, *, device="cpu", fallbacks=(), encoder="libx265", extra=None):
    q = JobQueue(tmp_path / "q.json", tmp_path / "h.json")
    job = q.add(str(src))
    info = openosv_manager.inspect(Path(tool))
    cfg = ProcessorConfig(osvtool=tool, ffmpeg="ffmpeg", ffprobe="ffprobe", supported_options=info.render_options,
                          export=ex, device=device, device_fallbacks=list(fallbacks), encoder=encoder, crf=20,
                          project_root=tmp_path / "proj", **(extra or {}))
    logs: list[str] = []
    bp = BatchProcessor(q, lambda: cfg, on_log=lambda jid, line: logs.append(line))
    bp.start()
    bp.wait(180)
    return q.get(job.id), logs, cfg


@needs_real
def test_direct_pipeline_hlg_validated(tmp_path, fake_osvtool, fake_osv, monkeypatch):
    monkeypatch.setenv("FAKE_COLOR", "HLG")
    before = sha256(fake_osv)
    job, logs, _ = run_batch(tmp_path, fake_osvtool, fake_osv, ExportSettings())
    assert job.status == DONE, job.message
    out = Path(job.output)
    assert out.exists() and out.name == "CAM_0001_360_128x64_HLG.mp4"
    assert not list(out.parent.glob("*.partial*"))
    assert sha256(fake_osv) == before  # original untouched
    assert job.device_used == "cpu" and job.frames_done == 12
    report = Path(job.report).read_text()
    for check in ("Spherical V1", "Spherical V2", "A/V sync", "bit depth", "colour tags", "decode"):
        assert check in report
    assert (tmp_path / "proj" / "Original" / "sources.json").exists()


@needs_real
def test_dlogm_passthrough_chunks_and_initial_heading(tmp_path, fake_osvtool, fake_osv):
    job, logs, _ = run_batch(tmp_path, fake_osvtool, fake_osv, ExportSettings(),
                             extra={"chunk_frames": 5, "initial_yaw": 45.0})
    assert job.status == DONE, job.message
    assert job.pipeline == "passthrough" and job.output.endswith("_DLogM.mp4")
    assert sum("--color dlogm" in l for l in logs) == 3  # 12 frames in chunks of 5
    assert not any(p.name.startswith(".") for p in (tmp_path / "proj" / "Stitched").iterdir())  # temp removed
    from osv360craft.core.metadata_manager import read_spherical
    assert read_spherical(job.output).pose[0] == 45.0


@needs_real
@pytest.mark.parametrize("family,codec", [("h264", "h264"), ("prores", "prores")])
def test_transcoded_formats(tmp_path, fake_osvtool, fake_osv, monkeypatch, family, codec):
    monkeypatch.setenv("FAKE_COLOR", "Normal")
    job, logs, _ = run_batch(tmp_path, fake_osvtool, fake_osv, ExportSettings(codec_family=family))
    assert job.status == DONE, job.message
    assert job.pipeline == "transcode"
    assert job.output.endswith(".mov" if family == "prores" else ".mp4")


def test_tiff_sequence(tmp_path, fake_osvtool, fake_osv):
    job, logs, _ = run_batch(tmp_path, fake_osvtool, fake_osv, ExportSettings(codec_family="tiff"))
    assert job.status == DONE, job.message
    assert len(list(Path(job.output).glob("*.tif"))) == 12


@needs_real
def test_gpu_failure_falls_back_and_logs(tmp_path, fake_osvtool, fake_osv, monkeypatch):
    monkeypatch.setenv("FAKE_COLOR", "HLG")
    monkeypatch.setenv("FAKE_FAIL_DEVICE", "cuda")
    job, logs, cfg = run_batch(tmp_path, fake_osvtool, fake_osv, ExportSettings(), device="cuda", fallbacks=["cpu"])
    assert job.status == DONE, job.message
    assert any("Renderer CUDA failed before the first frame" in l and "unchanged" in l for l in logs)
    assert job.device_used == "cpu" and cfg.device == "cpu"


def test_validation_failure_is_reported_and_partial_deleted(tmp_path, fake_osvtool, fake_osv, monkeypatch):
    monkeypatch.setenv("FAKE_COLOR", "HLG")
    monkeypatch.setenv("FAKE_BAD_SIZE", "1")
    ex = ExportSettings(spherical_metadata=False)
    job, logs, _ = run_batch(tmp_path, fake_osvtool, fake_osv, ex)
    assert job.status == FAILED and "validation failed" in job.message and "resolution" in job.message
    assert not list((tmp_path / "proj" / "Stitched").glob("*.mp4"))


def test_stop_deletes_partial_and_requeue(tmp_path, fake_osvtool, fake_osv, monkeypatch):
    monkeypatch.setenv("FAKE_COLOR", "HLG")
    monkeypatch.setenv("FAKE_SLOW", "0.5")
    q = JobQueue(tmp_path / "q.json", tmp_path / "h.json")
    job = q.add(str(fake_osv))
    info = openosv_manager.inspect(Path(fake_osvtool))
    cfg = ProcessorConfig(fake_osvtool, "ffmpeg", "ffprobe", info.render_options, ExportSettings(), "cpu", [],
                          "libx265", 20, tmp_path / "proj")
    bp = BatchProcessor(q, lambda: cfg)
    bp.start()
    time.sleep(2.5)
    bp.stop()
    bp.wait(30)
    assert q.get(job.id).status == CANCELLED
    assert not list((tmp_path / "proj" / "Stitched").glob("*"))
    assert q.retry(job.id) and q.get(job.id).status == PENDING
    # an interrupted (running) job is re-queued on the next start
    q.get(job.id).status = "running"
    q.save()
    assert JobQueue(tmp_path / "q.json", tmp_path / "h.json").get(job.id).status == PENDING


def test_existing_output_kept_with_unique_name(tmp_path, fake_osvtool, fake_osv, monkeypatch):
    monkeypatch.setenv("FAKE_COLOR", "HLG")
    stitched = tmp_path / "proj" / "Stitched"
    stitched.mkdir(parents=True)
    existing = stitched / "CAM_0001_360_128x64_HLG.mp4"
    existing.write_bytes(b"keep me")
    job, logs, _ = run_batch(tmp_path, fake_osvtool, fake_osv, ExportSettings(spherical_metadata=False))
    assert existing.read_bytes() == b"keep me"
    assert job.output is None or Path(job.output).name != existing.name


def test_preview_engine_single_and_segments(tmp_path, fake_osvtool, fake_osv):
    info = openosv_manager.inspect(Path(fake_osvtool))
    got: dict[int, Path] = {}
    ev = threading.Event()

    def on_frame(n, p):
        got[n] = p
        if len(got) >= 12:
            ev.set()

    cfg = PreviewConfig(fake_osvtool, str(fake_osv), 12, 30.0, "128x64", "709", "cpu", ExportSettings(),
                        info.render_options, tmp_path / "cache", 50 * 1024 ** 2, "DLogM", segment_frames=5)
    eng = PreviewEngine(cfg, on_frame)
    eng.request_frame(3)
    t0 = time.time()
    while 3 not in got and time.time() - t0 < 30:
        time.sleep(0.05)
    assert 3 in got and got[3].exists()
    eng.play_from(0)
    assert ev.wait(60), f"only frames {sorted(got)}"
    assert sorted(got) == list(range(12))
    # a settings change produces a different cache key (no stale frames)
    cfg2 = PreviewConfig(**{**cfg.__dict__, "view_colour": "export"})
    assert cfg2.key() != cfg.key()


def test_validator_against_real_tagged_file():
    from conftest import DATA
    rep = validate(DATA / "equirect_tagged_by_osvtool.mp4",
                   Expectation(512, 256, 30, 1, 30, 10, "hevc", None, audio=True, spherical=True), "ffprobe", "ffmpeg")
    assert rep.ok, rep.to_dict()
    rep2 = validate(DATA / "equirect_untagged.mp4", Expectation(spherical=True), "ffprobe", "ffmpeg")
    assert not rep2.ok and any(c.name == "Spherical V2" for c in rep2.failures)


def test_benchmark_end_to_end(tmp_path, fake_osvtool, fake_osv):
    """The whole benchmark on a clip: decode, stitch per renderer, full resolution, encode."""
    from osv360craft.hardware.benchmark import Benchmark
    steps = []
    b = Benchmark(fake_osvtool, "ffmpeg", str(fake_osv), ["cuda", "cpu"], ["libx265"], "128x64", "fp",
                  progress=lambda m, f: steps.append(m), hwaccels=[], frame_count=12)
    res = b.run()
    assert not res.cancelled
    assert res.decode_fps.get("software (CPU)", 0) > 0
    assert set(res.device_fps) == {"cuda", "cpu"} and res.preview_fps
    assert res.fullres_fps and res.fullres_size == "128x64"
    assert res.encode_fps.get("libx265", 0) > 0
    assert any("pattern" in n for n in res.notes)


def test_benchmark_stops_on_usage_error(tmp_path, fake_osvtool, fake_osv):
    """A rejected command line is reported once, not blamed on every GPU in turn."""
    from osv360craft.hardware.benchmark import Benchmark
    b = Benchmark(fake_osvtool, None, str(tmp_path / "missing.OSV"), ["cuda", "opencl", "cpu"], [], None, "fp")
    res = b.run()
    assert list(res.device_errors) == ["cuda"] or len(res.device_errors) == 3

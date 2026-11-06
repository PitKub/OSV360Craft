"""Sequential (by default) processing of the job queue in a worker thread.

For every job:
 1. probe the clip (osvtool probe + ffprobe);
 2. check free space for the output (and temporary chunks);
 3. render through the job's pipeline (see export_settings.pipeline_for)
    into a ``.partial`` file next to the final name;
 4. validate the result (metadata_validator); only a passing file is renamed
    to its final name and reported as done;
 5. write a JSON report and the raw tool log into the project folders.

Original recordings are only read. If a GPU renderer fails before producing
a frame, the next renderer in the plan's fallback order is tried and the
reason is logged; resolution and quality are never changed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from osv360craft.batch.job_manager import (CANCELLED, DONE, FAILED, RUNNING, SKIPPED, VALIDATING,
                                           ANALYSING, Job, JobQueue)
from osv360craft.core import metadata_manager, osv_probe
from osv360craft.core.openosv_manager import EXIT_MEANING, EXIT_RUNTIME
from osv360craft.export import encoder_manager as em
from osv360craft.export.export_settings import (ExportSettings, UnsupportedOption, build_render_command,
                                                parse_size, pipeline_for, validate_for_video)
from osv360craft.export.metadata_validator import Expectation, validate
from osv360craft.hardware.benchmark import parse_progress
from osv360craft.hardware.storage_detector import estimate_output_bytes
from osv360craft.utils.file_manager import (ProjectLayout, UnsafeOutputError, assert_safe_output, free_bytes,
                                            human_bytes, sanitize_name, unique_path)
from osv360craft.utils.logging import get_logger
from osv360craft.utils.process_manager import native_args, popen_flags

log = get_logger(__name__)
COLOUR_SUFFIX = {"dlogm": "DLogM", "pq": "PQ", "hlg": "HLG", "709-dji": "709DJI", "709-standard": "709",
                 "auto": "Auto"}


@dataclass
class ProcessorConfig:
    osvtool: str
    ffmpeg: str | None
    ffprobe: str | None
    supported_options: set[str]
    export: ExportSettings
    device: str
    device_fallbacks: list[str]
    encoder: str | None
    crf: int
    project_root: Path
    existing: str = "unique"            # unique | skip | replace
    save_audio_separately: bool = False
    keep_failed_partial: bool = False
    initial_yaw: float = 0.0            # lossless Spherical V2 pose, degrees
    decode_check: bool = True
    chunk_frames: int | None = None     # passthrough chunk size override
    plan_rows: list = field(default_factory=list)


class JobCancelled(Exception):
    pass


class JobFailed(Exception):
    pass


def error_lines(lines: list[str]) -> list[str]:
    out = []
    for l in lines:
        if l.startswith("error: "):
            out.append(l[7:])
        elif re.search(r"\[(error|critical)\]", l):
            out.append(re.sub(r"^\[[^\]]*\]\s*\[[^\]]*\]\s*\[(error|critical)\]\s*", "", l))
    return out


def chunk_size_for(width: int, height: int, free: int | None, override: int | None = None) -> int:
    """Frames per TIFF chunk: at most a quarter of free space, 30..600 frames."""
    if override:
        return max(1, override)
    frame_bytes = width * height * 6 + 4096
    if not free:
        return 60
    return int(max(30, min(600, (free // 4) // frame_bytes)))


class BatchProcessor:
    def __init__(self, queue: JobQueue, config_factory: Callable[[], ProcessorConfig],
                 on_job: Callable[[Job], None] = lambda j: None,
                 on_log: Callable[[str, str], None] = lambda jid, line: None,
                 on_state: Callable[[str], None] = lambda s: None) -> None:
        self.queue = queue
        self.config_factory = config_factory
        self.on_job, self.on_log, self.on_state = on_job, on_log, on_state
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._proc: subprocess.Popen | None = None
        self._proc_lock = threading.Lock()
        self.state = "idle"

    # ---- control ------------------------------------------------------
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        if self.running:
            return False
        self._stop.clear()
        self._pause.clear()
        self._thread = threading.Thread(target=self._loop, name="batch", daemon=True)
        self._thread.start()
        return True

    def pause_after_current(self) -> None:
        self._pause.set()
        self._set_state("pausing")

    def stop(self) -> None:
        """Stop now: the running tool is terminated and its partial output deleted."""
        self._stop.set()
        with self._proc_lock:
            p = self._proc
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _set_state(self, s: str) -> None:
        self.state = s
        self.on_state(s)

    # ---- loop ---------------------------------------------------------
    def _loop(self) -> None:
        self._set_state("running")
        try:
            cfg = self.config_factory()
        except Exception as e:  # configuration problems stop the batch with a clear reason
            log.error("Batch cannot start: %s", e)
            self._set_state(f"error: {e}")
            return
        while not self._stop.is_set():
            if self._pause.is_set():
                self._set_state("paused")
                return
            job = self.queue.claim_next()
            if not job:
                break
            self._process(job, cfg)
        self._set_state("stopped" if self._stop.is_set() else "idle")

    def _update(self, job: Job, **kw) -> None:
        for k, v in kw.items():
            setattr(job, k, v)
        self.queue.save()
        self.on_job(job)

    # ---- one job ------------------------------------------------------
    def _process(self, job: Job, cfg: ProcessorConfig) -> None:
        layout = ProjectLayout(cfg.project_root).ensure()
        stem = sanitize_name(Path(job.source).stem)
        log_path = layout.logs / f"{stem}_{job.id}.log"
        self._log_file = open(log_path, "a", encoding="utf-8")
        self._job = job
        partial: Path | None = None
        temp_dirs: list[Path] = []
        self._update(job, status=ANALYSING, started=time.time(), finished=None, attempts=job.attempts + 1,
                     log_file=str(log_path), message="analysing", frames_done=0, fps=0.0)
        try:
            src = Path(job.source)
            if not src.is_file():
                raise JobFailed("source file no longer exists")
            src_mtime, src_size = src.stat().st_mtime, src.stat().st_size
            clip = osv_probe.probe(Path(cfg.osvtool), src, cfg.ffprobe)
            job.clip = clip.to_dict()
            if not clip.ok:
                raise JobFailed(f"analysis failed: {clip.error}")
            ex = cfg.export
            errs = validate_for_video(ex)
            if errs:
                raise JobFailed("; ".join(errs))
            pipeline = pipeline_for(ex, clip.color_mode)
            colour_out = ex.colour.resolved(clip.color_mode)
            size = parse_size(ex.size) or clip.native_equirect
            if ex.mode == "reframe":
                size = parse_size(ex.size) or (1920, 1080)
            w, h = size
            frames = clip.frame_count or 0
            first = 0
            if ex.frame_range:
                a, b = (int(x) for x in ex.frame_range.split("-"))
                first, frames = a, b - a + 1
            fps_num, fps_den = clip.fps_num or 30, clip.fps_den or 1
            fps = fps_num / fps_den
            family = ex.codec_family
            ext = ".mov" if family == "prores" else ".mp4"
            name = f"{stem}_{'360' if ex.mode == 'equirect' else 'flat'}_{w}x{h}_{COLOUR_SUFFIX.get(colour_out, colour_out)}"
            if pipeline == "tiff":
                final = layout.stitched / name
            else:
                final = layout.stitched / f"{name}{ext}"
            if final.exists():
                if cfg.existing == "skip":
                    self._update(job, status=SKIPPED, output=str(final), message="output already exists; skipped",
                                 finished=time.time())
                    return
                if cfg.existing == "unique":
                    final = unique_path(final)
            assert_safe_output(final, [src] + ([Path(job.proxy)] if job.proxy else []))
            job.pipeline, job.output = pipeline, str(final)

            # ---- storage check
            seconds = frames / fps if fps else 0
            need = estimate_output_bytes(w, h, fps, seconds, cfg.crf)
            if pipeline == "passthrough":
                chunk = chunk_size_for(w, h, free_bytes(layout.stitched), cfg.chunk_frames)
                need += chunk * w * h * 6
            elif pipeline == "transcode":
                need *= 2
            elif pipeline == "tiff":
                need = frames * w * h * 6
            free = free_bytes(layout.stitched)
            if free is not None and free < need * 1.2:
                raise JobFailed(f"not enough free space on the output drive: about {human_bytes(need)} needed, "
                                f"{human_bytes(free)} free")

            self._index_original(layout, src, src_size, src_mtime)
            if cfg.save_audio_separately and clip.has_audio:
                self._extract_audio(cfg, src, layout.audio / f"{stem}.aac")

            job.settings_summary = {"pipeline": pipeline, "size": f"{w}x{h}", "native": clip.native_equirect,
                                    "colour": ex.colour.describe(clip.color_mode), "crf": cfg.crf,
                                    "encoder": cfg.encoder, "device": cfg.device, "frames": frames,
                                    "fps": clip.fps_label, "stitch": ex.stitch.changed()}
            self._update(job, status=RUNNING, frames_total=frames, device_requested=cfg.device,
                         encoder=cfg.encoder or "", message=f"rendering ({pipeline})")

            partial = final.with_name(final.stem + ".partial" + final.suffix) if pipeline != "tiff" \
                else final.with_name(final.name + ".partial")
            if partial.exists():
                (shutil.rmtree if partial.is_dir() else os.remove)(partial)

            if pipeline == "direct":
                self._render_direct(job, cfg, clip, partial, frames)
            elif pipeline == "passthrough":
                tmp = layout.stitched / f".{stem}_{job.id}_chunks"
                temp_dirs.append(tmp)
                self._render_passthrough(job, cfg, clip, partial, tmp, first, frames, w, h, colour_out, family)
            elif pipeline == "transcode":
                tmp = layout.stitched / f".{stem}_{job.id}_master"
                temp_dirs.append(tmp)
                self._render_transcode(job, cfg, clip, partial, tmp, frames, colour_out, family)
            else:
                partial.mkdir(parents=True)
                self._render_tiff(job, cfg, clip, partial, stem, frames)

            # ---- validation
            self._update(job, status=VALIDATING, message="validating output")
            if pipeline == "tiff":
                rep_ok, checks = self._validate_tiff(partial, frames)
                report_checks = checks
            else:
                if ex.mode == "equirect" and ex.spherical_metadata and cfg.initial_yaw:
                    metadata_manager.set_initial_pose(partial, cfg.initial_yaw)
                exp = Expectation(w, h, fps_num, fps_den, frames, 10,
                                  {"hevc": "hevc", "h264": "h264", "prores": "prores"}[family],
                                  "709" if colour_out.startswith("709") else colour_out,
                                  audio=clip.has_audio and ex.keep_audio,
                                  spherical=ex.mode == "equirect" and ex.spherical_metadata)
                vr = validate(partial, exp, cfg.ffprobe, cfg.ffmpeg, decode=cfg.decode_check)
                rep_ok, report_checks = vr.ok, vr.to_dict()["checks"]
            if (src.stat().st_mtime, src.stat().st_size) != (src_mtime, src_size):
                raise JobFailed("the original recording changed during processing; output discarded")
            report = {"job": job.to_dict(), "clip": clip.to_dict(), "plan": cfg.plan_rows,
                      "validation": report_checks, "ok": rep_ok}
            rpath = layout.reports / f"{stem}_{job.id}_report.json"
            rpath.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
            job.report = str(rpath)
            if not rep_ok:
                bad = [c for c in report_checks if c["status"] == "fail"]
                raise JobFailed("validation failed: " + "; ".join(f"{c['name']}: {c['detail']}" for c in bad))
            if final.exists() and cfg.existing == "replace":
                (shutil.rmtree if final.is_dir() else os.remove)(final)
            os.replace(partial, final)
            partial = None
            elapsed = time.time() - (job.started or time.time())
            self._update(job, status=DONE, frames_done=frames, finished=time.time(),
                         message=f"done and validated in {elapsed / 60:.1f} min")
        except JobCancelled:
            self._update(job, status=CANCELLED, finished=time.time(), message="stopped by user; partial output deleted")
        except (JobFailed, UnsupportedOption, UnsafeOutputError, ValueError, OSError) as e:
            log.error("Job %s failed: %s", job.name, e)
            self._update(job, status=FAILED, finished=time.time(), message=str(e))
        finally:
            if partial is not None and partial.exists() and not (cfg.keep_failed_partial and job.status == FAILED):
                try:
                    (shutil.rmtree if partial.is_dir() else os.remove)(partial)
                except OSError:
                    pass
            for d in temp_dirs:
                shutil.rmtree(d, ignore_errors=True)
            self._log_file.close()
            self.queue.record_history(job)
            self.queue.save()

    # ---- process running ----------------------------------------------
    def _run(self, args: list[str], job: Job, frame_offset: int = 0, total_override: int | None = None) -> tuple[int, list[str], bool]:
        """Run a tool, streaming stderr lines into the job log and progress.
        Returns (exit code, last lines, produced_any_progress)."""
        if self._stop.is_set():
            raise JobCancelled()
        cmdline = subprocess.list2cmdline(native_args(args))
        self._log(f"$ {cmdline}")
        job.command = cmdline
        tail: list[str] = []
        progressed = False
        with self._proc_lock:
            self._proc = subprocess.Popen(native_args(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          stdin=subprocess.DEVNULL, **popen_flags())
            p = self._proc
        assert p.stdout is not None
        buf = b""
        last_emit = 0.0
        while True:
            chunk = p.stdout.read1(65536) if hasattr(p.stdout, "read1") else p.stdout.read(4096)
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"\r\n|\n|\r", buf)
            buf = parts.pop()
            for raw in parts:
                line = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", raw.decode("utf-8", "replace")).rstrip()
                if not line:
                    continue
                tail = (tail + [line])[-60:]
                pr = parse_progress(line)
                if pr:
                    progressed = True
                    done, total, fps, dev = pr
                    job.frames_done = frame_offset + done
                    if total_override:
                        job.frames_total = total_override
                    job.fps, job.device_used = fps, dev or job.device_used
                    now = time.time()
                    if now - last_emit > 0.5 or done == total:
                        last_emit = now
                        self.on_job(job)
                    self._log_file.write(line + "\n")
                else:
                    self._log(line)
        rc = p.wait()
        with self._proc_lock:
            self._proc = None
        if self._stop.is_set():
            raise JobCancelled()
        self._log(f"exit code {rc}")
        return rc, tail, progressed

    def _log(self, line: str) -> None:
        try:
            self._log_file.write(line + "\n")
            self._log_file.flush()
        except (OSError, ValueError):
            pass
        self.on_log(self._job.id, line)

    def _osvtool_with_fallback(self, job: Job, cfg: ProcessorConfig, build: Callable[[str], list[str]],
                               frame_offset: int = 0, total: int | None = None) -> None:
        devices = [cfg.device] + [d for d in cfg.device_fallbacks if d != cfg.device]
        last_err = ""
        for i, dev in enumerate(devices):
            args = build(dev)
            rc, tail, progressed = self._run(args, job, frame_offset, total)
            if rc == 0:
                if dev != cfg.device:
                    self._log(f"Renderer {dev.upper()} is used for the rest of this batch.")
                    cfg.device_fallbacks = [d for d in devices if d != dev]
                    cfg.device = dev
                return
            errs = error_lines(tail)
            last_err = errs[-1] if errs else (tail[-1] if tail else "")
            meaning = EXIT_MEANING.get(rc, f"exit code {rc}")
            if rc == EXIT_RUNTIME and not progressed and i + 1 < len(devices):
                msg = (f"Renderer {dev.upper()} failed before the first frame ({last_err or meaning}); "
                       f"retrying with {devices[i + 1].upper()}. Resolution and quality are unchanged.")
                log.warning(msg)
                self._log(msg)
                job.message = msg
                self.on_job(job)
                continue
            raise JobFailed(f"osvtool {meaning}: {last_err}".rstrip(": "))
        raise JobFailed(f"every renderer failed: {last_err}")

    # ---- pipelines ----------------------------------------------------
    def _render_direct(self, job: Job, cfg: ProcessorConfig, clip, partial: Path, frames: int) -> None:
        if not cfg.encoder:
            raise JobFailed("no working HEVC encoder (see Hardware Analyzer)")

        def build(dev: str) -> list[str]:
            return build_render_command(cfg.osvtool, job.source, partial, cfg.export, device=dev, codec=cfg.encoder,
                                        crf=cfg.crf, ffmpeg=cfg.ffmpeg, supported=cfg.supported_options,
                                        source_colour=clip.color_mode)
        self._osvtool_with_fallback(job, cfg, build)

    def _render_tiff(self, job: Job, cfg: ProcessorConfig, clip, folder: Path, stem: str, frames: int) -> None:
        pattern = str(folder / f"{stem}_%06d.tif")

        def build(dev: str) -> list[str]:
            return build_render_command(cfg.osvtool, job.source, pattern, cfg.export, device=dev,
                                        supported=cfg.supported_options, source_colour=clip.color_mode)
        self._osvtool_with_fallback(job, cfg, build)

    @staticmethod
    def _numbered(folder: Path) -> list[tuple[int, Path]]:
        out = []
        for p in folder.glob("*.tif"):
            m = re.search(r"_(\d+)\.tif$", p.name)
            if m:
                out.append((int(m.group(1)), p))
        return sorted(out)

    def _validate_tiff(self, folder: Path, frames: int) -> tuple[bool, list[dict]]:
        files = self._numbered(folder)
        ok = len(files) == frames
        checks = [{"name": "frame files", "status": "pass" if ok else "fail",
                   "detail": f"{len(files)} TIFF files (expected {frames})"}]
        if files and any(p.stat().st_size == 0 for _, p in files):
            checks.append({"name": "file sizes", "status": "fail", "detail": "empty frame file found"})
            ok = False
        return ok, checks

    def _render_passthrough(self, job: Job, cfg: ProcessorConfig, clip, partial: Path, tmp: Path, first: int,
                            frames: int, w: int, h: int, colour: str, family: str) -> None:
        if not cfg.ffmpeg:
            raise JobFailed("FFmpeg is required to encode the D-Log M passthrough")
        encoder = cfg.encoder if family == "hevc" else em.default_encoder(family, {cfg.encoder or "": True,
                                                                                  "libx264": True, "prores_ks": True})
        if not encoder:
            raise JobFailed("no working encoder for this format")
        tmp.mkdir(parents=True, exist_ok=True)
        chunk = chunk_size_for(w, h, free_bytes(tmp), cfg.chunk_frames)
        segments: list[Path] = []
        seg_ext = ".mov" if family == "prores" else ".mp4"
        done = 0
        k = 0
        self._log(f"D-Log M passthrough: {frames} frames in chunks of {chunk} (16-bit TIFF, removed after encoding)")
        while done < frames:
            a = first + done
            b = min(first + frames - 1, a + chunk - 1)
            cdir = tmp / f"c{k:04d}"
            cdir.mkdir(exist_ok=True)
            pattern = str(cdir / "f_%06d.tif")

            def build(dev: str, a=a, b=b, pattern=pattern) -> list[str]:
                return build_render_command(cfg.osvtool, job.source, pattern, cfg.export, device=dev,
                                            supported=cfg.supported_options, frame_range=f"{a}-{b}",
                                            force_colour="dlogm", source_colour=clip.color_mode)
            self._osvtool_with_fallback(job, cfg, build, frame_offset=done, total=frames)
            files = self._numbered(cdir)
            want = b - a + 1
            if len(files) != want:
                raise JobFailed(f"chunk {k}: osvtool wrote {len(files)} frames, expected {want}")
            nums = [n for n, _ in files]
            if nums != list(range(nums[0], nums[0] + want)):
                raise JobFailed(f"chunk {k}: frame numbering has gaps")
            seg = tmp / f"seg_{k:04d}{seg_ext}"
            args = em.encode_tiff_chunk(cfg.ffmpeg, pattern, nums[0], want, clip.fps_num or 30, clip.fps_den or 1,
                                        seg, encoder, cfg.crf, family, colour, cfg.export.custom_bitrate_mbps)
            job.message = f"encoding chunk {k + 1} ({a}-{b})"
            self.on_job(job)
            rc, tail, _ = self._run(args, job)
            if rc != 0 or not seg.exists():
                raise JobFailed(f"FFmpeg failed on chunk {k}: {(tail or ['?'])[-1]}")
            shutil.rmtree(cdir, ignore_errors=True)
            segments.append(seg)
            done += want
            job.frames_done = done
            self.on_job(job)
            k += 1
        lst = tmp / "segments.txt"
        em.write_concat_list(segments, lst)
        audio = job.source if (clip.has_audio and cfg.export.keep_audio) else None
        if audio and first:
            raise JobFailed("a partial frame range with audio is not supported in passthrough mode; "
                            "disable audio or render the whole clip")
        rc, tail, _ = self._run(em.concat_and_mux(cfg.ffmpeg, lst, audio, partial, family), job)
        if rc != 0:
            raise JobFailed(f"FFmpeg could not join the chunks: {(tail or ['?'])[-1]}")
        self._tag_spherical(job, cfg, partial)

    def _render_transcode(self, job: Job, cfg: ProcessorConfig, clip, partial: Path, tmp: Path, frames: int,
                          colour: str, family: str) -> None:
        if not (cfg.ffmpeg and cfg.encoder):
            raise JobFailed("FFmpeg with a working HEVC encoder is needed for the master")
        tmp.mkdir(parents=True, exist_ok=True)
        master = tmp / "master.mp4"
        master_crf = min(cfg.crf, 10)

        def build(dev: str) -> list[str]:
            return build_render_command(cfg.osvtool, job.source, master, cfg.export, device=dev, codec=cfg.encoder,
                                        crf=master_crf, ffmpeg=cfg.ffmpeg, supported=cfg.supported_options,
                                        spherical=False, source_colour=clip.color_mode)
        self._log(f"Rendering an HEVC master at CRF {master_crf}, then converting to {family}")
        self._osvtool_with_fallback(job, cfg, build)
        enc = em.default_encoder(family, {"libx264": True, "prores_ks": True})
        job.message = f"converting master to {family}"
        self.on_job(job)
        rc, tail, _ = self._run(em.transcode(cfg.ffmpeg, master, partial, enc, cfg.crf, family, colour,
                                             cfg.export.custom_bitrate_mbps), job)
        if rc != 0:
            raise JobFailed(f"conversion failed: {(tail or ['?'])[-1]}")
        self._tag_spherical(job, cfg, partial)

    def _tag_spherical(self, job: Job, cfg: ProcessorConfig, path: Path) -> None:
        if cfg.export.mode != "equirect" or not cfg.export.spherical_metadata:
            return
        rc, tail, _ = self._run([cfg.osvtool, "spherical", str(path)], job)
        if rc != 0:
            raise JobFailed(f"osvtool spherical failed: {(error_lines(tail) or tail or ['?'])[-1]}")

    def _extract_audio(self, cfg: ProcessorConfig, src: Path, out: Path) -> None:
        out = unique_path(out)
        assert_safe_output(out, [src])
        rc, tail, _ = self._run([cfg.osvtool, "extract", str(src), "--audio", str(out)], self._job)
        if rc != 0:
            self._log(f"warning: audio extraction failed: {(tail or ['?'])[-1]}")

    def _index_original(self, layout: ProjectLayout, src: Path, size: int, mtime: float) -> None:
        idx = layout.original / "sources.json"
        try:
            data = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else []
        except (OSError, ValueError):
            data = []
        if not any(d.get("path") == str(src) for d in data):
            data.append({"path": str(src), "size": size, "mtime": mtime,
                         "note": "original left in place and opened read-only; not copied"})
            idx.write_text(json.dumps(data, indent=2), encoding="utf-8")

# Architecture

```text
            +------------------------------ OSV360Craft (Python 3.12, PySide6) ------------------------------+
 .OSV  -->  | hardware/   detectors -> HardwareReport -> auto_optimizer -> RenderPlan (decisions + reasons)  |
 (read      | core/       openosv_manager (version, backends, options) | osv_probe | metadata_manager        |
  only)     | export/     export_settings -> osvtool command | encoder_manager (FFmpeg) | metadata_validator  |
            | batch/      JobQueue (persistent) -> BatchProcessor (worker thread[s])                          |
            | live/       PreviewEngine (osvtool --frame / --range --purpose play) -> panorama_viewer (GL)    |
            | ui/         pages, themes, help                                                                |
            +---------------------------------------------+-------------------------------------------------+
                                                          | subprocess (argument lists, no shell)
                         OpenOSV osvtool.exe (Apache-2.0) | FFmpeg / ffprobe (GPL-3.0)
```

* **Nothing blocks the GUI thread.** Detection, probing and benchmarks run in a `QThreadPool`. Batch jobs run in their own threads and report through Qt signals. Preview frames are decoded to `QImage` on the engine thread.
* **The core is independent of Qt.** `core`, `hardware`, `export`, `batch` and `live/preview_engine` import no Qt, so they are tested without a display.
* **Pipelines** (`export_settings.pipeline_for`): `direct`, `passthrough` (D-Log M), `transcode` (H.264/ProRes) and `tiff`. See the integration report for why each exists.
* **Safety rules** are enforced in `utils/file_manager.py` and `batch_processor.py`:
  * originals are opened only for reading, and their size and mtime are checked again before the output is accepted;
  * output goes to `.partial` first and is renamed only after validation;
  * a protected extension or a path equal to a source is refused;
  * existing outputs are kept, skipped, or replaced only after the user confirms.
* **Fallback:** if osvtool exits with a runtime error before the first progress line, the next renderer in the plan is tried. That renderer is then used for the rest of the batch, and the change is logged. Size and quality are never changed.
* **Data:** settings, queue, history and benchmark are JSON files in `%LOCALAPPDATA%\OSV360Craft` (written atomically). Logs are kept there with rotation.

# Testing

## Automated (runs on every push, `.github/workflows/tests.yml`)

`python -m pytest -q` runs the suite below. It was last run on Linux with a real OpenOSV 0.6.4 `osvtool` built from source (CPU only): 29 passed.

| Area | What is tested |
|---|---|
| osvtool contract | Parsing of `--version`, `render --help`, `probe --json` and the progress lines. Every option OSV360Craft can pass appears in osvtool 0.6.4's `render --help`. An option the installed osvtool does not support is refused, not dropped. |
| Settings → command | A pure-stitch default passes no stitching or colour change. Source colour is resolved per clip. Stitching options are tri-state. Ranges are validated. Settings survive a round trip, and a damaged settings file falls back to the defaults. |
| Files | Folder scanning skips `.LRF`, empty files, `._` twins and duplicates. Writing over an original is refused. Existing outputs get unique names. |
| 360 metadata | The independent parser reads files tagged by the real `osvtool spherical`. The initial-heading edit changes only the 12 pose bytes, and ffprobe reads it back. |
| Pipelines (end to end, real FFmpeg) | Direct HEVC (HLG), D-Log M passthrough in chunks plus the initial heading, H.264 and ProRes, the 16-bit TIFF sequence, CUDA failure falling back to CPU with a log entry, validation failure (wrong size) with the partial file deleted, stop with the partial deleted and the job re-queued, an interrupted job re-queued after a restart, an existing output kept, and the original file's SHA-256 unchanged. |
| Validator | Passes a real osvtool-tagged file and fails an untagged one. |
| Preview engine | An exact single frame, segmented playback of every frame in order, and a cache key that changes when settings change. |
| Optimizer | Synthetic NVIDIA, AMD and CPU-only machines; a benchmark result overriding the heuristic; a user's quality never replaced by the profile; concurrency only on large machines; upscaling labelled. |
| UI | Every page opens, the theme switches and is remembered, and "Use this heading" reaches the settings (offscreen Qt). |

The end-to-end tests use `tests/fake_osvtool.py`, a stand-in that follows osvtool's documented contract (probe schema, progress format, `error:` lines, exit codes, image numbering) and makes real 10-bit media with FFmpeg. It does **not** stitch. 360 tagging in those tests is done by the real osvtool given in `OSV360CRAFT_TEST_OSVTOOL`; tests that need it are skipped without it.

## Manual checklist with real footage (required before calling a release verified)

Not yet done by the authors: no real Osmo 360 / Osmo 360 II footage was available while 1.0.0 was written. Please report results in an issue.

Use clips that cover:

- short and long recordings;
- 30 and 60 fps;
- 8K and 6K;
- 10-bit D-Log M and Normal;
- with audio;
- bright sky, low light, and near objects crossing the seam.

For each clip:

1. Both lenses appear in the stitch, and no lens is missing.
2. The output is a correct sphere in a 360 player (VLC, YouTube upload, a headset).
3. The horizon is correct with Off, Horizon levelling and Direction lock.
4. The seam is acceptable, compared with DJI Studio and with OpenOSV's own `osvtool render`.
5. Colours match the source in pure-stitch mode. D-Log M is still D-Log M, which you can check by applying the OpenOSV LUT.
6. ffprobe reports 10-bit.
7. The frame rate and frame count equal the source.
8. Audio is in sync at the end of a long clip.
9. Spherical metadata is present (the Reports JSON).
10. The file plays to the end without errors.
11. The original `.OSV` has the same SHA-256 afterwards.
12. Speed: record the Hardware Analyzer benchmark and the batch fps on the test PC.

## Windows hardware matrix (to do)

Run the Hardware Analyzer and export the JSON on:

- an NVIDIA RTX machine (CUDA, NVENC);
- an older NVIDIA GTX 10-series (should fall back to OpenCL);
- AMD (OpenCL, AMF);
- Intel Arc and Intel iGPU (OpenCL, Quick Sync);
- a CPU-only machine.

Attach the reports to the release notes.

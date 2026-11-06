# Contributing to OSV360Craft

Thank you for helping. Bug reports, test results with real footage and pull requests are all welcome.

## Reporting a problem

1. In OSV360Craft, open **Logs and Diagnostics → Save diagnostic bundle**. It contains the hardware report, the application log and the settings. It contains no media.
2. Open an issue with:
   - the bundle;
   - what you did;
   - what you expected;
   - the camera model, recording mode, frame rate and colour mode (from Live Stitching → Clip).

Please do not upload recordings that show other people without their consent.

## Testing with real footage

The most useful contribution right now is a filled-in copy of the manual checklist in [docs/TESTING.md](docs/TESTING.md), run on your camera and PC. It is especially valuable for the **Osmo 360 II**, AMD and Intel GPUs, and CPU-only machines.

## Development

```bat
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Set `OSV360CRAFT_TEST_OSVTOOL` to a real `osvtool` to run the integration tests as well.

Rules the code follows, so please keep them:

- Never write to, move or rename an original recording.
- Never report success without validating the output.
- Never change export resolution or quality silently. Every automatic choice carries a visible reason.
- Pass an OpenOSV option only when the user changed it ("OpenOSV default" means the option is left out).
- Keep the GUI thread free. Core modules (`core`, `hardware`, `export`, `batch`, `live/preview_engine`) must not import Qt.
- Keep OpenOSV unmodified and separate. It is run as a program, not vendored as source.
- Write user-facing text in clear, plain English.

By contributing you agree that your contribution is licensed under the MIT License.

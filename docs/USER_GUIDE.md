# OSV360Craft 1.0.0

**Stitch original DJI Osmo 360 `.OSV` recordings into 360-degree equirectangular video, with the stitching engine of the open-source OpenOSV project.** Your original files are never modified.

## Getting started

1. On first start OSV360Craft detects your CPU, GPU, memory, OpenOSV and FFmpeg, and tests every video encoder. The result and the chosen settings are in **Hardware Analyzer**.
2. Optionally run the **benchmark** there with one of your own clips (1-3 minutes on a GPU). It measures decoding, stitching and encoding on this computer and refines the automatic choices.
3. Look at a clip in **Live Stitching**, or go straight to **Batch Stitching**: add files or folders, choose a project folder, press **Start**.

## Pure stitch - no quality loss beyond the stitch itself

The defaults change nothing except what stitching requires:

- **Colour is kept**: D-Log M stays D-Log M, HLG stays HLG, Rec.709 stays Rec.709.
- **10-bit is kept** end to end; OpenOSV works internally in 16-bit or 32-bit floating point.
- **Native resolution** (2 x lens height: 7680 x 3840 for 8K recordings, 6000 x 3000 for 6K) and the **source frame rate**. Nothing is interpolated.
- **Original audio** is copied, not re-encoded.
- HEVC 10-bit at **CRF 16** (visually lossless). Choose *Maximum* (CRF 10) for archive masters.

Two things cannot be avoided: the stitch resamples the two fisheye images into one sphere, and a video file is compressed. For a completely uncompressed result choose *16-bit TIFF image sequence*.

**Why D-Log M takes longer:** OpenOSV only writes Rec.709, PQ or HLG straight to video. To keep D-Log M, OSV360Craft lets OpenOSV write lossless 16-bit TIFF chunks, encodes them to 10-bit HEVC with FFmpeg and joins them without re-encoding. Temporary chunks are deleted as it goes; the job needs some extra free disk space while it runs.

## Horizon, levelling and direction (Stitching Controls)

- **Horizon levelling** keeps the horizon level from the camera's gyroscope.
- **Direction lock** keeps the first frame's heading, like DJI Studio's direction lock.
- **RockSteady** smooths the camera's rotation; **RockSteady + horizon** also levels it.
- **Off** keeps the footage exactly as the camera moved.
- **Initial viewing direction**: the direction players open on. It is stored as 360 metadata and is lossless. In Live Stitching, turn the view and press *Use this heading*.

The gyro corrections happen inside the stitch, so they cost no extra quality.

## Live Stitching

Open an `.OSV` (or drop it on the page). Drag to look around, use the mouse wheel to zoom, right-drag or Alt+drag to roll, double-click to reset. **F** switches between the sphere and the flat panorama, **H** shows a horizon guide.

Every frame shown is a real OpenOSV stitch at the preview size. A paused frame is stitched exactly. During playback, frames appear as fast as your computer stitches them; the panel shows the real playback, stitching and decoding speeds. On most computers 8K playback is not real time; the 2K and Performance previews are faster. The preview size never changes the export.

## Batch Stitching

- Folders are searched with subfolders; `.LRF` proxies, empty files and duplicates are skipped.
- Jobs run one at a time by default. **Pause after current** finishes the running clip; **Stop** ends it now and deletes the unfinished file. **Retry failed** queues failed clips again. The queue is remembered when you close the app.
- Output goes to the project folder: `Stitched`, `Reports`, `Logs`, `Audio`, `Preview`, and `Original/sources.json` (a list of the originals, which stay where they are).
- A file is only reported as done after it has been **validated**: resolution and 2:1 aspect, 10-bit, frame rate, frame count, colour tags, audio and A/V sync, Spherical Video V1 and V2 metadata and a decode test. The report is in `Reports`. A file that fails validation keeps its `.partial` name only if you asked to keep failed files; otherwise it is deleted.

## Processing profiles

- **AUTO** (default): fastest working renderer and encoder found on this computer, CRF 16.
- **QUALITY**: CRF 12 and the largest preview.
- **PERFORMANCE**: CRF 20 and the smallest preview.

A quality you choose yourself in Export Settings always wins. If a GPU renderer fails at start-up, the next one is tried (for example CUDA, then OpenCL, then CPU) and the log says why. Resolution and quality are never changed silently.

## Requirements

- Windows 10 or 11, 64-bit. Microsoft Visual C++ 2015-2022 Redistributable (x64), needed by OpenOSV.
- GPU: CUDA on NVIDIA GTX 16 / RTX 20 and newer; OpenCL on AMD and Intel; otherwise the CPU (slow at 8K).
- The installer includes OpenOSV and FFmpeg. Without the installer, put OpenOSV's `cli` folder in `external\OpenOSV\cli` and FFmpeg in `external\ffmpeg`, or set the paths in Settings.

## Troubleshooting

- **osvtool could not start**: install the Visual C++ Redistributable from [aka.ms/vs/17/release/vc_redist.x64.exe](https://aka.ms/vs/17/release/vc_redist.x64.exe).
- **An encoder shows "failed test"**: the GPU or driver cannot run it; another one is used. Update the GPU driver.
- **An antivirus warning** (for example a name ending in `!ml`): machine-learning checks sometimes flag new, unsigned apps. Make sure you downloaded OSV360Craft from its GitHub Releases page, check the file on [virustotal.com](https://www.virustotal.com), and report a false detection to Microsoft at [microsoft.com/wdsi/filesubmission](https://www.microsoft.com/en-us/wdsi/filesubmission). Never turn your antivirus off to run an app; on a work computer, ask your IT or security team first.
- **Not enough free space**: an 8K render can need tens of GB, more while D-Log M chunks are encoded.
- **Reporting a problem**: Logs and Diagnostics > *Save diagnostic bundle* (no media is included) and open an issue at [https://github.com/PitKub/OSV360Craft/issues](https://github.com/PitKub/OSV360Craft/issues).

## Privacy

OSV360Craft works offline. It sends no media, settings or usage data anywhere. The only network actions are the links in Help, which open in your browser.

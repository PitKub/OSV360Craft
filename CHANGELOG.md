# Changelog

## Unreleased

- **Real-time 360 preview.**
  - **Source:** Live Stitching plays the `.LRF` proxy that the Osmo 360 records next to each `.OSV`. Without one, it decodes the `.OSV`'s two lenses on the GPU (NVDEC), scaled down. No proxy is created, nothing is written to disk.
  - **Stitching:** the graphics card projects the lenses onto the sphere with OpenOSV's lens calibration (Kannala–Brandt lenses and the rotation of each lens, as read from the clip).
  - **Exact frames:** pausing or scrubbing shows OpenOSV's exact stitch of that frame.
  - **Tests:** the projection is checked on a synthetic camera frame on both lenses and across the seams.
- **Viewer orientation fix.** The view rotation reached OpenGL transposed, so tilting while looking sideways twisted the view.
- **Faster exact-stitch playback.** Segments grow and two OpenOSV runs overlap, so start-up no longer stalls playback every segment.
- **Horizon guide** is now a smooth line one pixel wide at any zoom.
- **More space around text.** There is more room inside panels and under section titles everywhere. The Live Stitching side panel fits its width.

- **Live Stitching showed no picture on Windows.**
  - **The cause:** the OpenGL 360 viewer failed on start-up (PySide6 rejected an argument in one drawing call) and stayed black without saying so.
  - **The fix:** the viewer is fixed, and is now tested with a real OpenGL context.
  - **Fallback:** if OpenGL ever fails, the page switches to the flat software viewer and says why.
- **Faster scrubbing.** A paused frame now appears first as a quick render (OpenOSV's interactive mode, without waiting for the seam analyses), then is replaced by the exact stitch. The default preview on a GPU is now 2K; 4K stays selectable.
- **Fits 1920x1080 screens with scaling.**
  - **Window:** it opens sized to the screen, so the timeline and Play button are no longer hidden.
  - **Clip and performance panels:** they scroll instead of squashing their text.
- **Clip panel.**
  - **Lens count:** the cover picture inside an `.OSV` is no longer counted as a lens stream.
  - **OpenOSV's file-reader notes:** these are summarised, with details on hover. For example, Osmo 360 II files report "index table entry 'camd' failed verification", which OpenOSV skips.

- **No more antivirus false alarms from the build itself.**
  - **The problem:** the app was built as a single self-extracting EXE that unpacked its code into `%TEMP%` at every start. Microsoft Defender's machine-learning check flagged the unpacked `launcher.dll` (`Trojan:Win32/Bearfoos.A!ml`).
  - **The fix:** OSV360Craft is now a normal program folder (Nuitka standalone), and the installer installs that folder.
- **No PowerShell.** GPU detection now uses Windows display-device and registry information, and drive detection (SSD/HDD, bus) uses a direct Windows storage query. No hidden PowerShell process is started.
- **Faster start.** Encoder test results are remembered until FFmpeg changes; **Detect again** in Hardware Analyzer tests every encoder again.
- **Recordings on network drives.** Paths such as `//NAS/share/...` are passed to OpenOSV in Windows form. OpenOSV's command-line parser took them for options and stopped with "file is required".
- **Benchmark button.** It said "OpenOSV (osvtool) was not found" when it was pressed while hardware detection was still running. It is now disabled during detection. If OpenOSV really is missing, the message shows the reason and the path, and offers to detect again.
- **Benchmark.**
  - Each hardware decoder is tested separately, and its figures are real GPU figures.
  - 8K encode figures no longer include drawing the test pattern.
  - A command-line error is reported once, not blamed on every GPU.
  - Short clips work.

## 1.0.0 — first public release

- **Live Stitching.** Opens original DJI Osmo 360 `.OSV` files without exporting. Every frame shown is a real OpenOSV stitch.
  - An interactive 360° viewer (OpenGL, with a software fallback) and a flat panorama view.
  - Timeline, playback and frame stepping.
  - Native, 4K, 2K and Performance preview modes.
  - A horizon guide and both source lenses side by side.
  - Measured playback, stitching, decode and GPU figures.
- **Batch Stitching.**
  - **Adding clips:** files, folders with subfolders, or drag and drop. `.LRF` proxies, empty files and duplicates are skipped.
  - **Queue control:** a persistent queue with reordering, pause after the current clip, stop, retry failed clips, and resume after a restart.
  - **Progress:** real per-clip and overall progress, speed and time left.
  - **Output:** organised project folders (Stitched, Reports, Logs, Audio, Preview, Original). Existing outputs are never overwritten silently.
- **Pure stitch by default.**
  - Source colour is kept: D-Log M passthrough through lossless 16-bit TIFF chunks, HLG stays HLG, Rec.709 stays Rec.709.
  - 10-bit end to end, native resolution and source frame rate.
  - The original audio is copied, and HEVC Main10 is encoded at CRF 16.
- **Orientation.**
  - OpenOSV's gyro modes: Horizon levelling, Direction lock, RockSteady, RockSteady + horizon, and Off.
  - A lossless initial viewing direction (Spherical V2 pose, edited in place).
- **Formats.**
  - HEVC 10-bit, encoded directly by OpenOSV.
  - H.264 High 10 and ProRes 422 HQ.
  - 16-bit TIFF sequence.
- **Colour options.** Rec.709 with the DJI look or neutral, Rec.2100 PQ with OpenOSV's tone scales and peak brightness, and HLG.
- **Validation of every output before it is reported as done.**
  - Picture: resolution, 2:1 aspect, bit depth, frame rate, frame count and colour tags.
  - Audio: audio present and A/V sync.
  - 360 metadata: Spherical Video V1 and V2 (independent parser and ffprobe).
  - A decode test.
- **Hardware Analyzer.**
  - Detects the CPU, RAM, drives (SSD/HDD), every GPU (VRAM, driver, CUDA compute capability), OpenOSV's CUDA/OpenCL backends and FFmpeg.
  - Verifies every encoder with a real 10-bit test encode.
  - Optional benchmark on your own clip.
  - AUTO, QUALITY and PERFORMANCE plans, with the reason for every choice.
  - Exports the report as TXT or JSON.
- **Automatic renderer fallback** (for example CUDA, then OpenCL, then CPU), logged. Size and quality are never changed silently.
- **Windows installer** (Inno Setup).
  - An Express setup with the OpenOSV 0.6.4 engine and FFmpeg included.
  - Per-user or all-users install, English and Polish.
  - A check for the Visual C++ Redistributable.
- **Interface.** Dark and Light themes, User Guide, Donate and About dialogs, and a diagnostic bundle.

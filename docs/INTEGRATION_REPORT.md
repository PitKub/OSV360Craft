# OpenOSV integration report (Phase 1)

What OSV360Craft 1.0.0 relies on in OpenOSV, how it was verified, and what is still unverified. Every statement comes from the OpenOSV repository at tag `v0.6.4` (commit `d46b2bc`, 2026-10-25), its official Windows release, or a build of its source. Nothing here is taken from memory or marketing text.

## 1. What OpenOSV is

[OpenOSV](https://github.com/Kemerd/OpenOSV) is an independent, open-source toolkit for DJI Osmo 360 `.OSV` files. It has about 87,000 lines of C++20 and is licensed under Apache-2.0. Its parts:

- **Static libraries:** `osv_core`, `osv_container`, `osv_meta`, `osv_color`, `osv_video`, `osv_geom` and `osv_render`. The CPU, CUDA and OpenCL renderers share one kernel. There is also `osv_io`.
- **`osvtool`:** a command-line tool that probes, renders, extracts and makes LUTs. Its `render` command runs **the same clip engine as the Premiere and Resolve plug-ins**.
- **`osvgui`** ("OpenOSV Studio"): a small Dear ImGui batch front end for `osvtool`.
- **Plug-ins:** a Premiere Pro importer and effects, OpenFX plug-ins for Resolve and VEGAS, and a UXP/CEP panel.

Documented capabilities, all confirmed in the docs and source:

- `.OSV` container parsing: two HEVC Main10 lens tracks, AAC audio, and `djmd` protobuf metadata.
- Factory calibration (Kannala–Brandt), per-clip lens-rotation fitting and lens-protector correction.
- Parallax-aware carved seam (DIS optical flow and dynamic-programming seam).
- Exposure matching, the sky seam field, lens-shading correction and sun-ghost removal.
- D-Log M decoding. Outputs are Rec.709 (DJI look or neutral), Rec.2100 PQ with five tone scales and a peak roll-off, and HLG.
- Gyro stabilisation: off, horizon, full (direction lock), smooth, and smooth-horizon.
- Equirect and reframe output. Spherical Video V1 and V2 tagging.

## 2. Integration choice: `osvtool` through its command line

| Option | Finding | Decision |
|---|---|---|
| Public C ABI | None in `include/osv/`. The only C ABI, `plugins/common/OsvEngineAbi.h` (version 7), is private to the Premiere plug-ins. It is exported by `OpenOSVImporter.prm`, it is **CUDA-only** (it takes the caller's `CUcontext`), and it returns P010 device planes for the caller's own kernel. It has no open, close or frame-count calls, and its version is bumped on every layout change. | Not usable |
| Native library | C++20 static libraries with a `Result<>` API. They could be wrapped, but that means building OpenOSV (VS 2022, CUDA 12.8+, vcpkg) and tying OSV360Craft to internal headers. | Deferred (see §7) |
| `osvtool` CLI | Stable and documented. The probe JSON schema `openosv.probe/1` is a contract with the VEGAS extension. It has documented exit codes, a fixed progress-line format, and the same engine as the plug-ins. | **Used** |
| Rewrite in Python | Excluded by the project rules, and pointless. | No |

OSV360Craft never modifies OpenOSV. It runs the official binaries as a separate process.

## 3. The CLI contract OSV360Craft depends on (verified)

The contract was verified by reading `tools/osvtool/*.cpp` and by running a CPU-only build of OpenOSV 0.6.4 (Linux, built from the tagged source without changes). The full `render --help` of 0.6.4 is saved in [`osvtool-0.6.4-render-help.txt`](osvtool-0.6.4-render-help.txt). A unit test checks that every option OSV360Craft can pass appears in it.

- **`osvtool --version`** prints the version, the compiled modules, the linked FFmpeg (and whether it is a GPL build), and one line per GPU backend, giving either the device name or `unavailable (reason)`. This is how OSV360Craft learns what OpenOSV can render with on the machine.
- **`osvtool probe <file> --json -`** prints the `openosv.probe/1` JSON on stdout. Stable keys: `frameCount`, `fps{num,den,value}`, `durationSeconds`, `streamW/H`, `mode`, `colorModeName`, `hasAudio`, `audio` and `isLrf`.
- **`osvtool render`**
  - **Frame selection:** `--all`, `--range a-b` or `--frame N`.
  - **Output:** `--mode equirect|reframe` and `--size WxH`. Leaving out the size gives the native equirect, **2 × lens height by lens height**.
  - **Colour:** `--color pq|hlg|709|dlogm`, `--look`, `--tone` and `--hdr-peak`.
  - **Stabilisation:** `--stab`.
  - **Seam and photometric controls:** `--seam-blend`, `--parallax-blend`, `--seam-smoothing`, `--near-offset`, `--far-offset`, `--flare`, `--lens-align`, `--parallax-grid`, `--shading`, `--photo`, `--gain`, `--seam-search`, `--parallax`, `--hide-mount` and `--scene-light`.
  - **Engine and encoding:** `--purpose exact|interactive|play|playback`, `--device cpu|cuda|opencl|auto`, `--codec`, `--crf`, `--ffmpeg`, `--no-audio` and `--no-spherical-metadata`.
- **Options not given use OpenOSV's Source Settings defaults** under the default `--engine plugin`. OSV360Craft therefore passes only the settings a user changed; every stitching control starts at "OpenOSV default". Classic-only options such as `--hw` and `--threads` are rejected by the plug-in engine with a usage error, so OSV360Craft never passes them.
- **Progress:** every ten frames osvtool writes `  N/T frames  F fps  (device)` to stderr. Errors are written as `error: …`. Exit codes are 0 for success, 1 for usage, 2 for input and 3 for runtime errors.
- **Video output:** osvtool pipes `rgb48le` into an external `ffmpeg` and encodes `-pix_fmt yuv420p10le`, tagged `hvc1`. Encoder settings: NVENC `p5 vbr -cq`, AMF `cqp`, x265 `crf main10`. It copies the source audio (`-c:a copy`) and writes colour tags 1/1/1, 9/16/9 or 9/18/9.
- **`osvtool spherical <file>`** writes Spherical Video V1 (uuid XML) and V2 (`st3d`, `sv3d/proj/prhd`+`equi`) in place. This was verified by running it: the parser in OSV360Craft and ffprobe both read the tags back.
- **Image sequences:** `--out name_%06d.tif` writes 16-bit TIFF, and `.png` writes 16-bit PNG.

### Limits found in the CLI, and how OSV360Craft handles them

| Limit | Handling |
|---|---|
| `--color dlogm` is refused for video: "video output needs --color pq\|hlg\|709" (`CmdRender.cpp`). | The **D-Log M passthrough** pipeline. Lossless 16-bit TIFF chunks are encoded by FFmpeg to 10-bit HEVC (or H.264 High 10 / ProRes), joined with stream copy, and the audio is copied in. Chunks are 30–600 frames, sized to a quarter of the free space, and deleted as the job goes. |
| `--codec` always adds `-tag:v hvc1` and `yuv420p10le`, so H.264 and ProRes cannot be written directly. | H.264 and ProRes are made from an HEVC master at CRF ≤ 10 (one extra generation, stated in the UI), or straight from the TIFF chunks for D-Log M. |
| No bitrate option; quality is set with CRF/CQ. | A custom bitrate is offered for H.264 only, and refused with an explanation for direct HEVC. |
| `--yaw/--pitch/--roll` apply only to `--mode reframe`, not to equirect output (`buildCamera`). | Orientation for 360 output comes from the gyro modes (`--stab`). The **initial viewing direction** is written into the Spherical V2 `prhd` box in place (12 bytes, no re-encode, verified with ffprobe). |
| No CUDA-device option on the command line (only `--cl-device` for OpenCL). | The renderer is chosen per backend. Selecting between several NVIDIA GPUs is not offered. |
| No animated reframes in the CLI. | Out of scope: OSV360Craft exports the whole sphere. |
| On Windows, osvtool's parser (CLI11 2.7.2) treats any argument starting with `/` as a Windows-style option. Qt returns network paths as `//NAS/share/...`, so osvtool saw an option and stopped with `file is required`. This was found by the first benchmark on real hardware and reproduced with CLI11 2.7.2. | Every argument passed to a child process goes through `process_manager.native_args`, which converts `//server/share/...` and `C:/...` to native Windows form. A regression test covers it. |

## 4. Releases and Windows binaries (verified)

- Tags `v0.6.0` to `v0.6.4` exist. The release asset `OpenOSV-0.6.4-premiere-windows-x64.zip` (88.8 MB) was downloaded. Its SHA-256 is `d7def9561dfef9292f5b522422880c4308b9830b0dc14c87c5733416b626a46c`, and it is pinned in `scripts/get_openosv.ps1`.
- Its `cli\` folder holds: `osvtool.exe`, `osvgui.exe`, `avcodec-63.dll`, `avformat-63.dll`, `avutil-61.dll`, `swresample-7.dll`, `fmt.dll`, `spdlog.dll`, `miniz.dll`, `z.dll`, `OpenCL.dll` and `glfw3.dll`. The zip also contains `LICENSE`, `NOTICE`, `licenses\` (15 files) and `SHA256SUMS.txt`.
- OSV360Craft ships `osvtool.exe` and its DLLs, without `osvgui.exe` and `glfw3.dll`, together with OpenOSV's LICENSE, NOTICE and licence texts.
- **Requirements:** Windows 10/11 x64 and the **MSVC 2015-2022 runtime (x64)**, which is not included in OpenOSV's zip. The CUDA runtime is statically linked. `nvcuda.dll` comes from the driver and is delay-loaded, so osvtool runs without an NVIDIA GPU. CUDA targets compute capability 7.5, 8.6, 8.9 and 12.0, plus PTX (GTX 16 / RTX 20 and newer). AMD and Intel use OpenCL; otherwise the CPU renders.

## 5. Camera compatibility

- OpenOSV is written and tested against the **Osmo 360**: device name `Osmo OQ001` and schema `dvtm_osmo360.proto`. It verifies 6K mode on a real clip and also opens 4K and 8K clips. Per-lens sizes are 1920, 3000 and 3840 px.
- The **DJI Avata 360** is supported (`--fit avata360`), marked "not yet tested on Avata footage".
- **"Osmo 360 II" is not mentioned anywhere in the OpenOSV repository.** OpenOSV identifies a clip by its metadata layout and tries every known schema until one carries calibration, so a later model that keeps the same layout may work. **This is unverified.** OSV360Craft reports the camera name and any probe warnings for every clip, and refuses a clip that OpenOSV cannot identify as dual-fisheye.

## 6. Licences

| Component | Licence | How OSV360Craft uses it |
|---|---|---|
| OpenOSV (`osvtool.exe`) | Apache-2.0 | Runs it as a separate program. LICENSE, NOTICE and `licenses\` are shipped unchanged in `external\OpenOSV`. |
| OpenOSV's FFmpeg DLLs | LGPL-2.1+ (no GPL components; checked by OpenOSV's release script) | Shipped unchanged beside `osvtool.exe`, with OpenOSV's `FFmpeg-BUILD.txt`. |
| NVIDIA CUDA runtime (static in osvtool) | NVIDIA EULA (text in OpenOSV's `licenses\`) | Unchanged |
| FFmpeg (`ffmpeg.exe`, `ffprobe.exe`) | GPL-3.0 (BtbN build with x264/x265) | A separate program. The same pinned build as ProxyCraft. Sources are attached to each release. |
| Qt / PySide6 | LGPL-3.0 | Bundled in the EXE. OSV360Craft's source is public, so users can relink. |
| OSV360Craft | MIT | — |

OpenOSV's NOTICE records that it contains no DJI code, binaries, models, LUTs or artwork. OSV360Craft contains none either.

## 7. Live preview architecture

- **Requirement:** a stitched 360° preview without first exporting.
- **Finding:** no public frame API exists (§2). `osvtool render --purpose play` is OpenOSV's own playback mode: "the first frame parked, then one Interactive pass … what a viewer sees during playback". `--purpose exact` makes every analysis first.
- **Implemented:**
  - A parked frame is one exact render to PNG. It is cached in a byte-bounded LRU whose key includes every setting that changes the picture.
  - Playback renders 30-frame segments ahead of the play head with `--purpose play`, and frames are shown as they are written.
  - Each osvtool start re-opens the clip. Per-clip analyses (lens alignment and so on) are cached on disk by OpenOSV, so restarts are cheap but not free.
- **Honest limit:**
  - **Speed:** playback speed equals this machine's stitching speed at the preview size, which is measured and shown.
  - **Measured upstream (RTX 5090):** decoding both lenses takes 2.1 ms per frame pair, and rendering one 4K view takes about 0.7 ms. Writing PNG and process start-up add overhead on top.
  - **8K60:** real-time 8K/60 playback through this path is **not** expected on typical hardware. The 2K and Performance modes are the fast paths.
- **Next step, if real-time is needed:** a small C++ preview host linked against OpenOSV's libraries (`Pipeline`, `GpuClipDecoder`, `CudaRenderer`/`OpenClRenderer`). It would hand frames to the viewer through shared memory and remove the per-segment process start and PNG encoding. This needs an OpenOSV source build and tracking its internal API. It was deliberately left out of 1.0.

## 8. What could not be verified in this development environment

- No real Osmo 360 or Osmo 360 II recording was available. Stitch quality, the horizon, seams, colour and the timing of real 8K files have **not** been checked by OSV360Craft's authors. See [TESTING.md](TESTING.md).
- The development container had no GPU and no Windows. The Windows-specific detectors (CIM, registry, `nvidia-smi`, PowerShell) are covered by unit tests on parsed output only. They need a run on real Windows machines with NVIDIA, AMD and Intel GPUs.
- GitHub's release API was not reachable from the build environment. The asset was fetched directly from the release download URL.

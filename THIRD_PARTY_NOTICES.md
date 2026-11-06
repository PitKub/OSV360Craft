# Third-party notices

OSV360Craft's own code is released under the MIT License (see `LICENSE`). The program folder, the installer and the portable ZIP also contain, or work with, the third-party software below. Their licences are separate from OSV360Craft's.

## OpenOSV (included in the installer and portable ZIP, folder `external\OpenOSV`)

The stitching, decoding and colour engine is **OpenOSV 0.6.4**, `osvtool.exe` from the official release `OpenOSV-0.6.4-premiere-windows-x64.zip` (SHA-256 `d7def9561dfef9292f5b522422880c4308b9830b0dc14c87c5733416b626a46c`). Copyright 2026 The OpenOSV Contributors.

- Licence: Apache License 2.0. OpenOSV's `LICENSE` and `NOTICE` are included unchanged in `external\OpenOSV`. The Apache text is also in `licenses/Apache-2.0.txt`.
- OpenOSV's own third-party components ship beside it with their licence texts in `external\OpenOSV\licenses`:
  - FFmpeg libraries under LGPL-2.1, with build details in `FFmpeg-BUILD.txt`;
  - the NVIDIA CUDA runtime under the NVIDIA EULA;
  - the Khronos OpenCL ICD loader;
  - fmt, spdlog, nlohmann/json, CLI11, miniz, tinyexr and zlib.
- Source code: <https://github.com/Kemerd/OpenOSV/tree/v0.6.4>.
- OSV360Craft does not modify OpenOSV. It runs `osvtool.exe` as a separate program.
- OpenOSV is an independent project, not affiliated with DJI. Its NOTICE states that it contains no DJI code, binaries, models, LUTs or artwork.

## Qt and PySide6 (included in the OSV360Craft program folder)

OSV360Craft uses **Qt for Python (PySide6) 6.12.0** and the **Qt 6** libraries it includes, © The Qt Company Ltd. and other contributors.

- Licence: GNU Lesser General Public License v3 (LGPL-3.0). The full texts are in `licenses/LGPL-3.0.txt` and `licenses/GPL-3.0.txt`.
- Source code: PySide6 at <https://code.qt.io/cgit/pyside/pyside-setup.git/> and Qt at <https://download.qt.io/official_releases/qt/>.
- OSV360Craft does not modify Qt or PySide6. Its own source is public, so you can rebuild the app with any compatible PySide6 version (see `docs/BUILDING.md`).

## psutil (included in the OSV360Craft program folder)

**psutil**, © Giampaolo Rodola, BSD 3-Clause License: <https://github.com/giampaolo/psutil/blob/master/LICENSE>.

## Python (included in the OSV360Craft program folder)

The program folder contains the **Python** runtime and standard library, © Python Software Foundation, licensed under the Python Software Foundation License: <https://docs.python.org/3/license.html>.

## Nuitka (build tool)

The app is compiled with **Nuitka 4.3.4** (standalone mode): <https://nuitka.net>. See Nuitka's own documentation for the terms that apply to programs it compiles.

## FFmpeg and FFprobe (included in the installer and portable ZIP, folder `external\ffmpeg`)

The installer and portable ZIP contain **FFmpeg 9.0.2** (`n9.0.2-45-g1e65c6cd5d`): `ffmpeg.exe`, `ffprobe.exe` and their DLLs. This is the same build as ProxyCraft.

- These are unmodified binaries from BtbN FFmpeg-Builds, release `autobuild-2026-11-02-12-51`, file `ffmpeg-n9.0.2-45-g1e65c6cd5d-win64-gpl-shared-9.0.zip`, SHA-256 `5719531947c7168f05acac8f0f4cf8e9171b1eb8e0b469483fe8221dbb1d0991`.
- Licence: GNU General Public License v3 (GPL-3.0). The build includes GPL components such as x264 and x265. The licence text is in `external/ffmpeg/LICENSE.txt` and `licenses/GPL-3.0.txt`.
- FFmpeg is a separate program. OSV360Craft starts it as a separate process and does not link to it, so OSV360Craft's own code stays under the MIT License.
- Source code:
  - FFmpeg at commit `1e65c6cd5d3d2d12cbb374460dbf08bedf54772a`: <https://github.com/FFmpeg/FFmpeg/tree/1e65c6cd5d3d2d12cbb374460dbf08bedf54772a>
  - The build scripts, which pin the version of every library in this build: <https://github.com/BtbN/FFmpeg-Builds/tree/5ece62897c650b863c1ddda7b6964845344aef2d>
  - Both are attached to every OSV360Craft release on GitHub, as `FFmpeg-source-<commit>.tar.gz` and `FFmpeg-Builds-scripts-<tag>.zip`.
- More about FFmpeg's licensing: <https://ffmpeg.org/legal.html>.

FFmpeg is a trademark of Fabrice Bellard, originator of the FFmpeg project.

## Trademarks

DJI, Osmo, Osmo 360 and DJI Studio are trademarks of SZ DJI Technology Co., Ltd. NVIDIA, CUDA and NVENC are trademarks of NVIDIA Corporation. AMD is a trademark of Advanced Micro Devices, Inc. Intel and Quick Sync are trademarks of Intel Corporation. Windows is a trademark of Microsoft Corporation. OpenCL is a trademark of Apple Inc., used by permission by Khronos. These names are used only to describe compatibility. OSV360Craft is not affiliated with, endorsed by or sponsored by any of these companies.

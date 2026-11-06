# Building OSV360Craft for Windows

## Requirements

- Windows 10/11 x64, Python 3.12+ (64-bit) from python.org, and internet access for the first build.
- For the installer: Inno Setup 6. Install it with:

  ```bat
  winget install --id JRSoftware.InnoSetup -e
  ```

## One command

```bat
build.bat
```

`build.bat` does the following:

1. Installs the pinned tools from `requirements.txt`: PySide6 6.12.0, psutil, Nuitka 4.3.4 and ziglang.
2. Compiles `dist\OSV360Craft\` with `python -m nuitka --mode=standalone ...`: a normal program folder with `OSV360Craft.exe` and its DLLs. Nuitka uses the zig toolchain, so Visual Studio is not needed.
   - **Why not a single EXE:** a onefile build unpacks its code into `%TEMP%` at every start. Microsoft Defender's machine-learning check flagged that unpacked `launcher.dll` as `Trojan:Win32/Bearfoos.A!ml`. A standalone folder unpacks nothing.
3. Runs `scripts\get_openosv.ps1`, which downloads OpenOSV 0.6.4's Windows release and checks its SHA-256. It copies `osvtool.exe`, its DLLs and OpenOSV's licences to `vendor\OpenOSV`.
4. Runs `scripts\get_ffmpeg.ps1`, which downloads the pinned BtbN FFmpeg build and checks its SHA-256 (the same build ProxyCraft uses). It copies the build to `vendor\ffmpeg`, and the matching source archives to `vendor\release-sources`.
5. Copies both folders to `dist\OSV360Craft\external\` and zips the program folder as `OSV360Craft-<version>-Windows-portable.zip`.
6. Compiles `installer\OSV360Craft.iss` to `OSV360Craft-<version>-Setup.exe` when Inno Setup is found.

## Code signing (recommended)

Unsigned new apps are more likely to trigger SmartScreen and antivirus machine-learning checks. Open-source projects can get free code signing from [SignPath Foundation](https://signpath.org). Sign these files before attaching them to a release:

- `dist\OSV360Craft\OSV360Craft.exe`;
- the installer.

## Releasing

1. Update `osv360craft/__init__.py` (`__version__`) and `CHANGELOG.md`. `build.bat` reads the version from the package.
2. Run the tests, then run `build.bat` on Windows.
3. Create a GitHub release and attach:
   - `dist\OSV360Craft-<version>-Setup.exe`;
   - the portable ZIP;
   - the two FFmpeg source archives in `vendor\release-sources` (required by the GPL).

## Updating OpenOSV

1. Change `$Version` and `$ZipSha256` in `scripts\get_openosv.ps1`.
2. Run the tests with `OSV360CRAFT_TEST_OSVTOOL` pointing at the new `osvtool`. The option-compatibility test fails if an option OSV360Craft passes was renamed.
3. Save the new `osvtool render --help` output as `docs\osvtool-<version>-render-help.txt`.

## Running from source

See the README. The engine and FFmpeg are found in `vendor\`, `external\`, the usual install locations or `PATH`.

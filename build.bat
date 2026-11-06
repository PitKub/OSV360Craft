@echo off
rem Builds OSV360Craft for Windows: the program folder, the portable ZIP and the installer.
rem Needs: Python 3.12+ (64-bit), internet access for the first run, and for the
rem installer Inno Setup 6 (winget install --id JRSoftware.InnoSetup -e).
setlocal
cd /d "%~dp0"

python -m pip install -r requirements.txt
if errorlevel 1 goto :failed

for /f "delims=" %%V in ('python -c "import osv360craft; print(osv360craft.__version__)"') do set "APP_VERSION=%%V"
set "APP=dist\OSV360Craft"

rem Compile with Nuitka as a normal program folder (standalone), NOT a single
rem self-extracting EXE: a onefile build unpacks its code into %%TEMP%% on every
rem start, which antivirus machine-learning heuristics flag (for example
rem Microsoft Defender "Trojan:Win32/Bearfoos.A!ml" on the unpacked launcher.dll).
if exist "build\nuitka" rmdir /S /Q "build\nuitka"
python -m nuitka --mode=standalone --enable-plugins=pyside6 --noinclude-qt-translations ^
    --include-package=osv360craft --include-package=psutil ^
    --include-data-dir=assets=assets --include-data-dir=config=config ^
    --output-dir=build\nuitka --output-folder-name=OSV360Craft --output-filename=OSV360Craft.exe ^
    --zig --assume-yes-for-downloads --windows-console-mode=disable ^
    --windows-icon-from-ico=assets\OSV360Craft.ico --windows-company-name=PitKub ^
    --windows-product-name=OSV360Craft --windows-file-description=OSV360Craft ^
    --windows-product-version=%APP_VERSION%.0 --windows-file-version=%APP_VERSION%.0 ^
    launcher.py
if errorlevel 1 goto :failed
rem Nuitka adds ".dist" to --output-folder-name.
if not exist "build\nuitka\OSV360Craft.dist\OSV360Craft.exe" goto :missing_exe

if exist "dist" rmdir /S /Q "dist"
mkdir "dist" || goto :failed
move "build\nuitka\OSV360Craft.dist" "%APP%" >nul || goto :failed

copy /Y "README.md" "%APP%\README.md" >nul || goto :failed
copy /Y "LICENSE" "%APP%\LICENSE.txt" >nul || goto :failed
copy /Y "THIRD_PARTY_NOTICES.md" "%APP%\THIRD_PARTY_NOTICES.md" >nul || goto :failed
copy /Y "docs\USER_GUIDE.md" "%APP%\USER_GUIDE.md" >nul || goto :failed
if not exist "%APP%\licenses" mkdir "%APP%\licenses"
copy /Y "licenses\*.txt" "%APP%\licenses\" >nul || goto :failed

rem Bundled engine and encoder, pinned and checksum-verified by the scripts.
if not exist "vendor\OpenOSV\cli\osvtool.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\get_openosv.ps1"
    if errorlevel 1 goto :failed
)
if not exist "vendor\ffmpeg\ffmpeg.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\get_ffmpeg.ps1"
    if errorlevel 1 goto :failed
)
xcopy /E /I /Y /Q "vendor\OpenOSV" "%APP%\external\OpenOSV" >nul || goto :failed
xcopy /E /I /Y /Q "vendor\ffmpeg" "%APP%\external\ffmpeg" >nul || goto :failed

set "ZIP_NAME=OSV360Craft-%APP_VERSION%-Windows-portable.zip"
powershell -NoProfile -Command "Compress-Archive -Force -Path '%APP%' -DestinationPath 'dist\%ZIP_NAME%'"
if errorlevel 1 goto :failed

set "ISCC="
for %%I in (iscc.exe) do if not "%%~$PATH:I"=="" set "ISCC=%%~$PATH:I"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
set "SETUP_NAME=OSV360Craft-%APP_VERSION%-Setup.exe"
if defined ISCC (
    "%ISCC%" /Q /DAppVersion=%APP_VERSION% "installer\OSV360Craft.iss"
    if errorlevel 1 goto :failed
) else (
    set "SETUP_NAME="
    echo Inno Setup 6 was not found, so no installer was made.
)

echo.
echo Build complete.
echo App:       %APP%\OSV360Craft.exe  (with its DLLs, and external\OpenOSV and external\ffmpeg beside it)
if defined SETUP_NAME echo Installer: dist\%SETUP_NAME%
echo ZIP:       dist\%ZIP_NAME%
echo FFmpeg source to attach to the release: vendor\release-sources\
pause
exit /b 0

:missing_exe
echo The build finished without creating build\nuitka\OSV360Craft.dist\OSV360Craft.exe.
goto :failed

:failed
echo.
echo Build failed. Read the error above, fix the indicated issue, and retry.
pause
exit /b 1

@echo off
rem Builds OSV360Craft for Windows: the EXE, the release ZIP and the installer.
rem Needs: Python 3.12+ (64-bit), internet access for the first run, and for the
rem installer Inno Setup 6 (winget install --id JRSoftware.InnoSetup -e).
setlocal
cd /d "%~dp0"

python -m pip install -r requirements.txt
if errorlevel 1 goto :failed

for /f "delims=" %%S in ('python -c "import sysconfig; print(sysconfig.get_path('scripts'))"') do set "PY_SCRIPTS=%%S"
if not exist "%PY_SCRIPTS%\pyside6-deploy.exe" goto :missing_deploy

if not exist "dist" mkdir "dist"
if errorlevel 1 goto :failed

"%PY_SCRIPTS%\pyside6-deploy.exe" -c pysidedeploy.spec --force --nuitka-version 4.3.4
if errorlevel 1 goto :failed
if not exist "dist\OSV360Craft.exe" goto :missing_exe

if exist "dist\assets" rmdir /S /Q "dist\assets"
copy /Y "README.md" "dist\README.md" >nul || goto :failed
copy /Y "LICENSE" "dist\LICENSE.txt" >nul || goto :failed
copy /Y "THIRD_PARTY_NOTICES.md" "dist\THIRD_PARTY_NOTICES.md" >nul || goto :failed
copy /Y "docs\USER_GUIDE.md" "dist\USER_GUIDE.md" >nul || goto :failed
if not exist "dist\licenses" mkdir "dist\licenses"
copy /Y "licenses\*.txt" "dist\licenses\" >nul || goto :failed

rem Bundled engine and encoder, pinned and checksum-verified by the scripts.
if not exist "vendor\OpenOSV\cli\osvtool.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\get_openosv.ps1"
    if errorlevel 1 goto :failed
)
if not exist "vendor\ffmpeg\ffmpeg.exe" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "scripts\get_ffmpeg.ps1"
    if errorlevel 1 goto :failed
)
if exist "dist\external" rmdir /S /Q "dist\external"
xcopy /E /I /Y /Q "vendor\OpenOSV" "dist\external\OpenOSV" >nul || goto :failed
xcopy /E /I /Y /Q "vendor\ffmpeg" "dist\external\ffmpeg" >nul || goto :failed

for /f "delims=" %%V in ('python -c "import osv360craft; print(osv360craft.__version__)"') do set "APP_VERSION=%%V"
set "ZIP_NAME=OSV360Craft-%APP_VERSION%-Windows-portable.zip"
powershell -NoProfile -Command "Compress-Archive -Force -Path 'dist\OSV360Craft.exe','dist\README.md','dist\USER_GUIDE.md','dist\LICENSE.txt','dist\THIRD_PARTY_NOTICES.md','dist\licenses','dist\external' -DestinationPath 'dist\%ZIP_NAME%'"
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
echo EXE:       dist\OSV360Craft.exe  (finds dist\external\OpenOSV and dist\external\ffmpeg next to it)
if defined SETUP_NAME echo Installer: dist\%SETUP_NAME%
echo ZIP:       dist\%ZIP_NAME%
echo FFmpeg source to attach to the release: vendor\release-sources\
pause
exit /b 0

:missing_deploy
echo Could not find pyside6-deploy.exe in the Python Scripts folder.
echo Check that Python was installed correctly, then run build.bat again.
goto :failed

:missing_exe
echo The build finished without creating dist\OSV360Craft.exe.
goto :failed

:failed
echo.
echo Build failed. Read the error above, fix the indicated issue, and retry.
pause
exit /b 1

# Downloads the FFmpeg build that OSV360Craft releases include, and its source code.
#
#   powershell -ExecutionPolicy Bypass -File scripts\get_ffmpeg.ps1
#
# Result (all ignored by git):
#   vendor\ffmpeg\                ffmpeg.exe, ffprobe.exe, their DLLs, LICENSE.txt, SOURCE.txt
#   vendor\release-sources\       source archives to attach to the GitHub release (GPL)
#
# The build is pinned to one dated BtbN FFmpeg-Builds release and checked against its
# published SHA-256, so every OSV360Craft release ships exactly the binaries named here.
# To update FFmpeg, change the four values below.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$BuildTag    = "autobuild-2026-11-02-12-51"
$BuildZip    = "ffmpeg-n9.0.2-45-g1e65c6cd5d-win64-gpl-shared-9.0.zip"
$BuildSha256 = "5719531947c7168f05acac8f0f4cf8e9171b1eb8e0b469483fe8221dbb1d0991"
$FFmpegCommit = "1e65c6cd5d3d2d12cbb374460dbf08bedf54772a"   # FFmpeg n9.0.2-45-g1e65c6cd5d
$ScriptsCommit = "5ece62897c650b863c1ddda7b6964845344aef2d"  # BtbN/FFmpeg-Builds at $BuildTag

$root    = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)   # repository root
$vendor  = Join-Path $root "vendor"
$target  = Join-Path $vendor "ffmpeg"
$sources = Join-Path $vendor "release-sources"
$work    = Join-Path $vendor "download"
New-Item -ItemType Directory -Force $vendor, $sources, $work | Out-Null

function Get-File($url, $path) {
    if (Test-Path $path) { return }
    Write-Host "Downloading $url"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile "$path.part" -Headers @{ "User-Agent" = "OSV360Craft-build" }
    Move-Item -Force "$path.part" $path
}

# 1. The FFmpeg build, verified.
$zipPath = Join-Path $work $BuildZip
Get-File "https://github.com/BtbN/FFmpeg-Builds/releases/download/$BuildTag/$BuildZip" $zipPath
$actual = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $BuildSha256) {
    Remove-Item -Force $zipPath
    throw "Checksum mismatch for $BuildZip (got $actual). The download was deleted; run the script again."
}
Write-Host "Checksum OK: $BuildZip"

# 2. Keep only what OSV360Craft needs: ffmpeg.exe, ffprobe.exe, their DLLs and the licence.
$extract = Join-Path $work "extract"
if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
Expand-Archive -Path $zipPath -DestinationPath $extract
$buildRoot = Get-ChildItem $extract -Directory | Select-Object -First 1
if (Test-Path $target) { Remove-Item -Recurse -Force $target }
New-Item -ItemType Directory -Force $target | Out-Null
Copy-Item (Join-Path $buildRoot.FullName "bin\ffmpeg.exe"), (Join-Path $buildRoot.FullName "bin\ffprobe.exe") $target
Copy-Item (Join-Path $buildRoot.FullName "bin\*.dll") $target
Copy-Item (Join-Path $buildRoot.FullName "LICENSE.txt") (Join-Path $target "LICENSE.txt")
Remove-Item -Recurse -Force $extract

# 3. The corresponding source, to attach to each GitHub release.
$ffmpegSource  = "FFmpeg-source-$FFmpegCommit.tar.gz"
$scriptsSource = "FFmpeg-Builds-scripts-$BuildTag.zip"
Get-File "https://github.com/FFmpeg/FFmpeg/archive/$FFmpegCommit.tar.gz" (Join-Path $sources $ffmpegSource)
Get-File "https://github.com/BtbN/FFmpeg-Builds/archive/$ScriptsCommit.zip" (Join-Path $sources $scriptsSource)

$version = (& (Join-Path $target "ffmpeg.exe") -hide_banner -version | Select-Object -First 1)
@"
FFmpeg included with OSV360Craft
================================

$version

These are unmodified FFmpeg and FFprobe binaries from BtbN FFmpeg-Builds,
release $BuildTag, file $BuildZip
(SHA-256 $BuildSha256).

Licence: GNU General Public License version 3 (see LICENSE.txt in this folder).
FFmpeg is a separate program; OSV360Craft runs it and is itself MIT-licensed.

Source code for these binaries:
- FFmpeg, commit ${FFmpegCommit}:
  https://github.com/FFmpeg/FFmpeg/tree/$FFmpegCommit
- Build scripts, which pin the version of every library this build contains:
  https://github.com/BtbN/FFmpeg-Builds/tree/$ScriptsCommit
- Both are also attached to every OSV360Craft release on GitHub as
  $ffmpegSource and $scriptsSource.

To use a different FFmpeg, replace the files in this folder, or select another
ffmpeg.exe in OSV360Craft under Settings.
"@ | Set-Content -Encoding utf8 (Join-Path $target "SOURCE.txt")

$size = [math]::Round(((Get-ChildItem $target -File | Measure-Object Length -Sum).Sum) / 1MB, 1)
Write-Host ""
Write-Host "FFmpeg ready in vendor\ffmpeg ($size MB): $version"
Write-Host "Release sources in vendor\release-sources:"
Get-ChildItem $sources | ForEach-Object { Write-Host ("  {0} ({1} MB)" -f $_.Name, [math]::Round($_.Length / 1MB, 1)) }

# Downloads the OpenOSV engine that OSV360Craft releases include.
#
#   powershell -ExecutionPolicy Bypass -File scripts\get_openosv.ps1
#
# Result (ignored by git):
#   vendor\OpenOSV\cli\       osvtool.exe and exactly the DLLs it imports (LGPL FFmpeg, OpenCL ICD loader, ...)
#   vendor\OpenOSV\           LICENSE, NOTICE, README.txt, CHANGELOG.md and licenses\ from the OpenOSV release
#
# OpenOSV (https://github.com/Kemerd/OpenOSV) is Apache-2.0 software by The OpenOSV Contributors.
# The release is pinned and checked against the SHA-256 recorded below, so every OSV360Craft
# release ships exactly these binaries. OpenOSV's own osvgui.exe (and its glfw3.dll) is not
# needed and is left out. To update OpenOSV, change the three values below and run the tests.

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$Version   = "0.6.4"
$ZipName   = "OpenOSV-$Version-premiere-windows-x64.zip"
$ZipSha256 = "d7def9561dfef9292f5b522422880c4308b9830b0dc14c87c5733416b626a46c"

$root   = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$vendor = Join-Path $root "vendor"
$target = Join-Path $vendor "OpenOSV"
$work   = Join-Path $vendor "download"
New-Item -ItemType Directory -Force $vendor, $work | Out-Null

$zipPath = Join-Path $work $ZipName
if (-not (Test-Path $zipPath)) {
    $url = "https://github.com/Kemerd/OpenOSV/releases/download/v$Version/$ZipName"
    Write-Host "Downloading $url"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile "$zipPath.part" -Headers @{ "User-Agent" = "OSV360Craft-build" }
    Move-Item -Force "$zipPath.part" $zipPath
}
$actual = (Get-FileHash $zipPath -Algorithm SHA256).Hash.ToLower()
if ($actual -ne $ZipSha256) {
    Remove-Item -Force $zipPath
    throw "Checksum mismatch for $ZipName (got $actual). The download was deleted; run the script again."
}
Write-Host "Checksum OK: $ZipName"

$extract = Join-Path $work "openosv_extract"
if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
Expand-Archive -Path $zipPath -DestinationPath $extract
$rel = Get-ChildItem $extract -Directory | Select-Object -First 1
if (Test-Path $target) { Remove-Item -Recurse -Force $target }
New-Item -ItemType Directory -Force (Join-Path $target "cli"), (Join-Path $target "licenses") | Out-Null
Get-ChildItem (Join-Path $rel.FullName "cli") -File |
    Where-Object { $_.Name -notin @("osvgui.exe", "glfw3.dll") } |
    Copy-Item -Destination (Join-Path $target "cli")
foreach ($f in "LICENSE", "NOTICE", "README.txt", "CHANGELOG.md") {
    Copy-Item (Join-Path $rel.FullName $f) $target
}
Get-ChildItem (Join-Path $rel.FullName "licenses") -File |
    Where-Object { $_.Name -notin @("GLFW.txt", "Dear-ImGui.txt") } |
    Copy-Item -Destination (Join-Path $target "licenses")
Remove-Item -Recurse -Force $extract

$ver = & (Join-Path $target "cli\osvtool.exe") --version 2>&1 | Select-Object -First 1
Write-Host ""
Write-Host "OpenOSV ready in vendor\OpenOSV: $ver"

<#
.SYNOPSIS
  Build the Windows NSIS installer locally (same steps as the CI windows job).
.DESCRIPTION
  1. Build the PyInstaller --onedir sidecar and copy it into src-tauri\binaries\backend
  2. Download FFmpeg into src-tauri\binaries\ffmpeg
  3. Smoke-test the sidecar
  4. tauri build --bundles nsis
  Output: apps\desktop\src-tauri\target\release\bundle\nsis\Video Studio_<version>_x64-setup.exe
#>
param([switch]$SkipFfmpeg)
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

& (Join-Path $PSScriptRoot "build-sidecar.ps1")
if (-not $SkipFfmpeg) { & (Join-Path $PSScriptRoot "fetch-ffmpeg.ps1") }

$sidecar = Join-Path $Root "apps\desktop\src-tauri\binaries\backend\videostudio-backend.exe"
Push-Location (Join-Path $Root "backend")
uv run python ..\scripts\smoke_sidecar.py $sidecar
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Sidecar smoke test failed" }
Pop-Location

Push-Location (Join-Path $Root "apps\desktop")
try {
  npm ci
  npx tauri build --bundles nsis
  if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
} finally {
  Pop-Location
}
Get-ChildItem (Join-Path $Root "apps\desktop\src-tauri\target\release\bundle\nsis\*.exe") | ForEach-Object {
  Write-Host "Installer: $($_.FullName)"
}

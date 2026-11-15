<#
.SYNOPSIS
  Download a Windows x64 FFmpeg build into apps\desktop\src-tauri\binaries\ffmpeg.
.DESCRIPTION
  Binaries are never committed; this script runs in CI (and locally when building the
  installer). Tries the gyan.dev "essentials" release build first, then BtbN's GitHub
  release as a fallback.
#>
param(
  [string]$Dest = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) "apps\desktop\src-tauri\binaries\ffmpeg")
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # makes Invoke-WebRequest much faster

if ((Test-Path (Join-Path $Dest "ffmpeg.exe")) -and (Test-Path (Join-Path $Dest "ffprobe.exe"))) {
  Write-Host "FFmpeg already present in $Dest"
  exit 0
}

$sources = @(
  "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
  "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
)
$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("ffmpeg-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
$zip = Join-Path $tmp "ffmpeg.zip"

$ok = $false
foreach ($url in $sources) {
  try {
    Write-Host "Downloading $url"
    Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing
    $ok = $true
    break
  } catch {
    Write-Warning "Download failed: $($_.Exception.Message)"
  }
}
if (-not $ok) { throw "Could not download FFmpeg" }

Expand-Archive -Path $zip -DestinationPath $tmp -Force
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
foreach ($name in @("ffmpeg.exe", "ffprobe.exe")) {
  $found = Get-ChildItem -Path $tmp -Recurse -Filter $name | Select-Object -First 1
  if (-not $found) { throw "$name not found in archive" }
  Copy-Item $found.FullName -Destination (Join-Path $Dest $name) -Force
}
$license = Get-ChildItem -Path $tmp -Recurse -Include "LICENSE*", "COPYING*" | Select-Object -First 1
if ($license) { Copy-Item $license.FullName -Destination (Join-Path $Dest "FFMPEG-LICENSE.txt") -Force }
Remove-Item $tmp -Recurse -Force

& (Join-Path $Dest "ffmpeg.exe") -hide_banner -version | Select-Object -First 1
Write-Host "FFmpeg installed to $Dest"

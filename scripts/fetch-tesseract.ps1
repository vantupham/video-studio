<#
.SYNOPSIS
  Download Tesseract OCR for Windows x64 into apps\desktop\src-tauri\binaries\tesseract.
.DESCRIPTION
  Binaries are never committed; this script runs in CI (and locally when building the
  installer). Extracts tesseract.exe and its DLLs from the UB-Mannheim NSIS installer
  with 7-Zip (no installation, no admin rights), then adds the "fast" Vietnamese and
  English models to tessdata\ so OCR works offline on first use.
#>
param(
  [string]$Dest = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")) "apps\desktop\src-tauri\binaries\tesseract"),
  [string]$Version = "5.4.0.20240606"
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

$exe = Join-Path $Dest "tesseract.exe"
$models = @("vie", "eng")
if ((Test-Path $exe) -and -not ($models | Where-Object { -not (Test-Path (Join-Path $Dest "tessdata\$_.traineddata")) })) {
  Write-Host "Tesseract already present in $Dest"
  exit 0
}

$sevenZip = @(
  (Get-Command 7z -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
  "$env:ProgramFiles\7-Zip\7z.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $sevenZip) { throw "7-Zip is required to unpack the Tesseract installer" }

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("tesseract-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $tmp | Out-Null
$setup = Join-Path $tmp "tesseract-setup.exe"
$url = "https://github.com/UB-Mannheim/tesseract/releases/download/v$Version/tesseract-ocr-w64-setup-$Version.exe"
Write-Host "Downloading $url"
Invoke-WebRequest -Uri $url -OutFile $setup -UseBasicParsing

$unpacked = Join-Path $tmp "unpacked"
& $sevenZip x $setup "-o$unpacked" -y | Out-Null
if ($LASTEXITCODE -ne 0) { throw "7-Zip failed to unpack the Tesseract installer" }
$found = Get-ChildItem -Path $unpacked -Recurse -Filter "tesseract.exe" | Select-Object -First 1
if (-not $found) { throw "tesseract.exe not found in the installer" }
$srcDir = $found.DirectoryName

New-Item -ItemType Directory -Force -Path (Join-Path $Dest "tessdata") | Out-Null
Copy-Item $found.FullName -Destination $Dest -Force
# Only the runtime: tesseract.exe + DLLs (training tools, docs and the big "best"
# eng model are left out).
Get-ChildItem -Path $srcDir -Filter "*.dll" | Copy-Item -Destination $Dest -Force
foreach ($name in @("LICENSE", "LICENSE.txt", "COPYING")) {
  $lic = Join-Path $srcDir $name
  if (Test-Path $lic) { Copy-Item $lic -Destination (Join-Path $Dest "TESSERACT-LICENSE.txt") -Force; break }
}

foreach ($lang in $models) {
  $out = Join-Path $Dest "tessdata\$lang.traineddata"
  $modelUrl = "https://github.com/tesseract-ocr/tessdata_fast/raw/main/$lang.traineddata"
  Write-Host "Downloading $modelUrl"
  Invoke-WebRequest -Uri $modelUrl -OutFile $out -UseBasicParsing
  if ((Get-Item $out).Length -lt 100000) { throw "$lang.traineddata looks invalid" }
}
Remove-Item $tmp -Recurse -Force

& $exe --version 2>&1 | Select-Object -First 1
& $exe --list-langs --tessdata-dir (Join-Path $Dest "tessdata") 2>&1
if ($LASTEXITCODE -ne 0) { throw "tesseract.exe does not run" }
$mb = [math]::Round(((Get-ChildItem $Dest -Recurse | Measure-Object Length -Sum).Sum) / 1MB, 1)
Write-Host "Tesseract installed to $Dest ($mb MB)"

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
# Only the runtime: tesseract.exe + the DLLs it imports. The installer also carries the
# training tools and their dependencies (pango, cairo, ICU, glib…), docs and the big
# "best" eng model, none of which OCR needs.
Get-ChildItem -Path $srcDir -Filter "*.dll" | Copy-Item -Destination $Dest -Force
foreach ($name in @("LICENSE", "LICENSE.txt", "COPYING")) {
  $lic = Join-Path $srcDir $name
  if (Test-Path $lic) { Copy-Item $lic -Destination (Join-Path $Dest "TESSERACT-LICENSE.txt") -Force; break }
}
$prune = Join-Path $PSScriptRoot "prune_dlls.py"
if (Get-Command uv -ErrorAction SilentlyContinue) {
  & uv run --no-project python $prune $Dest tesseract.exe
} else {
  & python $prune $Dest tesseract.exe
}
if ($LASTEXITCODE -ne 0) { throw "Pruning unused Tesseract DLLs failed" }

# The UB-Mannheim binaries keep their DWARF debug info (libtesseract-5.dll alone is ~100 MB,
# ~4 MB without it). Any strip that understands PE works; the build still succeeds without.
$strip = @(
  (Get-Command llvm-strip -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
  "$env:ProgramFiles\LLVM\bin\llvm-strip.exe",
  (Get-Command strip -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
  "C:\msys64\mingw64\bin\strip.exe",
  "C:\Strawberry\c\bin\strip.exe"
) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($strip) {
  Write-Host "Stripping debug info with $strip"
  Get-ChildItem -Path $Dest -Include "*.dll", "*.exe" -File -Recurse | ForEach-Object {
    & $strip --strip-debug $_.FullName
    if ($LASTEXITCODE -ne 0) { throw "strip failed on $($_.Name)" }
  }
} else {
  Write-Warning "No strip tool found (llvm-strip / mingw strip): Tesseract DLLs keep their debug info (~100 MB larger)"
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

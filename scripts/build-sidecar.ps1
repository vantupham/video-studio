<#
.SYNOPSIS
  Build the backend sidecar (PyInstaller --onedir) and copy it to
  apps\desktop\src-tauri\binaries\backend for Tauri to bundle as a resource.
#>
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Dest = Join-Path $Root "apps\desktop\src-tauri\binaries\backend"

Push-Location (Join-Path $Root "backend")
try {
  uv sync --group build --quiet
  if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
  uv run pyinstaller packaging/videostudio-backend.spec --noconfirm --clean --distpath dist --workpath build
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
} finally {
  Pop-Location
}

Get-ChildItem $Dest -Force | Where-Object { $_.Name -ne "README.md" } | Remove-Item -Recurse -Force
Copy-Item -Path (Join-Path $Root "backend\dist\videostudio-backend\*") -Destination $Dest -Recurse -Force
Write-Host "Sidecar copied to $Dest"

<#
.SYNOPSIS
  Run Video Studio in development mode on Windows.
.DESCRIPTION
  Starts the FastAPI backend with auto-reload (token "dev-token") and then either the
  Tauri desktop window (default) or only the Vite dev server (-Web).
  Data is stored in <repo>\.dev-data unless VIDEOSTUDIO_DATA_DIR is set.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1 -Web
#>
param(
  [switch]$Web,
  [int]$Port = 8765
)
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Token = "dev-token"

if (-not $env:VIDEOSTUDIO_DATA_DIR) { $env:VIDEOSTUDIO_DATA_DIR = Join-Path $Root ".dev-data" }
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

foreach ($tool in @("uv", "npm")) {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "$tool is required (see README)" }
}

Push-Location (Join-Path $Root "backend")
uv sync --quiet
Pop-Location
if (-not (Test-Path (Join-Path $Root "apps\desktop\node_modules"))) {
  Push-Location (Join-Path $Root "apps\desktop"); npm ci; Pop-Location
}

Write-Host "Backend: http://127.0.0.1:$Port (token: $Token, data: $env:VIDEOSTUDIO_DATA_DIR)"
$env:VIDEOSTUDIO_TOKEN = $Token
$env:VIDEOSTUDIO_DEV = "1"
$backend = Start-Process -FilePath "uv" -ArgumentList @("run", "python", "-m", "videostudio", "--port", "$Port", "--reload") `
  -WorkingDirectory (Join-Path $Root "backend") -NoNewWindow -PassThru

try {
  Push-Location (Join-Path $Root "apps\desktop")
  if ($Web) {
    $env:VITE_BACKEND_URL = "http://127.0.0.1:$Port"
    $env:VITE_BACKEND_TOKEN = $Token
    Write-Host "UI: http://localhost:1420"
    npm run dev
  } else {
    $env:VIDEOSTUDIO_BACKEND_URL = "http://127.0.0.1:$Port"
    $env:VIDEOSTUDIO_BACKEND_TOKEN = $Token
    npx tauri dev
  }
} finally {
  Pop-Location
  if ($backend -and -not $backend.HasExited) {
    # /T also stops the uvicorn reloader's worker process.
    taskkill /PID $backend.Id /T /F | Out-Null
  }
}

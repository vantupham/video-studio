<#
.SYNOPSIS
  Install development dependencies on Windows (backend venv + frontend node_modules).
.DESCRIPTION
  Prerequisites: Node.js LTS, Rust stable (MSVC) + Visual Studio Build Tools, uv.
  Python 3.11+ is installed automatically by uv if missing.
#>
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
foreach ($tool in @("uv", "npm", "cargo")) {
  if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { Write-Warning "$tool not found on PATH" }
}
Push-Location (Join-Path $Root "backend"); uv sync; Pop-Location
Push-Location (Join-Path $Root "apps\desktop"); npm ci; Pop-Location
Write-Host "Done. Run: powershell -ExecutionPolicy Bypass -File scripts\dev.ps1"

<#
.SYNOPSIS
  Install the NSIS installer silently and verify the installed app (used by CI).
.DESCRIPTION
  1. Silent per-user install into a temp folder.
  2. Check the resource layout (backend\videostudio-backend.exe, backend\_internal, ffmpeg\,
     tesseract\ with the Vietnamese model).
  3. Smoke-test the installed sidecar, including a render of both styles with the
     bundled FFmpeg, fonts and style packs (demo LLM + offline mock voice). With
     -Fixtures: ingest DOCX, text PDF and a scanned PDF (bundled Tesseract OCR), render
     the DOCX and publish it to a mock YouTube account.
  4. Launch the app with VIDEOSTUDIO_NO_TRAY=1, check that it starts the sidecar, close
     the window and check that the sidecar is stopped.
  5. Launch normally: closing the window must only hide it (tray), the app and sidecar
     keep running; then kill the app hard and check the watchdog stops the sidecar.
  6. Silent uninstall.
#>
param(
  [Parameter(Mandatory = $true)][string]$Installer,
  [string]$InstallDir = (Join-Path $env:RUNNER_TEMP "VideoStudioInstall"),
  [string]$Fixtures = ""
)
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")

function Get-Sidecars {
  Get-CimInstance Win32_Process -Filter "Name = 'videostudio-backend.exe'" |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($InstallDir, [StringComparison]::OrdinalIgnoreCase) }
}

function Wait-Until([scriptblock]$Condition, [int]$Seconds, [string]$What) {
  $deadline = (Get-Date).AddSeconds($Seconds)
  while ((Get-Date) -lt $deadline) {
    if (& $Condition) { return }
    Start-Sleep -Milliseconds 500
  }
  throw "Timed out waiting for: $What"
}

Write-Host "== Installing $Installer into $InstallDir"
# /D must be the last argument and must not be quoted.
$p = Start-Process -FilePath $Installer -ArgumentList @("/S", "/D=$InstallDir") -Wait -PassThru
if ($p.ExitCode -ne 0) { throw "Installer exited with $($p.ExitCode)" }
Get-ChildItem $InstallDir | Format-Table Name, Length

$sidecar = Join-Path $InstallDir "backend\videostudio-backend.exe"
$required = @(
  $sidecar,
  (Join-Path $InstallDir "backend\_internal"),
  (Join-Path $InstallDir "ffmpeg\ffmpeg.exe"),
  (Join-Path $InstallDir "ffmpeg\ffprobe.exe"),
  (Join-Path $InstallDir "tesseract\tesseract.exe"),
  (Join-Path $InstallDir "tesseract\tessdata\vie.traineddata")
)
foreach ($path in $required) {
  if (-not (Test-Path $path)) { throw "Missing in install dir: $path" }
}
$tessMb = [math]::Round(((Get-ChildItem (Join-Path $InstallDir "tesseract") -Recurse | Measure-Object Length -Sum).Sum) / 1MB, 1)
Write-Host "Bundled Tesseract: $tessMb MB installed"
if ($tessMb -gt 60) { throw "Bundled Tesseract is $tessMb MB: unused DLLs or debug info were not removed (fetch-tesseract.ps1)" }

Write-Host "== Smoke-testing installed sidecar"
Push-Location (Join-Path $Root "backend")
$smokeArgs = @($sidecar, "--render")
if ($Fixtures) { $smokeArgs += @("--documents", $Fixtures, "--publish") }
uv run python ..\scripts\smoke_sidecar.py @smokeArgs
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { throw "Installed sidecar smoke test failed" }

$appExe = Get-ChildItem $InstallDir -Filter *.exe | Where-Object { $_.Name -notlike "uninstall*" } | Select-Object -First 1
if (-not $appExe) { throw "Main executable not found" }
Write-Host "== Launching $($appExe.FullName)"

$env:VIDEOSTUDIO_NO_TRAY = "1"
$app = Start-Process -FilePath $appExe.FullName -PassThru
Remove-Item Env:VIDEOSTUDIO_NO_TRAY
Wait-Until { @(Get-Sidecars).Count -ge 1 } 90 "sidecar to be started by the app"
$sc = @(Get-Sidecars)[0]
Write-Host "Sidecar running: pid $($sc.ProcessId), parent $($sc.ParentProcessId) (app pid $($app.Id))"
if ($sc.ParentProcessId -ne $app.Id) { throw "Sidecar parent is not the app" }

Write-Host "== Graceful close: sidecar must stop"
Wait-Until { $app.Refresh(); $app.MainWindowHandle -ne 0 } 30 "main window"
$null = $app.CloseMainWindow()
Wait-Until { @(Get-Sidecars).Count -eq 0 } 30 "sidecar to stop after window close"
Wait-Until { $app.HasExited } 15 "app to exit"

Write-Host "== Close to tray: the window hides, app and sidecar keep running"
$app = Start-Process -FilePath $appExe.FullName -PassThru
Wait-Until { @(Get-Sidecars).Count -ge 1 } 90 "sidecar to be started by the app (2nd run)"
Wait-Until { $app.Refresh(); $app.MainWindowHandle -ne 0 } 30 "main window (2nd run)"
$null = $app.CloseMainWindow()
Start-Sleep -Seconds 5
$app.Refresh()
if ($app.HasExited) { throw "App exited on window close; expected to keep running in the tray" }
if (@(Get-Sidecars).Count -lt 1) { throw "Sidecar stopped although the app is in the tray" }
Write-Host "App still running in the tray (pid $($app.Id))"

Write-Host "== Hard kill: watchdog must stop the sidecar"
Stop-Process -Id $app.Id -Force
Wait-Until { @(Get-Sidecars).Count -eq 0 } 30 "sidecar to stop after the app was killed"

Write-Host "== Uninstalling"
$uninstaller = Join-Path $InstallDir "uninstall.exe"
if (Test-Path $uninstaller) {
  Start-Process -FilePath $uninstaller -ArgumentList "/S" -Wait | Out-Null
}
Write-Host "INSTALLER VERIFICATION PASSED"

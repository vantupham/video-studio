# FFmpeg (downloaded, not committed)

`scripts/fetch-ffmpeg.ps1` downloads a Windows x64 build and places `ffmpeg.exe` and
`ffprobe.exe` here. Tauri installs this folder to `<install dir>/ffmpeg/` and the shell
passes it to the backend via `VIDEOSTUDIO_FFMPEG_DIR`.

During development on Linux/macOS the backend falls back to `ffmpeg` on `PATH`.

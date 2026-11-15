# Backend sidecar (generated, not committed)

`scripts/build-sidecar.ps1` (Windows) / `scripts/build-sidecar.sh` copies the PyInstaller
`--onedir` output here:

```
binaries/backend/
  videostudio-backend.exe
  _internal/...
```

Tauri bundles this whole folder as a resource (`bundle.resources` in `tauri.conf.json`),
so it is installed to `<install dir>/backend/`. The Rust shell launches
`backend/videostudio-backend.exe` from the resource directory.

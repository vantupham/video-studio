#!/usr/bin/env bash
# Build the backend sidecar with PyInstaller (--onedir) and copy it into
# apps/desktop/src-tauri/binaries/backend so Tauri bundles it as a resource.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/apps/desktop/src-tauri/binaries/backend"

cd "$ROOT/backend"
uv sync --group build --quiet
uv run pyinstaller packaging/videostudio-backend.spec --noconfirm --clean \
  --distpath dist --workpath build

find "$DEST" -mindepth 1 -maxdepth 1 ! -name README.md -exec rm -rf {} +
cp -a dist/videostudio-backend/. "$DEST/"
echo "Sidecar copied to $DEST"

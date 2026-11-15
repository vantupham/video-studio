#!/usr/bin/env bash
# Install development dependencies (backend venv + frontend node_modules).
# Linux also needs the Tauri system packages, e.g. on Debian/Ubuntu:
#   sudo apt install libwebkit2gtk-4.1-dev libsoup-3.0-dev librsvg2-dev \
#     libayatana-appindicator3-dev build-essential pkg-config libssl-dev
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
(cd "$ROOT/backend" && uv sync)
(cd "$ROOT/apps/desktop" && npm ci)
echo "Done. Run: scripts/dev.sh   (or scripts/dev.sh --web)"

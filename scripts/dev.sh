#!/usr/bin/env bash
# Run Video Studio in development mode (Linux/macOS).
#
#   scripts/dev.sh          backend (auto-reload) + Tauri desktop window
#   scripts/dev.sh --web    backend (auto-reload) + Vite only -> open http://localhost:1420
#
# The backend uses the development token "dev-token" and stores data in
# <repo>/.dev-data unless VIDEOSTUDIO_DATA_DIR is set.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="tauri"
[[ "${1:-}" == "--web" ]] && MODE="web"

PORT="${VIDEOSTUDIO_DEV_PORT:-8765}"
TOKEN="dev-token"
export VIDEOSTUDIO_DATA_DIR="${VIDEOSTUDIO_DATA_DIR:-$ROOT/.dev-data}"
export PYTHONUTF8=1

command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
command -v npm >/dev/null || { echo "Node.js (npm) is required"; exit 1; }

(cd "$ROOT/backend" && uv sync --quiet)
[[ -d "$ROOT/apps/desktop/node_modules" ]] || (cd "$ROOT/apps/desktop" && npm ci)

echo "Backend: http://127.0.0.1:$PORT (token: $TOKEN, data: $VIDEOSTUDIO_DATA_DIR)"
(cd "$ROOT/backend" && VIDEOSTUDIO_TOKEN="$TOKEN" VIDEOSTUDIO_DEV=1 \
  uv run python -m videostudio --port "$PORT" --reload) &
BACKEND_PID=$!
trap 'kill $BACKEND_PID 2>/dev/null || true; wait $BACKEND_PID 2>/dev/null || true' EXIT INT TERM

cd "$ROOT/apps/desktop"
if [[ "$MODE" == "web" ]]; then
  echo "UI: http://localhost:1420"
  VITE_BACKEND_URL="http://127.0.0.1:$PORT" VITE_BACKEND_TOKEN="$TOKEN" npm run dev
else
  VIDEOSTUDIO_BACKEND_URL="http://127.0.0.1:$PORT" VIDEOSTUDIO_BACKEND_TOKEN="$TOKEN" \
    npx tauri dev
fi

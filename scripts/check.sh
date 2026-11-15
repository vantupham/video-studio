#!/usr/bin/env bash
# Run every check that CI runs on Linux.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "== backend: ruff + pytest"
(cd "$ROOT/backend" && uv run ruff check . && uv run pytest -q)

echo "== frontend: typecheck + lint + test + build"
(cd "$ROOT/apps/desktop" && npm run typecheck && npm run lint && npm test && npm run build)

if command -v cargo >/dev/null; then
  echo "== tauri: cargo check"
  (cd "$ROOT/apps/desktop/src-tauri" && cargo check)
fi

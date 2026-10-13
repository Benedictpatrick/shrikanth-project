#!/usr/bin/env sh
# One-command launcher (macOS/Linux)
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="$ROOT/backend/.venv/bin/python"
if [ ! -x "$PY" ]; then
  python3 -m venv "$ROOT/backend/.venv"
  "$PY" -m pip install -q -r "$ROOT/backend/requirements.txt"
fi
if [ ! -d "$ROOT/frontend/dist" ]; then
  (cd "$ROOT/frontend" && npm install --no-audit --no-fund && npm run build)
fi
echo "Legacy Code Automation -> http://127.0.0.1:8000"
cd "$ROOT/backend" && exec "$PY" -m uvicorn app.main:app --port 8000

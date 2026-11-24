#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  source "$ROOT_DIR/.env"
  set +a
fi

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"

cleanup() {
  echo ""
  echo "Stopping Research Canvas servers..."
  kill "${BACKEND_PID:-}" "${FRONTEND_PID:-}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting FastAPI backend on http://$BACKEND_HOST:$BACKEND_PORT ..."
(
  cd "$ROOT_DIR/backend"
  exec uv run uvicorn app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
) &
BACKEND_PID=$!

echo "Starting Vite frontend on http://127.0.0.1:$FRONTEND_PORT ..."
(
  cd "$ROOT_DIR/frontend"
  exec pnpm dev --host 127.0.0.1 --port "$FRONTEND_PORT"
) &
FRONTEND_PID=$!

echo "Research Canvas is running:"
echo "  Frontend UI:  http://127.0.0.1:$FRONTEND_PORT"
echo "  Backend API:  http://$BACKEND_HOST:$BACKEND_PORT/api/health"
echo "Press Ctrl+C to stop both services."

wait

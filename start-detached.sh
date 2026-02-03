#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="${PID_DIR:-/tmp/llm-council}"
LOG_DIR="${LOG_DIR:-/tmp/llm-council}"

mkdir -p "$PID_DIR" "$LOG_DIR"

BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"
BACKEND_LOG="$LOG_DIR/backend.log"
FRONTEND_LOG="$LOG_DIR/frontend.log"

is_running() {
  local pid_file="$1"
  if [[ -f "$pid_file" ]]; then
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

echo "Starting LLM Council (detached)..."
echo ""

if is_running "$BACKEND_PID_FILE"; then
  echo "Backend already running (pid $(cat "$BACKEND_PID_FILE"))."
else
  echo "Starting backend on http://localhost:8001..."
  cd "$ROOT_DIR"
  nohup env UV_CACHE_DIR=/tmp/uv-cache PYTHONDONTWRITEBYTECODE=1 \
    uv run python -m backend.main --host 0.0.0.0 --port 8001 \
    > "$BACKEND_LOG" 2>&1 & echo $! > "$BACKEND_PID_FILE"
fi

if is_running "$FRONTEND_PID_FILE"; then
  echo "Frontend already running (pid $(cat "$FRONTEND_PID_FILE"))."
else
  echo "Starting frontend on http://localhost:5173..."
  cd "$ROOT_DIR/frontend"
  nohup npm run dev -- --host 0.0.0.0 --port 5173 --strictPort \
    > "$FRONTEND_LOG" 2>&1 & echo $! > "$FRONTEND_PID_FILE"
fi

echo ""
echo "✓ LLM Council is running in the background"
echo "  Backend:  http://localhost:8001"
echo "  Frontend: http://localhost:5173"
echo "  LAN:      http://<HOST_IP>:5173"
echo ""
echo "Logs:"
echo "  Backend:  $BACKEND_LOG"
echo "  Frontend: $FRONTEND_LOG"
echo ""
echo "To stop: ./stop-detached.sh"

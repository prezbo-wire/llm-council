#!/bin/bash
set -euo pipefail

PID_DIR="${PID_DIR:-/tmp/llm-council}"
BACKEND_PID_FILE="$PID_DIR/backend.pid"
FRONTEND_PID_FILE="$PID_DIR/frontend.pid"

stop_pid() {
  local name="$1"
  local pid_file="$2"
  if [[ ! -f "$pid_file" ]]; then
    echo "$name not running (no pid file)."
    return
  fi

  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    echo "$name pid file empty."
    rm -f "$pid_file"
    return
  fi

  if ! kill -0 "$pid" 2>/dev/null; then
    echo "$name not running (stale pid $pid)."
    rm -f "$pid_file"
    return
  fi

  echo "Stopping $name (pid $pid)..."
  kill "$pid" 2>/dev/null || true

  for _ in {1..10}; do
    if ! kill -0 "$pid" 2>/dev/null; then
      rm -f "$pid_file"
      echo "$name stopped."
      return
    fi
    sleep 0.3
  done

  echo "$name still running; forcing stop..."
  kill -9 "$pid" 2>/dev/null || true
  rm -f "$pid_file"
  echo "$name stopped."
}

stop_pid "Frontend" "$FRONTEND_PID_FILE"
stop_pid "Backend" "$BACKEND_PID_FILE"

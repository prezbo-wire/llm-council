#!/bin/bash
set -euo pipefail

# LLM Council - start backend and frontend in the background (stop with ./stop-detached.sh).
# Mese (the model router) must already be running; see README.md.

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="${PID_DIR:-/tmp/llm-council}"
LOG_DIR="${LOG_DIR:-/tmp/llm-council}"
DEFAULT_ORIGINS="http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000"

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

# Prints KEY's value from .env (last assignment wins), without surrounding quotes. Never echoes secrets.
env_file_value() {
  local key="$1" file="$ROOT_DIR/.env"
  [[ -f "$file" ]] || return 0
  { grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" || true; } | tail -n 1 |
    sed -E "s/^[[:space:]]*(export[[:space:]]+)?${key}=//; s/^[\"']//; s/[\"'][[:space:]]*$//"
}

# CORS origins: COUNCIL_ALLOWED_ORIGINS (env, else .env, else localhost defaults) plus the LAN and
# tailnet origins on :5173, de-duplicated.
council_origins() {
  local base="${COUNCIL_ALLOWED_ORIGINS:-$(env_file_value COUNCIL_ALLOWED_ORIGINS)}"
  local lan_ip="" ts_ip="" origins
  origins="${base:-$DEFAULT_ORIGINS}"
  lan_ip="$(ipconfig getifaddr en0 2>/dev/null || true)"
  if command -v tailscale >/dev/null 2>&1; then
    ts_ip="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
  elif [[ -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]]; then
    ts_ip="$(/Applications/Tailscale.app/Contents/MacOS/Tailscale ip -4 2>/dev/null | head -n 1 || true)"
  fi
  if [[ -n "$lan_ip" ]]; then origins="$origins,http://$lan_ip:5173"; fi
  if [[ -n "$ts_ip" ]]; then origins="$origins,http://$ts_ip:5173"; fi
  echo "$origins" | tr ',' '\n' | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//' | awk 'NF && !seen[$0]++' | paste -sd, -
}

check_mese() {
  local mese_url="${MESE_URL:-$(env_file_value MESE_URL)}"
  mese_url="${mese_url:-http://127.0.0.1:8790}"
  if [[ -z "${MESE_API_KEY:-}" && -z "$(env_file_value MESE_API_KEY)" ]]; then
    echo "WARNING: MESE_API_KEY is not set. Put the Mese host's ~/.mese/tokens/llm-council.token into .env" >&2
    echo "         as MESE_API_KEY=... (Mese on the Spark: copy it over ssh without printing it, see README)." >&2
    echo "         Until then every council call is rejected by Mese." >&2
  fi
  if command -v curl >/dev/null 2>&1 && ! curl -fsS -m 3 "$mese_url/healthz" >/dev/null 2>&1; then
    echo "WARNING: Mese is not reachable at $mese_url (GET /healthz failed)." >&2
    case "$mese_url" in
      http://127.0.0.1:*|http://localhost:*|http://\[::1\]:*)
        echo "         If Mese runs on the Spark, set MESE_URL=http://<spark-tailnet-ip>:8790 in .env." >&2
        echo "         If it runs here, start it (in the mese repo: bun src/server/main.ts)." >&2 ;;
      *)
        echo "         Check that MESE_URL is the Mese host's tailnet IP, that the tailnet ACL lets this machine" >&2
        echo "         reach port 8790 there, and that the service runs (on that host: systemctl --user status mese)." >&2 ;;
    esac
  fi
  echo "Mese:     $mese_url"
}

# The backend has no auth: anything that can reach COUNCIL_HOST:8001 can read conversations and spend
# through Mese. Print the bind so it is visible; README "Exposure" explains COUNCIL_HOST=<tailnet IP>.
print_bind() {
  local host="$1"
  echo "Backend:  bind $host:8001"
  if [[ "$host" == "0.0.0.0" || "$host" == "::" ]]; then
    echo "          (all interfaces, LAN included, and no auth; set COUNCIL_HOST to this machine's tailnet IP to narrow it)"
  fi
}

COUNCIL_ALLOWED_ORIGINS="$(council_origins)"
export COUNCIL_ALLOWED_ORIGINS
COUNCIL_HOST="${COUNCIL_HOST:-$(env_file_value COUNCIL_HOST)}"
COUNCIL_HOST="${COUNCIL_HOST:-0.0.0.0}"

echo "Starting LLM Council (detached)..."
echo ""
check_mese
print_bind "$COUNCIL_HOST"
echo "CORS:     $COUNCIL_ALLOWED_ORIGINS"
echo ""

if is_running "$BACKEND_PID_FILE"; then
  echo "Backend already running (pid $(cat "$BACKEND_PID_FILE"))."
else
  echo "Starting backend on http://$COUNCIL_HOST:8001..."
  cd "$ROOT_DIR"
  nohup env UV_CACHE_DIR=/tmp/uv-cache PYTHONDONTWRITEBYTECODE=1 \
    uv run python -m backend.main --host "$COUNCIL_HOST" --port 8001 \
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

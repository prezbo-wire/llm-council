#!/bin/bash

# LLM Council - start backend and frontend in the foreground (Ctrl+C stops both).
# Mese (the model router) must already be running; see README.md.

set -uo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

DEFAULT_ORIGINS="http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000"

# Prints KEY's value from .env (last assignment wins), without surrounding quotes. Never echoes secrets.
env_file_value() {
  local key="$1" file="$ROOT_DIR/.env"
  [[ -f "$file" ]] || return 0
  grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" | tail -n 1 |
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
  [[ -n "$lan_ip" ]] && origins="$origins,http://$lan_ip:5173"
  [[ -n "$ts_ip" ]] && origins="$origins,http://$ts_ip:5173"
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

export COUNCIL_ALLOWED_ORIGINS="$(council_origins)"

# Display only: the backend reads COUNCIL_HOST itself (environment, else .env, else 0.0.0.0).
BIND_HOST="${COUNCIL_HOST:-$(env_file_value COUNCIL_HOST)}"

echo "Starting LLM Council..."
echo ""
check_mese
print_bind "${BIND_HOST:-0.0.0.0}"
echo "CORS:     $COUNCIL_ALLOWED_ORIGINS"
echo ""

echo "Starting backend on http://localhost:8001..."
uv run python -m backend.main &
BACKEND_PID=$!

# Wait a bit for backend to start
sleep 2

echo "Starting frontend on http://localhost:5173..."
cd "$ROOT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "✓ LLM Council is running!"
echo "  Backend:  http://localhost:8001"
echo "  Frontend: http://localhost:5173"
echo "  LAN:      http://<HOST_IP>:5173"
echo ""
echo "Press Ctrl+C to stop both servers"

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null; exit" SIGINT SIGTERM
wait

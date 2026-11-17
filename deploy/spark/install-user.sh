#!/usr/bin/env bash
# Install or update llm-council on the DGX Spark, next to Mese, as two systemd --user services without sudo.
# deploy/spark/deploy.sh (on the Mac) copies the checkout here and runs this; run it by hand to re-apply.
#
#   deploy/spark/install-user.sh               # install or update, then (re)start both services
#   deploy/spark/install-user.sh --no-start    # install and enable, but leave them stopped
#   deploy/spark/install-user.sh --skip-sync   # skip `uv sync` (config and units only)
#
# Steps: check uv → uv sync --frozen --no-dev (Python from .python-version) → check frontend/dist →
# ~/.config/llm-council/llm-council.env (0600, written once, never overwritten) →
# ~/.config/llm-council/mese-token.env (0600, rewritten every run from Mese's llm-council token) →
# data dir (0700) → ~/.config/systemd/user/llm-council{,-ui}.service → enable (and restart) →
# check linger → wait for the backend and the UI.
#
# Idempotent. It prints paths only, never the token.
#
# First install only (ignored once ~/.config/llm-council/llm-council.env exists):
#   COUNCIL_HOST      bind address of the backend and the UI (default: the IPv4 of tailscale0)
#   COUNCIL_DATA_DIR  conversations and presets (default ~/.llm-council)
# Every run:
#   MESE_TOKEN_FILE   Mese's client token for llm-council (default: <MESE_DATA_DIR from
#                     ~/.config/mese/mese.env, else ~/.mese>/tokens/llm-council.token)
set -euo pipefail
umask 077

NO_START=0
SKIP_SYNC=0
for arg in "$@"; do
  case "$arg" in
    --no-start) NO_START=1 ;;
    --skip-sync) SKIP_SYNC=1 ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

say() { printf 'llm-council-install: %s\n' "$*"; }
warn() { printf 'llm-council-install: WARNING: %s\n' "$*" >&2; }
die() { printf 'llm-council-install: %s\n' "$*" >&2; exit 1; }

# Paths end up in systemd units and an EnvironmentFile: allow only characters that need no quoting there
# (no spaces, no '%' specifiers, no quotes or backslashes).
safe_path() {
  [[ "$2" =~ ^/[A-Za-z0-9._/+@-]+$ ]] || die "$1 must be an absolute path of letters, digits and ._/+@- only: '$2'"
}

perm_of() { stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd -P)"
CONFIG_DIR="$HOME/.config/llm-council"   # the units read %h/.config/llm-council/*.env
ENV_FILE="$CONFIG_DIR/llm-council.env"
TOKEN_ENV="$CONFIG_DIR/mese-token.env"
MESE_ENV="$HOME/.config/mese/mese.env"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNITS=(llm-council llm-council-ui)
ME="$(id -un)"

[[ $EUID -ne 0 ]] || die "run this as your own user, not as root: it installs systemd --user services"
[[ "$(uname -s)" == Linux ]] || die "this installer is for Linux with systemd --user"
command -v systemctl >/dev/null || die "systemctl not found"
safe_path "the checkout" "$REPO"
safe_path "\$HOME" "$HOME"
[[ -f "$REPO/backend/main.py" && -f "$REPO/uv.lock" ]] || die "$REPO does not look like the llm-council checkout"

# python-dotenv also reads a .env in the checkout or any parent directory for settings the units leave unset.
for dir in "$REPO" "$(dirname "$REPO")" "$HOME"; do
  if [[ -f "$dir/.env" ]]; then
    warn "$dir/.env exists; the backend also reads it for any variable the units do not set"
  fi
done

# ── 1. uv and the Python environment ──
UV=""
for candidate in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" "$(command -v uv || true)"; do
  if [[ -n "$candidate" && -x "$candidate" ]]; then UV="$candidate"; break; fi
done
[[ -n "$UV" ]] || die "uv is not installed. Install it (no sudo), then re-run this script:
  curl -LsSf https://astral.sh/uv/install.sh | sh"
say "$("$UV" --version): $UV"
if (( SKIP_SYNC )); then
  say "skipping uv sync (--skip-sync)"
else
  say "installing dependencies (uv sync --frozen --no-dev, Python $(cat "$REPO/.python-version" 2>/dev/null || echo '?'))"
  (cd "$REPO" && "$UV" sync --frozen --no-dev)
fi
PY="$REPO/.venv/bin/python"
[[ -x "$PY" ]] || die "$PY is missing; run without --skip-sync"
"$PY" -c 'import fastapi, uvicorn, httpx, dotenv, pydantic' || die "the virtualenv is incomplete; run without --skip-sync"
say "python $("$PY" -c 'import platform; print(platform.python_version())'): $PY"

# ── 2. the built UI ──
[[ -f "$REPO/frontend/dist/index.html" ]] ||
  die "frontend/dist/index.html is missing. Deploy with deploy/spark/deploy.sh from the Mac (it builds the UI),
  or build it here: cd frontend && npm ci && npm run build"

# ── 3. ~/.config/llm-council/llm-council.env (written once) ──
tailnet_ipv4() {
  local ip=""
  if command -v ip >/dev/null 2>&1; then
    ip="$(ip -4 -o addr show dev tailscale0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -n 1 || true)"
  fi
  if [[ -z "$ip" ]] && command -v tailscale >/dev/null 2>&1; then
    ip="$(tailscale ip -4 2>/dev/null | head -n 1 || true)"
  fi
  printf '%s' "$ip"
}

# This node's MagicDNS names, one per line: the full name and its first label.
magicdns_names() {
  command -v tailscale >/dev/null 2>&1 || return 0
  tailscale status --json 2>/dev/null | python3 -c '
import json, re, sys
try:
    name = (json.load(sys.stdin).get("Self") or {}).get("DNSName", "").rstrip(".").lower()
except ValueError:
    sys.exit(0)
if re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)*", name):
    print(name)
    if "." in name:
        print(name.split(".", 1)[0])
' 2>/dev/null || true
}

file_value() { [[ -f "$2" ]] || return 0; { grep -E "^$1=" "$2" || true; } | tail -n 1 | cut -d= -f2- | tr -d '\r'; }
url_host() { if [[ "$1" == *:* && "$1" != \[* ]]; then printf '[%s]' "$1"; else printf '%s' "$1"; fi; }

mkdir -p "$CONFIG_DIR"
chmod 0700 "$CONFIG_DIR"
if [[ -f "$ENV_FILE" ]]; then
  say "keeping $ENV_FILE"
else
  host="${COUNCIL_HOST:-$(tailnet_ipv4)}"
  [[ -n "$host" ]] || die "no tailnet IPv4 found (tailscale0). Set COUNCIL_HOST=<address> and re-run."
  [[ "$host" =~ ^[A-Za-z0-9.:-]+$ ]] || die "COUNCIL_HOST is not an address: '$host'"
  case "$host" in
    0.0.0.0|::) die "COUNCIL_HOST=$host would serve the unauthenticated backend on every interface; use the tailnet IPv4" ;;
  esac
  mese_host="$(file_value MESE_INFER_HOST "$MESE_ENV")"; mese_host="${mese_host:-127.0.0.1}"
  mese_port="$(file_value MESE_INFER_PORT "$MESE_ENV")"; mese_port="${mese_port:-8790}"
  ui_port="$(file_value COUNCIL_UI_PORT "$SCRIPT_DIR/llm-council.env.example")"; ui_port="${ui_port:-5173}"
  origins="http://$(url_host "$host"):$ui_port"
  while IFS= read -r name; do
    if [[ -n "$name" ]]; then origins="$origins,http://$name:$ui_port"; fi
  done < <(magicdns_names)
  data_dir="${COUNCIL_DATA_DIR:-$HOME/.llm-council}"
  safe_path COUNCIL_DATA_DIR "$data_dir"
  tmp="$(mktemp "$CONFIG_DIR/.llm-council.env.XXXXXX")"
  awk -v h="$host" -v m="http://$(url_host "$mese_host"):$mese_port" -v o="$origins" -v d="$data_dir" '
    /^COUNCIL_HOST=/            { print "COUNCIL_HOST=" h; next }
    /^MESE_URL=/                { print "MESE_URL=" m; next }
    /^COUNCIL_ALLOWED_ORIGINS=/ { print "COUNCIL_ALLOWED_ORIGINS=" o; next }
    /^COUNCIL_DATA_DIR=/        { print "COUNCIL_DATA_DIR=" d; next }
    { print }' "$SCRIPT_DIR/llm-council.env.example" > "$tmp"
  chmod 0600 "$tmp"
  mv "$tmp" "$ENV_FILE"
  say "wrote $ENV_FILE (COUNCIL_HOST=$host)"
fi
[[ "$(perm_of "$ENV_FILE")" == 600 ]] || { chmod 0600 "$ENV_FILE"; say "set $ENV_FILE to 0600"; }

HOST="$(file_value COUNCIL_HOST "$ENV_FILE")"
PORT="$(file_value COUNCIL_PORT "$ENV_FILE")"; PORT="${PORT:-8001}"
UI_PORT="$(file_value COUNCIL_UI_PORT "$ENV_FILE")"; UI_PORT="${UI_PORT:-5173}"
DATA_DIR="$(file_value COUNCIL_DATA_DIR "$ENV_FILE")"
MESE_URL="$(file_value MESE_URL "$ENV_FILE")"
ORIGINS="$(file_value COUNCIL_ALLOWED_ORIGINS "$ENV_FILE" | tr -d ' ')"
case "$HOST$MESE_URL$DATA_DIR$ORIGINS" in
  *"<"*) die "replace the <placeholders> in $ENV_FILE, then re-run" ;;
esac
case "$HOST" in
  ""|0.0.0.0|::|"[::]") die "COUNCIL_HOST='$HOST' in $ENV_FILE would serve the unauthenticated backend on every interface; use the tailnet IPv4" ;;
esac
# The backend's own default is 'data' inside the checkout, which the next deploy would delete.
[[ -n "$DATA_DIR" ]] || die "COUNCIL_DATA_DIR is empty in $ENV_FILE; the backend would write into $REPO/data"
safe_path COUNCIL_DATA_DIR "$DATA_DIR"
case "$DATA_DIR/" in "$REPO"/*) die "COUNCIL_DATA_DIR must be outside $REPO (a deploy replaces the checkout)" ;; esac
case ",$ORIGINS," in
  *,\*,*) warn "COUNCIL_ALLOWED_ORIGINS contains '*': any website can call the unauthenticated API from a tailnet browser (CORS and the Host allowlist are both off)" ;;
esac

# ── 4. the Mese client token (every run, so a rotated token is picked up) ──
mese_data="$(file_value MESE_DATA_DIR "$MESE_ENV")"
TOKEN_FILE="${MESE_TOKEN_FILE:-${mese_data:-$HOME/.mese}/tokens/llm-council.token}"
[[ -r "$TOKEN_FILE" ]] ||
  die "Mese's llm-council token is missing: $TOKEN_FILE. Install Mese first (mese/deploy/spark/install-user.sh)."
token="$(tr -d '\r\n' < "$TOKEN_FILE")"
[[ "$token" =~ ^mese_ck_[A-Za-z0-9_-]+$ ]] || { unset token; die "$TOKEN_FILE does not hold a Mese client token (mese_ck_…)"; }
tmp="$(mktemp "$CONFIG_DIR/.mese-token.env.XXXXXX")"
printf 'MESE_API_KEY=%s\n' "$token" > "$tmp"   # printf is a builtin: the token never reaches a process list
unset token
chmod 0600 "$tmp"
if [[ -f "$TOKEN_ENV" ]] && cmp -s "$tmp" "$TOKEN_ENV"; then
  rm -f "$tmp"
  say "token unchanged: $TOKEN_ENV"
else
  mv "$tmp" "$TOKEN_ENV"
  say "wrote $TOKEN_ENV from $TOKEN_FILE"
fi

# ── 5. data dir ──
mkdir -p "$DATA_DIR/conversations"
chmod 0700 "$DATA_DIR"
say "data dir $DATA_DIR"

# ── 6. the units ──
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
systemctl --user show-environment >/dev/null 2>&1 ||
  die "cannot reach your systemd user manager. Log in over SSH as $ME (not su/sudo -u), or ask an admin: sudo loginctl enable-linger $ME"
mkdir -p "$UNIT_DIR"
CHANGED=0
for unit in "${UNITS[@]}"; do
  target="$UNIT_DIR/$unit.service"
  rendered="$(mktemp "$UNIT_DIR/.$unit.service.XXXXXX")"
  {
    printf '# Installed by %s/deploy/spark/install-user.sh from deploy/spark/%s.service.\n' "$REPO" "$unit"
    printf '# Edit the template in the checkout and re-run the installer instead of editing this copy.\n\n'
    awk -v repo="$REPO" '
      /^\[Unit\]/ { body = 1 }
      body { gsub(/%h\/Development\/llm-council/, repo); print }' "$SCRIPT_DIR/$unit.service"
  } > "$rendered"
  chmod 0644 "$rendered"
  if [[ -f "$target" ]] && cmp -s "$rendered" "$target"; then
    rm -f "$rendered"
    say "unit unchanged: $target"
  else
    mv "$rendered" "$target"
    CHANGED=1
    say "installed $target"
  fi
done
if (( CHANGED )); then systemctl --user daemon-reload; fi
for unit in "${UNITS[@]}"; do
  systemctl --user enable "$unit.service" >/dev/null 2>&1 || die "systemctl --user enable $unit.service failed"
done

# Linux delivers a packet for the tailnet address that arrives on the LAN interface too (weak host model),
# so a LAN device that routes 100.x to this box reaches the unauthenticated backend unless a firewall drops
# it. tailscale's ts-input chain accepts tailnet traffic before ufw sees it.
if ! grep -qs '^ENABLED=yes' /etc/ufw/ufw.conf; then
  warn "ufw is not enabled: binding the tailnet address alone does not keep LAN devices away from ports $PORT and $UI_PORT."
  warn "once, as an admin: sudo ufw allow OpenSSH && sudo ufw deny $PORT/tcp && sudo ufw deny $UI_PORT/tcp && sudo ufw enable"
fi

# ── 7. linger: without it the services stop at logout and do not start at boot ──
linger="$(loginctl show-user "$ME" -p Linger --value 2>/dev/null || true)"
if [[ "$linger" == yes ]]; then
  say "linger is enabled for $ME"
elif loginctl --no-ask-password enable-linger "$ME" >/dev/null 2>&1; then
  say "enabled linger for $ME"
else
  warn "linger is off for $ME: llm-council stops when your last session ends and does not start at boot."
  warn "enabling it needs an admin, once: sudo loginctl enable-linger $ME"
fi

# ── 8. start and check ──
API_URL="http://$(url_host "$HOST"):$PORT"
UI_URL="http://$(url_host "$HOST"):$UI_PORT"
wait_for() {   # url, seconds
  local i
  for (( i = 0; i < $2; i++ )); do
    curl -fsS -m 3 -o /dev/null "$1" 2>/dev/null && return 0
    sleep 1
  done
  return 1
}
if (( NO_START )); then
  say "not started (--no-start). Start them with: systemctl --user start ${UNITS[*]}"
else
  systemctl --user restart "${UNITS[@]/%/.service}"
  if command -v curl >/dev/null 2>&1; then
    wait_for "$API_URL/api/mese/health" 30 ||
      die "the backend did not answer $API_URL/api/mese/health within 30 s. Logs: journalctl --user -u llm-council -n 50 --no-pager"
    health="$(curl -fsS -m 10 "$API_URL/api/mese/health" 2>/dev/null || true)"
    summary="$(printf '%s' "$health" | python3 -c '
import json, sys
try:
    h = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
print(("ok" if h.get("ok") else "NOT ok: " + str(h.get("error") or "?")) + ", token " + ("set" if h.get("token_set") else "NOT set"))
' 2>/dev/null || echo "unreadable answer")"
    say "backend answers $API_URL (Mese: $summary)"
    case "$summary" in ok,*" token set") ;; *) warn "llm-council cannot use Mese yet: $summary" ;; esac
    wait_for "$UI_URL/" 15 || die "the UI did not answer $UI_URL/ within 15 s. Logs: journalctl --user -u llm-council-ui -n 50 --no-pager"
    say "UI answers $UI_URL/"
  else
    say "curl not found; check with: systemctl --user status ${UNITS[*]}"
  fi
fi

cat <<EOF

llm-council on $(hostname 2>/dev/null || echo this host):
  checkout       $REPO
  units          $UNIT_DIR/llm-council.service, llm-council-ui.service
                 (journalctl --user -u llm-council -u llm-council-ui -f)
  environment    $ENV_FILE
  Mese token     $TOKEN_ENV (from $TOKEN_FILE)
  data dir       $DATA_DIR
  Mese           $MESE_URL
  UI             $UI_URL/
  API            $API_URL
  allowed hosts  $(file_value COUNCIL_ALLOWED_ORIGINS "$ENV_FILE")

Open the UI by one of the allowed origins above; any other hostname gets 400 invalid_host from the API.
EOF

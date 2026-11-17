#!/usr/bin/env bash
# Deploy this checkout to the DGX Spark, next to Mese. Run it on the Mac, from anywhere in the checkout.
#
#   deploy/spark/deploy.sh <user@spark>               # build the UI, copy the checkout, install, (re)start
#   deploy/spark/deploy.sh <user@spark> --no-start    # passed on to install-user.sh
#   deploy/spark/deploy.sh <user@spark> --skip-sync   # passed on to install-user.sh (no `uv sync`)
#
# 1. builds the UI here (npm run build into frontend/dist; `npm ci` first when node_modules is missing);
# 2. stages the files git tracks plus untracked files that are not ignored, and frontend/dist. So .env,
#    data/, .venv and node_modules never leave this machine; .claude/, any .env* file and the old
#    committed virtualenv env/ are dropped as well;
# 3. mirrors the stage to ~/$REMOTE_DIR on the Spark with rsync --delete (the Spark's .venv, data/ and .env
#    are kept), but only into a directory that is new, empty or an earlier deploy (it has .deploy-info);
# 4. runs deploy/spark/install-user.sh there;
# 5. checks that the UI and the API answer from this machine (the tailnet ACL can block what the Spark's
#    own checks over loopback do not see).
#
# REMOTE_DIR defaults to Development/llm-council (relative to the remote home directory).
# Conversations and presets live outside the checkout on the Spark (~/.llm-council), so a deploy never
# touches them.
set -euo pipefail

say() { printf 'llm-council-deploy: %s\n' "$*"; }
die() { printf 'llm-council-deploy: %s\n' "$*" >&2; exit 1; }

[[ $# -ge 1 && "$1" != -* ]] || { sed -n '2,21p' "$0"; exit 2; }
TARGET="$1"; shift
[[ "$TARGET" =~ ^[A-Za-z0-9._@:-]+$ ]] || die "the target must be user@host: '$TARGET'"
INSTALL_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --no-start|--skip-sync) INSTALL_ARGS+=("$arg") ;;
    *) die "unknown option: $arg" ;;
  esac
done
REMOTE_DIR="${REMOTE_DIR:-Development/llm-council}"
[[ "$REMOTE_DIR" =~ ^[A-Za-z0-9._+@-]+(/[A-Za-z0-9._+@-]+)*$ && "$REMOTE_DIR" != *..* && "/$REMOTE_DIR/" != */./* ]] ||
  die "REMOTE_DIR must be a relative path of letters, digits and ._+@- only: '$REMOTE_DIR'"

REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
[[ -f "$REPO/backend/main.py" && -f "$REPO/frontend/package.json" ]] || die "$REPO is not the llm-council checkout"
command -v rsync >/dev/null || die "rsync not found"
command -v npm >/dev/null || die "npm not found (needed to build the UI)"

# ── 1. the UI ──
if [[ ! -d "$REPO/frontend/node_modules" ]]; then
  say "installing frontend dependencies (npm ci)"
  npm --prefix "$REPO/frontend" ci
fi
say "building the UI (npm run build)"
npm --prefix "$REPO/frontend" run build >/dev/null
[[ -f "$REPO/frontend/dist/index.html" ]] || die "the UI build did not produce frontend/dist/index.html"

# ── 2. stage ──
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/llm-council-deploy.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT
PATHSPEC=(-- . ':(exclude).claude' ':(exclude)env' ':(exclude)data' ':(exclude,glob)**/.env' ':(exclude,glob)**/.env.*')
git -C "$REPO" ls-files -z --cached --others --exclude-standard "${PATHSPEC[@]}" |
  while IFS= read -r -d '' path; do
    # An untracked nested repository is listed as 'dir/': skip it. --cached also lists files deleted from
    # the working tree but not yet from the index.
    [[ "$path" != */ ]] || continue
    if [[ -e "$REPO/$path" || -L "$REPO/$path" ]]; then printf '%s\0' "$path"; fi
  done |
  (cd "$REPO" && COPYFILE_DISABLE=1 tar -c -n --null -T - -f -) | tar -x -C "$STAGE" -f -
mkdir -p "$STAGE/frontend"
cp -R "$REPO/frontend/dist" "$STAGE/frontend/dist"
commit="$(git -C "$REPO" rev-parse --short HEAD)"
dirty=""
[[ -z "$(git -C "$REPO" status --porcelain --untracked-files=normal "${PATHSPEC[@]}")" ]] || dirty=" (with uncommitted changes)"
printf 'commit %s%s, deployed %s from %s\n' "$commit" "$dirty" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$(hostname -s)" \
  > "$STAGE/.deploy-info"
leak="$(find "$STAGE" \( -name .env -o -name '.env.*' -o -name .envrc -o -name .git -o -name '*.token' \) -print -quit)"
[[ -z "$leak" ]] || die "refusing to deploy: ${leak#"$STAGE"/} reached the stage"
say "staged $(find "$STAGE" -type f | wc -l | tr -d ' ') files from $commit$dirty"

# ── 3. copy ──
# rsync --delete mirrors the stage, so only sync into a directory that is new, empty, or an earlier deploy.
ssh -o BatchMode=yes "$TARGET" "d='$REMOTE_DIR'; [ ! -e \"\$d/.git\" ] && { [ ! -e \"\$d\" ] || [ -f \"\$d/.deploy-info\" ] || [ -z \"\$(ls -A \"\$d\")\" ]; }" ||
  die "~/$REMOTE_DIR on $TARGET exists and was not created by deploy.sh; refusing to mirror into it"
ssh -o BatchMode=yes "$TARGET" "mkdir -p '$REMOTE_DIR'"
say "copying to $TARGET:$REMOTE_DIR (rsync --delete; the remote .venv, data/ and .env are kept)"
rsync -az --delete --exclude=/.venv/ --exclude=/data/ --exclude=/.env "$STAGE/" "$TARGET:$REMOTE_DIR/"

# ── 4. install ──
say "running deploy/spark/install-user.sh on $TARGET"
ssh -o BatchMode=yes "$TARGET" "bash '$REMOTE_DIR/deploy/spark/install-user.sh' ${INSTALL_ARGS[*]:-}"

# ── 5. reachability from this machine (the installer's own checks run over loopback and skip the tailnet ACL) ──
case " ${INSTALL_ARGS[*]:-} " in *" --no-start "*) exit 0 ;; esac
remote_env="$(ssh -o BatchMode=yes "$TARGET" "grep -E '^(COUNCIL_HOST|COUNCIL_PORT|COUNCIL_UI_PORT)=' ~/.config/llm-council/llm-council.env" || true)"
env_of() { printf '%s\n' "$remote_env" | sed -n "s/^$1=//p" | tail -n 1; }
host="$(env_of COUNCIL_HOST)"; port="$(env_of COUNCIL_PORT)"; ui_port="$(env_of COUNCIL_UI_PORT)"
[[ "$host" =~ ^[A-Za-z0-9.:-]+$ ]] || die "cannot read COUNCIL_HOST from ~/.config/llm-council/llm-council.env on $TARGET"
[[ "$host" != *:* ]] || host="[$host]"
for url in "http://$host:${ui_port:-5173}/" "http://$host:${port:-8001}/api/mese/health"; do
  curl -fsS -m 10 -o /dev/null "$url" ||
    die "$url answers on the Spark but not from this machine: check the tailnet ACL (members need tag:mese:5173,8001)"
done
say "reachable from this machine: http://$host:${ui_port:-5173}/"

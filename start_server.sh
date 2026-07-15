#!/usr/bin/env bash
#
# start_server.sh — one command to bring up the whole Team Collab stack and
# publish it on your own domain via the existing Cloudflare Tunnel.
#
#   frontend + backend (single origin)  ->  http://localhost:8900  ->  https://huntjob.space
#   Claude chat bus                     ->  http://localhost:8899  ->  https://bus.huntjob.space
#   remote worker (local only)          ->  http://localhost:8788
#
# Just run:  bash start_server.sh     (re-run any time to rebuild + restart)
#
# Notes
#  - The worker deliberately runs on :8788, NOT :8787, so it can never kill the
#    jobhunt server that also uses :8787 on this machine.
#  - PUBLIC_BASE_URL / BUS_PUBLIC_URL are exported so invite links and the
#    per-project "join this Claude group" command render real https URLs.
#  - GitHub "Sign in with GitHub": put GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET
#    in .env (OAuth App callback URL: $PUBLIC_BASE_URL/api/github/oauth/callback).
#  - Override anything: DOMAIN=example.com bash start_server.sh
#  - Local only (no Cloudflare Tunnel): NO_TUNNEL=1 bash start_server.sh

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
mkdir -p logs

# Load persisted settings (WORKER_TOKEN etc.) if present.
if [[ -f .env ]]; then set -a; source .env; set +a; fi

# --- config (override via env) ------------------------------------------------
DOMAIN="${DOMAIN:-huntjob.space}"
BUS_HOST="${BUS_HOST:-bus.$DOMAIN}"
TUNNEL="${TUNNEL:-jobhunt}"
APP_PORT="${APP_PORT:-8900}"
BUS_PORT="${BUS_PORT:-8899}"
WORKER_PORT="${WORKER_PORT:-8788}"
CF_CONFIG="$HOME/.cloudflared/config.yml"
NO_TUNNEL="${NO_TUNNEL:-0}"

if [[ "$NO_TUNNEL" == "1" ]]; then
  # Local-only run: invite links, OAuth callbacks, and bus joins point at localhost.
  export PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-http://127.0.0.1:$APP_PORT}"
  export BUS_PUBLIC_URL="${BUS_PUBLIC_URL:-http://127.0.0.1:$BUS_PORT}"
else
  export PUBLIC_BASE_URL="https://$DOMAIN"
  export BUS_PUBLIC_URL="https://$BUS_HOST"
fi

say()  { printf '\033[1;36m>> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }

free_port() {
  local pids; pids="$(lsof -ti tcp:"$1" 2>/dev/null || true)"
  [[ -n "$pids" ]] && kill $pids 2>/dev/null || true
  return 0
}

# --- 0. deps -----------------------------------------------------------------
python3 -c "import fastapi, uvicorn" 2>/dev/null || {
  say "Installing Python deps (fastapi, uvicorn, requests)..."
  pip3 install --quiet fastapi uvicorn requests || pip3 install --user --quiet fastapi uvicorn requests
}

# --- 1. build the frontend ---------------------------------------------------
say "Building frontend..."
( cd frontend && { [[ -d node_modules ]] || npm install --no-fund --no-audit >/dev/null 2>&1; }; npm run build )

# --- 2. backend on :$APP_PORT (API + the built SPA) --------------------------
say "Starting backend (API + website) on :$APP_PORT ..."
free_port "$APP_PORT"
nohup python3 -m uvicorn app.platform:app --host 127.0.0.1 --port "$APP_PORT" > logs/backend.log 2>&1 &

# --- 3. chat bus on :$BUS_PORT ----------------------------------------------
# Persist a REAL bus token in .env and make it authoritative: the bus reads
# env BUS_TOKEN first, and we mirror it to /tmp/claude-bus/token so the platform
# hands out the same token in join commands. This stops an ad-hoc/test value in
# the /tmp file from silently becoming the production secret.
if [[ -z "${BUS_TOKEN:-}" || "${BUS_TOKEN:-}" == "testbustoken123" ]]; then
  if command -v openssl >/dev/null 2>&1; then BUS_TOKEN="$(openssl rand -hex 16)"
  else BUS_TOKEN="$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')"; fi
  export BUS_TOKEN
  # replace any prior BUS_TOKEN line in .env, then append the new one
  [[ -f .env ]] && grep -v '^BUS_TOKEN=' .env > .env.tmp 2>/dev/null && mv .env.tmp .env
  echo "BUS_TOKEN=$BUS_TOKEN" >> .env
  say "Generated a new BUS_TOKEN (saved to .env)"
fi
mkdir -p /tmp/claude-bus
printf '%s' "$BUS_TOKEN" > /tmp/claude-bus/token
say "Starting chat bus on :$BUS_PORT ..."
free_port "$BUS_PORT"
BUS_TOKEN="$BUS_TOKEN" nohup python3 -m uvicorn app.chat_server:app --host 127.0.0.1 --port "$BUS_PORT" > logs/bus.log 2>&1 &

# --- 4. remote worker on :$WORKER_PORT (never :8787 — jobhunt owns that) -----
if [[ -z "${WORKER_TOKEN:-}" ]]; then
  if command -v openssl >/dev/null 2>&1; then WORKER_TOKEN="$(openssl rand -hex 24)"
  else WORKER_TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"; fi
  export WORKER_TOKEN
  echo "WORKER_TOKEN=$WORKER_TOKEN" >> .env
  say "Generated a new WORKER_TOKEN (saved to .env)"
fi
say "Starting remote worker on :$WORKER_PORT ..."
free_port "$WORKER_PORT"
CONTEXT_DIR="${CONTEXT_DIR:-$ROOT/context}" CLAUDE_BIN="${CLAUDE_BIN:-claude}" \
  nohup python3 -m uvicorn app.worker:app --host 127.0.0.1 --port "$WORKER_PORT" > logs/worker.log 2>&1 &

# --- 5. wait for the backend --------------------------------------------------
say "Waiting for backend to come up ..."
code=""
for _ in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$APP_PORT/" || true)"
  [[ "$code" == "200" ]] && break
  sleep 0.5
done
if [[ "$code" == "200" ]]; then
  say "Backend is up (serving the website)."
else
  warn "Backend did not report ready — check logs/backend.log"
fi

# --- 6. Cloudflare Tunnel -----------------------------------------------------
if [[ "$NO_TUNNEL" == "1" ]]; then
  say "NO_TUNNEL=1 — skipping Cloudflare Tunnel (local only)."
elif ! command -v cloudflared >/dev/null 2>&1; then
  warn "cloudflared not installed (brew install cloudflared) — local only at http://127.0.0.1:$APP_PORT"
else
  # Write the ingress config so the tunnel points at THIS app. Backed up once.
  CRED="$(ls "$HOME"/.cloudflared/*.json 2>/dev/null | head -1 || true)"
  TUNNEL_ID="$(cloudflared tunnel list 2>/dev/null | awk -v n="$TUNNEL" '$2==n {print $1}' | head -1)"
  if [[ -z "$TUNNEL_ID" || -z "$CRED" ]]; then
    warn "Tunnel '$TUNNEL' or its credentials not found — run: cloudflared tunnel login && cloudflared tunnel create $TUNNEL"
  else
    desired="$(cat <<EOF
tunnel: $TUNNEL_ID
credentials-file: $CRED

ingress:
  # Team Collab — backend serves the API and the built SPA (single origin).
  - hostname: $DOMAIN
    service: http://localhost:$APP_PORT
  - hostname: www.$DOMAIN
    service: http://localhost:$APP_PORT
  # Claude chat bus — powers the per-project "join this group" command.
  - hostname: $BUS_HOST
    service: http://localhost:$BUS_PORT
  - service: http_status:404
EOF
)"
    if [[ ! -f "$CF_CONFIG" ]] || ! diff -q <(printf '%s\n' "$desired") "$CF_CONFIG" >/dev/null 2>&1; then
      [[ -f "$CF_CONFIG" && ! -f "$CF_CONFIG.bak" ]] && cp "$CF_CONFIG" "$CF_CONFIG.bak" && say "Backed up old tunnel config to $CF_CONFIG.bak"
      mkdir -p "$(dirname "$CF_CONFIG")"
      printf '%s\n' "$desired" > "$CF_CONFIG"
      say "Wrote tunnel config ($DOMAIN -> :$APP_PORT, $BUS_HOST -> :$BUS_PORT)"
    fi

    # Make sure DNS routes exist (no-op/error-safe if already routed).
    for h in "$DOMAIN" "www.$DOMAIN" "$BUS_HOST"; do
      cloudflared tunnel route dns "$TUNNEL" "$h" >/dev/null 2>&1 || true
    done

    say "Starting Cloudflare Tunnel '$TUNNEL' ..."
    pkill -f "cloudflared tunnel" 2>/dev/null || true
    sleep 1
    nohup cloudflared tunnel run "$TUNNEL" > logs/cloudflared.log 2>&1 &
    sleep 5

    if grep -qiE "Registered tunnel connection|Connection .* registered" logs/cloudflared.log 2>/dev/null; then
      say "Tunnel connected."
    else
      warn "Tunnel may still be connecting — check logs/cloudflared.log"
    fi
  fi
fi

echo
printf '\033[1;32m==============================================================\033[0m\n'
if [[ "$NO_TUNNEL" == "1" ]]; then
  printf '\033[1;32m  Website:  %s\033[0m\n' "$PUBLIC_BASE_URL"
  printf '\033[1;32m  Bus:      %s\033[0m\n' "$BUS_PUBLIC_URL"
else
  printf '\033[1;32m  Website:  https://%s\033[0m\n' "$DOMAIN"
  printf '\033[1;32m  Bus:      https://%s\033[0m\n' "$BUS_HOST"
fi
printf '\033[1;32m==============================================================\033[0m\n'
echo
say "Local: app :$APP_PORT  bus :$BUS_PORT  worker :$WORKER_PORT (jobhunt's :8787 untouched)"
say "Logs:  logs/backend.log  logs/bus.log  logs/worker.log  logs/cloudflared.log"

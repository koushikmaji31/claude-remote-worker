#!/usr/bin/env bash
#
# start_server.sh — one command to bring up the whole Team Collab stack:
#   • builds the frontend and serves it from the backend (single origin)
#   • backend / product API + SPA  ->  http://127.0.0.1:8900
#   • agent chat bus               ->  http://127.0.0.1:8899
#   • remote worker                ->  http://127.0.0.1:8787
#   • ngrok tunnels (web + bus) and prints your public website link
#
# Just run:  bash start_server.sh   (re-run any time to rebuild + restart)

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
mkdir -p logs

# Load persisted settings (WORKER_TOKEN etc.) if present.
if [[ -f .env ]]; then set -a; source .env; set +a; fi

say()  { printf '\033[1;36m>> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m!! %s\033[0m\n' "$*"; }

# Kill whatever currently holds a TCP port (best-effort).
free_port() {
  local pids; pids="$(lsof -ti tcp:"$1" 2>/dev/null || true)"
  [[ -n "$pids" ]] && kill $pids 2>/dev/null || true
}

# --- 0. deps -----------------------------------------------------------------
python3 -c "import fastapi, uvicorn" 2>/dev/null || {
  say "Installing Python deps (fastapi, uvicorn, requests)..."
  pip3 install --quiet fastapi uvicorn requests || pip3 install --user --quiet fastapi uvicorn requests
}

# --- 1. build the frontend ---------------------------------------------------
say "Building frontend..."
( cd frontend && [[ -d node_modules ]] || npm install --no-fund --no-audit >/dev/null 2>&1; npm run build )

# --- 2. backend on :8900 (serves API + the built SPA) ------------------------
say "Starting backend (API + website) on :8900 ..."
free_port 8900
nohup python3 -m uvicorn app.platform:app --host 127.0.0.1 --port 8900 > logs/backend.log 2>&1 &

# --- 3. agent chat bus on :8899 ---------------------------------------------
say "Starting chat bus on :8899 ..."
free_port 8899
nohup python3 -m uvicorn app.chat_server:app --host 127.0.0.1 --port 8899 > logs/bus.log 2>&1 &

# --- 4. remote worker on :8787 ----------------------------------------------
if [[ -z "${WORKER_TOKEN:-}" ]]; then
  if command -v openssl >/dev/null 2>&1; then WORKER_TOKEN="$(openssl rand -hex 24)"
  else WORKER_TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"; fi
  export WORKER_TOKEN
  echo "WORKER_TOKEN=$WORKER_TOKEN" >> .env
  say "Generated a new WORKER_TOKEN (saved to .env)"
fi
say "Starting remote worker on :8787 ..."
free_port 8787
CONTEXT_DIR="${CONTEXT_DIR:-$ROOT/context}" CLAUDE_BIN="${CLAUDE_BIN:-claude}" \
  nohup python3 -m uvicorn app.worker:app --host 127.0.0.1 --port 8787 > logs/worker.log 2>&1 &

# --- 5. wait for the backend to answer --------------------------------------
say "Waiting for backend to come up ..."
for _ in $(seq 1 30); do
  code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8900/ || true)"
  [[ "$code" == "200" ]] && break
  sleep 0.5
done
[[ "${code:-}" == "200" ]] && say "Backend is up (serving the website)." || warn "Backend did not report ready — check logs/backend.log"

# --- 6. ngrok tunnels + public link -----------------------------------------
if command -v ngrok >/dev/null 2>&1; then
  # Does a currently-running ngrok already forward to :8900? If not, (re)start it
  # so it picks up the web->:8900 tunnel from ngrok.yml.
  has_8900="$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c '
import sys, json
try: t = json.load(sys.stdin).get("tunnels", [])
except Exception: t = []
print("yes" if any(str(x.get("config",{}).get("addr","")).endswith(":8900") for x in t) else "no")' 2>/dev/null || echo no)"
  if [[ "$has_8900" != "yes" ]]; then
    say "Starting ngrok (web -> :8900, bus -> :8899) ..."
    pkill -f "ngrok start" 2>/dev/null || true
    sleep 1
    nohup ngrok start web bus > logs/ngrok.log 2>&1 &
    sleep 4
  else
    say "ngrok already forwarding to :8900 — reusing it."
  fi
  # Pull the public URL of the tunnel that forwards to :8900.
  URL="$(curl -s http://127.0.0.1:4040/api/tunnels 2>/dev/null | python3 -c '
import sys, json
try:
    t = json.load(sys.stdin).get("tunnels", [])
except Exception:
    t = []
web = [x["public_url"] for x in t if str(x.get("config", {}).get("addr","")).endswith(":8900") and x["public_url"].startswith("https")]
print(web[0] if web else "")' 2>/dev/null)"
  echo
  if [[ -n "$URL" ]]; then
    printf '\033[1;32m==============================================================\033[0m\n'
    printf '\033[1;32m  Your website is live at:  %s\033[0m\n' "$URL"
    printf '\033[1;32m==============================================================\033[0m\n'
  else
    warn "ngrok is up but no :8900 tunnel URL yet — check http://127.0.0.1:4040 or logs/ngrok.log"
  fi
else
  warn "ngrok not installed — website is local only at http://127.0.0.1:8900"
fi

echo
say "All set. Logs: logs/backend.log  logs/bus.log  logs/worker.log  logs/ngrok.log"

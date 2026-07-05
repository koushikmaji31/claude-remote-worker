#!/usr/bin/env bash
# One-click installer for the Team Collab Platform.
# Safe to rerun: skips completed steps, restarts servers cleanly.
set -euo pipefail

REPO_SSH="https://github.com/koushikmaji31/claude-remote-worker.git"
REPO_SLUG="koushikmaji31/claude-remote-worker"
REPO_DIR="claude-remote-worker"

info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

# --- 1. Prerequisites -------------------------------------------------------
missing=0
need() {
  if ! command -v "$1" >/dev/null 2>&1; then
    printf '\033[1;31mMissing:\033[0m %s — install with: %s\n' "$1" "$2"
    missing=1
  fi
}
need git     "brew install git"
need python3 "brew install python"
need node    "brew install node"
need npm     "brew install node"
[ "$missing" -eq 1 ] && fail "Install the missing tools above, then rerun ./install.sh"
info "Prerequisites OK: git, python3, node, npm"

# --- 2. Get the repo --------------------------------------------------------
if [ -f docs/API_CONTRACT.md ] && [ -d frontend ]; then
  info "Already inside the repo — skipping clone."
else
  if [ ! -d "$REPO_DIR" ]; then
    info "Cloning $REPO_SLUG ..."
    git clone "$REPO_SSH" "$REPO_DIR" 2>/dev/null \
      || { command -v gh >/dev/null 2>&1 && gh repo clone "$REPO_SLUG" "$REPO_DIR"; } \
      || fail "Clone failed. Make sure you have access (try: gh auth login) and rerun."
  else
    info "Repo directory exists — skipping clone."
  fi
  cd "$REPO_DIR"
fi
ROOT="$(pwd)"

# --- 3. Python deps ---------------------------------------------------------
info "Installing Python deps (fastapi, uvicorn, requests)..."
pip3 install --quiet fastapi uvicorn requests || pip3 install --user --quiet fastapi uvicorn requests

# --- 4. Frontend deps -------------------------------------------------------
info "Installing frontend deps..."
(cd frontend && npm install --no-fund --no-audit)

# --- 5. Start servers -------------------------------------------------------
mkdir -p logs

if lsof -ti tcp:8900 >/dev/null 2>&1; then
  info "Backend already running on :8900 — leaving it."
else
  info "Starting backend on :8900 (logs/backend.log)..."
  nohup python3 -m uvicorn app.platform:app --host 127.0.0.1 --port 8900 \
    >"$ROOT/logs/backend.log" 2>&1 &
fi

if lsof -ti tcp:5173 >/dev/null 2>&1; then
  info "Frontend already running on :5173 — leaving it."
else
  info "Starting frontend on :5173 (logs/frontend.log)..."
  (cd frontend && nohup npm run dev >"$ROOT/logs/frontend.log" 2>&1 &)
fi

# Wait for both to answer.
for i in $(seq 1 30); do
  ok=1
  curl -sf -o /dev/null http://127.0.0.1:8900/api/join/_healthcheck_ || [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8900/api/join/_healthcheck_)" = "404" ] || ok=0
  curl -sf -o /dev/null http://localhost:5173/ || ok=0
  [ "$ok" -eq 1 ] && break
  sleep 1
done
[ "$ok" -eq 1 ] || fail "Servers did not come up — check logs/backend.log and logs/frontend.log"

# --- 6. Next steps ----------------------------------------------------------
cat <<'EOF'

✅ All set!

Next steps:
  1. Open http://localhost:5173 in your browser
  2. Register with your name + email
  3. Paste the invite link/key Koushik shared (or open the ?join=CODE link) to join the team

Logs: logs/backend.log, logs/frontend.log — rerun ./install.sh any time; it's idempotent.
EOF

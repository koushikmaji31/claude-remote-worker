#!/usr/bin/env bash
#
# Boot the Claude Code remote worker.
#
#   bash run.sh
#
# It auto-generates a WORKER_TOKEN the first time (saved to .env, gitignored) and reuses it
# after. Override anything via env vars before running, e.g. PORT=9000 bash run.sh
#
set -euo pipefail

# Always operate relative to this script's location, not the caller's cwd.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# --- load saved settings (token etc.) if present ---
if [[ -f .env ]]; then
  set -a; source .env; set +a
fi

# --- defaults (override by exporting before running, or by editing .env) ---
export CONTEXT_DIR="${CONTEXT_DIR:-$ROOT/context}"
export CLAUDE_BIN="${CLAUDE_BIN:-claude}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8787}"

# --- first-run: mint a token and persist it so the value is stable across restarts ---
if [[ -z "${WORKER_TOKEN:-}" ]]; then
  if command -v openssl >/dev/null 2>&1; then
    WORKER_TOKEN="$(openssl rand -hex 24)"
  else
    WORKER_TOKEN="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  export WORKER_TOKEN
  echo "WORKER_TOKEN=$WORKER_TOKEN" >> .env
  echo ">> Generated a new WORKER_TOKEN and saved it to .env"
fi

# --- preflight checks (fail early with a clear message) ---
if ! command -v "$CLAUDE_BIN" >/dev/null 2>&1; then
  echo "ERROR: '$CLAUDE_BIN' not found on PATH. Install Claude Code or set CLAUDE_BIN." >&2
  exit 1
fi
if ! python3 -c "import fastapi, uvicorn" 2>/dev/null; then
  echo ">> Installing fastapi + uvicorn ..."
  pip install --quiet fastapi uvicorn
fi

echo "----------------------------------------------------------------"
echo " Claude Code remote worker"
echo "   context dir : $CONTEXT_DIR"
echo "   claude bin  : $(command -v "$CLAUDE_BIN")"
echo "   listening   : http://$HOST:$PORT   (token in .env)"
echo ""
echo " Smoke test (in another terminal):"
echo "   curl -s localhost:$PORT/ask \\"
echo "     -H \"Authorization: Bearer \$WORKER_TOKEN\" -H 'Content-Type: application/json' \\"
echo "     -d '{\"prompt\":\"Say hi and tell me what context you have.\"}'"
echo "----------------------------------------------------------------"

# uvicorn must import "app.worker", so run from repo root with app/ as a package path.
exec python3 -m uvicorn app.worker:app --host "$HOST" --port "$PORT"

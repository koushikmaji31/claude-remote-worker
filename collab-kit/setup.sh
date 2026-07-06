#!/bin/bash
# One-time setup on each developer machine: point this machine's Claude
# sessions at the shared chat bus.
#
# Usage: ./setup.sh <BUS_URL> <BUS_TOKEN>
# e.g.:  ./setup.sh https://xxxx.ngrok-free.app 0123abcd...

set -e
BUS_URL=${1:?usage: setup.sh <BUS_URL> <BUS_TOKEN>}
BUS_TOKEN=${2:?usage: setup.sh <BUS_URL> <BUS_TOKEN>}

mkdir -p /tmp/claude-bus
printf '%s' "${BUS_URL%/}" > /tmp/claude-bus/url
printf '%s' "$BUS_TOKEN"   > /tmp/claude-bus/token
chmod +x hooks/*.sh 2>/dev/null || true

if command -v curl >/dev/null; then
  ok=$(curl -s -m 10 -H "Authorization: Bearer $BUS_TOKEN" "${BUS_URL%/}/health" || true)
  if [ "$ok" = '{"ok":true}' ]; then
    echo "Bus reachable at $BUS_URL — setup complete."
  else
    echo "WARNING: could not reach the bus at $BUS_URL (got: ${ok:-nothing})."
    echo "Config was written anyway; check the URL/token or the hub machine."
  fi
fi

echo "Now run: claude   (inside this repo) — your session auto-joins the bus."
echo "Optional fixed name: CLAUDE_BUS_NAME=<yourname> claude"

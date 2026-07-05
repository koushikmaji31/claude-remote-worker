#!/bin/bash
# Background bus listener for a Claude session. Run me with run_in_background;
# I long-poll the bus and EXIT as soon as messages arrive (printing them),
# which makes the harness wake the session — hands-free delivery in a plain
# claude terminal, no spinner, no typing.
#
# Usage: bash hooks/bus_wait.sh <name>

NAME=${1:?usage: bus_wait.sh <name>}
. "$(dirname "$0")/bus_env.sh" 2>/dev/null || BUS_URL=${CLAUDE_BUS_URL:-http://127.0.0.1:8899}

while true; do
  [ -f /tmp/claude-bus/off ] && exit 0
  out=$(curl -s -m 60 "${BUS_AUTH[@]}" "$BUS_URL/recv?name=$NAME&timeout=50")
  if [ -z "$out" ]; then sleep 3; continue; fi
  if [ "$out" != '{"messages":[]}' ]; then
    printf '%s\n' "$out"
    exit 0
  fi
done

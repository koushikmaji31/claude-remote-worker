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
  out=$(curl -s -m 60 "${BUS_AUTH[@]}" "$BUS_URL/recv?name=$NAME&timeout=50&room=$BUS_ROOM")
  if [ -z "$out" ]; then sleep 3; continue; fi
  if [ "$out" != '{"messages":[]}' ]; then
    # Drop pure roster notices (joins) — not worth waking the agent for.
    # Anything actionable (including presence alerts) still wakes it.
    filtered=$(printf '%s' "$out" | python3 -c '
import sys, json
try:
    msgs = json.load(sys.stdin).get("messages", [])
except Exception:
    msgs = []
keep = [m for m in msgs if "is online (new Claude session joined the bus)" not in m.get("text", "")]
print(json.dumps({"messages": keep}) if keep else "")
')
    if [ -n "$filtered" ]; then
      printf '%s\n' "$filtered"
      exit 0
    fi
  fi
done

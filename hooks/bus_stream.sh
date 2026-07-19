#!/bin/bash
# PERSISTENT bus listener for a Claude session. Run me ONCE via the Monitor tool
# (persistent:true, description 'bus'): each new message is one stdout line, and
# I never exit — so the session gets an event per message without re-arming the
# listener every turn. (Contrast bus_wait.sh, which exits on the first message
# and must be restarted; that's the run_in_background fallback for envs without
# the Monitor tool.)
#
# Usage: bash hooks/bus_stream.sh <name>

NAME=${1:?usage: bus_stream.sh <name>}
. "$(dirname "$0")/bus_env.sh" 2>/dev/null || BUS_URL=${CLAUDE_BUS_URL:-http://127.0.0.1:8899}
[ -z "${BUS_ROOM:-}" ] && exit 0   # no project room => not on the bus (no global channel)

while true; do
  [ -f /tmp/claude-bus/off ] && exit 0
  out=$(curl -s -m 60 "${BUS_AUTH[@]}" "$BUS_URL/recv?name=$NAME&timeout=50&room=$BUS_ROOM")
  if [ -z "$out" ]; then sleep 3; continue; fi
  [ "$out" = '{"messages":[]}' ] && continue
  # Drop roster/presence churn (joins + bus-server presence alerts); print one
  # line per batch of real messages, then KEEP LISTENING (no exit).
  printf '%s' "$out" | python3 -c '
import sys, json
try:
    msgs = json.load(sys.stdin).get("messages", [])
except Exception:
    msgs = []
def silent(m):
    return m.get("from") == "bus-server" or "is online (new Claude session joined" in m.get("text", "")
keep = [m for m in msgs if not silent(m)]
if keep:
    print(json.dumps({"messages": keep}), flush=True)
'
done

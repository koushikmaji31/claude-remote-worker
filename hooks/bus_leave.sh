#!/bin/bash
# SessionEnd hook: unregister this session from the chat bus so the roster
# stays accurate without manual cleanup. (Force-killed terminals skip this;
# the server's 30-minute auto-prune covers those.)

input=$(cat)
sid=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
namefile=/tmp/claude-bus/names/$sid
[ -s "$namefile" ] || exit 0
name=$(cat "$namefile")
. "$(dirname "$0")/bus_env.sh" 2>/dev/null || BUS_URL=${CLAUDE_BUS_URL:-http://127.0.0.1:8899}

curl -s -m 3 "${BUS_AUTH[@]}" -X POST "$BUS_URL/unregister?name=$name" >/dev/null 2>&1
# Stop this session's listener so it doesn't keep polling for a dead session
pkill -f "bus_wait.sh $name" 2>/dev/null
rm -f "$namefile"
exit 0

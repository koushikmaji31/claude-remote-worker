#!/bin/bash
# UserPromptSubmit hook: when the user sends a prompt, pick up any bus messages
# that queued while this session was idle and inject them as context — like /btw.

input=$(cat)
sid=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
namefile=/tmp/claude-bus/names/$sid
[ -s "$namefile" ] || exit 0
[ -f /tmp/claude-bus/off ] && exit 0
name=$(cat "$namefile")
. "$(dirname "$0")/bus_env.sh"

out=$(curl -s -m 5 "${BUS_AUTH[@]}" "$BUS_URL/recv?name=$name&timeout=0&room=$BUS_ROOM")
[ -z "$out" ] && exit 0
printf '%s' "$out" | python3 "$(dirname "$0")/bus_format.py" prompt
exit 0

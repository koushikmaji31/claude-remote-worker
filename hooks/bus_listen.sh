#!/bin/bash
# Stop hook: quick check of the chat bus when this Claude finishes a turn.
# If messages arrived while it was working, it reads them now (stop is blocked once);
# otherwise it goes idle normally — no busy spinner. Messages sent while idle are
# read on the user's next prompt (see bus_check.sh). Kill switch: touch /tmp/claude-bus/off

input=$(cat)
sid=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
namefile=/tmp/claude-bus/names/$sid
[ -s "$namefile" ] || exit 0
[ -f /tmp/claude-bus/off ] && exit 0
name=$(cat "$namefile")

out=$(curl -s -m 10 "localhost:8899/recv?name=$name&timeout=3")
[ -z "$out" ] && exit 0
printf '%s' "$out" | python3 "$(dirname "$0")/bus_format.py" stop
exit 0

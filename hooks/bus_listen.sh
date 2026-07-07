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
. "$(dirname "$0")/bus_env.sh"

out=$(curl -s -m 10 "${BUS_AUTH[@]}" "$BUS_URL/recv?name=$name&timeout=3&room=$BUS_ROOM")
if [ -n "$out" ]; then
  result=$(printf '%s' "$out" | python3 "$(dirname "$0")/bus_format.py" stop)
  if [ -n "$result" ]; then
    printf '%s\n' "$result"
    exit 0
  fi
fi

# Safety net: never go idle without the background listener running, or bus
# messages will queue with nothing to wake this session.
if ! pgrep -f "bus_wait.sh $name" >/dev/null 2>&1; then
  HOOKS_DIR=$(cd "$(dirname "$0")" && pwd)
  printf '{"decision":"block","reason":"Your bus listener is not running, so incoming bus messages cannot wake you. Start it NOW with the Bash tool (run_in_background=true): bash %s/bus_wait.sh %s — then end your turn."}\n' "$HOOKS_DIR" "$name"
fi
exit 0

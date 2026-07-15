#!/bin/bash
# SessionStart hook: auto-join this Claude session to the chat bus.
# Bus location/token come from hooks/bus_env.sh (env or /tmp/claude-bus files).
# Starts a local bus server only when the bus URL is local.

input=$(cat)
sid=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
mkdir -p /tmp/claude-bus/names
[ -f /tmp/claude-bus/off ] && exit 0
. "$(dirname "$0")/bus_env.sh"

# No project room => do NOT join any bus. There is no shared/global channel:
# a session only talks within its project. Run join-bus.sh <url> <token> <invite>
# in this repo to attach it to a project.
if [ -z "$BUS_ROOM" ]; then
  cat <<'EOF'
{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Not connected to any project bus (no project room set for this repo). There is no global channel. To join this repo to a project, run the join-bus command from the project's page (writes .claude/bus-room), then restart."}}
EOF
  exit 0
fi

# Start the bus server if it's down — but only for a local bus; never
# auto-start anything when pointed at a remote bus.
if ! curl -s -m 2 "${BUS_AUTH[@]}" "$BUS_URL/health" >/dev/null 2>&1; then
  bus_is_local || exit 0
  (cd /Users/koushikmaji31/Downloads/claude-remote-worker && nohup python3 -m uvicorn app.chat_server:app --host 127.0.0.1 --port 8899 >/tmp/claude-bus/server.log 2>&1 &)
  for i in $(seq 1 20); do curl -s -m 1 "$BUS_URL/health" >/dev/null 2>&1 && break; sleep 0.5; done
fi

namefile=/tmp/claude-bus/names/$sid
if [ -n "$CLAUDE_BUS_NAME" ]; then
  # Full explicit override always wins.
  name=$CLAUDE_BUS_NAME
elif [ -s "$namefile" ]; then
  # Reuse the name already assigned to this session.
  name=$(cat "$namefile")
else
  # Derive a per-machine base name and append the lowest free number,
  # e.g. koushik_1, koushik_2 (or shantanu_1 on another machine).
  # Base = the teammate's platform name (BUS_USER: env CLAUDE_BUS_USER or
  # /tmp/claude-bus/user, written at join time by join-bus.sh), else the
  # account's first name from the OS full name, else the login name with
  # trailing digits stripped. Falls back to "agent".
  base=${BUS_USER:-}
  if [ -z "$base" ]; then
    fullname=$(id -F 2>/dev/null)          # macOS real name, e.g. "Koushik Maji"
    base=${fullname%% *}                    # first token -> "Koushik"
  fi
  [ -z "$base" ] && base=$(id -un 2>/dev/null || whoami)
  base=$(printf '%s' "$base" | tr 'A-Z' 'a-z' | tr -cd 'a-z0-9_-')
  base=${base%%[0-9]*}
  [ -z "$base" ] && base=agent
  taken=$(curl -s -m 3 "${BUS_AUTH[@]}" "$BUS_URL/who?room=*")
  n=1
  while case "$taken" in *"\"${base}_${n}\""*) true ;; *) false ;; esac; do
    n=$((n + 1))
  done
  name=${base}_${n}
fi
printf '%s' "$name" > "$namefile"
# Register on the bus so broadcasts reach us (does NOT consume queued messages)
curl -s -m 3 "${BUS_AUTH[@]}" -X POST "$BUS_URL/register?name=$name&room=$BUS_ROOM" >/dev/null 2>&1
# Announce ourselves so every other session learns our name automatically
curl -s -m 3 "${BUS_AUTH[@]}" "$BUS_URL/send" -H 'Content-Type: application/json' \
  -d "{\"sender\":\"$name\",\"to\":null,\"room\":\"$BUS_ROOM\",\"text\":\"[$name] is online (new Claude session joined the group).\"}" >/dev/null 2>&1
online=$(curl -s -m 3 "${BUS_AUTH[@]}" "$BUS_URL/who?room=$BUS_ROOM" | python3 -c 'import sys,json;print(", ".join(json.load(sys.stdin).get("clients",[])))' 2>/dev/null)

auth_hint=""
[ -n "$BUS_TOKEN" ] && auth_hint=" -H 'Authorization: Bearer $BUS_TOKEN'"
HOOKS_DIR=$(cd "$(dirname "$0")" && pwd)

cat <<EOF
{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"You are '$name' on the Claude chat bus at $BUS_URL (already registered and announced; online: $online). LISTENER: on your first turn and before ending every turn, ensure this is running via the Bash tool with run_in_background=true and description 'bus': bash $HOOKS_DIR/bus_wait.sh $name — when it completes, its output is new bus messages: show them as 'BUS <- [sender]: text', handle them, restart the listener. MESSAGING: use the MCP bus tools (bus_send with sender=$name / bus_check / bus_who; curl $BUS_URL/send$auth_hint as fallback); show sends as 'BUS -> [name]: text'; plain text, no emojis. TEAM NORMS DIGEST (do NOT read docs or bus history at startup — this digest is enough until a task needs more): claim paths on broadcast before editing (CLAIM <paths> — $name, task: ...), first claim wins, release in your push announcement; broadcast = one-line milestones/claims/pushes with commit hash, DM = per-file asks; git add only your claimed paths, commit-or-stash before rebase; announce before restarting shared infra. STARTUP DISCIPLINE: keep your first turn MINIMAL — start the listener, tell the user your bus name and that you are ready, handle only ACTIONABLE queued messages. Never reply to or comment on <agent> is online announcements or presence alerts; no greeting chatter, no thanks/ack messages unless asked a direct question; do not message other agents at startup."}}
EOF

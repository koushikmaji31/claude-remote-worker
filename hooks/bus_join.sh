#!/bin/bash
# SessionStart hook: auto-join this Claude session to the local chat bus (port 8899).
# Starts the bus server if it isn't running, picks a name, and tells Claude about it.

input=$(cat)
sid=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null)
mkdir -p /tmp/claude-bus/names
[ -f /tmp/claude-bus/off ] && exit 0

# Start the bus server if it's down
if ! curl -s -m 2 localhost:8899/health >/dev/null 2>&1; then
  (cd /Users/koushikmaji31/Downloads/claude-remote-worker && nohup python3 -m uvicorn app.chat_server:app --host 127.0.0.1 --port 8899 >/tmp/claude-bus/server.log 2>&1 &)
  for i in $(seq 1 20); do curl -s -m 1 localhost:8899/health >/dev/null 2>&1 && break; sleep 0.5; done
fi

namefile=/tmp/claude-bus/names/$sid
if [ -n "$CLAUDE_BUS_NAME" ]; then
  name=$CLAUDE_BUS_NAME
elif [ -s "$namefile" ]; then
  name=$(cat "$namefile")
else
  taken=$(curl -s -m 3 localhost:8899/who)
  name=agent-f
  for c in agent-a agent-b agent-c agent-d agent-e; do
    case "$taken" in *"\"$c\""*) ;; *) name=$c; break ;; esac
  done
fi
printf '%s' "$name" > "$namefile"
# Register on the bus so broadcasts reach us (does NOT consume queued messages)
curl -s -m 3 -X POST "localhost:8899/register?name=$name" >/dev/null 2>&1
# Announce ourselves so every other session learns our name automatically
curl -s -m 3 localhost:8899/send -H 'Content-Type: application/json' \
  -d "{\"sender\":\"$name\",\"to\":null,\"text\":\"👋 [$name] just came online (new Claude session joined the bus).\"}" >/dev/null 2>&1
online=$(curl -s -m 3 localhost:8899/who | python3 -c 'import sys,json;print(", ".join(json.load(sys.stdin).get("clients",[])))' 2>/dev/null)

cat <<EOF
{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"You are connected to the local Claude chat bus at http://127.0.0.1:8899 as '$name'. Currently on the bus: $online. Your arrival was already announced to everyone, and you'll be told automatically when others join. New messages for you are delivered automatically — at the end of your turn if they arrive while you work, or with the user's next prompt if they arrive while you're idle. You don't need to poll. To send a message: curl -s localhost:8899/send -H 'Content-Type: application/json' -d '{\"sender\":\"$name\",\"to\":\"other-name\",\"text\":\"...\"}' — use \"to\":null to broadcast to everyone. Full chat log: curl -s localhost:8899/history. IMPORTANT: whenever you receive bus messages, first show them to the user verbatim in your response (e.g. '📨 [sender] text'), then act on them if reasonable and reply to the sender over the bus. Likewise, when you send a bus message, show the user what you sent (e.g. '📤 to [name]: text'). The user watches the conversation through your terminal. Tell the user your bus name when you first respond."}}
EOF

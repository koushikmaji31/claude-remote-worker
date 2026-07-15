# Shared bus config for the chat-bus hooks. Sourced, not executed.
# URL: env CLAUDE_BUS_URL > file /tmp/claude-bus/url > default localhost.
# Token: env CLAUDE_BUS_TOKEN > file /tmp/claude-bus/token > none.
# The server only enforces the token for remote/forwarded callers, so
# localhost keeps working without one.

if [ -n "$CLAUDE_BUS_URL" ]; then
  BUS_URL=$CLAUDE_BUS_URL
elif [ -s /tmp/claude-bus/url ]; then
  BUS_URL=$(cat /tmp/claude-bus/url)
else
  BUS_URL=http://127.0.0.1:8899
fi
BUS_URL=${BUS_URL%/}

if [ -n "$CLAUDE_BUS_TOKEN" ]; then
  BUS_TOKEN=$CLAUDE_BUS_TOKEN
elif [ -s /tmp/claude-bus/token ]; then
  BUS_TOKEN=$(cat /tmp/claude-bus/token)
else
  BUS_TOKEN=""
fi

BUS_AUTH=()
[ -n "$BUS_TOKEN" ] && BUS_AUTH=(-H "Authorization: Bearer $BUS_TOKEN")

# Room: which project group this session is joined to. Resolved PER-REPO so two
# repos on the same machine stay in different rooms (the old machine-global
# /tmp/claude-bus/room collapsed every session into whichever room was written
# last). Order: env CLAUDE_BUS_ROOM > <repo>/.claude/bus-room > /tmp fallback > global.
_repo_room="${CLAUDE_PROJECT_DIR:-$PWD}/.claude/bus-room"
if [ -n "$CLAUDE_BUS_ROOM" ]; then
  BUS_ROOM=$CLAUDE_BUS_ROOM
elif [ -s "$_repo_room" ]; then
  BUS_ROOM=$(cat "$_repo_room")
elif [ -s /tmp/claude-bus/room ]; then
  BUS_ROOM=$(cat /tmp/claude-bus/room)
else
  BUS_ROOM=global
fi

# Agent name prefix for this teammate ("koushik" -> koushik_1, koushik_2 ...).
# env CLAUDE_BUS_USER > file /tmp/claude-bus/user (written by join-bus.sh from
# the platform account name) > empty (bus_join falls back to the OS account).
if [ -n "$CLAUDE_BUS_USER" ]; then
  BUS_USER=$CLAUDE_BUS_USER
elif [ -s /tmp/claude-bus/user ]; then
  BUS_USER=$(cat /tmp/claude-bus/user)
else
  BUS_USER=""
fi

bus_is_local() {
  case "$BUS_URL" in
    http://127.0.0.1:*|http://localhost:*|http://127.0.0.1|http://localhost) return 0 ;;
    *) return 1 ;;
  esac
}

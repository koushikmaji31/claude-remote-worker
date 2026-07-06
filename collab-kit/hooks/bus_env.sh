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

bus_is_local() {
  case "$BUS_URL" in
    http://127.0.0.1:*|http://localhost:*|http://127.0.0.1|http://localhost) return 0 ;;
    *) return 1 ;;
  esac
}

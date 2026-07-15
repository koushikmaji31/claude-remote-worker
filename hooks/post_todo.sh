#!/bin/bash
# PostToolUse hook for TodoWrite: auto-publish this agent's todo list to the
# project's Ticket board so teammates see it live on the dashboard. Uses curl
# (passes Cloudflare) + the bus token; scoped to the repo's project room.
# No project room => no-op (there is no global board). Kill switch: /tmp/claude-bus/off
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
input=$(cat)
[ -f /tmp/claude-bus/off ] && exit 0

# Project room (invite code) for this repo.
ROOM=${CLAUDE_BUS_ROOM:-}
if [ -z "$ROOM" ]; then
  _rr="${CLAUDE_PROJECT_DIR:-$PWD}/.claude/bus-room"
  if [ -s "$_rr" ]; then ROOM=$(cat "$_rr"); elif [ -s /tmp/claude-bus/room ]; then ROOM=$(cat /tmp/claude-bus/room); fi
fi
[ "$ROOM" = "global" ] && ROOM=""
[ -z "$ROOM" ] && exit 0

# The Ticket API lives on the platform (not the bus). Derive it from the bus URL:
# strip a leading 'bus.' host and map the local bus port 8899 -> platform 8900.
BUS=$([ -s /tmp/claude-bus/url ] && cat /tmp/claude-bus/url || echo "http://127.0.0.1:8900")
PLATFORM=$(printf '%s' "$BUS" | sed -e 's#//bus\.#//#' -e 's#:8899#:8900#')
PLATFORM=${PLATFORM%/}
TOKEN=$(cat /tmp/claude-bus/token 2>/dev/null || true)
AUTH=(); [ -n "$TOKEN" ] && AUTH=(-H "Authorization: Bearer $TOKEN")

# Agent name (same as the bus name for this session).
sid=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null || true)
AGENT=${CLAUDE_BUS_NAME:-}
if [ -z "$AGENT" ]; then
  nf=/tmp/claude-bus/names/$sid
  if [ -n "$sid" ] && [ -s "$nf" ]; then AGENT=$(cat "$nf"); else AGENT=$(hostname 2>/dev/null || echo agent); fi
fi

payload=$(printf '%s' "$input" | python3 "$HERE/todo_payload.py" "$AGENT")
curl -s -m 5 "${AUTH[@]}" -X POST "$PLATFORM/api/ticket/$ROOM/tasks" \
  -H 'Content-Type: application/json' -d "$payload" >/dev/null 2>&1 || true
exit 0

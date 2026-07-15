#!/bin/bash
# PostToolUse hook for Write/Edit/MultiEdit.
#
# After Claude writes a file, compute THIS machine's pending footprint (touched
# line ranges per file vs the shared base origin/main) and POST it to the
# conflict server. The server compares our footprint against every OTHER
# machine in the same project and returns any overlapping-line conflicts.
# If a conflict is found we emit a PostToolUse warning back to Claude so it
# reconciles (pull/rebase or coordinate on the bus) BEFORE going further.
#
# Config (env or /tmp/claude-bus files, mirroring bus_env.sh conventions):
#   CONFLICT_URL      conflict server base url (default http://127.0.0.1:8901)
#   CONFLICT_PROJECT  developer group id     (default from /tmp/claude-bus/room, else 'default')
#   CONFLICT_MACHINE  this machine/agent id  (default from /tmp/claude-bus/names/<sid>, else hostname)
#   CONFLICT_REPO     repo to diff           (default: CLAUDE_PROJECT_DIR, else the tool's cwd)
#   CONFLICT_BASE     base ref to diff vs    (default origin/main)
# Kill switch: touch /tmp/claude-bus/conflict-off

set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
input=$(cat)

[ -f /tmp/claude-bus/conflict-off ] && exit 0

# --- resolve config -------------------------------------------------------
URL=${CONFLICT_URL:-http://127.0.0.1:8901}
URL=${URL%/}
BASE=${CONFLICT_BASE:-origin/main}

sid=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null || true)

PROJECT=${CONFLICT_PROJECT:-}
if [ -z "$PROJECT" ]; then
  if [ -s /tmp/claude-bus/room ]; then PROJECT=$(cat /tmp/claude-bus/room); else PROJECT=default; fi
fi

MACHINE=${CONFLICT_MACHINE:-}
if [ -z "$MACHINE" ]; then
  namefile=/tmp/claude-bus/names/$sid
  if [ -n "$sid" ] && [ -s "$namefile" ]; then MACHINE=$(cat "$namefile"); else MACHINE=$(hostname 2>/dev/null || echo machine); fi
fi

# Repo to diff: explicit override, else the harness project dir, else the tool cwd.
REPO=${CONFLICT_REPO:-${CLAUDE_PROJECT_DIR:-}}
if [ -z "$REPO" ]; then
  REPO=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("cwd",""))' 2>/dev/null || true)
fi
[ -z "$REPO" ] && REPO=$(pwd)

# --- compute this machine's footprint -------------------------------------
footprint=$(python3 "$HERE/diff_hunks.py" "$REPO" "$BASE" 2>/dev/null || echo '{}')

# Build the report payload: {project, machine, base_sha, files}
payload=$(printf '%s' "$footprint" | python3 "$HERE/format_report.py" payload "$PROJECT" "$MACHINE")

# --- report to server & read back conflicts -------------------------------
resp=$(curl -s -m 5 -X POST "$URL/diff/report" \
  -H 'Content-Type: application/json' -d "$payload" 2>/dev/null || echo '')

[ -z "$resp" ] && exit 0   # server down: never block the write

# Emit a PostToolUse warning if the server reported overlapping conflicts
# (empty output from the formatter = no conflict = hook stays silent).
printf '%s' "$resp" | python3 "$HERE/format_report.py" warning
exit 0

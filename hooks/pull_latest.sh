#!/bin/bash
# SessionStart hook: pull the latest shared code ONCE so this machine starts
# from the same base as everyone else, then register this machine's initial
# (clean) footprint with the conflict server.
#
# "Once": guarded by a per-session marker so repeated SessionStart events
# (resume/compaction) don't re-pull mid-work.
#
# Config: same env/files as post_write.sh (CONFLICT_URL/PROJECT/MACHINE/REPO/BASE).
# Kill switch: touch /tmp/claude-bus/conflict-off

set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
input=$(cat)
[ -f /tmp/claude-bus/conflict-off ] && exit 0

sid=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("session_id",""))' 2>/dev/null || true)

# Pull only once per session.
mkdir -p /tmp/claude-bus/pulled
marker=/tmp/claude-bus/pulled/${sid:-nosid}
[ -f "$marker" ] && exit 0

URL=${CONFLICT_URL:-http://127.0.0.1:8901}; URL=${URL%/}
BASE=${CONFLICT_BASE:-origin/main}

PROJECT=${CONFLICT_PROJECT:-}
[ -z "$PROJECT" ] && { [ -s /tmp/claude-bus/room ] && PROJECT=$(cat /tmp/claude-bus/room) || PROJECT=default; }

MACHINE=${CONFLICT_MACHINE:-}
if [ -z "$MACHINE" ]; then
  namefile=/tmp/claude-bus/names/$sid
  if [ -n "$sid" ] && [ -s "$namefile" ]; then MACHINE=$(cat "$namefile"); else MACHINE=$(hostname 2>/dev/null || echo machine); fi
fi

REPO=${CONFLICT_REPO:-${CLAUDE_PROJECT_DIR:-}}
[ -z "$REPO" ] && REPO=$(printf '%s' "$input" | python3 -c 'import sys,json;print(json.load(sys.stdin).get("cwd",""))' 2>/dev/null || true)
[ -z "$REPO" ] && REPO=$(pwd)

note=""
if git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  # Fetch + fast-forward pull; capture but don't fail the session on pull error.
  if git -C "$REPO" remote | grep -q .; then
    if git -C "$REPO" pull --ff-only >/dev/null 2>&1; then
      note="pulled latest ($BASE) at startup."
    else
      note="could not fast-forward pull (local changes or diverged); start from current HEAD."
    fi
  else
    note="no git remote configured; skipped pull."
  fi
else
  note="not a git repo; skipped pull."
fi

# Register initial footprint (usually empty after a clean pull) so the server
# knows this machine exists in the project from the start.
footprint=$(python3 "$HERE/diff_hunks.py" "$REPO" "$BASE" 2>/dev/null || echo '{}')
payload=$(printf '%s' "$footprint" | python3 "$HERE/format_report.py" payload "$PROJECT" "$MACHINE")
curl -s -m 5 -X POST "$URL/diff/report" -H 'Content-Type: application/json' -d "$payload" >/dev/null 2>&1 || true

touch "$marker"

cat <<EOF
{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"Conflict-guard active for project '$PROJECT' as machine '$MACHINE'. $note After each Write/Edit you will be warned if another machine's uncommitted changes overlap the lines you touched."}}
EOF
exit 0

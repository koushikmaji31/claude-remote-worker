#!/bin/bash
# One-command client install: connect the current project to the shared
# Claude chat bus. Run from INSIDE your project repo (e.g. retrive/):
#
#   curl -sL https://raw.githubusercontent.com/koushikmaji31/claude-remote-worker/main/join-bus.sh \
#     | bash -s -- <BUS_URL> <BUS_TOKEN>
#
# Then just run `claude` in the repo (approve hooks + MCP once) and say hi.

set -e
BUS_URL=${1:?usage: join-bus.sh <BUS_URL> <BUS_TOKEN> [ROOM] [NAME]}
BUS_TOKEN=${2:?usage: join-bus.sh <BUS_URL> <BUS_TOKEN> [ROOM] [NAME]}
BUS_ROOM=${3:-global}   # project group (invite code); 'global' = shared bus
BUS_USER=${4:-}         # teammate name from the platform -> agent prefix (koushik_1, ...)
KIT_TARBALL="https://github.com/koushikmaji31/claude-remote-worker/archive/refs/heads/main.tar.gz"

if [ ! -d .git ]; then
  echo "WARNING: current dir is not a git repo root — installing here anyway: $(pwd)"
fi

echo "Installing collab kit into $(pwd) ..."
tmp=$(mktemp -d)
curl -sL "$KIT_TARBALL" | tar -xz -C "$tmp"
src=$(echo "$tmp"/*/collab-kit)
mkdir -p hooks .claude
cp "$src"/hooks/* hooks/
cp "$src"/.mcp.json .mcp.json
# Wire the hooks: if the repo already has a settings.json, MERGE the bus hook
# entries into it (idempotent) instead of skipping — otherwise a repo with its
# own settings never auto-joins the bus.
if [ -f .claude/settings.json ]; then
  if python3 "$src"/hooks/merge_settings.py .claude/settings.json "$src"/.claude/settings.json; then
    :
  else
    echo "NOTE: could not auto-merge .claude/settings.json — wrote .claude/settings.bus.json to merge manually."
    cp "$src"/.claude/settings.json .claude/settings.bus.json
  fi
else
  cp "$src"/.claude/settings.json .claude/settings.json
fi
rm -rf "$tmp"
chmod +x hooks/*.sh

mkdir -p /tmp/claude-bus
printf '%s' "${BUS_URL%/}" > /tmp/claude-bus/url
printf '%s' "$BUS_TOKEN"   > /tmp/claude-bus/token
printf '%s' "$BUS_ROOM"    > /tmp/claude-bus/room
[ -n "$BUS_USER" ] && printf '%s' "$BUS_USER" > /tmp/claude-bus/user

# Pin the room to THIS repo so different repos on one machine stay in different
# groups (the /tmp copy is a machine-global fallback that any join/tool overwrites).
printf '%s' "$BUS_ROOM" > .claude/bus-room

ok=$(curl -s -m 10 -H "Authorization: Bearer $BUS_TOKEN" "${BUS_URL%/}/health" || true)
if [ "$ok" = '{"ok":true}' ]; then
  echo "Bus reachable — you are connected."
else
  echo "WARNING: bus not reachable at $BUS_URL (got: ${ok:-nothing}). Config written anyway."
fi

echo
echo "Done. Now run:  claude    (in this repo; approve hooks + MCP server once)"
echo "Fixed name:     CLAUDE_BUS_NAME=<yourname> claude"
echo "Tip: commit hooks/ .claude/ .mcp.json to the repo so teammates skip this install."

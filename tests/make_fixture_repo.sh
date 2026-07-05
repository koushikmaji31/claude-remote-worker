#!/bin/bash
# Creates a throwaway git repo with two branches that (a) differ and (b) conflict,
# for exercising the /rpc git.diff and git.conflicts methods.
# Prints the repo path on stdout (last line).
set -e
FIX=${1:-/tmp/claude-bus/fixture-repo}
rm -rf "$FIX"
mkdir -p "$FIX"
cd "$FIX"
git init -q -b main
git config user.email fixture@test.local
git config user.name Fixture
printf 'line1\nline2\nline3\n' > app.txt
git add app.txt && git commit -qm "base"
git checkout -qb feature-a
printf 'line1\nA-CHANGE\nline3\nadded-by-a\n' > app.txt
git add app.txt && git commit -qm "a: change line2, add line"
git checkout -q main
git checkout -qb feature-b
printf 'line1\nB-CHANGE\nline3\n' > app.txt
git add app.txt && git commit -qm "b: change line2 differently"
git checkout -q main
echo "$FIX"

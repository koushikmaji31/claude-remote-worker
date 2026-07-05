#!/bin/bash
# End-to-end smoke test for the team-collab platform backend (docs/API_CONTRACT.md).
# Usage: bash tests/smoke_platform.sh   (backend must be running on :8900)
set -u
BASE=http://127.0.0.1:8900
REPO=/Users/koushikmaji31/Downloads/claude-remote-worker
PASS=0; FAIL=0
RUN=$RANDOM$RANDOM

check() { # check <desc> <actual> <grep-pattern>
  if printf '%s' "$2" | grep -q "$3"; then PASS=$((PASS+1)); echo "  ok: $1"
  else FAIL=$((FAIL+1)); echo "  FAIL: $1"; echo "    got: $2"; fi
}
jqget() { printf '%s' "$1" | python3 -c "import sys,json;print(json.load(sys.stdin)$2)" 2>/dev/null; }

echo "== health"
r=$(curl -s -m 5 "$BASE/api/register" -X POST -H 'Content-Type: application/json' -d '{}')
[ -z "$r" ] && { echo "FATAL: backend not reachable on :8900"; exit 1; }

echo "== register two users"
A=$(curl -s "$BASE/api/register" -X POST -H 'Content-Type: application/json' -d "{\"name\":\"Alice\",\"email\":\"alice$RUN@x.com\"}")
B=$(curl -s "$BASE/api/register" -X POST -H 'Content-Type: application/json' -d "{\"name\":\"Bob\",\"email\":\"bob$RUN@x.com\"}")
check "alice registered" "$A" '"token"'
check "bob registered" "$B" '"token"'
TA=$(jqget "$A" '["token"]'); TB=$(jqget "$B" '["token"]')
BID=$(jqget "$B" '["user_id"]')

echo "== duplicate email -> 409"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/register" -X POST -H 'Content-Type: application/json' -d "{\"name\":\"Alice2\",\"email\":\"alice$RUN@x.com\"}")
check "409 on dup email" "$code" '^409$'

echo "== login"
L=$(curl -s "$BASE/api/login" -X POST -H 'Content-Type: application/json' -d "{\"email\":\"alice$RUN@x.com\"}")
check "login returns token" "$L" '"token"'

echo "== auth required"
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/me")
check "401 without token" "$code" '^401$'
ME=$(curl -s "$BASE/api/me" -H "Authorization: Bearer $TA")
check "/api/me with token" "$ME" 'Alice'

echo "== create project (alice = admin)"
P=$(curl -s "$BASE/api/projects" -X POST -H "Authorization: Bearer $TA" -H 'Content-Type: application/json' -d '{"name":"demo-proj"}')
check "project created" "$P" '"invite_code"'
PID=$(jqget "$P" '["project_id"]'); CODE=$(jqget "$P" '["invite_code"]')

echo "== join preview (no auth) + bob joins"
J=$(curl -s "$BASE/api/join/$CODE")
check "join preview" "$J" 'demo-proj'
JB=$(curl -s "$BASE/api/join/$CODE" -X POST -H "Authorization: Bearer $TB")
check "bob joined as member" "$JB" '"member"'

echo "== membership + detail"
D=$(curl -s "$BASE/api/projects/$PID" -H "Authorization: Bearer $TA")
check "detail lists bob" "$D" 'Bob'
code=$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/projects/$PID" -H "Authorization: Bearer invalidtoken")
check "401 bad token on detail" "$code" '^401$'

echo "== messages"
curl -s "$BASE/api/projects/$PID/messages" -X POST -H "Authorization: Bearer $TB" -H 'Content-Type: application/json' -d '{"text":"hello from bob"}' >/dev/null
M=$(curl -s "$BASE/api/projects/$PID/messages?since_id=0" -H "Authorization: Bearer $TA")
check "message log shows bob's msg" "$M" 'hello from bob'

echo "== admin controls"
code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$BASE/api/projects/$PID/members/$BID" -H "Authorization: Bearer $TB")
check "403 non-admin remove" "$code" '^403$'
R=$(curl -s -X DELETE "$BASE/api/projects/$PID/members/$BID" -H "Authorization: Bearer $TA")
check "admin removes bob" "$R" '"ok"'
D2=$(curl -s "$BASE/api/projects/$PID" -H "Authorization: Bearer $TA")
check "bob gone from members" "$(printf '%s' "$D2" | grep -c Bob)" '^0$'

echo "== rpc git.* (against fixture repo with conflicting branches)"
FIX=$(bash "$(dirname "$0")/make_fixture_repo.sh" | tail -1)
RPC=$(curl -s "$BASE/rpc" -X POST -H "Authorization: Bearer $TA" -H 'Content-Type: application/json' -d "{\"method\":\"git.branches\",\"params\":{\"repo_path\":\"$FIX\"},\"id\":1}")
check "git.branches lists feature-a" "$RPC" 'feature-a'
DIFF=$(curl -s "$BASE/rpc" -X POST -H "Authorization: Bearer $TA" -H 'Content-Type: application/json' -d "{\"method\":\"git.diff\",\"params\":{\"repo_path\":\"$FIX\",\"base\":\"main\",\"head\":\"feature-a\"},\"id\":2}")
check "git.diff shows A-CHANGE" "$DIFF" 'A-CHANGE'
CONF=$(curl -s "$BASE/rpc" -X POST -H "Authorization: Bearer $TA" -H 'Content-Type: application/json' -d "{\"method\":\"git.conflicts\",\"params\":{\"repo_path\":\"$FIX\",\"base\":\"feature-a\",\"head\":\"feature-b\"},\"id\":3}")
check "git.conflicts finds app.txt" "$CONF" 'app.txt'
RPCE=$(curl -s "$BASE/rpc" -X POST -H "Authorization: Bearer $TA" -H 'Content-Type: application/json' -d '{"method":"nope","params":{},"id":4}')
check "unknown method -> -32601" "$RPCE" '32601'

echo
echo "RESULT: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]

#!/usr/bin/env python3
"""PreToolUse hook: gate risky tool calls on a human's approval from the Activity page.

Flavor B of remote steering. When enabled, before a gated tool (Bash/Write/Edit/…)
runs, this posts an "Approve <tool>?" decision to the platform and BLOCKS until a
human clicks Approve/Deny in the Activity feed. Approve → the tool runs; Deny (or
timeout) → the tool call is blocked and the reason is fed back to Claude.

It reuses the same decisions round-trip as the `ask_human` MCP tool, so no new
backend is needed — an approval is just a decision with options ["approve","deny"].

OPT-IN: does nothing unless REMOTE_APPROVAL=1 (so normal local sessions are
unaffected). Gate only autonomous / delegated agents where you want a human in
the loop. Register as a PreToolUse hook (see README snippet at bottom).

Contract: reads the hook JSON on stdin; exit 0 = allow the tool, exit 2 = block
it (stderr is shown to Claude). Fails OPEN on infra errors (won't brick an agent),
fails CLOSED (deny) only on explicit human deny or timeout.
"""

import os
import sys
import json
import time
import urllib.parse
import urllib.request
import urllib.error

# Which tools require approval, and how long to wait for a human.
GATED = set((os.environ.get("REMOTE_APPROVAL_TOOLS")
             or "Bash,Write,Edit,MultiEdit,NotebookEdit").split(","))
TIMEOUT = int(os.environ.get("REMOTE_APPROVAL_TIMEOUT", "600"))   # 10 min → deny
POLL = int(os.environ.get("REMOTE_APPROVAL_POLL", "3"))


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _allow():   # let the tool run
    sys.exit(0)


def _deny(reason):   # block the tool; reason goes back to Claude
    sys.stderr.write(reason)
    sys.exit(2)


def _platform_url():
    env = os.environ.get("TICKET_URL")
    if env:
        return env.rstrip("/")
    bus = _read("/tmp/claude-bus/url")
    if bus:
        p = urllib.parse.urlsplit(bus)
        host = (p.hostname or "127.0.0.1")
        if host.startswith("bus."):
            host = host[4:]
        port = 8900 if p.port == 8899 else p.port
        netloc = f"{host}:{port}" if port else host
        return urllib.parse.urlunsplit((p.scheme or "http", netloc, "", "", "")).rstrip("/")
    return "http://127.0.0.1:8900"


def _room():
    r = (os.environ.get("CLAUDE_BUS_ROOM")
         or _read(os.path.join(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(), ".claude", "bus-room"))
         or _read("/tmp/claude-bus/room") or "")
    return "" if r == "global" else r


def _token():
    return os.environ.get("CLAUDE_BUS_TOKEN") or _read("/tmp/claude-bus/token")


def _http(method, path, body=None):
    req = urllib.request.Request(_platform_url() + path, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "TeamCollab-Approval/1.0")
    tok = _token()
    if tok:
        req.add_header("Authorization", f"Bearer {tok}")
    data = json.dumps(body).encode() if body is not None else None
    with urllib.request.urlopen(req, data=data, timeout=20) as r:
        return json.loads(r.read().decode())


def _summary(tool, ti):
    if tool == "Bash":
        return (ti.get("command") or "")[:300]
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        return (ti.get("file_path") or ti.get("notebook_path") or "")[:300]
    return json.dumps(ti)[:300]


def main():
    if os.environ.get("REMOTE_APPROVAL") not in ("1", "true", "True"):
        _allow()                      # feature off → no-op
    try:
        payload = json.load(sys.stdin)
    except Exception:
        _allow()                      # can't parse → don't block the agent
    tool = payload.get("tool_name") or payload.get("tool") or ""
    if tool not in GATED:
        _allow()                      # not a risky tool → allow

    room = _room()
    if not room:
        _allow()                      # not attached to a project → can't gate

    ti = payload.get("tool_input") or payload.get("toolInput") or {}
    agent = os.environ.get("CLAUDE_BUS_NAME") or "an agent"
    question = f"Approve {tool}? {_summary(tool, ti)}".strip()

    try:
        created = _http("POST", f"/api/ticket/{room}/decisions",
                        {"agent": agent, "question": question, "options": ["approve", "deny"]})
        did = created.get("id")
        if not did:
            _allow()                  # couldn't create → fail open
    except Exception:
        _allow()                      # infra error → fail open (don't brick agent)

    deadline = time.time() + TIMEOUT
    while time.time() < deadline:
        time.sleep(POLL)
        try:
            res = _http("GET", f"/api/ticket/{room}/decisions/{did}")
        except Exception:
            continue
        if res.get("status") == "answered":
            if res.get("answer") == "approve":
                _allow()
            _deny(f"A human denied this {tool} call from the Activity page.")
    _deny(f"No human approved this {tool} call within {TIMEOUT}s — blocked for safety. "
          f"Ask again or proceed with a safer alternative.")


if __name__ == "__main__":
    main()

# --- Register (opt-in) in .claude/settings.json ---------------------------
#   "hooks": { "PreToolUse": [{ "matcher": "Bash|Write|Edit|MultiEdit",
#     "hooks": [{ "type": "command", "command": "python3 hooks/approve_tool.py" }] }] }
# Then run the agent with REMOTE_APPROVAL=1 to require Activity-page approval.

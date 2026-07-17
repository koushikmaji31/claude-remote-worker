#!/usr/bin/env python3
"""Minimal MCP (stdio) server exposing the per-project Ticket dashboard as tools.

No third-party deps — speaks MCP's JSON-RPC over stdin/stdout directly.
Configured via .mcp.json at the repo root; every Claude session in this project
gets: ticket_set_tasks, ticket_list, ticket_set_ticket, ticket_get_ticket.

The platform URL resolves from env TICKET_URL, else it's derived from the bus url
in /tmp/claude-bus/url (strip a leading 'bus.' host, map port :8899 -> :8900),
fallback http://127.0.0.1:8900. Auth uses the bus token (/tmp/claude-bus/token)
and the room (=project invite_code, /tmp/claude-bus/room). The agent name comes
from /tmp/claude-bus/names/* or CLAUDE_BUS_NAME (fallback hostname).
"""

import os
import sys
import json
import time
import socket
import urllib.parse
import urllib.request
import urllib.error

# ask_human blocks the tool call until a human answers on the Activity page.
ASK_HUMAN_TIMEOUT = int(os.environ.get("ASK_HUMAN_TIMEOUT", "600"))  # 10 min
ASK_HUMAN_POLL = int(os.environ.get("ASK_HUMAN_POLL", "3"))          # seconds


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _derive_url():
    """TICKET_URL env, else derive the platform URL from the bus url."""
    env = os.environ.get("TICKET_URL")
    if env:
        return env.rstrip("/")
    bus = _read("/tmp/claude-bus/url")
    if bus:
        parts = urllib.parse.urlsplit(bus)
        host = parts.hostname or "127.0.0.1"
        if host.startswith("bus."):
            host = host[len("bus."):]
        port = parts.port
        if port == 8899:
            port = 8900
        netloc = host
        if port:
            netloc = f"{host}:{port}"
        return urllib.parse.urlunsplit((parts.scheme or "http", netloc, "", "", "")).rstrip("/")
    return "http://127.0.0.1:8900"


def _agent_name():
    name = os.environ.get("CLAUDE_BUS_NAME")
    if name:
        return name
    names_dir = "/tmp/claude-bus/names"
    try:
        for fn in sorted(os.listdir(names_dir)):
            val = _read(os.path.join(names_dir, fn))
            if val:
                return val
    except OSError:
        pass
    return socket.gethostname()


def _room():
    """Room (=project invite code) resolved PER-REPO, not machine-global:
    env CLAUDE_BUS_ROOM > <repo>/.claude/bus-room > /tmp/claude-bus/room.
    Returns "" when this repo isn't attached to a project — there is no 'global'
    pool, so ticket tools then refuse rather than touching a shared channel."""
    r = (os.environ.get("CLAUDE_BUS_ROOM")
         or _read(os.path.join(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd(), ".claude", "bus-room"))
         or _read("/tmp/claude-bus/room") or "")
    return "" if r == "global" else r


def _bus_token():
    # Resolved fresh per call so a bus token rotation is picked up without a restart.
    return os.environ.get("CLAUDE_BUS_TOKEN") or _read("/tmp/claude-bus/token")


def _http(method, path, body=None):
    req = urllib.request.Request(_derive_url() + path, method=method)
    req.add_header("Content-Type", "application/json")
    # Cloudflare 403s the default "Python-urllib" User-Agent; any real UA passes.
    req.add_header("User-Agent", "TeamCollab-MCP/1.0")
    token = _bus_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode(errors='replace')}"}
    except urllib.error.URLError as e:
        return {"error": str(e)}


TOOLS = [
    {
        "name": "ticket_set_tasks",
        "description": "Publish your task list to the Ticket dashboard so every agent on "
                       "this project can see your progress. Replaces your previous list.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "tasks": {
                    "type": "array",
                    "description": "Your task list.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "description": "Task description"},
                            "status": {"type": "string", "enum": ["todo", "doing", "done"],
                                       "description": "Task status (default 'todo')"},
                        },
                        "required": ["text"],
                    },
                },
            },
            "required": ["tasks"],
        },
    },
    {
        "name": "ticket_list",
        "description": "Fetch the shared ticket and every agent's task list for this project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ticket_set_ticket",
        "description": "Set/replace the shared ticket (project context) for this project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "body": {"type": "string", "description": "Ticket body / shared context"},
            },
            "required": ["body"],
        },
    },
    {
        "name": "ticket_get_ticket",
        "description": "Fetch just the shared ticket (project context) for this project.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "ticket_card_create",
        "description": "Create a card on the shared Jira-like board (starts in To Do). "
                       "Both agents and humans share this board.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Card title"},
                "body": {"type": "string", "description": "Optional details"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "ticket_card_move",
        "description": "Move a board card to 'todo' or 'doing'. Note: only a HUMAN can move a "
                       "card to 'done' — agents cannot (the server rejects it).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "description": "Card id (from ticket_list -> cards)"},
                "status": {"type": "string", "enum": ["todo", "doing"],
                           "description": "New status (agents may not set 'done')"},
            },
            "required": ["id", "status"],
        },
    },
    {
        "name": "my_tasks",
        "description": "List the ticket-board cards assigned to YOU (this agent). Check this at the "
                       "start of your turn and work your assigned queue. Returns cards with id, "
                       "title, body, status — move them with ticket_card_move as you work.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "signal",
        "description": "Post a HIGH-SIGNAL update to the team Activity feed — the kind of thing a "
                       "teammate glancing at the dashboard would want to know, phrased concretely "
                       "(name the file, the number, the specific decision). Emit as you work: a "
                       "milestone, a decision or assumption you made, a risk you spotted, or a "
                       "blocker/question. Not a task list — one crisp sentence. Examples: "
                       "\"wired the broadcast layer in canvas/cursors.ts — 47 lines so far\"; "
                       "\"assuming cookies (not server actions) for the session refactor\"; "
                       "\"flagged 3 LCP regressions on /pricing\".",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The update, one concrete sentence"},
                "kind": {"type": "string",
                         "enum": ["progress", "decision", "assumption", "blocker", "question", "risk"],
                         "description": "progress|decision|assumption|blocker|question|risk"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "ask_human",
        "description": "Ask a human a yes/no or multiple-choice question and BLOCK until they "
                       "answer from the Activity page. Use this when you hit a decision only a "
                       "human should make (destructive action, ambiguous requirement, which "
                       "approach). Returns {\"answer\": \"<chosen option>\"}. Times out after a "
                       "while and returns {\"status\": \"timeout\"} — treat that as no decision.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "The question to ask the human"},
                "options": {"type": "array", "items": {"type": "string"},
                            "description": "Choices (default [\"yes\",\"no\"])"},
            },
            "required": ["question"],
        },
    },
]


def call_tool(name, args):
    room = _room()      # resolved fresh: room/agent can change between calls
    if not room:
        return {"error": "This repo is not attached to a project (no room). There is "
                "no global ticket board — run the join-bus command for your project "
                "in this repo, then restart Claude."}
    agent = _agent_name()
    if name == "ticket_set_tasks":
        return _http("POST", f"/api/ticket/{room}/tasks",
                     {"agent": agent, "tasks": args.get("tasks", [])})
    if name == "ticket_list":
        return _http("GET", f"/api/ticket/{room}")
    if name == "ticket_set_ticket":
        return _http("POST", f"/api/ticket/{room}/ticket",
                     {"agent": agent, "body": args.get("body", "")})
    if name == "ticket_get_ticket":
        res = _http("GET", f"/api/ticket/{room}")
        if isinstance(res, dict) and "error" in res:
            return res
        return {"ticket": (res or {}).get("ticket")}
    if name == "ticket_card_create":
        return _http("POST", f"/api/ticket/{room}/cards",
                     {"agent": agent, "title": args.get("title", ""), "body": args.get("body", "")})
    if name == "ticket_card_move":
        return _http("PATCH", f"/api/ticket/{room}/cards/{args.get('id')}",
                     {"agent": agent, "status": args.get("status")})
    if name == "my_tasks":
        res = _http("GET", f"/api/ticket/{room}")
        if isinstance(res, dict) and "error" in res:
            return res
        cards = (res or {}).get("cards", [])
        mine = [c for c in cards if c.get("assigned_to") == agent and c.get("status") != "done"]
        return {"agent": agent, "tasks": mine}
    if name == "signal":
        return _http("POST", f"/api/ticket/{room}/signals",
                     {"agent": agent, "kind": args.get("kind") or "note", "text": args.get("text", "")})
    if name == "ask_human":
        q = (args.get("question") or "").strip()
        if not q:
            return {"error": "question is required"}
        opts = [str(o) for o in (args.get("options") or [])] or ["yes", "no"]
        created = _http("POST", f"/api/ticket/{room}/decisions",
                        {"agent": agent, "question": q, "options": opts})
        if not isinstance(created, dict) or "id" not in created:
            return created if isinstance(created, dict) else {"error": "could not create decision"}
        did = created["id"]
        # Block-poll until a human answers on the Activity page (or we time out).
        deadline = time.time() + ASK_HUMAN_TIMEOUT
        while time.time() < deadline:
            time.sleep(ASK_HUMAN_POLL)
            res = _http("GET", f"/api/ticket/{room}/decisions/{did}")
            if isinstance(res, dict) and res.get("status") == "answered":
                return {"answer": res.get("answer")}
        return {"status": "timeout",
                "detail": "No human answered in time; proceed cautiously or ask again."}
    return {"error": f"unknown tool {name}"}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        mid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            resp = {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ticket", "version": "1.0.0"}}
        elif method == "tools/list":
            resp = {"tools": TOOLS}
        elif method == "tools/call":
            params = msg.get("params", {})
            result = call_tool(params.get("name"), params.get("arguments") or {})
            resp = {"content": [{"type": "text", "text": json.dumps(result)}]}
        elif method in ("notifications/initialized", "notifications/cancelled"):
            continue  # notifications need no reply
        elif mid is None:
            continue
        else:
            sys.stdout.write(json.dumps(
                {"jsonrpc": "2.0", "id": mid,
                 "error": {"code": -32601, "message": f"method not found: {method}"}}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": resp}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

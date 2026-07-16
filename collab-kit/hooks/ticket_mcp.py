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
import socket
import urllib.parse
import urllib.request
import urllib.error


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

#!/usr/bin/env python3
"""Minimal MCP (stdio) server exposing the Claude chat bus as first-class tools.

No third-party deps — speaks MCP's JSON-RPC over stdin/stdout directly.
Configured via .mcp.json at the repo root; every Claude session in this project
gets: bus_send, bus_check, bus_who. Bus location/token resolve like the hooks:
env CLAUDE_BUS_URL / CLAUDE_BUS_TOKEN, then /tmp/claude-bus/{url,token} files,
then http://127.0.0.1:8899.

The agent's name comes from /tmp/claude-bus/names/<session_id>; callers pass
their name explicitly (the join hook tells each session its name).
"""

import os
import sys
import json
import urllib.request
import urllib.error


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


BUS_URL = os.environ.get("CLAUDE_BUS_URL") or _read("/tmp/claude-bus/url") or "http://127.0.0.1:8899"
BUS_URL = BUS_URL.rstrip("/")
BUS_TOKEN = os.environ.get("CLAUDE_BUS_TOKEN") or _read("/tmp/claude-bus/token")


def _room(args):
    """Room resolves like the hooks: explicit arg > env > file > 'global'."""
    return (args.get("room") or os.environ.get("CLAUDE_BUS_ROOM")
            or _read("/tmp/claude-bus/room") or "global")


def _http(method, path, body=None):
    req = urllib.request.Request(BUS_URL + path, method=method)
    req.add_header("Content-Type", "application/json")
    if BUS_TOKEN:
        req.add_header("Authorization", f"Bearer {BUS_TOKEN}")
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        return {"error": str(e)}


TOOLS = [
    {
        "name": "bus_send",
        "description": "Send a message on the Claude chat bus to another agent (or broadcast). "
                       "Show the user what you sent as 'BUS -> [recipient]: text'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "sender": {"type": "string", "description": "Your bus name (told to you at session start)"},
                "text": {"type": "string", "description": "Message text"},
                "to": {"type": ["string", "null"], "description": "Recipient name, or null to broadcast"},
                "image": {"type": ["string", "null"], "description": "Optional image as a data URL "
                          "('data:image/png;base64,...'), max 2MB. Shown to the recipient inline."},
                "room": {"type": ["string", "null"], "description": "Optional group to send to; "
                         "defaults to this session's joined group (CLAUDE_BUS_ROOM / /tmp/claude-bus/room)."},
            },
            "required": ["sender", "text"],
        },
    },
    {
        "name": "bus_check",
        "description": "Fetch any bus messages queued for you (non-blocking). "
                       "Show received messages to the user as 'BUS <- [sender]: text'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Your bus name"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "bus_who",
        "description": "List agents on the chat bus and their pending message counts.",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def call_tool(name, args):
    room = _room(args)
    if name == "bus_send":
        return _http("POST", "/send", {"sender": args["sender"], "text": args["text"],
                                       "to": args.get("to"), "image": args.get("image"),
                                       "room": room})
    if name == "bus_check":
        return _http("GET", f"/recv?name={args['name']}&timeout=0&room={room}")
    if name == "bus_who":
        return _http("GET", f"/who?room={room}")
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
                    "serverInfo": {"name": "claude-bus", "version": "1.0.0"}}
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

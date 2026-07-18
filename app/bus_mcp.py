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


# Resolved FRESH on every call (not cached at import) so a bus token rotation is
# picked up without restarting the MCP server / Claude.
def _bus_url():
    url = os.environ.get("CLAUDE_BUS_URL") or _read("/tmp/claude-bus/url") or "http://127.0.0.1:8899"
    return url.rstrip("/")


def _bus_token():
    return os.environ.get("CLAUDE_BUS_TOKEN") or _read("/tmp/claude-bus/token")


def _room(args):
    """Room resolves PER-REPO (not machine-global): explicit arg > env
    CLAUDE_BUS_ROOM > <repo>/.claude/bus-room > /tmp/claude-bus/room > 'global'.
    The MCP launches with cwd = the repo root, so cwd/.claude/bus-room is this
    repo's room even when another repo on the machine uses a different one."""
    base = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    room = (args.get("room") or os.environ.get("CLAUDE_BUS_ROOM")
            or _read(os.path.join(base, ".claude", "bus-room"))
            or _read("/tmp/claude-bus/room") or "")
    # No "global" pool: unset/global means this repo is not attached to a project.
    return "" if room == "global" else room


def _http(method, path, body=None):
    req = urllib.request.Request(_bus_url() + path, method=method)
    req.add_header("Content-Type", "application/json")
    # Cloudflare returns 403 to the default "Python-urllib" User-Agent, so the
    # bus behind the tunnel rejected every MCP call. Any real UA passes.
    req.add_header("User-Agent", "TeamCollab-MCP/1.0")
    token = _bus_token()
    if token:
        req.add_header("Authorization", f"Bearer {token}")
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
    {
        "name": "peers_touching",
        "description": "See which OTHER machines in your project currently have uncommitted "
                       "changes, and which files (with +/- line counts). Use this after a "
                       "merge-conflict warning to find whose diff to read.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": ["string", "null"], "description": "Optional: only peers touching this file"},
            },
        },
    },
    {
        "name": "peer_diff",
        "description": "Fetch another machine's ACTUAL uncommitted unified diff so you can "
                       "reconcile directly instead of asking on the bus. Omit file for all files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "machine": {"type": "string", "description": "The peer machine id (from peers_touching)"},
                "file": {"type": ["string", "null"], "description": "Optional: just this file's diff"},
            },
            "required": ["machine"],
        },
    },
]


def call_tool(name, args):
    room = _room(args)
    if not room:
        return {"error": "This repo is not attached to a project bus (no room). "
                "There is no global channel — run the join-bus command for your "
                "project in this repo, then restart Claude."}
    if name == "bus_send":
        return _http("POST", "/send", {"sender": args["sender"], "text": args["text"],
                                       "to": args.get("to"), "image": args.get("image"),
                                       "room": room})
    if name == "bus_check":
        return _http("GET", f"/recv?name={args['name']}&timeout=0&room={room}")
    if name == "bus_who":
        return _http("GET", f"/who?room={room}")
    if name == "peers_touching":
        import socket
        res = _http("GET", f"/diff/peers?project={room}&exclude={socket.gethostname()}")
        peers = res.get("peers", []) if isinstance(res, dict) else []
        f = args.get("file")
        if f:
            peers = [{**p, "files": [x for x in p["files"] if x["path"] == f]} for p in peers]
            peers = [p for p in peers if p["files"]]
        return {"peers": peers}
    if name == "peer_diff":
        q = f"/diff/peer?project={room}&machine={args.get('machine','')}"
        if args.get("file"):
            q += f"&file={args['file']}"
        return _http("GET", q)
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

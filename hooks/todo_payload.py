#!/usr/bin/env python3
"""Build the Ticket /tasks payload from a TodoWrite PostToolUse hook input.

Reads the hook JSON on stdin (has tool_input.todos = [{content,status,...}]),
maps Claude's todo statuses to the board's, and prints {"agent","tasks"} JSON.
Status map: pending->todo, in_progress->doing, completed->done.

Usage: todo_payload.py <agent-name>   < hook-input.json
"""
import json
import sys

_STATUS = {"pending": "todo", "in_progress": "doing", "completed": "done"}


def main(agent):
    try:
        data = json.load(sys.stdin)
    except Exception:
        data = {}
    todos = (data.get("tool_input") or {}).get("todos") or []
    tasks = []
    for t in todos:
        text = (t.get("content") or t.get("activeForm") or "").strip()
        if not text:
            continue
        tasks.append({"text": text, "status": _STATUS.get(t.get("status"), "todo")})
    print(json.dumps({"agent": agent, "tasks": tasks}))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "agent")

#!/usr/bin/env python3
"""AI signal distiller — Agent-FM style.

Takes a blob of a coding agent's RAW activity (tool calls, diffs, assistant
notes, commit messages…) and uses an LLM to decide, dynamically, what's worth
surfacing — inventing the category label itself rather than picking from a fixed
enum. Emits each as a signal to the Activity feed.

Uses the local `claude` CLI (the harness — no API key) run from a NEUTRAL dir so
project hooks/MCP don't hijack the headless call (same trick as the memory judge).
It's slow (~1-2 min), so run it in the background / on a cadence, never inline.

Usage:
    python3 -m app.distill_signals --room <invite_code> [--platform URL] [--token T]
                                   [--file activity.txt]   # else reads stdin

Resolution when flags omitted: env TICKET_URL / CLAUDE_BUS_TOKEN, then
/tmp/claude-bus/{url,token}, then localhost.
"""

import os
import re
import sys
import json
import shutil
import argparse
import tempfile
import subprocess
import urllib.parse
import urllib.request

PROMPT = """You are the signal distiller for a team dashboard (like Agent FM). Below is the \
RAW activity of a coding agent. Extract ONLY the updates a busy teammate glancing at the \
dashboard would want — a concrete milestone, a decision or assumption made, a risk or \
regression spotted, a blocker or a question needing a human. Ignore routine noise \
(reads, greps, thinking, boilerplate).

For each update output an object with:
  "category": a SHORT lowercase label YOU choose that best fits this specific update \
(you are not limited to a fixed list — pick the most informative word, e.g. progress, \
decision, assumption, blocker, question, risk, regression, refactor, test, security…),
  "severity": "high" if it needs a human's attention (blocker, question, risk, breakage) \
else "low",
  "text": ONE concrete sentence — name the file, the number, the specific decision.

Output ONLY a JSON array (possibly empty). No prose, no code fences.

RAW ACTIVITY:
"""


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def _platform_url(arg):
    if arg:
        return arg.rstrip("/")
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


def _extract_json_array(s):
    """Pull the first JSON array out of the model's reply (tolerates stray text/fences)."""
    s = s.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\n?|\n?```$", "", s).strip()
    i, j = s.find("["), s.rfind("]")
    if i == -1 or j == -1 or j < i:
        return []
    try:
        out = json.loads(s[i:j + 1])
        return out if isinstance(out, list) else []
    except json.JSONDecodeError:
        return []


def distill(activity):
    """Run the harness on the activity blob; return a list of signal dicts."""
    claude = shutil.which(os.environ.get("CLAUDE_BIN", "claude"))
    if not claude or not activity.strip():
        return []
    try:
        p = subprocess.run(
            [claude, "-p", PROMPT + activity[:12000], "--output-format", "json"],
            capture_output=True, text=True, timeout=300, cwd=tempfile.gettempdir())
        out = json.loads(p.stdout).get("result", "") if p.stdout else ""
    except Exception:
        return []
    return _extract_json_array(out)


def post_signal(platform, token, room, sig):
    body = {
        "agent": sig.get("agent") or "distiller",
        "kind": (sig.get("category") or sig.get("kind") or "update"),
        "severity": sig.get("severity"),
        "text": sig.get("text") or "",
    }
    if not body["text"].strip():
        return False
    req = urllib.request.Request(f"{platform}/api/ticket/{room}/signals",
                                 method="POST", data=json.dumps(body).encode())
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "TeamCollab-Distiller/1.0")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        urllib.request.urlopen(req, timeout=15)
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--room")
    ap.add_argument("--platform")
    ap.add_argument("--token")
    ap.add_argument("--file")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    room = args.room or os.environ.get("CLAUDE_BUS_ROOM") or _read("/tmp/claude-bus/room")
    if not room or room == "global":
        print("no room (project) resolved", file=sys.stderr)
        sys.exit(1)
    platform = _platform_url(args.platform)
    token = args.token or os.environ.get("CLAUDE_BUS_TOKEN") or _read("/tmp/claude-bus/token")

    activity = _read(args.file) if args.file else sys.stdin.read()
    signals = distill(activity)

    if args.dry_run:
        print(json.dumps(signals, indent=2))
        return
    sent = sum(post_signal(platform, token, room, s) for s in signals)
    print(f"[distill] emitted {sent}/{len(signals)} signals to room '{room}'")


if __name__ == "__main__":
    main()

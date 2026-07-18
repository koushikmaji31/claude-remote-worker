#!/usr/bin/env python3
"""Signal watcher — the capture layer that feeds the AI distiller.

Reads a Claude Code session transcript, strips it IN CODE (free) down to the
decision-bearing essentials (assistant text + one-line tool summaries; drops
thinking, tool-result bodies, file dumps, and read/grep noise), then hands the
compact slice to the AI distiller (`app.distill_signals`) which invents dynamic
categories and emits high-signal updates to the Activity feed.

Token-lean by design: pre-stripping is free, only *new* turns are processed
(offset tracked per transcript), and the LLM runs on a cadence — not per turn.

Usage:
    # one pass (e.g. from a Stop hook):
    python3 -m app.watch_signals --project-dir . --once
    # continuous, every 5 min:
    python3 -m app.watch_signals --project-dir . --interval 300

Room/platform/token resolve like the other tools (flags > env > /tmp/claude-bus/*).
"""

import os
import re
import sys
import json
import time
import argparse

from app.distill_signals import distill, post_signal, _platform_url, _read

STATE_DIR = os.path.join(os.path.expanduser("~"), ".claude-remote-worker")
STATE_FILE = os.path.join(STATE_DIR, "signal_offsets.json")

# Tools that don't carry signal on their own — don't let them trigger a distill.
_NOISE_TOOLS = {"Read", "Glob", "Grep", "LS", "TodoWrite", "NotebookRead", "WebFetch"}
_ACTION_TOOLS = {"Edit", "Write", "MultiEdit", "Bash", "NotebookEdit"}


def _mangle(path):
    """Claude Code stores transcripts under ~/.claude/projects/<mangled-abs-path>/."""
    return re.sub(r"[^a-zA-Z0-9]", "-", os.path.abspath(path))


def _transcript_dir(project_dir):
    return os.path.join(os.path.expanduser("~"), ".claude", "projects", _mangle(project_dir))


def _active_transcript(project_dir):
    d = _transcript_dir(project_dir)
    if not os.path.isdir(d):
        return None
    jsonls = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".jsonl")]
    if not jsonls:
        return None
    return max(jsonls, key=os.path.getmtime)   # most recently written session


def _load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {}


def _save_state(state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def _summarize_tool(name, ti):
    if name == "Bash":
        return (ti.get("command") or "")[:160]
    if name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return (ti.get("file_path") or ti.get("notebook_path") or "")[:160]
    return ""


def strip_lines(lines):
    """Turn raw transcript JSONL lines into a compact, signal-bearing blob.
    Returns (blob, has_action) — has_action gates whether it's worth distilling."""
    out, has_action = [], False
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if ev.get("type") not in ("assistant", "user"):
            continue
        msg = ev.get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if not isinstance(content, list):
            continue
        for b in content:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                txt = (b.get("text") or "").strip()
                if txt:
                    out.append(f"[{msg.get('role','?')}] {txt[:600]}")
            elif t == "tool_use":
                name = b.get("name", "")
                summ = _summarize_tool(name, b.get("input") or {})
                if name in _ACTION_TOOLS:
                    has_action = True
                    out.append(f"tool {name}: {summ}")
                # noise tools (Read/Grep/…) are skipped entirely
            # thinking, tool_result bodies, images → dropped (token sink, low signal)
    return "\n".join(out), has_action


def compute_metrics(lines):
    """Roll up token usage + tool calls/errors + last tool from a slice (free)."""
    ti = to = calls = errs = 0
    last_tool = ""
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        msg = ev.get("message") or {}
        if ev.get("type") == "assistant":
            u = msg.get("usage") or {}
            ti += int(u.get("input_tokens") or 0)
            to += int(u.get("output_tokens") or 0)
        content = msg.get("content")
        if isinstance(content, list):
            for b in content:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use":
                    calls += 1
                    last_tool = b.get("name") or last_tool
                elif b.get("type") == "tool_result" and b.get("is_error"):
                    errs += 1
    return {"tokens_in": ti, "tokens_out": to, "tool_calls": calls,
            "tool_errors": errs, "last_tool": last_tool}


def _has_metrics(m):
    return bool(m.get("tokens_in") or m.get("tokens_out") or m.get("tool_calls")
                or m.get("tool_errors") or m.get("last_tool"))


def _agent_name():
    n = os.environ.get("CLAUDE_BUS_NAME")
    if n:
        return n
    try:
        d = "/tmp/claude-bus/names"
        for fn in sorted(os.listdir(d)):
            v = _read(os.path.join(d, fn))
            if v:
                return v
    except OSError:
        pass
    return ""


def post_metrics(platform, token, room, agent, m):
    if not agent or not _has_metrics(m):
        return
    import urllib.request
    body = {"agent": agent, **m}
    req = urllib.request.Request(f"{platform}/api/ticket/{room}/metrics",
                                 method="POST", data=json.dumps(body).encode())
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "TeamCollab-Metrics/1.0")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        urllib.request.urlopen(req, timeout=15)
    except Exception:
        pass


def run_once(project_dir, room, platform, token, min_new=1):
    path = _active_transcript(project_dir)
    if not path:
        return "no transcript found"
    state = _load_state()
    start = int(state.get(path, 0))
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return "could not read transcript"
    new = lines[start:]
    if len(new) < min_new:
        return f"no new turns ({len(new)} new)"
    blob, has_action = strip_lines(new)
    # metrics roll-up is free (no LLM) — always post it for this agent
    post_metrics(platform, token, room, _agent_name(), compute_metrics(new))
    # advance the offset regardless, so we don't reprocess this slice
    state[path] = len(lines)
    _save_state(state)
    if not blob.strip() or not has_action:
        return f"skip: {len(new)} lines, metrics posted, nothing to distill"
    signals = distill(blob)
    sent = sum(post_signal(platform, token, room, s) for s in signals)
    return f"distilled {len(new)} new lines -> {sent} signal(s), metrics posted"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", default=os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd())
    ap.add_argument("--room")
    ap.add_argument("--platform")
    ap.add_argument("--token")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true", help="print the stripped blob, don't distill")
    args = ap.parse_args()

    room = args.room or os.environ.get("CLAUDE_BUS_ROOM") or _read("/tmp/claude-bus/room")
    if not room or room == "global":
        print("no room (project) resolved", file=sys.stderr)
        sys.exit(1)
    platform = _platform_url(args.platform)
    token = args.token or os.environ.get("CLAUDE_BUS_TOKEN") or _read("/tmp/claude-bus/token")

    if args.dry_run:
        path = _active_transcript(args.project_dir)
        if not path:
            print("no transcript found"); return
        state = _load_state()
        lines = open(path).readlines()[int(state.get(path, 0)):]
        blob, has_action = strip_lines(lines)
        print(f"# transcript: {path}\n# new lines: {len(lines)}  has_action: {has_action}\n")
        print(blob[:4000] or "(nothing signal-bearing)")
        return

    if args.once:
        print("[watch]", run_once(args.project_dir, room, platform, token))
        return
    while True:
        try:
            print("[watch]", run_once(args.project_dir, room, platform, token), flush=True)
        except Exception as e:
            print("[watch] error:", e, flush=True)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

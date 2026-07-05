"""
Claude Code remote worker — thin HTTP wrapper around the `claude` CLI (headless).

This is the piece you asked for: it lets YOUR server invoke Claude Code (with memory +
tools, not a stateless raw-API call) and get the answer back as JSON.

  Your server  --HTTP-->  this worker  --subprocess-->  `claude -p ... --output-format json`
                                                              |
                                                              v
                                              answer (+ session id) back to your server

Run it on your laptop (primary) AND on a cheap always-on VPS (fallback). Both point at the
same `context/` dir (CLAUDE.md + memory/), kept in git so they behave identically.

Quickstart:
    pip install fastapi uvicorn
    export WORKER_TOKEN="choose-a-long-random-secret"
    export CONTEXT_DIR="$HOME/Desktop/claude-remote-worker/context"
    uvicorn worker:app --host 0.0.0.0 --port 8787

Call it:
    curl -s localhost:8787/ask \
      -H "Authorization: Bearer $WORKER_TOKEN" \
      -H "Content-Type: application/json" \
      -d '{"prompt": "Summarize the git status of this repo and suggest next steps."}' | jq
"""

import os
import json
import shutil
import subprocess
from typing import Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# --- config (env-driven so laptop and VPS share the same code) ---
WORKER_TOKEN = os.environ.get("WORKER_TOKEN")                       # required: shared secret
CONTEXT_DIR  = os.environ.get("CONTEXT_DIR", os.getcwd())           # where CLAUDE.md + memory/ live
CLAUDE_BIN   = os.environ.get("CLAUDE_BIN", "claude")              # path to the claude CLI
DEFAULT_TIMEOUT = int(os.environ.get("CLAUDE_TIMEOUT", "300"))      # seconds; long tasks need headroom
# Optional: restrict what Claude may do in headless mode. Comma-separated tool names, or "" for default.
ALLOWED_TOOLS = os.environ.get("ALLOWED_TOOLS", "")
# Allow ALL permissions (edit/write/bash/etc.) with no prompts. ON by default.
# Set SKIP_PERMISSIONS=0 to make Claude ask/operate read-only instead.
SKIP_PERMISSIONS = os.environ.get("SKIP_PERMISSIONS", "1") not in ("0", "false", "False", "")

app = FastAPI(title="Claude Code Remote Worker")


class AskRequest(BaseModel):
    prompt: str
    # Pass a prior session_id to CONTINUE that conversation (memory across calls).
    session_id: Optional[str] = None
    # Override the working dir per request (e.g. point at a specific repo). Defaults to CONTEXT_DIR.
    cwd: Optional[str] = None
    timeout: Optional[int] = None


def _auth(authorization: Optional[str]):
    if not WORKER_TOKEN:
        raise HTTPException(500, "WORKER_TOKEN not set on the worker")
    expected = f"Bearer {WORKER_TOKEN}"
    if authorization != expected:
        raise HTTPException(401, "bad or missing Authorization header")


@app.get("/health")
def health():
    """Your server (or the queue dispatcher) hits this to decide if this worker is alive.
    This is how 'laptop when on, cloud when off' routing works: try the laptop's /health,
    fall back to the VPS if it doesn't answer."""
    return {"ok": True, "claude": shutil.which(CLAUDE_BIN) or "NOT FOUND", "context_dir": CONTEXT_DIR}


@app.post("/ask")
def ask(req: AskRequest, authorization: Optional[str] = Header(default=None)):
    # Auth disabled for now (no token required). Re-enable by uncommenting:
    # _auth(authorization)

    cmd = [CLAUDE_BIN, "-p", req.prompt, "--output-format", "json"]
    if req.session_id:
        cmd += ["--resume", req.session_id]   # continue prior conversation -> memory across calls
    if SKIP_PERMISSIONS:
        cmd += ["--dangerously-skip-permissions"]   # allow edit/write/bash/etc. with no prompts
    if ALLOWED_TOOLS:
        cmd += ["--allowed-tools", ALLOWED_TOOLS]

    workdir = req.cwd or CONTEXT_DIR
    try:
        proc = subprocess.run(
            cmd,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=req.timeout or DEFAULT_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, "claude timed out")

    if proc.returncode != 0:
        raise HTTPException(500, f"claude failed: {proc.stderr.strip()[:2000]}")

    # `--output-format json` prints a single JSON object: result text, session_id, cost, etc.
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        payload = {"result": proc.stdout}

    return {
        "result": payload.get("result", proc.stdout),
        "session_id": payload.get("session_id"),   # store this to continue the conversation later
        "raw": payload,
    }

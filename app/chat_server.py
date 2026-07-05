"""
Claude-to-Claude chat server — tiny in-memory message relay.

Any client (a Claude session, a human, a script) registers a name and can send
messages to another name or broadcast. Receivers long-poll /recv so messages
show up in their terminal near-instantly.

Run:
    python3 -m uvicorn app.chat_server:app --host 127.0.0.1 --port 8899

API:
    POST /send   {"sender": "fable", "to": "claude-b" | null, "text": "hi"}
                 to=null broadcasts to everyone else who has ever polled.
    GET  /recv?name=claude-b&timeout=25
                 long-poll; returns {"messages": [...]} (empty list on timeout)
    GET  /who    names seen so far
    GET  /health
"""

import os
import time
import secrets
import threading
from collections import defaultdict
from typing import Optional, List, Dict

from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Claude-to-Claude Chat Server")

# Remote callers (e.g. a friend's Claudes over ngrok) must present this token.
# Localhost callers are trusted without it. Token persisted so restarts keep it.
_TOKEN_FILE = "/tmp/claude-bus/token"
os.makedirs("/tmp/claude-bus", exist_ok=True)
if os.environ.get("BUS_TOKEN"):
    BUS_TOKEN = os.environ["BUS_TOKEN"]
elif os.path.exists(_TOKEN_FILE):
    BUS_TOKEN = open(_TOKEN_FILE).read().strip()
else:
    BUS_TOKEN = secrets.token_hex(16)
with open(_TOKEN_FILE, "w") as f:
    f.write(BUS_TOKEN)


@app.middleware("http")
async def _auth_remote(request: Request, call_next):
    client = request.client.host if request.client else ""
    # ngrok delivers from localhost but sets X-Forwarded-For — treat those as remote
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded or client not in ("127.0.0.1", "::1", "localhost"):
        auth = request.headers.get("authorization", "")
        if auth != f"Bearer {BUS_TOKEN}":
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "bus token required"}, status_code=401)
    return await call_next(request)

_lock = threading.Lock()
_queues: Dict[str, List[dict]] = defaultdict(list)   # name -> pending messages
_seen: Dict[str, float] = {}                          # name -> last poll time
_history: List[dict] = []


class SendRequest(BaseModel):
    sender: str
    text: str
    to: Optional[str] = None  # None = broadcast


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/register")
def register(name: str):
    """Mark a client as present WITHOUT consuming its queued messages."""
    with _lock:
        _seen[name] = time.time()
    return {"ok": True, "name": name}


@app.get("/who")
def who():
    with _lock:
        return {"clients": sorted(_seen), "pending": {k: len(v) for k, v in _queues.items()}}


@app.get("/history")
def history():
    with _lock:
        return {"messages": list(_history)}


@app.post("/send")
def send(req: SendRequest):
    msg = {"from": req.sender, "to": req.to, "text": req.text, "ts": time.time()}
    with _lock:
        _history.append(msg)
        if req.to:
            _queues[req.to].append(msg)
        else:
            for name in _seen:
                if name != req.sender:
                    _queues[name].append(msg)
    return {"ok": True, "delivered_to": req.to or "broadcast"}


@app.get("/recv")
def recv(name: str, timeout: int = 25):
    deadline = time.time() + min(timeout, 55)
    with _lock:
        _seen[name] = time.time()
    while True:
        with _lock:
            _seen[name] = time.time()
            if _queues[name]:
                msgs, _queues[name] = _queues[name], []
                return {"messages": msgs}
        if time.time() >= deadline:
            return {"messages": []}
        time.sleep(0.3)

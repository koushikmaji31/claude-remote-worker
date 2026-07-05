"""
Claude-to-Claude chat server — SQLite-backed message relay.

Any client (a Claude session, a human, a script) registers a name and can send
messages to another name or broadcast. Receivers long-poll /recv so messages
show up near-instantly. All state lives in SQLite, so restarting the server
never loses queued messages (each client has a delivery cursor).

Auth: localhost callers are trusted. Remote callers (anything with an
X-Forwarded-For header — e.g. via ngrok — or a non-local peer address) must
send `Authorization: Bearer <token>`; the token persists in /tmp/claude-bus/token.

Run:
    python3 -m uvicorn app.chat_server:app --host 127.0.0.1 --port 8899

API:
    POST /register?name=X          mark presence; new clients start at the log tail
    POST /send   {"sender": "fable", "to": "agent-b" | null, "text": "hi"}
                 to=null broadcasts to every registered client except the sender.
    GET  /recv?name=X&timeout=25   long-poll; {"messages":[...]} (empty on timeout)
    GET  /who                      registered names
    GET  /history                  full message log
    GET  /health
"""

import os
import time
import secrets
import sqlite3
import threading
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Claude-to-Claude Chat Server")

_DIR = "/tmp/claude-bus"
os.makedirs(_DIR, exist_ok=True)
_DB = os.path.join(_DIR, "bus.db")
_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(_DB)
    c.row_factory = sqlite3.Row
    return c


with _conn() as _c:
    _c.executescript("""
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender TEXT NOT NULL,
            recipient TEXT,            -- NULL = broadcast
            text TEXT NOT NULL,
            ts REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS clients(
            name TEXT PRIMARY KEY,
            cursor INTEGER NOT NULL DEFAULT 0,   -- last delivered message id
            last_seen REAL NOT NULL
        );
    """)

# --- auth token (persists across restarts) ---
_TOKEN_FILE = os.path.join(_DIR, "token")
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
            return JSONResponse({"detail": "bus token required"}, status_code=401)
    return await call_next(request)


class SendRequest(BaseModel):
    sender: str
    text: str
    to: Optional[str] = None  # None = broadcast


def _ensure_client(c, name: str):
    """Register presence. New clients start at the current log tail so they
    don't replay history that predates them."""
    row = c.execute("SELECT name FROM clients WHERE name=?", (name,)).fetchone()
    if row:
        c.execute("UPDATE clients SET last_seen=? WHERE name=?", (time.time(), name))
    else:
        tail = c.execute("SELECT COALESCE(MAX(id),0) m FROM messages").fetchone()["m"]
        c.execute("INSERT INTO clients(name, cursor, last_seen) VALUES(?,?,?)",
                  (name, tail, time.time()))


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/register")
def register(name: str):
    with _lock, _conn() as c:
        _ensure_client(c, name)
    return {"ok": True, "name": name}


STALE_AFTER = 90     # seconds without a poll before an agent is flagged deaf (team norm)
PRUNE_AFTER = 1800   # seconds of silence before assuming the session is closed and unregistering it


def _stale_watch():
    """Broadcast when an agent goes deaf (no poll in STALE_AFTER seconds).
    One alert per deaf episode; recovery clears it."""
    alerted = set()
    while True:
        time.sleep(30)
        try:
            now = time.time()
            with _lock, _conn() as c:
                rows = c.execute("SELECT name, last_seen FROM clients").fetchall()
                for r in rows:
                    age = now - r["last_seen"]
                    if age > PRUNE_AFTER:
                        c.execute("DELETE FROM clients WHERE name=?", (r["name"],))
                        alerted.discard(r["name"])
                        continue
                    if age > STALE_AFTER and r["name"] not in alerted:
                        alerted.add(r["name"])
                        c.execute(
                            "INSERT INTO messages(sender, recipient, text, ts) VALUES(?,?,?,?)",
                            ("bus-server", None,
                             f"PRESENCE ALERT: [{r['name']}] appears deaf — no bus poll for {int(age)}s. "
                             f"Its queued messages will wait; someone with terminal access may need to nudge it.",
                             now))
                    elif age <= STALE_AFTER:
                        alerted.discard(r["name"])
        except Exception:
            pass


threading.Thread(target=_stale_watch, daemon=True).start()


@app.get("/who")
def who():
    now = time.time()
    with _lock, _conn() as c:
        rows = c.execute("SELECT name, cursor, last_seen FROM clients ORDER BY name").fetchall()
        pend, presence = {}, {}
        for r in rows:
            n = c.execute(
                "SELECT COUNT(*) n FROM messages WHERE id>? AND (recipient=? OR (recipient IS NULL AND sender!=?))",
                (r["cursor"], r["name"], r["name"])).fetchone()["n"]
            pend[r["name"]] = n
            age = int(now - r["last_seen"])
            presence[r["name"]] = {"last_seen_secs_ago": age, "stale": age > STALE_AFTER}
        return {"clients": [r["name"] for r in rows], "pending": pend, "presence": presence}


@app.get("/history")
def history():
    with _lock, _conn() as c:
        rows = c.execute("SELECT sender, recipient, text, ts FROM messages ORDER BY id").fetchall()
        return {"messages": [
            {"from": r["sender"], "to": r["recipient"], "text": r["text"], "ts": r["ts"]}
            for r in rows]}


@app.post("/send")
def send(req: SendRequest):
    with _lock, _conn() as c:
        c.execute("INSERT INTO messages(sender, recipient, text, ts) VALUES(?,?,?,?)",
                  (req.sender, req.to, req.text, time.time()))
    return {"ok": True, "delivered_to": req.to or "broadcast"}


@app.get("/recv")
def recv(name: str, timeout: int = 25):
    deadline = time.time() + min(timeout, 55)
    with _lock, _conn() as c:
        _ensure_client(c, name)
    while True:
        with _lock, _conn() as c:
            cur = c.execute("SELECT cursor FROM clients WHERE name=?", (name,)).fetchone()["cursor"]
            rows = c.execute(
                "SELECT id, sender, recipient, text, ts FROM messages "
                "WHERE id>? AND (recipient=? OR (recipient IS NULL AND sender!=?)) ORDER BY id",
                (cur, name, name)).fetchall()
            if rows:
                c.execute("UPDATE clients SET cursor=?, last_seen=? WHERE name=?",
                          (rows[-1]["id"], time.time(), name))
                return {"messages": [
                    {"from": r["sender"], "to": r["recipient"], "text": r["text"], "ts": r["ts"]}
                    for r in rows]}
            c.execute("UPDATE clients SET last_seen=? WHERE name=?", (time.time(), name))
        if time.time() >= deadline:
            return {"messages": []}
        time.sleep(0.3)

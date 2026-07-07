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

from fastapi import FastAPI, Request, HTTPException
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
            image TEXT,                -- optional data URL ("data:image/png;base64,...")
            room TEXT NOT NULL DEFAULT 'global',  -- project group (invite code); 'global' = shared bus
            ts REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS clients(
            name TEXT PRIMARY KEY,
            cursor INTEGER NOT NULL DEFAULT 0,   -- last delivered message id
            room TEXT NOT NULL DEFAULT 'global', -- the group this session is joined to
            last_seen REAL NOT NULL
        );
    """)
    # Safe migrations for DBs created before newer columns existed.
    _mcols = {r["name"] for r in _c.execute("PRAGMA table_info(messages)").fetchall()}
    if "image" not in _mcols:
        _c.execute("ALTER TABLE messages ADD COLUMN image TEXT")
    if "room" not in _mcols:
        _c.execute("ALTER TABLE messages ADD COLUMN room TEXT NOT NULL DEFAULT 'global'")
    _ccols = {r["name"] for r in _c.execute("PRAGMA table_info(clients)").fetchall()}
    if "room" not in _ccols:
        _c.execute("ALTER TABLE clients ADD COLUMN room TEXT NOT NULL DEFAULT 'global'")

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
    text: str = ""
    to: Optional[str] = None  # None = broadcast
    image: Optional[str] = None  # optional data URL ("data:image/...;base64,...")
    room: str = "global"  # project group (invite code); 'global' = shared bus


def _ensure_client(c, name: str, room: str = "global"):
    """Register presence and keep the session's room current. New clients start
    at the current log tail so they don't replay history that predates them."""
    row = c.execute("SELECT name FROM clients WHERE name=?", (name,)).fetchone()
    if row:
        c.execute("UPDATE clients SET last_seen=?, room=? WHERE name=?",
                  (time.time(), room, name))
    else:
        tail = c.execute("SELECT COALESCE(MAX(id),0) m FROM messages").fetchone()["m"]
        c.execute("INSERT INTO clients(name, cursor, room, last_seen) VALUES(?,?,?,?)",
                  (name, tail, room, time.time()))


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/register")
def register(name: str, room: str = "global"):
    with _lock, _conn() as c:
        _ensure_client(c, name, room)
    return {"ok": True, "name": name, "room": room}


@app.post("/unregister")
def unregister(name: str):
    """Clean goodbye: a session's SessionEnd hook calls this so closed
    terminals leave the roster immediately instead of waiting for prune."""
    with _lock, _conn() as c:
        c.execute("DELETE FROM clients WHERE name=?", (name,))
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
                rows = c.execute("SELECT name, room, last_seen FROM clients").fetchall()
                for r in rows:
                    age = now - r["last_seen"]
                    if age > PRUNE_AFTER:
                        c.execute("DELETE FROM clients WHERE name=?", (r["name"],))
                        alerted.discard(r["name"])
                        continue
                    if age > STALE_AFTER and r["name"] not in alerted:
                        alerted.add(r["name"])
                        c.execute(
                            "INSERT INTO messages(sender, recipient, text, room, ts) VALUES(?,?,?,?,?)",
                            ("bus-server", None,
                             f"PRESENCE ALERT: [{r['name']}] appears deaf — no bus poll for {int(age)}s. "
                             f"Its queued messages will wait; someone with terminal access may need to nudge it.",
                             r["room"], now))
                    elif age <= STALE_AFTER:
                        alerted.discard(r["name"])
        except Exception:
            pass


threading.Thread(target=_stale_watch, daemon=True).start()


@app.get("/who")
def who(room: str = "global"):
    """List sessions in a room. room='*' returns every session across all rooms
    (used for globally-unique name allocation when a new session joins)."""
    now = time.time()
    all_rooms = room == "*"
    with _lock, _conn() as c:
        if all_rooms:
            rows = c.execute("SELECT name, cursor, room, last_seen FROM clients ORDER BY name").fetchall()
        else:
            rows = c.execute("SELECT name, cursor, room, last_seen FROM clients WHERE room=? ORDER BY name",
                             (room,)).fetchall()
        pend, presence = {}, {}
        for r in rows:
            n = c.execute(
                "SELECT COUNT(*) n FROM messages WHERE id>? AND room=? "
                "AND (recipient=? OR (recipient IS NULL AND sender!=?))",
                (r["cursor"], r["room"], r["name"], r["name"])).fetchone()["n"]
            pend[r["name"]] = n
            age = int(now - r["last_seen"])
            presence[r["name"]] = {"last_seen_secs_ago": age, "stale": age > STALE_AFTER}
        return {"clients": [r["name"] for r in rows], "pending": pend, "presence": presence}


@app.get("/history")
def history(room: str = "global"):
    with _lock, _conn() as c:
        rows = c.execute("SELECT sender, recipient, text, image, ts FROM messages "
                         "WHERE room=? ORDER BY id", (room,)).fetchall()
        return {"messages": [
            {"from": r["sender"], "to": r["recipient"], "text": r["text"], "image": r["image"], "ts": r["ts"]}
            for r in rows]}


MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2MB cap on image data-URL payloads


@app.post("/send")
def send(req: SendRequest):
    if req.image and len(req.image) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 2MB limit")
    with _lock, _conn() as c:
        c.execute("INSERT INTO messages(sender, recipient, text, image, room, ts) VALUES(?,?,?,?,?,?)",
                  (req.sender, req.to, req.text, req.image, req.room, time.time()))
    return {"ok": True, "delivered_to": req.to or "broadcast", "room": req.room}


@app.get("/recv")
def recv(name: str, timeout: int = 25, room: str = "global"):
    deadline = time.time() + min(timeout, 55)
    with _lock, _conn() as c:
        _ensure_client(c, name, room)
    while True:
        with _lock, _conn() as c:
            row = c.execute("SELECT cursor, room FROM clients WHERE name=?", (name,)).fetchone()
            cur, croom = row["cursor"], row["room"]
            rows = c.execute(
                "SELECT id, sender, recipient, text, image, ts FROM messages "
                "WHERE id>? AND room=? AND (recipient=? OR (recipient IS NULL AND sender!=?)) ORDER BY id",
                (cur, croom, name, name)).fetchall()
            if rows:
                c.execute("UPDATE clients SET cursor=?, last_seen=? WHERE name=?",
                          (rows[-1]["id"], time.time(), name))
                return {"messages": [
                    {"from": r["sender"], "to": r["recipient"], "text": r["text"], "image": r["image"], "ts": r["ts"]}
                    for r in rows]}
            c.execute("UPDATE clients SET last_seen=? WHERE name=?", (time.time(), name))
        if time.time() >= deadline:
            return {"messages": []}
        time.sleep(0.3)

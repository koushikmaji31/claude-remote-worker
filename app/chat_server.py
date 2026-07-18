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
    room: str = ""  # project group (invite code); required — there is no shared/global bus


def _ensure_client(c, name: str, room: str = ""):
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


def _require_room(room: str, star_ok: bool = False):
    """Enforce strict project isolation: every request must name a specific
    project room. There is no 'global' pool and no cross-project channel."""
    if star_ok and room == "*":
        return
    if not room or room == "global":
        raise HTTPException(status_code=400,
                            detail="a specific project room is required (no global bus)")


@app.post("/register")
def register(name: str, room: str = ""):
    _require_room(room)
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
def who(room: str = ""):
    """List sessions in a room. room='*' returns every session across all rooms
    (used for globally-unique name allocation when a new session joins)."""
    _require_room(room, star_ok=True)
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
def history(room: str = ""):
    _require_room(room)
    with _lock, _conn() as c:
        rows = c.execute("SELECT sender, recipient, text, image, ts FROM messages "
                         "WHERE room=? ORDER BY id", (room,)).fetchall()
        return {"messages": [
            {"from": r["sender"], "to": r["recipient"], "text": r["text"], "image": r["image"], "ts": r["ts"]}
            for r in rows]}


MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2MB cap on image data-URL payloads


@app.post("/send")
def send(req: SendRequest):
    _require_room(req.room)
    if req.image and len(req.image) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds 2MB limit")
    with _lock, _conn() as c:
        c.execute("INSERT INTO messages(sender, recipient, text, image, room, ts) VALUES(?,?,?,?,?,?)",
                  (req.sender, req.to, req.text, req.image, req.room, time.time()))
    return {"ok": True, "delivered_to": req.to or "broadcast", "room": req.room}


@app.get("/recv")
def recv(name: str, timeout: int = 25, room: str = ""):
    _require_room(room)
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


# ============================================================
# Merge-conflict early-warning (folded into the bus so it rides the same tunnel
# + token auth). Each machine POSTs its pending footprint (touched line ranges
# per file vs the shared base) for its project; we return any OTHER machine in
# the same project whose ranges overlap — a merge conflict forming in real time.
# State is in-memory and keyed by (project, machine); a report replaces that
# machine's footprint. Project = the bus room (invite code); no 'global'/empty.
# ============================================================
_diff_state = {}          # _diff_state[project][machine] = {"base_sha", "files"}
_diff_lock = threading.Lock()


MAX_DIFF_BYTES = 256 * 1024  # per-report unified-diff cap (ticket #15)


class DiffReport(BaseModel):
    project: str = ""
    machine: str = ""              # stable host id
    agent: str = ""                # bus name for display (e.g. koushik_2)
    base_sha: Optional[str] = None
    files: dict = {}               # {path: [[start,end], ...]} touched line ranges
    diff: str = ""                 # full unified diff vs base (split per-file server-side)


class DiffClear(BaseModel):
    project: str = ""
    machine: str = ""


def _ranges_overlap(a, b):
    return a[0] <= b[1] and b[0] <= a[1]


def _split_diff(unified):
    """Split a full `git diff` into {path: {"diff","added","removed"}} per file."""
    out, path, buf = {}, None, []

    def flush():
        if path and buf:
            text = "".join(buf)
            added = sum(1 for l in text.splitlines() if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in text.splitlines() if l.startswith("-") and not l.startswith("---"))
            out[path] = {"diff": text, "added": added, "removed": removed}

    for line in (unified or "").splitlines(keepends=True):
        if line.startswith("diff --git "):
            flush(); buf = [line]
            # "diff --git a/x b/x" -> take the b/ path
            parts = line.split(" b/", 1)
            path = parts[1].strip() if len(parts) == 2 else None
        elif path is not None:
            buf.append(line)
    flush()
    return out


def _find_conflicts(project, reporter, files):
    out = []
    for machine, other in _diff_state.get(project, {}).items():
        if machine == reporter:
            continue
        ofiles = other.get("files", {})
        for path, mine in files.items():
            theirs = ofiles.get(path)
            if not theirs:
                continue
            for mr in mine:
                for tr in theirs:
                    if _ranges_overlap(mr, tr):
                        out.append({"machine": machine, "file": path,
                                    "your_lines": mr, "their_lines": tr,
                                    "their_base_sha": other.get("base_sha")})
    return out


@app.post("/diff/report")
def diff_report(req: DiffReport):
    _require_room(req.project)
    if not req.machine:
        raise HTTPException(status_code=400, detail="machine required")
    diff = req.diff or ""
    if len(diff) > MAX_DIFF_BYTES:
        diff = diff[:MAX_DIFF_BYTES] + "\n[... diff truncated at 256KB ...]\n"
    with _diff_lock:
        conflicts = _find_conflicts(req.project, req.machine, req.files or {})
        _diff_state.setdefault(req.project, {})[req.machine] = {
            "base_sha": req.base_sha, "files": req.files or {},
            "agent": req.agent or req.machine, "updated": time.time(),
            "perfile": _split_diff(diff)}
    return {"ok": True, "conflicts": conflicts}


@app.post("/diff/clear")
def diff_clear(req: DiffClear):
    _require_room(req.project)
    with _diff_lock:
        _diff_state.get(req.project, {}).pop(req.machine, None)
    return {"ok": True}


@app.get("/diff/state")
def diff_state(project: str = ""):
    _require_room(project)
    with _diff_lock:
        return {"project": project, "state": _diff_state.get(project, {})}


# --- Peer diff sharing (ticket #15): see teammates' actual diffs, not just ranges ---
@app.get("/diff/peers")
def diff_peers(project: str = "", exclude: str = ""):
    """Who is touching what, with per-file +/- counts. Excludes `exclude` (the
    caller's own machine) so an agent sees only OTHERS."""
    _require_room(project)
    peers = []
    with _diff_lock:
        for machine, e in _diff_state.get(project, {}).items():
            if machine == exclude:
                continue
            perfile = e.get("perfile") or {}
            files = [{"path": p, "added": d["added"], "removed": d["removed"]}
                     for p, d in sorted(perfile.items())]
            if not files:
                continue
            peers.append({"machine": machine, "agent": e.get("agent") or machine,
                          "files": files, "updated": e.get("updated")})
    peers.sort(key=lambda x: x["agent"])
    return {"peers": peers}


@app.get("/diff/peer")
def diff_peer(project: str = "", machine: str = "", file: str = ""):
    """One machine's actual unified diff — a single file, or all concatenated."""
    _require_room(project)
    with _diff_lock:
        e = _diff_state.get(project, {}).get(machine)
        if not e:
            raise HTTPException(status_code=404, detail="no diff for that machine")
        perfile = e.get("perfile") or {}
        agent = e.get("agent") or machine
        if file:
            d = perfile.get(file)
            if not d:
                raise HTTPException(status_code=404, detail="no diff for that file")
            return {"machine": machine, "agent": agent, "file": file, "diff": d["diff"]}
        alldiff = "".join(d["diff"] for _, d in sorted(perfile.items()))
        return {"machine": machine, "agent": agent, "file": None, "diff": alldiff}

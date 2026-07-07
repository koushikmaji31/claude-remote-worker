"""Team Collab Platform backend — see docs/API_CONTRACT.md (v1)."""
import os
import secrets
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Optional, Union

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH = Path(__file__).resolve().parent.parent / "platform.db"

app = FastAPI(title="Team Collab Platform")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"]
    + ([os.environ["PUBLIC_BASE_URL"].rstrip("/")] if os.environ.get("PUBLIC_BASE_URL") else []),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = db()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            token TEXT UNIQUE NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            admin_id INTEGER NOT NULL REFERENCES users(id),
            invite_code TEXT UNIQUE NOT NULL,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS members(
            project_id INTEGER REFERENCES projects(id),
            user_id INTEGER REFERENCES users(id),
            role TEXT CHECK(role IN ('admin','member')),
            PRIMARY KEY(project_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY,
            project_id INTEGER REFERENCES projects(id),
            sender TEXT NOT NULL,
            text TEXT NOT NULL,
            image TEXT,
            ts REAL NOT NULL
        );
        """
    )
    # Safe migration for DBs created before the image column existed.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "image" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN image TEXT")
    conn.commit()
    conn.close()


init_db()


def current_user(request: Request) -> sqlite3.Row:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = auth[7:].strip()
    conn = db()
    try:
        row = conn.execute("SELECT * FROM users WHERE token=?", (token,)).fetchone()
    finally:
        conn.close()
    if not row:
        raise HTTPException(401, "Invalid token")
    return row


def require_member(conn, pid: int, user_id: int) -> sqlite3.Row:
    proj = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    if not proj:
        raise HTTPException(404, "Project not found")
    mem = conn.execute(
        "SELECT * FROM members WHERE project_id=? AND user_id=?", (pid, user_id)
    ).fetchone()
    if not mem:
        raise HTTPException(403, "Not a member of this project")
    return proj


def invite_link(code: str) -> str:
    base = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8900").rstrip("/")
    return f"{base}/?join={code}"


# ---------- Auth ----------

class RegisterIn(BaseModel):
    name: str
    email: str


class LoginIn(BaseModel):
    email: str


@app.post("/api/register")
def register(body: RegisterIn):
    conn = db()
    try:
        token = secrets.token_hex(16)
        try:
            cur = conn.execute(
                "INSERT INTO users(name, email, token) VALUES(?,?,?)",
                (body.name, body.email, token),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "Email already registered")
        return {"user_id": cur.lastrowid, "name": body.name, "email": body.email, "token": token}
    finally:
        conn.close()


@app.post("/api/login")
def login(body: LoginIn):
    conn = db()
    try:
        row = conn.execute("SELECT * FROM users WHERE email=?", (body.email,)).fetchone()
        if not row:
            raise HTTPException(404, "Unknown email")
        return {"user_id": row["id"], "name": row["name"], "email": row["email"], "token": row["token"]}
    finally:
        conn.close()


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"user_id": user["id"], "name": user["name"], "email": user["email"]}


# ---------- Projects ----------

class ProjectIn(BaseModel):
    name: str


@app.post("/api/projects")
def create_project(body: ProjectIn, user=Depends(current_user)):
    conn = db()
    try:
        code = secrets.token_hex(8)
        cur = conn.execute(
            "INSERT INTO projects(name, admin_id, invite_code, created_at) VALUES(?,?,?,datetime('now'))",
            (body.name, user["id"], code),
        )
        pid = cur.lastrowid
        conn.execute(
            "INSERT INTO members(project_id, user_id, role) VALUES(?,?, 'admin')",
            (pid, user["id"]),
        )
        conn.commit()
        return {"project_id": pid, "name": body.name, "invite_code": code, "invite_link": invite_link(code)}
    finally:
        conn.close()


@app.get("/api/projects")
def list_projects(user=Depends(current_user)):
    conn = db()
    try:
        rows = conn.execute(
            """
            SELECT p.id, p.name, m.role,
                   (SELECT name FROM users WHERE id=p.admin_id) AS admin_name,
                   (SELECT COUNT(*) FROM members WHERE project_id=p.id) AS member_count
            FROM projects p JOIN members m ON m.project_id=p.id
            WHERE m.user_id=?
            ORDER BY p.id
            """,
            (user["id"],),
        ).fetchall()
        return {
            "projects": [
                {
                    "project_id": r["id"],
                    "name": r["name"],
                    "role": r["role"],
                    "admin_name": r["admin_name"],
                    "member_count": r["member_count"],
                }
                for r in rows
            ]
        }
    finally:
        conn.close()


@app.get("/api/projects/{pid}")
def get_project(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        proj = require_member(conn, pid, user["id"])
        members = conn.execute(
            """
            SELECT u.id, u.name, u.email, m.role
            FROM members m JOIN users u ON u.id=m.user_id
            WHERE m.project_id=? ORDER BY u.id
            """,
            (pid,),
        ).fetchall()
        return {
            "project_id": proj["id"],
            "name": proj["name"],
            "invite_code": proj["invite_code"],
            "invite_link": invite_link(proj["invite_code"]),
            "admin_id": proj["admin_id"],
            "members": [
                {"user_id": r["id"], "name": r["name"], "email": r["email"], "role": r["role"]}
                for r in members
            ],
        }
    finally:
        conn.close()


@app.get("/api/join/{invite_code}")
def join_preview(invite_code: str):
    conn = db()
    try:
        proj = conn.execute("SELECT * FROM projects WHERE invite_code=?", (invite_code,)).fetchone()
        if not proj:
            raise HTTPException(404, "Invalid invite code")
        admin = conn.execute("SELECT name FROM users WHERE id=?", (proj["admin_id"],)).fetchone()
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM members WHERE project_id=?", (proj["id"],)
        ).fetchone()["c"]
        return {
            "project_id": proj["id"],
            "name": proj["name"],
            "admin_name": admin["name"] if admin else None,
            "member_count": count,
        }
    finally:
        conn.close()


@app.post("/api/join/{invite_code}")
def join(invite_code: str, user=Depends(current_user)):
    conn = db()
    try:
        proj = conn.execute("SELECT * FROM projects WHERE invite_code=?", (invite_code,)).fetchone()
        if not proj:
            raise HTTPException(404, "Invalid invite code")
        conn.execute(
            "INSERT OR IGNORE INTO members(project_id, user_id, role) VALUES(?,?, 'member')",
            (proj["id"], user["id"]),
        )
        conn.commit()
        role = conn.execute(
            "SELECT role FROM members WHERE project_id=? AND user_id=?",
            (proj["id"], user["id"]),
        ).fetchone()["role"]
        return {"project_id": proj["id"], "name": proj["name"], "role": role}
    finally:
        conn.close()


def require_admin(conn, pid: int, user_id: int) -> sqlite3.Row:
    proj = require_member(conn, pid, user_id)
    if proj["admin_id"] != user_id:
        raise HTTPException(403, "Admin only")
    return proj


@app.delete("/api/projects/{pid}/members/{user_id}")
def remove_member(pid: int, user_id: int, user=Depends(current_user)):
    conn = db()
    try:
        require_admin(conn, pid, user["id"])
        if user_id == user["id"]:
            raise HTTPException(409, "Admin can't remove self")
        cur = conn.execute(
            "DELETE FROM members WHERE project_id=? AND user_id=?", (pid, user_id)
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Not a member")
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


class TransferIn(BaseModel):
    user_id: int


@app.post("/api/projects/{pid}/transfer-admin")
def transfer_admin(pid: int, body: TransferIn, user=Depends(current_user)):
    conn = db()
    try:
        require_admin(conn, pid, user["id"])
        target = conn.execute(
            "SELECT * FROM members WHERE project_id=? AND user_id=?", (pid, body.user_id)
        ).fetchone()
        if not target:
            raise HTTPException(404, "Target is not a member")
        conn.execute("UPDATE projects SET admin_id=? WHERE id=?", (body.user_id, pid))
        conn.execute(
            "UPDATE members SET role='member' WHERE project_id=? AND user_id=?", (pid, user["id"])
        )
        conn.execute(
            "UPDATE members SET role='admin' WHERE project_id=? AND user_id=?", (pid, body.user_id)
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- Messages ----------

class MessageIn(BaseModel):
    text: str = ""
    image: Optional[str] = None  # optional data URL ("data:image/...;base64,...")


@app.post("/api/projects/{pid}/messages")
def post_message(pid: int, body: MessageIn, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        if not (body.text or "").strip() and not body.image:
            raise HTTPException(400, "Message must have text or an image")
        if body.image and len(body.image) > 2 * 1024 * 1024:
            raise HTTPException(413, "Image exceeds 2MB limit")
        conn.execute(
            "INSERT INTO messages(project_id, sender, text, image, ts) VALUES(?,?,?,?,?)",
            (pid, user["name"], body.text, body.image, time.time()),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.get("/api/projects/{pid}/messages")
def get_messages(pid: int, since_id: int = 0, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        rows = conn.execute(
            "SELECT id, sender, text, image, ts FROM messages WHERE project_id=? AND id>? ORDER BY id",
            (pid, since_id),
        ).fetchall()
        return {"messages": [dict(r) for r in rows]}
    finally:
        conn.close()


# ---------- Agent RPC ----------

def _git(repo_path: str, *args: str) -> str:
    if not Path(repo_path).is_dir():
        raise ValueError(f"repo_path not a directory: {repo_path}")
    res = subprocess.run(
        ["git", "-C", repo_path, *args], capture_output=True, text=True, timeout=30
    )
    if res.returncode != 0:
        raise RuntimeError(res.stderr.strip() or f"git exited {res.returncode}")
    return res.stdout


def rpc_git_branches(params):
    out = _git(params["repo_path"], "branch", "--list")
    branches, current = [], None
    for line in out.splitlines():
        name = line[2:].strip()
        if line.startswith("* "):
            current = name
        branches.append(name)
    return {"branches": branches, "current": current}


def rpc_git_diff(params):
    return {"diff": _git(params["repo_path"], "diff", params["base"], params["head"])}


def rpc_git_conflicts(params):
    repo, base, head = params["repo_path"], params["base"], params["head"]
    res = subprocess.run(
        ["git", "-C", repo, "merge-tree", "--write-tree", "--name-only", base, head],
        capture_output=True, text=True, timeout=30,
    )
    lines = res.stdout.splitlines()
    # merge-tree output: OID line, then conflicted file names (exit code 1 on conflicts)
    conflicts = lines[1:] if res.returncode == 1 else []
    return {"conflicts": [f for f in conflicts if f]}


RPC_METHODS = {
    "git.branches": (rpc_git_branches, {"repo_path"}),
    "git.diff": (rpc_git_diff, {"repo_path", "base", "head"}),
    "git.conflicts": (rpc_git_conflicts, {"repo_path", "base", "head"}),
}


class RpcIn(BaseModel):
    method: str
    params: dict = {}
    id: Union[int, str, None] = None


@app.post("/rpc")
def rpc(body: RpcIn, user=Depends(current_user)):
    entry = RPC_METHODS.get(body.method)
    if not entry:
        return {"error": {"code": -32601, "message": f"Unknown method: {body.method}"}, "id": body.id}
    fn, required = entry
    if not required.issubset(body.params):
        return {
            "error": {"code": -32602, "message": f"Missing params: {sorted(required - set(body.params))}"},
            "id": body.id,
        }
    try:
        return {"result": fn(body.params), "id": body.id}
    except ValueError as e:
        return {"error": {"code": -32602, "message": str(e)}, "id": body.id}
    except Exception as e:
        return {"error": {"code": -32000, "message": str(e)}, "id": body.id}


# --- Serve the built frontend (single-origin: API + SPA on one port) ---
# Registered LAST so all /api and /rpc routes above take precedence.
_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if (_DIST / "index.html").exists():
    _ASSETS = _DIST / "assets"
    if _ASSETS.is_dir():
        app.mount("/assets", StaticFiles(directory=_ASSETS), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        # Never shadow the API surface.
        if full_path.startswith(("api", "rpc")) or full_path in ("health",):
            raise HTTPException(404, "Not found")
        candidate = _DIST / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        # Client-side routes (e.g. /project/5) fall back to the SPA shell.
        return FileResponse(_DIST / "index.html")

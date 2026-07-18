"""Team Collab Platform backend — see docs/API_CONTRACT.md (v1)."""
import json
import os
import secrets
import sqlite3
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB_PATH = Path(__file__).resolve().parent.parent / "platform.db"

# ---------- Secret sealing for stored GitHub tokens ----------
# Tokens are encrypted at rest with Fernet when TOKEN_ENCRYPTION_KEY is set.
# Without a key we fall back to plaintext (dev only) and flag it via /api/github/status
# so the UI can warn. Generate a key: python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
_ENC_KEY = os.environ.get("TOKEN_ENCRYPTION_KEY", "").strip()
try:
    from cryptography.fernet import Fernet, InvalidToken

    _FERNET = Fernet(_ENC_KEY.encode()) if _ENC_KEY else None
except Exception:  # cryptography missing or bad key -> degrade to plaintext
    _FERNET, InvalidToken = None, Exception

TOKENS_ENCRYPTED = _FERNET is not None


def _seal(secret: str) -> str:
    """Encrypt a secret for storage. Prefix marks the scheme so _unseal is unambiguous."""
    if _FERNET:
        return "enc:" + _FERNET.encrypt(secret.encode()).decode()
    return "plain:" + secret


def _unseal(stored: str) -> str:
    if stored.startswith("enc:"):
        if not _FERNET:
            raise RuntimeError("Token was encrypted but TOKEN_ENCRYPTION_KEY is not set")
        return _FERNET.decrypt(stored[4:].encode()).decode()
    if stored.startswith("plain:"):
        return stored[6:]
    return stored  # legacy/unprefixed


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
        -- GitHub identity: one linked GitHub account per platform user (Phase 1, PAT-based).
        -- token_enc is sealed at rest (see _seal/_unseal). auth_kind future-proofs OAuth/App.
        CREATE TABLE IF NOT EXISTS gh_identities(
            user_id INTEGER PRIMARY KEY REFERENCES users(id),
            gh_login TEXT NOT NULL,
            gh_id INTEGER,
            token_enc TEXT NOT NULL,
            auth_kind TEXT NOT NULL DEFAULT 'pat',
            scopes TEXT,
            connected_at REAL NOT NULL
        );
        -- Repo link: map a project to a GitHub repo. One repo per project (Phase 1).
        CREATE TABLE IF NOT EXISTS repo_links(
            project_id INTEGER PRIMARY KEY REFERENCES projects(id),
            owner TEXT NOT NULL,
            repo TEXT NOT NULL,
            default_branch TEXT,
            linked_by INTEGER REFERENCES users(id),
            linked_at REAL NOT NULL
        );
        -- Pending GitHub OAuth handshakes (Phase 3). Single-use, short-lived:
        -- the browser leaves for github.com without our bearer token, so the
        -- state row is how the callback finds the platform user again.
        CREATE TABLE IF NOT EXISTS gh_oauth_states(
            state TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            return_to TEXT,
            created_at REAL NOT NULL
        );
        -- Ticket: one pasted ticket (shared context) per project, plus each
        -- agent's live task list (tasks = JSON array of {"text","status"}).
        CREATE TABLE IF NOT EXISTS tickets(
            project_id INTEGER PRIMARY KEY REFERENCES projects(id),
            body TEXT,
            set_by TEXT,
            ts REAL
        );
        CREATE TABLE IF NOT EXISTS agent_tasks(
            project_id INTEGER REFERENCES projects(id),
            agent TEXT,
            tasks TEXT,
            ts REAL,
            PRIMARY KEY(project_id, agent)
        );
        -- Jira-like ticket cards: a shared board of work items with a status
        -- workflow (todo|doing|done). Humans and agents both create/move cards,
        -- but only a human (member-auth) may set status='done'.
        CREATE TABLE IF NOT EXISTS ticket_cards(
            id INTEGER PRIMARY KEY,
            project_id INTEGER REFERENCES projects(id),
            title TEXT NOT NULL,
            body TEXT,
            status TEXT NOT NULL DEFAULT 'todo',
            created_by TEXT,
            updated_by TEXT,
            created_at REAL,
            updated_at REAL
        );
        -- Jira integration (Phase 1): per-user Atlassian identity + per-project
        -- link to a Jira Cloud project. token_enc is sealed at rest (_seal/_unseal).
        CREATE TABLE IF NOT EXISTS jira_identities(
            user_id INTEGER PRIMARY KEY REFERENCES users(id),
            site TEXT,            -- e.g. yoursite.atlassian.net
            cloud_id TEXT,        -- Atlassian cloud id (used on the OAuth path)
            account_id TEXT,
            email TEXT,
            display_name TEXT,
            token_enc TEXT NOT NULL,
            auth_kind TEXT NOT NULL DEFAULT 'token',  -- 'token' (API token) | 'oauth'
            connected_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jira_links(
            project_id INTEGER PRIMARY KEY REFERENCES projects(id),
            site TEXT NOT NULL,
            cloud_id TEXT,
            project_key TEXT NOT NULL,
            project_name TEXT,
            linked_by INTEGER REFERENCES users(id),
            linked_at REAL NOT NULL
        );
        -- Human-in-the-loop decisions: an agent asks a yes/no or multiple-choice
        -- question and blocks; a human answers from the Activity page; the answer
        -- flows back to unblock the agent. status: pending|answered.
        CREATE TABLE IF NOT EXISTS decisions(
            id INTEGER PRIMARY KEY,
            project_id INTEGER REFERENCES projects(id),
            agent TEXT,
            question TEXT NOT NULL,
            options TEXT NOT NULL,      -- JSON array of option strings
            status TEXT NOT NULL DEFAULT 'pending',
            answer TEXT,
            answered_by TEXT,
            created_at REAL,
            answered_at REAL
        );
        -- Agent roster: user-created/named logical agents (assignees). A live
        -- Claude terminal binds to one by joining the bus with that name.
        CREATE TABLE IF NOT EXISTS agents(
            id INTEGER PRIMARY KEY,
            project_id INTEGER REFERENCES projects(id),
            name TEXT NOT NULL,
            role TEXT,
            created_at REAL,
            UNIQUE(project_id, name)
        );
        -- Per-agent fleet metrics (item 4): rolled up from the transcript by the
        -- watcher. One row per agent; upserted.
        CREATE TABLE IF NOT EXISTS agent_metrics(
            project_id INTEGER REFERENCES projects(id),
            agent TEXT,
            tokens_in INTEGER DEFAULT 0,
            tokens_out INTEGER DEFAULT 0,
            tool_calls INTEGER DEFAULT 0,
            tool_errors INTEGER DEFAULT 0,
            updated_at REAL,
            PRIMARY KEY(project_id, agent)
        );
        -- Signals: high-signal updates for the Activity feed (Agent-FM style).
        -- `kind` is a DYNAMIC, AI-assigned category label (free text, not an enum);
        -- `severity` (high|low) is set by the distiller / emitter.
        CREATE TABLE IF NOT EXISTS signals(
            id INTEGER PRIMARY KEY,
            project_id INTEGER REFERENCES projects(id),
            agent TEXT,
            kind TEXT NOT NULL DEFAULT 'update',
            severity TEXT NOT NULL DEFAULT 'low',
            text TEXT NOT NULL,
            ts REAL
        );
        """
    )
    # Safe migration for DBs created before the image column existed.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(messages)").fetchall()}
    if "image" not in cols:
        conn.execute("ALTER TABLE messages ADD COLUMN image TEXT")
    # can_manage: per-member capability to connect/manage integrations
    # (GitHub/Jira/GitLab). The project admin always can; others only if the
    # admin grants it. Everyone else sees integrations read-only.
    mcols = {r["name"] for r in conn.execute("PRAGMA table_info(members)").fetchall()}
    if "can_manage" not in mcols:
        conn.execute("ALTER TABLE members ADD COLUMN can_manage INTEGER NOT NULL DEFAULT 0")
    # provider: which git host an identity / repo-link / oauth-handshake belongs to.
    # Existing rows predate GitLab support, so they default to 'github'.
    for table in ("gh_identities", "repo_links", "gh_oauth_states"):
        tcols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if "provider" not in tcols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN provider TEXT NOT NULL DEFAULT 'github'")
    # source/external_* let a ticket_card mirror an external issue (Jira) instead of
    # a locally-authored card. 'local' cards keep the existing behaviour untouched.
    ccols = {r["name"] for r in conn.execute("PRAGMA table_info(ticket_cards)").fetchall()}
    for col, ddl in (("source", "source TEXT NOT NULL DEFAULT 'local'"),
                     ("external_id", "external_id TEXT"),
                     ("external_url", "external_url TEXT"),
                     ("meta", "meta TEXT")):
        if col not in ccols:
            conn.execute(f"ALTER TABLE ticket_cards ADD COLUMN {ddl}")
    # signals.severity added after the table shipped with kind-only
    scols = {r["name"] for r in conn.execute("PRAGMA table_info(signals)").fetchall()}
    if scols and "severity" not in scols:
        conn.execute("ALTER TABLE signals ADD COLUMN severity TEXT NOT NULL DEFAULT 'low'")
    # ticket_cards.assigned_to: which roster agent owns this task
    ccols = {r["name"] for r in conn.execute("PRAGMA table_info(ticket_cards)").fetchall()}
    if ccols and "assigned_to" not in ccols:
        conn.execute("ALTER TABLE ticket_cards ADD COLUMN assigned_to TEXT")
    # agent_metrics.last_tool: the most recent tool the agent used (STATE/TOOL row)
    amcols = {r["name"] for r in conn.execute("PRAGMA table_info(agent_metrics)").fetchall()}
    if amcols and "last_tool" not in amcols:
        conn.execute("ALTER TABLE agent_metrics ADD COLUMN last_tool TEXT")
    # agents.owner_user_id: agents are private to the user who created them.
    agcols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)").fetchall()}
    if agcols and "owner_user_id" not in agcols:
        conn.execute("ALTER TABLE agents ADD COLUMN owner_user_id INTEGER")
        # backfill pre-existing agents to the project admin so they stay visible
        conn.execute(
            "UPDATE agents SET owner_user_id=(SELECT admin_id FROM projects WHERE projects.id=agents.project_id) "
            "WHERE owner_user_id IS NULL")
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
            SELECT u.id, u.name, u.email, m.role, m.can_manage
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
            # this caller's own capability, so the UI can gate connect controls
            "can_manage": can_manage(conn, pid, user["id"]),
            "members": [
                {"user_id": r["id"], "name": r["name"], "email": r["email"], "role": r["role"],
                 # admin implicitly manages; others per the granted flag
                 "can_manage": bool(r["can_manage"]) or r["id"] == proj["admin_id"]}
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


def can_manage(conn, pid: int, user_id: int) -> bool:
    """True if the user may connect/manage integrations for this project:
    the admin always can; other members only if granted can_manage."""
    proj = conn.execute("SELECT admin_id FROM projects WHERE id=?", (pid,)).fetchone()
    if proj and proj["admin_id"] == user_id:
        return True
    row = conn.execute(
        "SELECT can_manage FROM members WHERE project_id=? AND user_id=?", (pid, user_id)
    ).fetchone()
    return bool(row and row["can_manage"])


def require_manage(conn, pid: int, user_id: int):
    require_member(conn, pid, user_id)
    if not can_manage(conn, pid, user_id):
        raise HTTPException(403, "You do not have permission to manage integrations for this project")


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


class ManageIn(BaseModel):
    can_manage: bool


@app.post("/api/projects/{pid}/members/{user_id}/can-manage")
def set_can_manage(pid: int, user_id: int, body: ManageIn, user=Depends(current_user)):
    """Admin grants/revokes a member's ability to connect + manage integrations."""
    conn = db()
    try:
        require_admin(conn, pid, user["id"])
        cur = conn.execute(
            "UPDATE members SET can_manage=? WHERE project_id=? AND user_id=?",
            (1 if body.can_manage else 0, pid, user_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Not a member")
        conn.commit()
        return {"ok": True, "user_id": user_id, "can_manage": body.can_manage}
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


# ---------- Per-project bus group (join command) ----------

RAW_JOIN_URL = (
    "https://raw.githubusercontent.com/koushikmaji31/claude-remote-worker/main/join-bus.sh"
)


def _bus_public_url() -> str:
    """Public URL of the chat bus. env BUS_PUBLIC_URL > ngrok :8899 tunnel > localhost."""
    env = os.environ.get("BUS_PUBLIC_URL")
    if env:
        return env.rstrip("/")
    try:
        import json
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1) as r:
            tunnels = json.load(r).get("tunnels", [])
        for t in tunnels:
            addr = str(t.get("config", {}).get("addr", ""))
            if addr.endswith(":8899") and t.get("public_url", "").startswith("https"):
                return t["public_url"]
    except Exception:
        pass
    return "http://127.0.0.1:8899"


def _bus_token() -> str:
    try:
        with open("/tmp/claude-bus/token") as f:
            return f.read().strip()
    except OSError:
        return ""


def _bus_base_name(display_name: str) -> str:
    """Teammate display name -> agent name prefix, e.g. 'Koushik Maji' -> 'koushik'.
    Mirrors the sanitizing in hooks/bus_join.sh (lowercase, alnum, cut at first digit)."""
    import re

    first = (display_name or "").strip().split()[0] if (display_name or "").strip() else ""
    base = re.sub(r"[^a-z0-9_-]", "", first.lower())
    base = re.split(r"[0-9]", base)[0]
    return base or "agent"


@app.get("/api/projects/{pid}/bus")
def project_bus(pid: int, user=Depends(current_user)):
    """Return the one-command join for this project's Claude group. The bus room
    is the project's invite code, so joining scopes a Claude session to this group.
    The teammate's platform name rides along so their agents are named after them
    (koushik_1, koushik_2 ...) instead of after the local machine account."""
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        row = conn.execute("SELECT invite_code FROM projects WHERE id=?", (pid,)).fetchone()
        if not row:
            raise HTTPException(404, "Project not found")
        room = row["invite_code"]
        url, token = _bus_public_url(), _bus_token()
        base = _bus_base_name(user["name"])
        command = f"curl -sL {RAW_JOIN_URL} | bash -s -- {url} {token} {room} {base}"
        return {"bus_url": url, "room": room, "token": token, "name": base, "command": command}
    finally:
        conn.close()


# ---------- GitHub integration (Phase 1: identity + repo link) ----------

GH_API = "https://api.github.com"


def _gh_api(token: str, method: str, path: str, body=None, timeout: int = 15):
    """Call the GitHub REST API with a user token. Returns (status, json, headers)."""
    import requests

    url = path if path.startswith("http") else f"{GH_API}{path}"
    resp = requests.request(
        method,
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        json=body,
        timeout=timeout,
    )
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data, resp.headers


def _user_gh_token(conn, user_id: int) -> str:
    row = conn.execute(
        "SELECT token_enc FROM gh_identities WHERE user_id=?", (user_id,)
    ).fetchone()
    if not row:
        raise HTTPException(400, "GitHub not connected")
    return _unseal(row["token_enc"])


class GitHubConnectIn(BaseModel):
    token: str


@app.post("/api/github/connect")
def github_connect(body: GitHubConnectIn, user=Depends(current_user)):
    """Validate a personal access token against GitHub and store it (sealed)."""
    token = body.token.strip()
    if not token:
        raise HTTPException(400, "Token required")
    try:
        status, data, headers = _gh_api(token, "GET", "/user")
    except Exception as e:
        raise HTTPException(502, f"Could not reach GitHub: {e}")
    if status == 401:
        raise HTTPException(401, "GitHub rejected the token (invalid or expired)")
    if status != 200:
        raise HTTPException(400, f"GitHub error ({status}): {data.get('message', 'unknown')}")
    scopes = headers.get("X-OAuth-Scopes", "")
    conn = db()
    try:
        conn.execute(
            """INSERT INTO gh_identities(user_id, gh_login, gh_id, token_enc, auth_kind, scopes, connected_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 gh_login=excluded.gh_login, gh_id=excluded.gh_id, token_enc=excluded.token_enc,
                 auth_kind=excluded.auth_kind, scopes=excluded.scopes, connected_at=excluded.connected_at""",
            (user["id"], data["login"], data.get("id"), _seal(token), "pat", scopes, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"connected": True, "login": data["login"], "scopes": scopes, "encrypted": TOKENS_ENCRYPTED}


# ---------- GitHub OAuth (Phase 3) ----------
# Uses a GitHub OAuth App: set GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET (e.g. in .env)
# and register the callback URL as  <PUBLIC_BASE_URL>/api/github/oauth/callback.
# PAT connect above stays as a fallback for servers without an OAuth app.

GH_OAUTH_CLIENT_ID = os.environ.get("GITHUB_CLIENT_ID", "").strip()
GH_OAUTH_CLIENT_SECRET = os.environ.get("GITHUB_CLIENT_SECRET", "").strip()
GH_OAUTH_CONFIGURED = bool(GH_OAUTH_CLIENT_ID and GH_OAUTH_CLIENT_SECRET)
GH_OAUTH_STATE_TTL = 15 * 60  # seconds a pending handshake stays valid


def _public_base() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:8900").rstrip("/")


def _oauth_redirect_uri() -> str:
    return f"{_public_base()}/api/github/oauth/callback"


@app.get("/api/github/oauth/config")
def github_oauth_config():
    """Let the UI know whether 'Sign in with GitHub' is available on this server."""
    return {"configured": GH_OAUTH_CONFIGURED}


class OAuthStartIn(BaseModel):
    return_to: str = "/"  # SPA path to land on after the callback


@app.post("/api/github/oauth/start")
def github_oauth_start(body: OAuthStartIn, user=Depends(current_user)):
    if not GH_OAUTH_CONFIGURED:
        raise HTTPException(400, "GitHub OAuth is not configured (set GITHUB_CLIENT_ID / GITHUB_CLIENT_SECRET)")
    return_to = body.return_to
    if not return_to.startswith("/") or return_to.startswith("//"):
        return_to = "/"
    state = secrets.token_urlsafe(32)
    conn = db()
    try:
        conn.execute(
            "DELETE FROM gh_oauth_states WHERE created_at < ?", (time.time() - GH_OAUTH_STATE_TTL,)
        )
        conn.execute(
            "INSERT INTO gh_oauth_states(state, user_id, return_to, created_at) VALUES(?,?,?,?)",
            (state, user["id"], return_to, time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    from urllib.parse import urlencode

    params = urlencode({
        "client_id": GH_OAUTH_CLIENT_ID,
        "redirect_uri": _oauth_redirect_uri(),
        "scope": "repo read:org",
        "state": state,
    })
    return {"authorize_url": f"https://github.com/login/oauth/authorize?{params}"}


@app.get("/api/github/oauth/callback")
def github_oauth_callback(code: str = "", state: str = "", error: str = ""):
    """Browser lands here from github.com; no bearer token, so the state row is the auth."""
    from urllib.parse import quote

    def bounce(dest: str, status: str, reason: str = ""):
        sep = "&" if "?" in dest else "?"
        extra = f"&github_reason={quote(reason)}" if reason else ""
        return RedirectResponse(f"{dest}{sep}github={status}{extra}", status_code=302)

    conn = db()
    try:
        conn.execute(
            "DELETE FROM gh_oauth_states WHERE created_at < ?", (time.time() - GH_OAUTH_STATE_TTL,)
        )
        row = conn.execute("SELECT * FROM gh_oauth_states WHERE state=?", (state,)).fetchone()
        if row:  # single-use, consumed even if the exchange below fails
            conn.execute("DELETE FROM gh_oauth_states WHERE state=?", (state,))
        conn.commit()
        if not row:
            return bounce("/", "error", "Login link expired — try again")
        return_to = row["return_to"] or "/"
        if error:
            return bounce(return_to, "error", error)
        if not code:
            return bounce(return_to, "error", "GitHub did not return a code")

        import requests

        try:
            resp = requests.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id": GH_OAUTH_CLIENT_ID,
                    "client_secret": GH_OAUTH_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": _oauth_redirect_uri(),
                },
                headers={"Accept": "application/json"},
                timeout=15,
            )
            payload = resp.json()
        except Exception:
            return bounce(return_to, "error", "Could not reach GitHub to exchange the code")
        access_token = payload.get("access_token")
        if not access_token:
            return bounce(return_to, "error", payload.get("error_description") or "Token exchange failed")

        try:
            status_code, gh_user, _ = _gh_api(access_token, "GET", "/user")
        except Exception:
            return bounce(return_to, "error", "Could not fetch your GitHub profile")
        if status_code != 200:
            return bounce(return_to, "error", f"GitHub /user returned {status_code}")

        conn.execute(
            """INSERT INTO gh_identities(user_id, gh_login, gh_id, token_enc, auth_kind, scopes, connected_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 gh_login=excluded.gh_login, gh_id=excluded.gh_id, token_enc=excluded.token_enc,
                 auth_kind=excluded.auth_kind, scopes=excluded.scopes, connected_at=excluded.connected_at""",
            (
                row["user_id"], gh_user["login"], gh_user.get("id"),
                _seal(access_token), "oauth", payload.get("scope", ""), time.time(),
            ),
        )
        conn.commit()
        return bounce(return_to, "connected")
    finally:
        conn.close()


# ---------- GitLab integration (OAuth sign-in, mirrors GitHub) ----------
# A user connects EITHER GitHub or GitLab; the identity row (keyed by user_id)
# carries a `provider` so the read path knows which API to call. Register a
# GitLab OAuth application (gitlab.com/-/user_settings/applications) and set
# GITLAB_CLIENT_ID / GITLAB_CLIENT_SECRET; callback = <base>/api/gitlab/oauth/callback.

GITLAB_HOST = os.environ.get("GITLAB_HOST", "gitlab.com").strip().rstrip("/")
GL_API = f"https://{GITLAB_HOST}/api/v4"
GL_OAUTH_CLIENT_ID = os.environ.get("GITLAB_CLIENT_ID", "").strip()
GL_OAUTH_CLIENT_SECRET = os.environ.get("GITLAB_CLIENT_SECRET", "").strip()
GL_OAUTH_CONFIGURED = bool(GL_OAUTH_CLIENT_ID and GL_OAUTH_CLIENT_SECRET)


def _gl_api(token: str, method: str, path: str, body=None, timeout: int = 15):
    """Call the GitLab REST API (v4) with a user token. Returns (status, json, headers)."""
    import requests

    url = path if path.startswith("http") else f"{GL_API}{path}"
    resp = requests.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        json=body,
        timeout=timeout,
    )
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data, resp.headers


def _gl_redirect_uri() -> str:
    return f"{_public_base()}/api/gitlab/oauth/callback"


@app.get("/api/gitlab/oauth/config")
def gitlab_oauth_config():
    return {"configured": GL_OAUTH_CONFIGURED, "host": GITLAB_HOST}


@app.post("/api/gitlab/oauth/start")
def gitlab_oauth_start(body: OAuthStartIn, user=Depends(current_user)):
    if not GL_OAUTH_CONFIGURED:
        raise HTTPException(400, "GitLab OAuth is not configured (set GITLAB_CLIENT_ID / GITLAB_CLIENT_SECRET)")
    return_to = body.return_to
    if not return_to.startswith("/") or return_to.startswith("//"):
        return_to = "/"
    state = secrets.token_urlsafe(32)
    conn = db()
    try:
        conn.execute(
            "DELETE FROM gh_oauth_states WHERE created_at < ?", (time.time() - GH_OAUTH_STATE_TTL,)
        )
        conn.execute(
            "INSERT INTO gh_oauth_states(state, user_id, return_to, created_at, provider) VALUES(?,?,?,?,?)",
            (state, user["id"], return_to, time.time(), "gitlab"),
        )
        conn.commit()
    finally:
        conn.close()
    from urllib.parse import urlencode

    params = urlencode({
        "client_id": GL_OAUTH_CLIENT_ID,
        "redirect_uri": _gl_redirect_uri(),
        "response_type": "code",
        "scope": "read_api read_user",
        "state": state,
    })
    return {"authorize_url": f"https://{GITLAB_HOST}/oauth/authorize?{params}"}


@app.get("/api/gitlab/oauth/callback")
def gitlab_oauth_callback(code: str = "", state: str = "", error: str = ""):
    """Browser lands here from gitlab.com; the state row is the auth (no bearer token)."""
    from urllib.parse import quote

    def bounce(dest: str, status: str, reason: str = ""):
        sep = "&" if "?" in dest else "?"
        extra = f"&gitlab_reason={quote(reason)}" if reason else ""
        return RedirectResponse(f"{dest}{sep}gitlab={status}{extra}", status_code=302)

    conn = db()
    try:
        conn.execute(
            "DELETE FROM gh_oauth_states WHERE created_at < ?", (time.time() - GH_OAUTH_STATE_TTL,)
        )
        row = conn.execute(
            "SELECT * FROM gh_oauth_states WHERE state=? AND provider='gitlab'", (state,)
        ).fetchone()
        if row:  # single-use
            conn.execute("DELETE FROM gh_oauth_states WHERE state=?", (state,))
        conn.commit()
        if not row:
            return bounce("/", "error", "Login link expired — try again")
        return_to = row["return_to"] or "/"
        if error:
            return bounce(return_to, "error", error)
        if not code:
            return bounce(return_to, "error", "GitLab did not return a code")

        import requests

        try:
            resp = requests.post(
                f"https://{GITLAB_HOST}/oauth/token",
                data={
                    "client_id": GL_OAUTH_CLIENT_ID,
                    "client_secret": GL_OAUTH_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": _gl_redirect_uri(),
                },
                headers={"Accept": "application/json"},
                timeout=15,
            )
            payload = resp.json()
        except Exception:
            return bounce(return_to, "error", "Could not reach GitLab to exchange the code")
        access_token = payload.get("access_token")
        if not access_token:
            return bounce(return_to, "error", payload.get("error_description") or "Token exchange failed")

        try:
            status_code, gl_user, _ = _gl_api(access_token, "GET", "/user")
        except Exception:
            return bounce(return_to, "error", "Could not fetch your GitLab profile")
        if status_code != 200:
            return bounce(return_to, "error", f"GitLab /user returned {status_code}")

        conn.execute(
            """INSERT INTO gh_identities(user_id, gh_login, gh_id, token_enc, auth_kind, scopes, connected_at, provider)
               VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 gh_login=excluded.gh_login, gh_id=excluded.gh_id, token_enc=excluded.token_enc,
                 auth_kind=excluded.auth_kind, scopes=excluded.scopes, connected_at=excluded.connected_at,
                 provider=excluded.provider""",
            (
                row["user_id"], gl_user["username"], gl_user.get("id"),
                _seal(access_token), "oauth", payload.get("scope", ""), time.time(), "gitlab",
            ),
        )
        conn.commit()
        return bounce(return_to, "connected")
    finally:
        conn.close()


@app.get("/api/github/status")
def github_status(user=Depends(current_user)):
    conn = db()
    try:
        row = conn.execute(
            "SELECT gh_login, scopes, auth_kind, connected_at, provider FROM gh_identities WHERE user_id=?", (user["id"],)
        ).fetchone()
    finally:
        conn.close()
    availability = {
        "encrypted": TOKENS_ENCRYPTED,
        "oauth_available": GH_OAUTH_CONFIGURED,          # GitHub
        "gitlab_oauth_available": GL_OAUTH_CONFIGURED,   # GitLab
        "gitlab_host": GITLAB_HOST,
    }
    if not row:
        return {"connected": False, **availability}
    return {
        "connected": True,
        "provider": row["provider"] or "github",
        "login": row["gh_login"],
        "scopes": row["scopes"],
        "auth_kind": row["auth_kind"],
        "connected_at": row["connected_at"],
        **availability,
    }


@app.delete("/api/github/disconnect")
def github_disconnect(user=Depends(current_user)):
    conn = db()
    try:
        conn.execute("DELETE FROM gh_identities WHERE user_id=?", (user["id"],))
        conn.commit()
    finally:
        conn.close()
    return {"connected": False, "encrypted": TOKENS_ENCRYPTED}


class RepoLinkIn(BaseModel):
    owner: str = ""
    repo: str = ""
    full_name: str = ""  # convenience: "owner/repo" instead of owner+repo


def _repo_link_row(conn, pid: int):
    return conn.execute("SELECT * FROM repo_links WHERE project_id=?", (pid,)).fetchone()


@app.post("/api/projects/{pid}/github/link")
def github_link_repo(pid: int, body: RepoLinkIn, user=Depends(current_user)):
    """Link a GitHub repo to the project (integration managers only), validated
    against the linker's GitHub token."""
    conn = db()
    try:
        require_manage(conn, pid, user["id"])
        idrow = conn.execute(
            "SELECT token_enc, provider FROM gh_identities WHERE user_id=?", (user["id"],)
        ).fetchone()
        if not idrow:
            raise HTTPException(400, "Connect a GitHub or GitLab account first")
        provider = idrow["provider"] or "github"
        token = _unseal(idrow["token_enc"])
        full_path = body.full_name.strip() or f"{body.owner.strip()}/{body.repo.strip()}".strip("/")
        if "/" not in full_path:
            raise HTTPException(400, "Give the repository as owner/repo (or group/project on GitLab)")

        if provider == "gitlab":
            from urllib.parse import quote
            slug = quote(full_path, safe="")  # URL-encoded full path is GitLab's project id
            try:
                status, data, _ = _gl_api(token, "GET", f"/projects/{slug}")
            except Exception as e:
                raise HTTPException(502, f"Could not reach GitLab: {e}")
            if status == 404:
                raise HTTPException(404, f"Project {full_path} not found or not accessible with your account")
            if status != 200:
                raise HTTPException(400, f"GitLab error ({status}): {data.get('message', 'unknown') if isinstance(data, dict) else 'unknown'}")
            canonical = data["path_with_namespace"]          # e.g. group/subgroup/project
            owner, repo = canonical.rsplit("/", 1)
            default_branch = data.get("default_branch")
            html_url, is_private = data.get("web_url"), data.get("visibility") != "public"
        else:
            owner, repo = full_path.split("/", 1)
            try:
                status, data, _ = _gh_api(token, "GET", f"/repos/{owner}/{repo}")
            except Exception as e:
                raise HTTPException(502, f"Could not reach GitHub: {e}")
            if status == 404:
                raise HTTPException(404, f"Repo {owner}/{repo} not found or not accessible with your token")
            if status != 200:
                raise HTTPException(400, f"GitHub error ({status}): {data.get('message', 'unknown')}")
            owner, repo = data["owner"]["login"], data["name"]
            canonical = data["full_name"]
            default_branch = data.get("default_branch")
            html_url, is_private = data.get("html_url"), data.get("private")

        conn.execute(
            """INSERT INTO repo_links(project_id, owner, repo, default_branch, linked_by, linked_at, provider)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(project_id) DO UPDATE SET
                 owner=excluded.owner, repo=excluded.repo, default_branch=excluded.default_branch,
                 linked_by=excluded.linked_by, linked_at=excluded.linked_at, provider=excluded.provider""",
            (pid, owner, repo, default_branch, user["id"], time.time(), provider),
        )
        conn.commit()
        return {
            "linked": True, "provider": provider,
            "owner": owner, "repo": repo, "full_name": canonical,
            "default_branch": default_branch, "private": is_private, "html_url": html_url,
        }
    finally:
        conn.close()


@app.get("/api/projects/{pid}/github")
def github_get_link(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        row = _repo_link_row(conn, pid)
        if not row:
            return {"linked": False}
        return {
            "linked": True,
            "provider": row["provider"] or "github",
            "owner": row["owner"],
            "repo": row["repo"],
            "full_name": f"{row['owner']}/{row['repo']}",
            "default_branch": row["default_branch"],
            "linked_at": row["linked_at"],
        }
    finally:
        conn.close()


@app.delete("/api/projects/{pid}/github/link")
def github_unlink_repo(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_manage(conn, pid, user["id"])
        conn.execute("DELETE FROM repo_links WHERE project_id=?", (pid,))
        conn.commit()
        return {"linked": False}
    finally:
        conn.close()


# ---------- GitHub integration (Phase 2: read) ----------

# Tiny in-process TTL cache so polling the UI doesn't burn the GitHub rate limit.
_GH_CACHE: dict = {}
_GH_CACHE_TTL = 25.0  # seconds


def _cache_get(key):
    hit = _GH_CACHE.get(key)
    if hit and time.time() < hit[0]:
        return hit[1]
    return None


def _cache_put(key, value, ttl: float = _GH_CACHE_TTL):
    _GH_CACHE[key] = (time.time() + ttl, value)


def _project_repo(conn, pid: int):
    row = _repo_link_row(conn, pid)
    if not row:
        raise HTTPException(400, "No GitHub repo linked to this project")
    return row["owner"], row["repo"], row["linked_by"]


def _project_gh_token(conn, pid: int, requester_id: int) -> str:
    """Token used for project reads: the repo-linker's, else the requester's own."""
    _, _, linked_by = _project_repo(conn, pid)
    for uid in (linked_by, requester_id):
        if uid is None:
            continue
        row = conn.execute(
            "SELECT token_enc FROM gh_identities WHERE user_id=?", (uid,)
        ).fetchone()
        if row:
            return _unseal(row["token_enc"])
    raise HTTPException(400, "No GitHub token available for this repo; an admin must reconnect GitHub")


def _gh_read(token: str, path: str, params: str = ""):
    """GET the GitHub API, mapping auth/rate-limit/not-found to HTTP errors. Returns (data, ratelimit)."""
    try:
        status, data, headers = _gh_api(token, "GET", path + params)
    except Exception as e:
        raise HTTPException(502, f"Could not reach GitHub: {e}")
    remaining = headers.get("X-RateLimit-Remaining")
    if status == 401:
        raise HTTPException(401, "GitHub token invalid or expired")
    if status == 403 and remaining == "0":
        raise HTTPException(429, "GitHub API rate limit exceeded; try again shortly")
    if status == 404:
        raise HTTPException(404, "Not found on GitHub (check repo access / token scope)")
    if status >= 400:
        raise HTTPException(400, f"GitHub error ({status}): {data.get('message', 'unknown') if isinstance(data, dict) else 'unknown'}")
    ratelimit = {
        "remaining": remaining,
        "limit": headers.get("X-RateLimit-Limit"),
        "reset": headers.get("X-RateLimit-Reset"),
    }
    return data, ratelimit


def _gh_project_read(conn, pid, user_id, path, params="", cache_key=None):
    if cache_key:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
    owner, repo, _ = _project_repo(conn, pid)
    token = _project_gh_token(conn, pid, user_id)
    data, ratelimit = _gh_read(token, f"/repos/{owner}/{repo}{path}", params)
    result = (data, ratelimit, owner, repo)
    if cache_key:
        _cache_put(cache_key, result)
    return result


# ---- GitLab read path: same normalized shapes as GitHub, different API ----

def _project_provider(conn, pid: int) -> str:
    row = _repo_link_row(conn, pid)
    return (row["provider"] if row and row["provider"] else "github")


def _project_token(conn, pid: int, requester_id: int) -> str:
    """Read token for either provider: the repo-linker's, else the requester's own."""
    row = _repo_link_row(conn, pid)
    for uid in ((row["linked_by"] if row else None), requester_id):
        if uid is None:
            continue
        r = conn.execute("SELECT token_enc FROM gh_identities WHERE user_id=?", (uid,)).fetchone()
        if r:
            return _unseal(r["token_enc"])
    raise HTTPException(400, "No git token available for this repo; a manager must reconnect their account")


def _gl_project_slug(owner: str, repo: str) -> str:
    from urllib.parse import quote
    return quote(f"{owner}/{repo}", safe="")  # GitLab wants the URL-encoded full path


def _gl_read(token: str, path: str, params: str = ""):
    """GET the GitLab API, mapping errors to HTTP. Returns (data, ratelimit)."""
    try:
        status, data, headers = _gl_api(token, "GET", path + params)
    except Exception as e:
        raise HTTPException(502, f"Could not reach GitLab: {e}")
    if status == 401:
        raise HTTPException(401, "GitLab token invalid or expired")
    if status == 429:
        raise HTTPException(429, "GitLab API rate limit exceeded; try again shortly")
    if status == 404:
        raise HTTPException(404, "Not found on GitLab (check project access / token scope)")
    if status >= 400:
        msg = (data.get("message") or data.get("error")) if isinstance(data, dict) else "unknown"
        raise HTTPException(400, f"GitLab error ({status}): {msg}")
    ratelimit = {"remaining": headers.get("RateLimit-Remaining"), "limit": headers.get("RateLimit-Limit")}
    return data, ratelimit


def _gl_project_read(conn, pid, user_id, path, params="", cache_key=None):
    if cache_key:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
    row = _repo_link_row(conn, pid)
    token = _project_token(conn, pid, user_id)
    slug = _gl_project_slug(row["owner"], row["repo"])
    data, rl = _gl_read(token, f"/projects/{slug}{path}", params)
    result = (data, rl, row["owner"], row["repo"])
    if cache_key:
        _cache_put(cache_key, result)
    return result


def _gl_branches(conn, pid, user_id):
    data, rl, owner, repo = _gl_project_read(
        conn, pid, user_id, "/repository/branches", "?per_page=100", cache_key=("branches", pid)
    )
    branches = [{"name": b["name"], "sha": b["commit"]["id"], "protected": b.get("protected", False)} for b in data]
    return {"repo": f"{owner}/{repo}", "branches": branches, "ratelimit": rl}


def _gl_pulls(conn, pid, user_id):
    data, rl, owner, repo = _gl_project_read(
        conn, pid, user_id, "/merge_requests", "?state=opened&per_page=50&order_by=updated_at",
        cache_key=("pulls", pid),
    )
    pulls = [{
        "number": m["iid"], "title": m["title"], "user": (m.get("author") or {}).get("username", "?"),
        "head": m["source_branch"], "base": m["target_branch"],
        "draft": m.get("draft", m.get("work_in_progress", False)),
        "updated_at": m["updated_at"], "html_url": m["web_url"],
    } for m in data]
    return {"repo": f"{owner}/{repo}", "pulls": pulls, "ratelimit": rl}


def _gl_issues(conn, pid, user_id):
    data, rl, owner, repo = _gl_project_read(
        conn, pid, user_id, "/issues", "?state=opened&per_page=50&order_by=updated_at",
        cache_key=("issues", pid),
    )
    issues = [{
        "number": i["iid"], "title": i["title"], "user": (i.get("author") or {}).get("username", "?"),
        "comments": i.get("user_notes_count", 0), "labels": i.get("labels", []),
        "updated_at": i["updated_at"], "html_url": i["web_url"],
    } for i in data]
    return {"repo": f"{owner}/{repo}", "issues": issues, "ratelimit": rl}


def _gl_pull_detail(conn, pid, user_id, number):
    detail, rl, owner, repo = _gl_project_read(conn, pid, user_id, f"/merge_requests/{number}")
    changes, _rl2, _o, _r = _gl_project_read(conn, pid, user_id, f"/merge_requests/{number}/changes")
    diffs = changes.get("changes", []) if isinstance(changes, dict) else []
    has_conflicts = detail.get("has_conflicts")
    return {
        "repo": f"{owner}/{repo}",
        "number": detail["iid"], "title": detail["title"], "state": detail["state"],
        "user": (detail.get("author") or {}).get("username", "?"),
        "head": detail["source_branch"], "base": detail["target_branch"],
        "mergeable": (None if has_conflicts is None else not has_conflicts),
        "mergeable_state": ("dirty" if has_conflicts else "clean") if has_conflicts is not None else "unknown",
        "additions": None, "deletions": None, "changed_files": len(diffs),
        "html_url": detail["web_url"],
        "files": [{
            "filename": c.get("new_path") or c.get("old_path"),
            "status": "renamed" if c.get("renamed_file") else "added" if c.get("new_file") else "removed" if c.get("deleted_file") else "modified",
            "additions": None, "deletions": None, "patch": c.get("diff", ""),
        } for c in diffs],
        "ratelimit": rl,
    }


@app.get("/api/projects/{pid}/github/branches")
def github_branches(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        if _project_provider(conn, pid) == "gitlab":
            return _gl_branches(conn, pid, user["id"])
        data, rl, owner, repo = _gh_project_read(
            conn, pid, user["id"], "/branches", "?per_page=100", cache_key=("branches", pid)
        )
        branches = [{"name": b["name"], "sha": b["commit"]["sha"], "protected": b.get("protected", False)} for b in data]
        return {"repo": f"{owner}/{repo}", "branches": branches, "ratelimit": rl}
    finally:
        conn.close()


@app.get("/api/projects/{pid}/github/pulls")
def github_pulls(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        if _project_provider(conn, pid) == "gitlab":
            return _gl_pulls(conn, pid, user["id"])
        data, rl, owner, repo = _gh_project_read(
            conn, pid, user["id"], "/pulls", "?state=open&per_page=50&sort=updated&direction=desc",
            cache_key=("pulls", pid),
        )
        pulls = [{
            "number": p["number"], "title": p["title"], "user": p["user"]["login"],
            "head": p["head"]["ref"], "base": p["base"]["ref"], "draft": p.get("draft", False),
            "updated_at": p["updated_at"], "html_url": p["html_url"],
        } for p in data]
        return {"repo": f"{owner}/{repo}", "pulls": pulls, "ratelimit": rl}
    finally:
        conn.close()


@app.get("/api/projects/{pid}/github/issues")
def github_issues(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        if _project_provider(conn, pid) == "gitlab":
            return _gl_issues(conn, pid, user["id"])
        data, rl, owner, repo = _gh_project_read(
            conn, pid, user["id"], "/issues", "?state=open&per_page=50&sort=updated&direction=desc",
            cache_key=("issues", pid),
        )
        # The issues endpoint also returns PRs; filter those out.
        issues = [{
            "number": i["number"], "title": i["title"], "user": i["user"]["login"],
            "comments": i.get("comments", 0), "labels": [l["name"] for l in i.get("labels", [])],
            "updated_at": i["updated_at"], "html_url": i["html_url"],
        } for i in data if "pull_request" not in i]
        return {"repo": f"{owner}/{repo}", "issues": issues, "ratelimit": rl}
    finally:
        conn.close()


@app.get("/api/github/repos")
def github_repos(user=Depends(current_user)):
    """List the connected user's repositories for whichever provider they linked
    (GitHub or GitLab), most-recently-active first, up to 100."""
    conn = db()
    try:
        row = conn.execute(
            "SELECT token_enc, provider FROM gh_identities WHERE user_id=?", (user["id"],)
        ).fetchone()
        if not row:
            raise HTTPException(400, "Connect a GitHub or GitLab account first")
        token = _unseal(row["token_enc"])
        provider = row["provider"] or "github"
    finally:
        conn.close()

    if provider == "gitlab":
        status, data, _ = _gl_api(
            token, "GET",
            "/projects?membership=true&per_page=100&order_by=last_activity_at&simple=true",
        )
        if status != 200 or not isinstance(data, list):
            raise HTTPException(status if status >= 400 else 502, "Failed to list GitLab projects")
        repos = [{
            "full_name": r["path_with_namespace"],
            "private": r.get("visibility") != "public",
            "description": r.get("description") or "",
        } for r in data]
    else:
        status, data, _ = _gh_api(
            token, "GET",
            "/user/repos?per_page=100&sort=updated&affiliation=owner,collaborator,organization_member",
        )
        if status != 200 or not isinstance(data, list):
            raise HTTPException(status if status >= 400 else 502, "Failed to list GitHub repositories")
        repos = [{
            "full_name": r["full_name"],
            "private": r.get("private", False),
            "description": r.get("description") or "",
        } for r in data]

    return {"provider": provider, "repos": repos}


@app.get("/api/projects/{pid}/github/pulls/{number}")
def github_pull_detail(pid: int, number: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        if _project_provider(conn, pid) == "gitlab":
            return _gl_pull_detail(conn, pid, user["id"], number)
        detail, rl, owner, repo = _gh_project_read(conn, pid, user["id"], f"/pulls/{number}")
        files, _rl2, _o, _r = _gh_project_read(conn, pid, user["id"], f"/pulls/{number}/files", "?per_page=100")
        return {
            "repo": f"{owner}/{repo}",
            "number": detail["number"], "title": detail["title"], "state": detail["state"],
            "user": detail["user"]["login"], "head": detail["head"]["ref"], "base": detail["base"]["ref"],
            "mergeable": detail.get("mergeable"), "mergeable_state": detail.get("mergeable_state"),
            "additions": detail.get("additions"), "deletions": detail.get("deletions"),
            "changed_files": detail.get("changed_files"), "html_url": detail["html_url"],
            "files": [{
                "filename": f["filename"], "status": f["status"],
                "additions": f["additions"], "deletions": f["deletions"], "patch": f.get("patch", ""),
            } for f in files],
            "ratelimit": rl,
        }
    finally:
        conn.close()


# Branch-history graph (Phase 3): commit DAG across recent branches, one payload
# the UI can lay out as lanes. REST (not GraphQL) so fine-grained PATs keep working.
GRAPH_MAX_BRANCHES = 12
GRAPH_COMMITS_PER_BRANCH = 30
GRAPH_CACHE_TTL = 60.0  # heavier than the list endpoints (1 call per branch)


GRAPH_DISK = Path(__file__).resolve().parent.parent / ".graph-cache"
_graph_refreshing = set()
_graph_lock = threading.Lock()


def _graph_disk_read(pid):
    try:
        with open(GRAPH_DISK / f"{pid}.json") as f:
            return json.load(f)  # {"ts": float, "result": {...}}
    except (OSError, ValueError):
        return None


def _graph_disk_write(pid, result):
    try:
        GRAPH_DISK.mkdir(exist_ok=True)
        p = GRAPH_DISK / f"{pid}.json"
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump({"ts": time.time(), "result": result}, f)
        os.replace(tmp, p)
    except OSError:
        pass


def _build_graph_result(conn, pid, user_id):
    """The expensive part: fetch branches + commits/branch + PRs from GitHub."""
    from urllib.parse import quote
    owner, repo, _ = _project_repo(conn, pid)
    token = _project_gh_token(conn, pid, user_id)
    link = _repo_link_row(conn, pid)
    default_branch = link["default_branch"]

    branches_raw, rl = _gh_read(token, f"/repos/{owner}/{repo}/branches", "?per_page=100")
    # Default branch first so shared history is attributed to it.
    branches_raw.sort(key=lambda b: (b["name"] != default_branch, b["name"].lower()))
    truncated = len(branches_raw) > GRAPH_MAX_BRANCHES
    branches_raw = branches_raw[:GRAPH_MAX_BRANCHES]

    commits: dict = {}
    for b in branches_raw:
        data, rl = _gh_read(
            token,
            f"/repos/{owner}/{repo}/commits",
            f"?sha={quote(b['name'], safe='')}&per_page={GRAPH_COMMITS_PER_BRANCH}",
        )
        for c in data:
            if c["sha"] in commits:
                continue
            meta = c["commit"]
            commits[c["sha"]] = {
                "sha": c["sha"],
                "parents": [p["sha"] for p in c.get("parents", [])],
                "message": (meta.get("message") or "").split("\n", 1)[0][:140],
                "author": (c.get("author") or {}).get("login") or (meta.get("author") or {}).get("name") or "?",
                "date": (meta.get("committer") or meta.get("author") or {}).get("date"),
                "branch": b["name"],
            }
    nodes = sorted(commits.values(), key=lambda n: n["date"] or "", reverse=True)

    pulls_data, rl = _gh_read(token, f"/repos/{owner}/{repo}/pulls", "?state=open&per_page=50")
    pulls = [{
        "number": p["number"], "title": p["title"], "head": p["head"]["ref"],
        "base": p["base"]["ref"], "draft": p.get("draft", False), "html_url": p["html_url"],
    } for p in pulls_data]

    return {
        "repo": f"{owner}/{repo}",
        "default_branch": default_branch,
        "branches": [
            {"name": b["name"], "tip": b["commit"]["sha"], "protected": b.get("protected", False)}
            for b in branches_raw
        ],
        "commits": nodes,
        "pulls": pulls,
        "truncated": truncated,
        "commits_per_branch": GRAPH_COMMITS_PER_BRANCH,
        "ratelimit": rl,
    }


def _graph_refresh_bg(pid, user_id):
    """Rebuild the graph off the request path so a stale hit stays instant."""
    with _graph_lock:
        if pid in _graph_refreshing:
            return
        _graph_refreshing.add(pid)

    def run():
        try:
            c = db()
            try:
                res = _build_graph_result(c, pid, user_id)
                _cache_put(("graph", pid), res, ttl=GRAPH_CACHE_TTL)
                _graph_disk_write(pid, res)
            finally:
                c.close()
        except Exception:
            pass
        finally:
            with _graph_lock:
                _graph_refreshing.discard(pid)

    threading.Thread(target=run, daemon=True).start()


@app.get("/api/projects/{pid}/github/graph")
def github_graph(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        mem = _cache_get(("graph", pid))
        if mem is not None:
            return mem
        # Stale-while-revalidate: any prior build (persisted to disk, survives
        # restarts) is returned INSTANTLY and refreshed in the background. Only a
        # truly cold cache blocks on GitHub.
        disk = _graph_disk_read(pid)
        if disk is not None:
            _graph_refresh_bg(pid, user["id"])
            out = dict(disk.get("result") or {})
            out["cached"] = True
            out["stale_age"] = int(time.time() - disk.get("ts", 0))
            return out
        result = _build_graph_result(conn, pid, user["id"])
        _cache_put(("graph", pid), result, ttl=GRAPH_CACHE_TTL)
        _graph_disk_write(pid, result)
        return result
    finally:
        conn.close()


# ---------- Agent RPC ----------

# ---------- Merge-conflict prediction (exact, via a cached bare mirror) ----------
# Answers "would merging head into base conflict?" BEFORE anyone merges, using
# `git merge-tree` on a server-side bare mirror of the project's linked repo.
# The repo is always derived from the project's repo_link — never from a
# client-supplied path (the old /rpc git.* surface let any member read any
# directory on this host; it is gone).

REPO_CACHE = Path(__file__).resolve().parent.parent / ".repo-cache"
_MIRROR_TIMEOUT = 180  # seconds; clone of a large repo can be slow on first use


def _run_git(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, timeout=timeout)


def _repo_mirror(owner: str, repo: str, token: str) -> Path:
    """Bare mirror of the linked repo, cloned on first use and fetched thereafter.

    The token is passed per-invocation and never written to .git/config, so a
    revoked/rotated token can't linger on disk.
    """
    REPO_CACHE.mkdir(exist_ok=True)
    path = REPO_CACHE / f"{owner}__{repo}.git"
    auth_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    refspec = "+refs/heads/*:refs/heads/*"

    if not (path / "HEAD").exists():
        res = _run_git("clone", "--bare", auth_url, str(path), timeout=_MIRROR_TIMEOUT)
        if res.returncode != 0:
            raise HTTPException(502, "Could not clone the linked repo (check GitHub access)")
        # Drop the tokenised remote so the secret isn't persisted.
        _run_git("-C", str(path), "remote", "set-url", "origin",
                 f"https://github.com/{owner}/{repo}.git")
    else:
        res = _run_git("-C", str(path), "fetch", "--prune", auth_url, refspec,
                       timeout=_MIRROR_TIMEOUT)
        if res.returncode != 0:
            raise HTTPException(502, "Could not fetch the linked repo (check GitHub access)")
    return path


def _mirror_branches(path: Path) -> list:
    res = _run_git("-C", str(path), "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return [b for b in res.stdout.splitlines() if b]


@app.get("/api/projects/{pid}/github/conflicts")
def github_conflicts(pid: int, base: str, head: str, user=Depends(current_user)):
    """Exact merge-conflict preview between two branches of the linked repo."""
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        owner, repo, _ = _project_repo(conn, pid)
        token = _project_gh_token(conn, pid, user["id"])
    finally:
        conn.close()

    if base == head:
        raise HTTPException(400, "base and head must be different branches")

    path = _repo_mirror(owner, repo, token)

    # Only ever pass branch names git itself reported: blocks option injection
    # (e.g. a branch named "--upload-pack=...") and bad refs.
    known = _mirror_branches(path)
    for name in (base, head):
        if name not in known:
            raise HTTPException(404, f"Unknown branch: {name}")

    res = _run_git("-C", str(path), "merge-tree", "--write-tree", "--name-only", base, head)
    if res.returncode not in (0, 1):
        raise HTTPException(500, f"git merge-tree failed: {res.stderr.strip()[:300]}")

    # merge-tree exits 1 when the merge is not clean. Its stdout is three
    # blank-line-separated sections: the merged tree OID, the conflicted paths,
    # then human-readable messages ("Auto-merging x", "CONFLICT (content): ...").
    # Only the middle section is a file list — take lines after the OID up to the
    # first blank line, or the messages get mistaken for filenames.
    conflicts = []
    if res.returncode == 1:
        for line in res.stdout.splitlines()[1:]:
            if not line.strip():
                break
            conflicts.append(line)

    ahead = _run_git("-C", str(path), "rev-list", "--count", f"{base}..{head}").stdout.strip()
    behind = _run_git("-C", str(path), "rev-list", "--count", f"{head}..{base}").stdout.strip()

    return {
        "base": base,
        "head": head,
        "clean": res.returncode == 0,
        "conflicts": conflicts,
        "ahead": int(ahead or 0),    # commits on head not in base
        "behind": int(behind or 0),  # commits on base not in head
    }


# ---------- Ticket (shared ticket + live per-agent task lists) ----------

def _clean_tasks(raw) -> str:
    """Validate an incoming task list and return it as a JSON string.
    Accepts a list of objects with 'text' and optional 'status'
    (todo|doing|done, default 'todo'). Caps at 200. Raises 400 on bad input."""
    if not isinstance(raw, list):
        raise HTTPException(400, "tasks must be a list")
    if len(raw) > 200:
        raise HTTPException(400, "too many tasks (max 200)")
    cleaned = []
    for item in raw:
        if not isinstance(item, dict) or "text" not in item:
            raise HTTPException(400, "each task must be an object with 'text'")
        text = item.get("text")
        if not isinstance(text, str):
            raise HTTPException(400, "task 'text' must be a string")
        status = item.get("status", "todo")
        if status not in ("todo", "doing", "done"):
            raise HTTPException(400, "task 'status' must be todo|doing|done")
        cleaned.append({"text": text, "status": status})
    return json.dumps(cleaned)


def _ticket_state(conn, pid: int) -> dict:
    """Assemble the dashboard/agent view for a project id."""
    trow = conn.execute(
        "SELECT body, set_by, ts FROM tickets WHERE project_id=?", (pid,)
    ).fetchone()
    ticket = (
        {"body": trow["body"], "set_by": trow["set_by"], "ts": trow["ts"]}
        if trow else None
    )
    agents = []
    for row in conn.execute(
        "SELECT agent, tasks, ts FROM agent_tasks WHERE project_id=? ORDER BY agent",
        (pid,),
    ).fetchall():
        try:
            tasks = json.loads(row["tasks"]) if row["tasks"] else []
        except (ValueError, TypeError):
            tasks = []
        agents.append({"agent": row["agent"], "tasks": tasks, "ts": row["ts"]})
    cards = []
    for r in conn.execute(
        "SELECT * FROM ticket_cards WHERE project_id=? ORDER BY id", (pid,)
    ).fetchall():
        keys = r.keys()
        source = r["source"] if "source" in keys else "local"
        meta = None
        if "meta" in keys and r["meta"]:
            try:
                meta = json.loads(r["meta"])
            except (ValueError, TypeError):
                meta = None
        cards.append({
            "id": r["id"], "title": r["title"], "body": r["body"], "status": r["status"],
            "assigned_to": r["assigned_to"] if "assigned_to" in keys else None,
            "created_by": r["created_by"], "updated_by": r["updated_by"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
            "source": source or "local",
            "external_id": r["external_id"] if "external_id" in keys else None,
            "external_url": r["external_url"] if "external_url" in keys else None,
            "meta": meta,
        })
    return {"ticket": ticket, "agents": agents, "cards": cards}


_CARD_STATUS = ("todo", "doing", "done")


def _card_row(conn, pid, cid):
    row = conn.execute(
        "SELECT * FROM ticket_cards WHERE id=? AND project_id=?", (cid, pid)
    ).fetchone()
    if not row:
        raise HTTPException(404, "Card not found")
    return row


def _apply_card_update(conn, pid, cid, fields, who, allow_done):
    """Patch a card. allow_done=False (agents) forbids moving a card to 'done'."""
    row = _card_row(conn, pid, cid)
    sets, vals = [], []
    if "title" in fields and fields["title"] is not None:
        sets.append("title=?"); vals.append(str(fields["title"]))
    if "body" in fields and fields["body"] is not None:
        sets.append("body=?"); vals.append(str(fields["body"]))
    if "status" in fields and fields["status"] is not None:
        status = fields["status"]
        if status not in _CARD_STATUS:
            raise HTTPException(400, "status must be todo|doing|done")
        if status == "done" and not allow_done:
            raise HTTPException(403, "only a human can move a card to Done")
        sets.append("status=?"); vals.append(status)
    if not sets:
        return _card_row(conn, pid, cid)
    sets.append("updated_by=?"); vals.append(who)
    sets.append("updated_at=?"); vals.append(time.time())
    vals.extend([cid, pid])
    conn.execute(f"UPDATE ticket_cards SET {', '.join(sets)} WHERE id=? AND project_id=?", vals)
    conn.commit()
    return _card_row(conn, pid, cid)


def _create_card(conn, pid, title, body, who):
    if not (title or "").strip():
        raise HTTPException(400, "title is required")
    now = time.time()
    cur = conn.execute(
        "INSERT INTO ticket_cards(project_id, title, body, status, created_by, updated_by, created_at, updated_at) "
        "VALUES(?,?,?,'todo',?,?,?,?)",
        (pid, title.strip(), body or "", who, who, now, now),
    )
    conn.commit()
    return cur.lastrowid


def _bus_project(conn, request: Request, invite_code: str) -> int:
    """Authorize a bus-token request and resolve invite_code -> project_id."""
    auth = request.headers.get("Authorization", "")
    token = auth[7:].strip() if auth.startswith("Bearer ") else ""
    expected = _bus_token()
    if not expected or token != expected:
        raise HTTPException(401, "Invalid bus token")
    proj = conn.execute(
        "SELECT id FROM projects WHERE invite_code=?", (invite_code,)
    ).fetchone()
    if not proj:
        raise HTTPException(404, "Project not found")
    return proj["id"]


class TicketIn(BaseModel):
    body: str


@app.get("/api/projects/{pid}/ticket")
def get_ticket(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        return _ticket_state(conn, pid)
    finally:
        conn.close()


# ---------- Activity feed (signal feed across a project's agents) ----------
# Aggregates signals already flowing on the platform + bus into one
# attention-ranked stream: blockers (conflict-guard), stuck agents (derived),
# progress (TodoWrite bridge), and claims/pushes (bus broadcasts). The single
# `needs_you` count is the product: 0 = keep working, >0 = look.
ACTIVITY_STUCK_SECS = int(os.environ.get("ACTIVITY_STUCK_SECS", "600"))  # 10 min


def _bus_local_get(path: str):
    """GET a JSON endpoint on the local chat bus (same host → trusted, no token)."""
    import urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8899" + path, timeout=3) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


def _ranges_overlap(a, b):
    return a[0] <= b[1] and b[0] <= a[1]


@app.get("/api/projects/{pid}/activity")
def project_activity(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        row = conn.execute("SELECT invite_code FROM projects WHERE id=?", (pid,)).fetchone()
        room = row["invite_code"] if row else None
        state = _ticket_state(conn, pid)
    finally:
        conn.close()

    now = time.time()
    events = []

    # --- Blockers: overlapping live edits (conflict-guard on the bus) ---
    diff = _bus_local_get(f"/diff/state?project={room}").get("state", {}) if room else {}
    machines = list(diff.items())
    for i in range(len(machines)):
        for j in range(i + 1, len(machines)):
            m1, d1 = machines[i]
            m2, d2 = machines[j]
            f1, f2 = d1.get("files", {}), d2.get("files", {})
            for path in set(f1) & set(f2):
                if any(_ranges_overlap(r1, r2) for r1 in f1[path] for r2 in f2[path]):
                    events.append({
                        "type": "blocker", "severity": "high", "ts": now,
                        "agents": [m1, m2], "file": path,
                        "title": f"{m1} & {m2} are both editing {path}",
                        "detail": "Overlapping edits — a merge conflict is forming.",
                    })

    # --- Progress + stuck: derived from each agent's task list ---
    for a in state.get("agents", []):
        tasks = a.get("tasks", [])
        if not tasks:
            continue
        done = sum(1 for t in tasks if t.get("status") == "done")
        doing = next((t.get("text") for t in tasks if t.get("status") == "doing"), None)
        unfinished = any(t.get("status") != "done" for t in tasks)
        ts = a.get("ts") or now
        age = now - ts
        if unfinished and age > ACTIVITY_STUCK_SECS:
            events.append({
                "type": "stuck", "severity": "high", "ts": ts, "agents": [a["agent"]],
                "title": f"{a['agent']} may be stuck",
                "detail": f"No task update in {int(age // 60)} min · {done}/{len(tasks)} done"
                          + (f" · was: {doing}" if doing else ""),
            })
        else:
            events.append({
                "type": "progress", "severity": "low", "ts": ts, "agents": [a["agent"]],
                "title": f"{a['agent']} · {done}/{len(tasks)} tasks done",
                "detail": (f"now: {doing}" if doing else "all tasks complete"),
            })

    # --- Claims / pushes: recent broadcasts on the bus ---
    if room:
        for m in _bus_local_get(f"/history?room={room}").get("messages", [])[-120:]:
            if m.get("to"):  # broadcasts only
                continue
            text = (m.get("text") or "").strip()
            up = text.upper()
            if up.startswith("CLAIM"):
                events.append({"type": "claim", "severity": "low", "ts": m.get("ts"),
                               "agents": [m.get("from")], "title": f"{m.get('from')} claimed a path",
                               "detail": text[:200]})
            elif up.startswith("PUSHED") or up.startswith("PUSH "):
                events.append({"type": "push", "severity": "low", "ts": m.get("ts"),
                               "agents": [m.get("from")], "title": f"{m.get('from')} pushed",
                               "detail": text[:200]})

    # --- Signals: AI-distilled / agent-narrated updates (dynamic categories) ---
    sconn = db()
    try:
        for r in sconn.execute(
            "SELECT agent, kind, severity, text, ts FROM signals WHERE project_id=? "
            "ORDER BY id DESC LIMIT 40", (pid,)
        ).fetchall():
            events.append({
                "type": "signal", "kind": r["kind"],
                "severity": r["severity"] or "low",
                "ts": r["ts"], "agents": [r["agent"]],
                "title": r["text"], "detail": None,
            })
    finally:
        sconn.close()

    # --- Decisions: agents blocked waiting for a human yes/no or choice ---
    dconn = db()
    try:
        for r in dconn.execute(
            "SELECT id, agent, question, options, created_at FROM decisions "
            "WHERE project_id=? AND status='pending' ORDER BY id", (pid,)
        ).fetchall():
            try:
                opts = json.loads(r["options"])
            except (ValueError, TypeError):
                opts = ["yes", "no"]
            events.append({
                "type": "decision", "severity": "high", "ts": r["created_at"],
                "agents": [r["agent"]], "decision_id": r["id"], "options": opts,
                "title": f"{r['agent'] or 'An agent'} needs a decision",
                "detail": r["question"],
            })
    finally:
        dconn.close()

    sev = {"high": 0, "med": 1, "low": 2}
    events.sort(key=lambda e: (sev.get(e.get("severity"), 3), -(e.get("ts") or 0)))
    needs_you = sum(1 for e in events if e.get("severity") == "high")
    return {"needs_you": needs_you, "events": events[:50]}


# ---------- Signals (AI-distilled / agent-narrated Activity updates) ----------
# `kind` is a DYNAMIC category the AI (or agent) chooses — no fixed enum. We only
# derive a severity fallback from the label's meaning when one isn't supplied.
_HIGH_HINTS = ("block", "risk", "question", "error", "conflict", "regress",
               "security", "fail", "bug", "warn", "broke", "stuck", "vuln")


def _derive_severity(kind: str, text: str) -> str:
    blob = f"{kind} {text}".lower()
    return "high" if any(h in blob for h in _HIGH_HINTS) else "low"


class SignalIn(BaseModel):
    agent: str = ""
    kind: str = "update"      # dynamic label, AI-assigned
    severity: Optional[str] = None
    text: str


@app.post("/api/ticket/{invite_code}/signals")
def create_signal_bus(invite_code: str, body: SignalIn, request: Request):
    """Post a high-signal update (from the AI distiller or an agent)."""
    conn = db()
    try:
        pid = _bus_project(conn, request, invite_code)
        if not (body.text or "").strip():
            raise HTTPException(400, "text is required")
        kind = (body.kind or "update").strip().lower()[:32] or "update"
        sev = body.severity if body.severity in ("high", "low") else _derive_severity(kind, body.text)
        conn.execute(
            "INSERT INTO signals(project_id, agent, kind, severity, text, ts) VALUES(?,?,?,?,?,?)",
            (pid, body.agent, kind, sev, body.text.strip()[:500], time.time()),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- Human-in-the-loop decisions ----------
class DecisionCreate(BaseModel):
    agent: str = ""
    question: str
    options: list = ["yes", "no"]


class DecisionAnswer(BaseModel):
    answer: str


@app.post("/api/ticket/{invite_code}/decisions")
def create_decision_bus(invite_code: str, body: DecisionCreate, request: Request):
    """Agent (bus token) asks a question and gets a decision id to poll."""
    conn = db()
    try:
        pid = _bus_project(conn, request, invite_code)
        if not (body.question or "").strip():
            raise HTTPException(400, "question is required")
        opts = [str(o) for o in (body.options or [])] or ["yes", "no"]
        cur = conn.execute(
            "INSERT INTO decisions(project_id, agent, question, options, status, created_at) "
            "VALUES(?,?,?,?,'pending',?)",
            (pid, body.agent, body.question.strip(), json.dumps(opts), time.time()),
        )
        conn.commit()
        return {"id": cur.lastrowid, "status": "pending"}
    finally:
        conn.close()


@app.get("/api/ticket/{invite_code}/decisions/{did}")
def poll_decision_bus(invite_code: str, did: int, request: Request):
    """Agent polls this until status flips to 'answered'; then reads 'answer'."""
    conn = db()
    try:
        pid = _bus_project(conn, request, invite_code)
        row = conn.execute(
            "SELECT status, answer FROM decisions WHERE id=? AND project_id=?", (did, pid)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Decision not found")
        return {"status": row["status"], "answer": row["answer"]}
    finally:
        conn.close()


@app.post("/api/projects/{pid}/decisions/{did}/answer")
def answer_decision(pid: int, did: int, body: DecisionAnswer, user=Depends(current_user)):
    """A human answers a pending decision from the Activity page."""
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        row = conn.execute(
            "SELECT options, status FROM decisions WHERE id=? AND project_id=?", (did, pid)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Decision not found")
        if row["status"] != "pending":
            raise HTTPException(409, "Decision already answered")
        try:
            opts = json.loads(row["options"])
        except (ValueError, TypeError):
            opts = ["yes", "no"]
        if body.answer not in opts:
            raise HTTPException(400, f"answer must be one of {opts}")
        conn.execute(
            "UPDATE decisions SET status='answered', answer=?, answered_by=?, answered_at=? "
            "WHERE id=? AND project_id=?",
            (body.answer, user["name"], time.time(), did, pid),
        )
        conn.commit()
        return {"ok": True, "answer": body.answer}
    finally:
        conn.close()


# ---------- Agent roster (fleet) ----------
class AgentIn(BaseModel):
    name: str
    role: str = ""


class AssignIn(BaseModel):
    agent: Optional[str] = None   # None/"" un-assigns


@app.get("/api/projects/{pid}/agents")
def list_agents(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        rows = conn.execute(
            "SELECT id, name, role, created_at FROM agents WHERE project_id=? AND owner_user_id=? ORDER BY name",
            (pid, user["id"])).fetchall()
        return {"agents": [dict(r) for r in rows]}
    finally:
        conn.close()


@app.post("/api/projects/{pid}/agents")
def create_agent(pid: int, body: AgentIn, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        # names are unique per project, so a name maps to exactly one owner
        try:
            cur = conn.execute(
                "INSERT INTO agents(project_id, name, role, created_at, owner_user_id) VALUES(?,?,?,?,?)",
                (pid, name, body.role.strip(), time.time(), user["id"]))
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "That agent name is taken in this project")
        return {"id": cur.lastrowid, "name": name, "role": body.role.strip()}
    finally:
        conn.close()


@app.post("/api/projects/{pid}/agents/{aid}")
def update_agent(pid: int, aid: int, body: AgentIn, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        old = conn.execute("SELECT name FROM agents WHERE id=? AND project_id=? AND owner_user_id=?",
                           (aid, pid, user["id"])).fetchone()
        if not old:
            raise HTTPException(404, "Agent not found")
        name = (body.name or "").strip()
        if not name:
            raise HTTPException(400, "name is required")
        try:
            conn.execute("UPDATE agents SET name=?, role=? WHERE id=? AND project_id=?",
                         (name, body.role.strip(), aid, pid))
            # keep card assignments pointing at the renamed agent
            if name != old["name"]:
                conn.execute("UPDATE ticket_cards SET assigned_to=? WHERE project_id=? AND assigned_to=?",
                             (name, pid, old["name"]))
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(409, "An agent with that name already exists")
        return {"ok": True, "name": name}
    finally:
        conn.close()


@app.delete("/api/projects/{pid}/agents/{aid}")
def delete_agent(pid: int, aid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        conn.execute("DELETE FROM agents WHERE id=? AND project_id=? AND owner_user_id=?",
                     (aid, pid, user["id"]))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/projects/{pid}/cards/{cid}/assign")
def assign_card(pid: int, cid: int, body: AssignIn, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        _card_row(conn, pid, cid)
        conn.execute("UPDATE ticket_cards SET assigned_to=?, updated_at=? WHERE id=? AND project_id=?",
                     ((body.agent or None), time.time(), cid, pid))
        conn.commit()
        return {"ok": True, "assigned_to": body.agent or None}
    finally:
        conn.close()


class MetricsIn(BaseModel):
    agent: str
    tokens_in: int = 0
    tokens_out: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    last_tool: Optional[str] = None


@app.post("/api/ticket/{invite_code}/metrics")
def upsert_metrics_bus(invite_code: str, body: MetricsIn, request: Request):
    """Watcher posts rolled-up per-agent metrics + last tool (bus token)."""
    conn = db()
    try:
        pid = _bus_project(conn, request, invite_code)
        conn.execute(
            "INSERT INTO agent_metrics(project_id, agent, tokens_in, tokens_out, tool_calls, tool_errors, last_tool, updated_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id, agent) DO UPDATE SET "
            "tokens_in=agent_metrics.tokens_in+excluded.tokens_in, "
            "tokens_out=agent_metrics.tokens_out+excluded.tokens_out, "
            "tool_calls=agent_metrics.tool_calls+excluded.tool_calls, "
            "tool_errors=agent_metrics.tool_errors+excluded.tool_errors, "
            "last_tool=COALESCE(NULLIF(excluded.last_tool,''), agent_metrics.last_tool), "
            "updated_at=excluded.updated_at",
            (pid, body.agent, body.tokens_in, body.tokens_out, body.tool_calls,
             body.tool_errors, body.last_tool or "", time.time()))
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


STUCK_SECS = int(os.environ.get("ACTIVITY_STUCK_SECS", "600"))


@app.get("/api/projects/{pid}/interactions")
def project_interactions(pid: int, user=Depends(current_user)):
    """Agent-to-agent communication observability — who DM'd whom, and what.
    Zero-cost: the bus already stores every message; we just surface it."""
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        row = conn.execute("SELECT invite_code FROM projects WHERE id=?", (pid,)).fetchone()
        room = row["invite_code"] if row else None
        mine = {r["name"] for r in conn.execute(
            "SELECT name FROM agents WHERE project_id=? AND owner_user_id=?", (pid, user["id"])).fetchall()}
    finally:
        conn.close()
    if not room:
        return {"messages": [], "pairs": []}

    msgs = _bus_local_get(f"/history?room={room}").get("messages", [])
    directed = []
    for m in msgs:
        frm, to, text = m.get("from"), m.get("to"), (m.get("text") or "").strip()
        if not text or frm == "bus-server":     # drop presence/system noise
            continue
        if not to:                                # broadcasts belong in Activity
            continue
        # PRIVATE: only exchanges involving one of YOUR agents (the other side is
        # shown as a bare name, never their roster/state)
        if frm not in mine and to not in mine:
            continue
        directed.append({"from": frm, "to": to, "text": text, "ts": m.get("ts")})

    pairs = {}
    for m in directed:
        key = tuple(sorted([m["from"], m["to"]]))
        p = pairs.setdefault(key, {"a": key[0], "b": key[1], "count": 0, "last_text": "", "last_ts": None})
        p["count"] += 1
        if not p["last_ts"] or (m["ts"] or 0) > p["last_ts"]:
            p["last_ts"] = m["ts"]; p["last_text"] = m["text"][:140]
    pair_list = sorted(pairs.values(), key=lambda p: -(p["last_ts"] or 0))
    return {"messages": directed[-80:], "pairs": pair_list}


@app.get("/api/projects/{pid}/fleet")
def project_fleet(pid: int, user=Depends(current_user)):
    """The observability + assignment roster: one status object per agent."""
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        row = conn.execute("SELECT invite_code FROM projects WHERE id=?", (pid,)).fetchone()
        room = row["invite_code"] if row else None
        state = _ticket_state(conn, pid)
        # PRIVATE PER USER: only the caller's own agents are visible.
        roster = {r["name"]: dict(r) for r in conn.execute(
            "SELECT id, name, role FROM agents WHERE project_id=? AND owner_user_id=?",
            (pid, user["id"])).fetchall()}
        metrics = {r["agent"]: dict(r) for r in conn.execute(
            "SELECT * FROM agent_metrics WHERE project_id=?", (pid,)).fetchall()}
        sig_rows = conn.execute(
            "SELECT agent, kind, severity, text, ts FROM signals WHERE project_id=? ORDER BY id DESC LIMIT 200",
            (pid,)).fetchall()
        pending = conn.execute(
            "SELECT id, agent, question, options FROM decisions WHERE project_id=? AND status='pending' ORDER BY id",
            (pid,)).fetchall()
    finally:
        conn.close()

    now = time.time()
    who = _bus_local_get(f"/who?room={room}") if room else {}
    presence = who.get("presence", {})
    online = set(who.get("clients", []))

    # conflicts: agents whose live edits overlap
    conflicted = set()
    diff = _bus_local_get(f"/diff/state?project={room}").get("state", {}) if room else {}
    machines = list(diff.items())
    for i in range(len(machines)):
        for j in range(i + 1, len(machines)):
            m1, d1 = machines[i]; m2, d2 = machines[j]
            f1, f2 = d1.get("files", {}), d2.get("files", {})
            for path in set(f1) & set(f2):
                if any(_ranges_overlap(r1, r2) for r1 in f1[path] for r2 in f2[path]):
                    conflicted.update([m1, m2])

    # Files each agent is currently touching (from the peer-diff bus): agent ->
    # [{path, added, removed}], so a card can show live, uncommitted changes.
    # machine_by_agent lets the detail view fetch that agent's actual diffs.
    files_by_agent = {}
    machine_by_agent = {}
    for machine, d in diff.items():
        ag = d.get("agent") or machine
        perfile = d.get("perfile") or {}
        if not perfile:
            continue
        machine_by_agent[ag] = machine
        flist = files_by_agent.setdefault(ag, [])
        for path, info in sorted(perfile.items()):
            flist.append({"path": path,
                          "added": info.get("added", 0),
                          "removed": info.get("removed", 0)})

    tasks_by_agent = {a["agent"]: a for a in state.get("agents", [])}
    dec_by_agent = {}
    for p in pending:
        try:
            opts = json.loads(p["options"])
        except (ValueError, TypeError):
            opts = ["yes", "no"]
        dec_by_agent.setdefault(p["agent"], []).append(
            {"id": p["id"], "question": p["question"], "options": opts})
    sig_by_agent = {}
    for s in sig_rows:
        sig_by_agent.setdefault(s["agent"], []).append(dict(s))

    # Fleet-visible agents = your own roster PLUS any agent currently live on the
    # project bus (auto-discovery for online agents) — otherwise the "N online"
    # count has no cards behind it, and the narration log stays empty. Discovered
    # agents have no roster id, so the card offers "+ Add to roster". Offline
    # agents show only if they're yours.
    names = set(roster) | {c for c in online if c}
    names.discard(None); names.discard("")

    # flat narration log (newest first) — signals + claims/pushes from any agent
    # visible in the fleet (your roster + everyone live on the bus).
    log = [dict(s) for s in sig_rows if s["agent"] in names]
    if room:
        for m in _bus_local_get(f"/history?room={room}").get("messages", []):
            if m.get("to") or (m.get("from") not in names):   # broadcasts by fleet agents
                continue
            text = (m.get("text") or "").strip()
            up = text.upper()
            if up.startswith("CLAIM"):
                log.append({"agent": m["from"], "kind": "claim", "severity": "low", "text": text[:200], "ts": m.get("ts")})
            elif up.startswith("PUSHED") or up.startswith("PUSH "):
                log.append({"agent": m["from"], "kind": "push", "severity": "low", "text": text[:200], "ts": m.get("ts")})
    log.sort(key=lambda e: -(e.get("ts") or 0))
    log = log[:30]

    agents = []
    needs_you = 0
    for name in sorted(names):
        pres = presence.get(name)
        if name in online:
            live = "stale" if (pres and pres.get("stale")) else "online"
        else:
            live = "offline"
        t = tasks_by_agent.get(name)
        tasks = (t or {}).get("tasks", [])
        done = sum(1 for x in tasks if x.get("status") == "done")
        doing = next((x.get("text") for x in tasks if x.get("status") == "doing"), None)
        last_task_ts = (t or {}).get("ts")
        unfinished = any(x.get("status") != "done" for x in tasks)
        queue = [c for c in state["cards"] if c.get("assigned_to") == name and c["status"] != "done"]
        sigs = sig_by_agent.get(name, [])[:6]
        decs = dec_by_agent.get(name, [])
        ndec = len(decs)
        mrow = metrics.get(name) or {}

        # health, by priority
        if name in conflicted:
            health = "blocked"
        elif ndec:
            health = "needs_decision"
        elif live == "online" and unfinished and last_task_ts and (now - last_task_ts) > STUCK_SECS:
            health = "stuck"
        elif live == "offline":
            health = "offline"
        elif doing:
            health = "working"
        else:
            health = "idle"
        if health in ("blocked", "needs_decision", "stuck"):
            needs_you += 1

        agents.append({
            "name": name, "role": (roster.get(name) or {}).get("role", ""),
            "id": (roster.get(name) or {}).get("id"),
            "planned": name not in online and name in roster,
            "live": live, "health": health,
            "current": doing, "tool": mrow.get("last_tool") or None,
            "files": files_by_agent.get(name, []),
            "machine": machine_by_agent.get(name),
            "tasks_done": done, "tasks_total": len(tasks),
            "queue": [{"id": c["id"], "title": c["title"], "status": c["status"]} for c in queue],
            "decisions": decs,
            "signals": sigs,
            "metrics": metrics.get(name),
        })

    unassigned = [c for c in state["cards"] if not c.get("assigned_to") and c["status"] != "done"]
    working = sum(1 for a in agents if a["health"] == "working")
    return {
        "agents": agents,
        "unassigned": [{"id": c["id"], "title": c["title"], "status": c["status"]} for c in unassigned],
        "log": log,
        "needs_you": needs_you,
        "online": len(online),
        "working": working,
    }


@app.post("/api/projects/{pid}/ticket/ticket")
def set_ticket_ticket(pid: int, body: TicketIn, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        conn.execute(
            "INSERT INTO tickets(project_id, body, set_by, ts) VALUES(?,?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET body=excluded.body, "
            "set_by=excluded.set_by, ts=excluded.ts",
            (pid, body.body, user["name"], time.time()),
        )
        conn.commit()
        return _ticket_state(conn, pid)
    finally:
        conn.close()


class AgentTasksIn(BaseModel):
    agent: str
    tasks: list


class AgentTicketIn(BaseModel):
    agent: str
    body: str


@app.get("/api/ticket/{invite_code}")
def get_ticket_bus(invite_code: str, request: Request):
    conn = db()
    try:
        pid = _bus_project(conn, request, invite_code)
        return _ticket_state(conn, pid)
    finally:
        conn.close()


@app.post("/api/ticket/{invite_code}/tasks")
def set_ticket_tasks_bus(invite_code: str, body: AgentTasksIn, request: Request):
    conn = db()
    try:
        pid = _bus_project(conn, request, invite_code)
        if not body.agent:
            raise HTTPException(400, "agent is required")
        tasks_json = _clean_tasks(body.tasks)
        conn.execute(
            "INSERT INTO agent_tasks(project_id, agent, tasks, ts) VALUES(?,?,?,?) "
            "ON CONFLICT(project_id, agent) DO UPDATE SET tasks=excluded.tasks, "
            "ts=excluded.ts",
            (pid, body.agent, tasks_json, time.time()),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


@app.post("/api/ticket/{invite_code}/ticket")
def set_ticket_ticket_bus(invite_code: str, body: AgentTicketIn, request: Request):
    conn = db()
    try:
        pid = _bus_project(conn, request, invite_code)
        if not body.agent:
            raise HTTPException(400, "agent is required")
        conn.execute(
            "INSERT INTO tickets(project_id, body, set_by, ts) VALUES(?,?,?,?) "
            "ON CONFLICT(project_id) DO UPDATE SET body=excluded.body, "
            "set_by=excluded.set_by, ts=excluded.ts",
            (pid, body.body, body.agent, time.time()),
        )
        conn.commit()
        return {"ok": True}
    finally:
        conn.close()


# ---------- Ticket cards (Jira-like board) ----------

class CardCreate(BaseModel):
    title: str
    body: str = ""


class CardPatch(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None


class AgentCardCreate(BaseModel):
    agent: str
    title: str
    body: str = ""


class AgentCardPatch(BaseModel):
    agent: str
    status: Optional[str] = None
    title: Optional[str] = None
    body: Optional[str] = None


# -- human (member auth): full control, including moving a card to Done --
@app.post("/api/projects/{pid}/cards")
def create_card(pid: int, body: CardCreate, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        _create_card(conn, pid, body.title, body.body, user["name"])
        return _ticket_state(conn, pid)
    finally:
        conn.close()


@app.patch("/api/projects/{pid}/cards/{cid}")
def update_card(pid: int, cid: int, body: CardPatch, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        _apply_card_update(conn, pid, cid, body.dict(exclude_unset=True), user["name"], allow_done=True)
        return _ticket_state(conn, pid)
    finally:
        conn.close()


@app.delete("/api/projects/{pid}/cards/{cid}")
def delete_card(pid: int, cid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        _card_row(conn, pid, cid)
        conn.execute("DELETE FROM ticket_cards WHERE id=? AND project_id=?", (cid, pid))
        conn.commit()
        return _ticket_state(conn, pid)
    finally:
        conn.close()


# -- agent (bus token): create + move To Do/In Progress, but NOT to Done --
@app.post("/api/ticket/{invite_code}/cards")
def create_card_bus(invite_code: str, body: AgentCardCreate, request: Request):
    conn = db()
    try:
        pid = _bus_project(conn, request, invite_code)
        if not body.agent:
            raise HTTPException(400, "agent is required")
        _create_card(conn, pid, body.title, body.body, body.agent)
        return {"ok": True}
    finally:
        conn.close()


@app.patch("/api/ticket/{invite_code}/cards/{cid}")
def update_card_bus(invite_code: str, cid: int, body: AgentCardPatch, request: Request):
    conn = db()
    try:
        pid = _bus_project(conn, request, invite_code)
        if not body.agent:
            raise HTTPException(400, "agent is required")
        _apply_card_update(conn, pid, cid,
                           body.dict(exclude_unset=True, exclude={"agent"}),
                           body.agent, allow_done=False)
        return {"ok": True}
    finally:
        conn.close()


# ---------- Jira integration (Phase 1: identity + project link) ----------
# A user connects their Atlassian account with an API token (email + token, the
# fast path) or "Continue with Atlassian" OAuth. Then a manager links a Jira
# Cloud project to this platform project. Reads come in Phase 2.

JIRA_OAUTH_CLIENT_ID = os.environ.get("JIRA_CLIENT_ID", "").strip()
JIRA_OAUTH_CLIENT_SECRET = os.environ.get("JIRA_CLIENT_SECRET", "").strip()
JIRA_OAUTH_CONFIGURED = bool(JIRA_OAUTH_CLIENT_ID and JIRA_OAUTH_CLIENT_SECRET)
JIRA_SCOPES = "read:jira-work read:jira-user"


def _jira_base_headers(row, token: str):
    """(base_url, headers) for a Jira identity row — Basic for token auth,
    Bearer against api.atlassian.com for OAuth."""
    if (row["auth_kind"] or "token") == "oauth":
        base = f"https://api.atlassian.com/ex/jira/{row['cloud_id']}/rest/api/3"
        headers = {"Authorization": f"Bearer {token}"}
    else:
        import base64
        basic = base64.b64encode(f"{row['email']}:{token}".encode()).decode()
        base = f"https://{row['site']}/rest/api/3"
        headers = {"Authorization": f"Basic {basic}"}
    headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    return base, headers


def _jira_api(row, token: str, method: str, path: str, body=None, timeout: int = 15):
    """Call the Jira Cloud REST API v3. Returns (status, json, headers)."""
    import requests

    base, headers = _jira_base_headers(row, token)
    url = path if path.startswith("http") else f"{base}{path}"
    resp = requests.request(method, url, headers=headers, json=body, timeout=timeout)
    try:
        data = resp.json()
    except ValueError:
        data = {}
    return resp.status_code, data, resp.headers


def _jira_identity(conn, user_id: int):
    return conn.execute("SELECT * FROM jira_identities WHERE user_id=?", (user_id,)).fetchone()


def _jira_link_row(conn, pid: int):
    return conn.execute("SELECT * FROM jira_links WHERE project_id=?", (pid,)).fetchone()


class JiraConnectIn(BaseModel):
    site: str
    email: str
    token: str


@app.post("/api/jira/connect")
def jira_connect(body: JiraConnectIn, user=Depends(current_user)):
    """Validate an Atlassian API token (email + token against a site) and store it sealed."""
    site = body.site.strip().replace("https://", "").replace("http://", "").strip("/")
    email, token = body.email.strip(), body.token.strip()
    if not (site and email and token):
        raise HTTPException(400, "site, email and token are required")
    probe = {"auth_kind": "token", "site": site, "email": email, "cloud_id": None}
    try:
        status, data, _ = _jira_api(probe, token, "GET", "/myself")
    except Exception as e:
        raise HTTPException(502, f"Could not reach Jira ({site}): {e}")
    if status in (401, 403):
        raise HTTPException(401, "Jira rejected the token (check email + token + site)")
    if status != 200:
        raise HTTPException(400, f"Jira error ({status}): {data.get('message', 'unknown') if isinstance(data, dict) else 'unknown'}")
    conn = db()
    try:
        conn.execute(
            """INSERT INTO jira_identities(user_id, site, cloud_id, account_id, email, display_name, token_enc, auth_kind, connected_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 site=excluded.site, cloud_id=excluded.cloud_id, account_id=excluded.account_id,
                 email=excluded.email, display_name=excluded.display_name, token_enc=excluded.token_enc,
                 auth_kind=excluded.auth_kind, connected_at=excluded.connected_at""",
            (user["id"], site, None, data.get("accountId"), email,
             data.get("displayName"), _seal(token), "token", time.time()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"connected": True, "account": data.get("displayName") or email, "site": site,
            "auth_kind": "token", "encrypted": TOKENS_ENCRYPTED}


@app.get("/api/jira/status")
def jira_status(user=Depends(current_user)):
    conn = db()
    try:
        row = _jira_identity(conn, user["id"])
    finally:
        conn.close()
    availability = {"encrypted": TOKENS_ENCRYPTED, "oauth_available": JIRA_OAUTH_CONFIGURED}
    if not row:
        return {"connected": False, **availability}
    return {
        "connected": True,
        "account": row["display_name"] or row["email"],
        "account_id": row["account_id"],
        "site": row["site"],
        "auth_kind": row["auth_kind"],
        "connected_at": row["connected_at"],
        **availability,
    }


@app.delete("/api/jira/disconnect")
def jira_disconnect(user=Depends(current_user)):
    conn = db()
    try:
        conn.execute("DELETE FROM jira_identities WHERE user_id=?", (user["id"],))
        conn.commit()
    finally:
        conn.close()
    return {"connected": False}


class JiraLinkIn(BaseModel):
    project_key: str


@app.post("/api/projects/{pid}/jira/link")
def jira_link_project(pid: int, body: JiraLinkIn, user=Depends(current_user)):
    """Link a Jira Cloud project (by key) to this project — managers only,
    validated against the linker's Jira account."""
    conn = db()
    try:
        require_manage(conn, pid, user["id"])
        idrow = _jira_identity(conn, user["id"])
        if not idrow:
            raise HTTPException(400, "Connect your Jira account first")
        key = body.project_key.strip().upper()
        if not key:
            raise HTTPException(400, "project_key is required")
        token = _unseal(idrow["token_enc"])
        try:
            status, data, _ = _jira_api(idrow, token, "GET", f"/project/{key}")
        except Exception as e:
            raise HTTPException(502, f"Could not reach Jira: {e}")
        if status == 404:
            raise HTTPException(404, f"Jira project {key} not found or not accessible with your account")
        if status != 200:
            raise HTTPException(400, f"Jira error ({status}): {data.get('message', 'unknown') if isinstance(data, dict) else 'unknown'}")
        conn.execute(
            """INSERT INTO jira_links(project_id, site, cloud_id, project_key, project_name, linked_by, linked_at)
               VALUES(?,?,?,?,?,?,?)
               ON CONFLICT(project_id) DO UPDATE SET
                 site=excluded.site, cloud_id=excluded.cloud_id, project_key=excluded.project_key,
                 project_name=excluded.project_name, linked_by=excluded.linked_by, linked_at=excluded.linked_at""",
            (pid, idrow["site"], idrow["cloud_id"], data.get("key", key),
             data.get("name"), user["id"], time.time()),
        )
        conn.commit()
        return {
            "linked": True, "project_key": data.get("key", key), "project_name": data.get("name"),
            "site": idrow["site"], "url": f"https://{idrow['site']}/browse/{key}",
        }
    finally:
        conn.close()


@app.get("/api/projects/{pid}/jira")
def jira_get_link(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        row = _jira_link_row(conn, pid)
        if not row:
            return {"linked": False}
        return {
            "linked": True, "project_key": row["project_key"], "project_name": row["project_name"],
            "site": row["site"], "url": f"https://{row['site']}/browse/{row['project_key']}",
            "linked_at": row["linked_at"],
        }
    finally:
        conn.close()


@app.delete("/api/projects/{pid}/jira/link")
def jira_unlink_project(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_manage(conn, pid, user["id"])
        conn.execute("DELETE FROM jira_links WHERE project_id=?", (pid,))
        conn.commit()
        return {"linked": False}
    finally:
        conn.close()


# ---- Jira read (Phase 2): mirror issues into the board ----

# Jira statusCategory.key -> our board column category.
_JIRA_CAT = {"new": "todo", "indeterminate": "doing", "done": "done"}
_JIRA_COL_ORDER = {"todo": 0, "doing": 1, "done": 2}
# Story-points custom field (varies per Jira site; overridable). customfield_10016
# is the Cloud default. Epic comes from the issue's `parent`.
JIRA_POINTS_FIELD = os.environ.get("JIRA_POINTS_FIELD", "customfield_10016").strip()


def _project_jira_ctx(conn, pid: int, requester_id: int):
    """(link_row, identity_row, token) for reads — the linker's Jira account, else the requester's."""
    link = _jira_link_row(conn, pid)
    if not link:
        raise HTTPException(400, "No Jira project linked to this project")
    for uid in (link["linked_by"], requester_id):
        if uid is None:
            continue
        idrow = _jira_identity(conn, uid)
        if idrow:
            return link, idrow, _unseal(idrow["token_enc"])
    raise HTTPException(400, "No Jira token available for this project; a manager must reconnect Jira")


def _jira_search(idrow, token, jql: str, fields: str, max_results: int = 100):
    """Run a JQL search, tolerating both the new (/search/jql) and legacy (/search) endpoints."""
    from urllib.parse import quote
    q = quote(jql)
    for path in (f"/search/jql?jql={q}&maxResults={max_results}&fields={fields}",
                 f"/search?jql={q}&maxResults={max_results}&fields={fields}"):
        try:
            status, data, _ = _jira_api(idrow, token, "GET", path)
        except Exception as e:
            raise HTTPException(502, f"Could not reach Jira: {e}")
        if status in (404, 410):
            continue  # endpoint not available on this deployment -> try the other
        if status in (401, 403):
            raise HTTPException(401, "Jira token invalid or lacks access")
        if status != 200:
            msg = data.get("message") or (data.get("errorMessages") or ["unknown"])[0] if isinstance(data, dict) else "unknown"
            raise HTTPException(400, f"Jira error ({status}): {msg}")
        return data
    raise HTTPException(400, "Jira search endpoint not available")


@app.post("/api/projects/{pid}/jira/sync")
def jira_sync(pid: int, user=Depends(current_user)):
    """Mirror the linked Jira project's issues INTO ticket_cards (source='jira'),
    mapping Jira statusCategory -> todo|doing|done. Member-auth (a human/system
    path), so the done-mapping never hits the agent-only-Done guard. Upserts by
    external_id (the Jira key) and prunes jira cards whose issue is gone."""
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        link, idrow, token = _project_jira_ctx(conn, pid, user["id"])
        jql = f'project = "{link["project_key"]}" ORDER BY updated DESC'
        fields = f"summary,status,issuetype,priority,assignee,labels,updated,parent,{JIRA_POINTS_FIELD}"
        data = _jira_search(idrow, token, jql, fields)
        who = "jira"
        now = time.time()
        seen_keys, created, updated = [], 0, 0
        for it in data.get("issues", []):
            key = it.get("key")
            if not key:
                continue
            seen_keys.append(key)
            f = it.get("fields", {}) or {}
            st = f.get("status") or {}
            status = _JIRA_CAT.get(((st.get("statusCategory") or {}).get("key")), "todo")
            title = f.get("summary", "")
            parent = f.get("parent") or {}
            points = f.get(JIRA_POINTS_FIELD)
            meta = json.dumps({
                "type": (f.get("issuetype") or {}).get("name", ""),
                "priority": (f.get("priority") or {}).get("name", ""),
                "assignee": (f.get("assignee") or {}).get("displayName"),
                "labels": f.get("labels", []) or [],
                "jira_status": st.get("name", ""),
                "points": points if isinstance(points, (int, float)) else None,
                "epic_key": parent.get("key"),
                "epic_name": (parent.get("fields") or {}).get("summary"),
            })
            url = f"https://{link['site']}/browse/{key}"
            existing = conn.execute(
                "SELECT id FROM ticket_cards WHERE project_id=? AND source='jira' AND external_id=?",
                (pid, key),
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE ticket_cards SET title=?, status=?, meta=?, external_url=?, updated_by=?, updated_at=? WHERE id=?",
                    (title, status, meta, url, who, now, existing["id"]),
                )
                updated += 1
            else:
                conn.execute(
                    "INSERT INTO ticket_cards(project_id, title, body, status, created_by, updated_by, "
                    "created_at, updated_at, source, external_id, external_url, meta) "
                    "VALUES(?,?,?,?,?,?,?,?,'jira',?,?,?)",
                    (pid, title, "", status, who, who, now, now, key, url, meta),
                )
                created += 1
        if seen_keys:
            ph = ",".join("?" * len(seen_keys))
            cur = conn.execute(
                f"DELETE FROM ticket_cards WHERE project_id=? AND source='jira' AND external_id NOT IN ({ph})",
                (pid, *seen_keys),
            )
        else:
            cur = conn.execute("DELETE FROM ticket_cards WHERE project_id=? AND source='jira'", (pid,))
        conn.commit()
        return {"synced": len(seen_keys), "created": created, "updated": updated,
                "removed": cur.rowcount, "at": now, "project_key": link["project_key"]}
    finally:
        conn.close()


@app.get("/api/projects/{pid}/jira/sprint")
def jira_sprint(pid: int, user=Depends(current_user)):
    """Sprint/progress summary + a burndown series, computed from the mirrored Jira
    cards (points from meta, completion inferred from a done card's updated_at).
    No live-Jira call — works whenever issues have been synced into the board."""
    import datetime
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        rows = conn.execute(
            "SELECT status, updated_at, meta FROM ticket_cards WHERE project_id=? AND source='jira'", (pid,)
        ).fetchall()
        pts_by = {"todo": 0.0, "doing": 0.0, "done": 0.0}   # story points per status
        counts = {"todo": 0, "doing": 0, "done": 0}          # issue counts per status
        events = []  # (updated_at, points, 1) for done cards
        for r in rows:
            try:
                m = json.loads(r["meta"]) if r["meta"] else {}
            except (ValueError, TypeError):
                m = {}
            pts = m.get("points")
            pts = pts if isinstance(pts, (int, float)) else 0
            st = r["status"] if r["status"] in pts_by else "todo"
            pts_by[st] += pts
            counts[st] += 1
            if st == "done":
                events.append((r["updated_at"] or time.time(), pts, 1))
        # Measure in story points when any exist; otherwise fall back to issue count
        # so a board with no estimates still shows meaningful Done/Remaining.
        total_pts = sum(pts_by.values())
        unit = "points" if total_pts > 0 else "issues"
        if unit == "points":
            by_status = {k: round(v, 1) for k, v in pts_by.items()}
            done_events = [(ts, p) for ts, p, _ in events]
            scope = round(total_pts, 1)
        else:
            by_status = {k: float(v) for k, v in counts.items()}
            done_events = [(ts, 1) for ts, _, _ in events]
            scope = float(len(rows))
        completed = round(by_status["done"], 1)
        remaining = round(scope - completed, 1)
        # 14-day burndown ending today: ideal is linear scope->0; remaining subtracts
        # points of cards done on/before each day.
        DAYS = 14
        today = datetime.date.today()
        start = today - datetime.timedelta(days=DAYS - 1)
        burndown = []
        for i in range(DAYS):
            d = start + datetime.timedelta(days=i)
            end_ts = time.mktime((d + datetime.timedelta(days=1)).timetuple())
            done_by = sum(pts for ts, pts in done_events if ts <= end_ts)
            burndown.append({
                "day": d.isoformat(),
                "ideal": round(scope * (1 - i / (DAYS - 1)), 2) if DAYS > 1 else 0,
                "remaining": round(scope - done_by, 2),
            })
        return {
            "unit": unit,
            "scope": scope, "completed": completed, "remaining": remaining,
            "by_status": {k: round(v, 1) for k, v in by_status.items()},
            "counts": counts, "total": len(rows), "burndown": burndown, "window_days": DAYS,
        }
    finally:
        conn.close()


# ---- Jira write (Phase 3): create an issue, transition status back to Jira ----

def _adf(text: str) -> dict:
    """Minimal Atlassian Document Format doc for a plain-text description (v3 API)."""
    return {"type": "doc", "version": 1,
            "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def _mirror_jira_card(conn, pid, link, key, status, title, meta_extra=None):
    """Upsert a single Jira issue into ticket_cards as a source='jira' card."""
    now = time.time()
    meta = json.dumps({"type": "", "priority": "", "assignee": None, "labels": [],
                       "jira_status": "", **(meta_extra or {})})
    url = f"https://{link['site']}/browse/{key}"
    existing = conn.execute(
        "SELECT id FROM ticket_cards WHERE project_id=? AND source='jira' AND external_id=?",
        (pid, key),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE ticket_cards SET title=?, status=?, meta=?, external_url=?, updated_by='jira', updated_at=? WHERE id=?",
            (title, status, meta, url, now, existing["id"]),
        )
    else:
        conn.execute(
            "INSERT INTO ticket_cards(project_id, title, body, status, created_by, updated_by, "
            "created_at, updated_at, source, external_id, external_url, meta) "
            "VALUES(?,?,?,?,?,?,?,?,'jira',?,?,?)",
            (pid, title, "", status, "jira", "jira", now, now, key, url, meta),
        )


class JiraIssueCreateIn(BaseModel):
    summary: str
    issue_type: str = "Task"
    description: str = ""


@app.post("/api/projects/{pid}/jira/issues")
def jira_create_issue(pid: int, body: JiraIssueCreateIn, user=Depends(current_user)):
    """Create a Jira issue in the linked project and mirror it into the board."""
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        link, idrow, token = _project_jira_ctx(conn, pid, user["id"])
        summary = body.summary.strip()
        if not summary:
            raise HTTPException(400, "summary is required")
        itype = body.issue_type.strip() or "Task"
        fields = {"project": {"key": link["project_key"]}, "summary": summary,
                  "issuetype": {"name": itype}}
        if body.description.strip():
            fields["description"] = _adf(body.description.strip())
        try:
            status, data, _ = _jira_api(idrow, token, "POST", "/issue", body={"fields": fields})
        except Exception as e:
            raise HTTPException(502, f"Could not reach Jira: {e}")
        if status in (401, 403):
            raise HTTPException(401, "Jira token invalid or lacks create permission")
        if status not in (200, 201):
            errs = data.get("errors") if isinstance(data, dict) else None
            msg = "; ".join(f"{k}: {v}" for k, v in errs.items()) if errs else (
                data.get("errorMessages", ["unknown"])[0] if isinstance(data, dict) else "unknown")
            raise HTTPException(400, f"Jira rejected the issue: {msg}")
        key = data.get("key")
        _mirror_jira_card(conn, pid, link, key, "todo", summary,
                          {"type": itype, "jira_status": "To Do"})
        conn.commit()
        return {"created": True, "key": key, "url": f"https://{link['site']}/browse/{key}"}
    finally:
        conn.close()


class JiraTransitionIn(BaseModel):
    to: str  # todo | doing | done


@app.post("/api/projects/{pid}/jira/issues/{key}/transition")
def jira_transition(pid: int, key: str, body: JiraTransitionIn, user=Depends(current_user)):
    """Move a Jira issue to the target column by running the matching Jira workflow
    transition, then update the mirrored card. Member-auth (human/system path)."""
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        target = body.to
        if target not in ("todo", "doing", "done"):
            raise HTTPException(400, "to must be todo|doing|done")
        link, idrow, token = _project_jira_ctx(conn, pid, user["id"])
        try:
            st, data, _ = _jira_api(idrow, token, "GET", f"/issue/{key}/transitions")
        except Exception as e:
            raise HTTPException(502, f"Could not reach Jira: {e}")
        if st != 200:
            raise HTTPException(400, f"Could not list Jira transitions for {key}")
        wanted = None
        for tr in data.get("transitions", []):
            cat = _JIRA_CAT.get((((tr.get("to") or {}).get("statusCategory") or {}).get("key")))
            if cat == target:
                wanted = tr
                break
        if not wanted:
            raise HTTPException(400, f"No Jira transition to '{target}' is available for {key}")
        st2, d2, _ = _jira_api(idrow, token, "POST", f"/issue/{key}/transitions",
                               body={"transition": {"id": wanted["id"]}})
        if st2 not in (200, 204):
            msg = d2.get("errorMessages", ["transition failed"])[0] if isinstance(d2, dict) else "transition failed"
            raise HTTPException(400, f"Jira rejected the transition: {msg}")
        # Reflect it on the mirrored card immediately (next sync will confirm).
        row = conn.execute(
            "SELECT id, meta FROM ticket_cards WHERE project_id=? AND source='jira' AND external_id=?",
            (pid, key),
        ).fetchone()
        if row:
            try:
                m = json.loads(row["meta"]) if row["meta"] else {}
            except (ValueError, TypeError):
                m = {}
            m["jira_status"] = (wanted.get("to") or {}).get("name", "")
            conn.execute(
                "UPDATE ticket_cards SET status=?, meta=?, updated_by='jira', updated_at=? WHERE id=?",
                (target, json.dumps(m), time.time(), row["id"]),
            )
            conn.commit()
        return {"ok": True, "key": key, "status": target,
                "jira_status": (wanted.get("to") or {}).get("name")}
    finally:
        conn.close()


# ---- Jira, agent/bus-token path (Phase 4b): agents drive Jira, human-only-Done ----
# Same create/transition capability for agents, authorized by the shared bus token
# (like the /api/ticket/{invite_code}/cards endpoints). The one hard rule: an agent
# may move an issue To Do <-> In Progress but NEVER to Done — only a human can.

def _do_jira_create(conn, pid, agent, summary, itype, description):
    link, idrow, token = _project_jira_ctx(conn, pid, None)  # None -> use the linker's token
    summary = (summary or "").strip()
    if not summary:
        raise HTTPException(400, "summary is required")
    itype = (itype or "Task").strip() or "Task"
    fields = {"project": {"key": link["project_key"]}, "summary": summary, "issuetype": {"name": itype}}
    if (description or "").strip():
        fields["description"] = _adf(description.strip())
    try:
        status, data, _ = _jira_api(idrow, token, "POST", "/issue", body={"fields": fields})
    except Exception as e:
        raise HTTPException(502, f"Could not reach Jira: {e}")
    if status not in (200, 201):
        errs = data.get("errors") if isinstance(data, dict) else None
        msg = "; ".join(f"{k}: {v}" for k, v in errs.items()) if errs else (
            data.get("errorMessages", ["unknown"])[0] if isinstance(data, dict) else "unknown")
        raise HTTPException(400, f"Jira rejected the issue: {msg}")
    key = data.get("key")
    _mirror_jira_card(conn, pid, link, key, "todo", summary, {"type": itype, "jira_status": "To Do"})
    conn.commit()
    return {"created": True, "key": key, "url": f"https://{link['site']}/browse/{key}", "by": agent}


class AgentJiraCreate(BaseModel):
    agent: str
    summary: str
    issue_type: str = "Task"
    description: str = ""


class AgentJiraTransition(BaseModel):
    agent: str
    to: str  # todo | doing  (NOT done — human-only)


@app.post("/api/ticket/{invite_code}/jira/issues")
def jira_create_issue_bus(invite_code: str, body: AgentJiraCreate, request: Request):
    conn = db()
    try:
        pid = _bus_project(conn, request, invite_code)
        if not body.agent:
            raise HTTPException(400, "agent is required")
        return _do_jira_create(conn, pid, body.agent, body.summary, body.issue_type, body.description)
    finally:
        conn.close()


@app.post("/api/ticket/{invite_code}/jira/issues/{key}/transition")
def jira_transition_bus(invite_code: str, key: str, body: AgentJiraTransition, request: Request):
    conn = db()
    try:
        pid = _bus_project(conn, request, invite_code)
        if not body.agent:
            raise HTTPException(400, "agent is required")
        target = body.to
        if target == "done":
            raise HTTPException(403, "only a human can move a Jira issue to Done")
        if target not in ("todo", "doing"):
            raise HTTPException(400, "to must be todo|doing (agents cannot set done)")
        link, idrow, token = _project_jira_ctx(conn, pid, None)
        try:
            st, data, _ = _jira_api(idrow, token, "GET", f"/issue/{key}/transitions")
        except Exception as e:
            raise HTTPException(502, f"Could not reach Jira: {e}")
        if st != 200:
            raise HTTPException(400, f"Could not list Jira transitions for {key}")
        wanted = None
        for tr in data.get("transitions", []):
            cat = _JIRA_CAT.get((((tr.get("to") or {}).get("statusCategory") or {}).get("key")))
            if cat == target:
                wanted = tr
                break
        if not wanted:
            raise HTTPException(400, f"No Jira transition to '{target}' is available for {key}")
        st2, d2, _ = _jira_api(idrow, token, "POST", f"/issue/{key}/transitions",
                               body={"transition": {"id": wanted["id"]}})
        if st2 not in (200, 204):
            raise HTTPException(400, "Jira rejected the transition")
        row = conn.execute(
            "SELECT id, meta FROM ticket_cards WHERE project_id=? AND source='jira' AND external_id=?",
            (pid, key),
        ).fetchone()
        if row:
            try:
                m = json.loads(row["meta"]) if row["meta"] else {}
            except (ValueError, TypeError):
                m = {}
            m["jira_status"] = (wanted.get("to") or {}).get("name", "")
            conn.execute(
                "UPDATE ticket_cards SET status=?, meta=?, updated_by=?, updated_at=? WHERE id=?",
                (target, json.dumps(m), f"agent:{body.agent}", time.time(), row["id"]),
            )
            conn.commit()
        return {"ok": True, "key": key, "status": target, "by": body.agent}
    finally:
        conn.close()


# ---- Jira comments + inline edit (Phase 4c) ----

def _adf_to_text(node) -> str:
    """Flatten an Atlassian Document Format node to plain text."""
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    text = "".join(_adf_to_text(ch) for ch in (node.get("content") or []))
    if node.get("type") in ("paragraph", "heading"):
        return text + "\n"
    return text


@app.get("/api/projects/{pid}/jira/issues/{key}/comments")
def jira_comments(pid: int, key: str, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        _link, idrow, token = _project_jira_ctx(conn, pid, user["id"])
        try:
            st, data, _ = _jira_api(idrow, token, "GET", f"/issue/{key}/comment?maxResults=50&orderBy=created")
        except Exception as e:
            raise HTTPException(502, f"Could not reach Jira: {e}")
        if st != 200:
            raise HTTPException(400, f"Could not load comments for {key}")
        comments = [{
            "author": (c.get("author") or {}).get("displayName", "?"),
            "body": _adf_to_text(c.get("body")).strip(),
            "created": c.get("created"),
        } for c in data.get("comments", [])]
        return {"key": key, "comments": comments}
    finally:
        conn.close()


class JiraCommentIn(BaseModel):
    body: str


@app.post("/api/projects/{pid}/jira/issues/{key}/comments")
def jira_add_comment(pid: int, key: str, body: JiraCommentIn, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        text = body.body.strip()
        if not text:
            raise HTTPException(400, "comment body is required")
        _link, idrow, token = _project_jira_ctx(conn, pid, user["id"])
        st, data, _ = _jira_api(idrow, token, "POST", f"/issue/{key}/comment", body={"body": _adf(text)})
        if st not in (200, 201):
            raise HTTPException(400, f"Jira rejected the comment ({st})")
        return {"ok": True, "id": data.get("id")}
    finally:
        conn.close()


@app.get("/api/projects/{pid}/jira/assignable")
def jira_assignable(pid: int, user=Depends(current_user)):
    """Users assignable on the linked Jira project (for the assignee editor)."""
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        link, idrow, token = _project_jira_ctx(conn, pid, user["id"])
        try:
            st, data, _ = _jira_api(idrow, token, "GET",
                                    f"/user/assignable/search?project={link['project_key']}&maxResults=50")
        except Exception as e:
            raise HTTPException(502, f"Could not reach Jira: {e}")
        if st != 200:
            raise HTTPException(400, "Could not list assignable users")
        users = [{"account_id": u.get("accountId"), "name": u.get("displayName", "?")}
                 for u in (data if isinstance(data, list) else []) if u.get("accountId")]
        return {"users": users}
    finally:
        conn.close()


class JiraEditIn(BaseModel):
    assignee_account_id: Optional[str] = None  # provide (possibly "") to change; "" = unassign
    assignee_name: Optional[str] = None
    priority: Optional[str] = None


@app.patch("/api/projects/{pid}/jira/issues/{key}")
def jira_edit_issue(pid: int, key: str, body: JiraEditIn, user=Depends(current_user)):
    """Edit a Jira issue's assignee and/or priority, then update the mirror card."""
    conn = db()
    try:
        require_member(conn, pid, user["id"])
        provided = body.dict(exclude_unset=True)
        fields = {}
        if "assignee_account_id" in provided:
            fields["assignee"] = {"accountId": body.assignee_account_id} if body.assignee_account_id else None
        if "priority" in provided and body.priority:
            fields["priority"] = {"name": body.priority}
        if not fields:
            raise HTTPException(400, "nothing to update")
        _link, idrow, token = _project_jira_ctx(conn, pid, user["id"])
        st, data, _ = _jira_api(idrow, token, "PUT", f"/issue/{key}", body={"fields": fields})
        if st not in (200, 204):
            errs = data.get("errors") if isinstance(data, dict) else None
            msg = "; ".join(f"{k}: {v}" for k, v in errs.items()) if errs else f"Jira error {st}"
            raise HTTPException(400, msg)
        # Reflect on the mirror card.
        row = conn.execute(
            "SELECT id, meta FROM ticket_cards WHERE project_id=? AND source='jira' AND external_id=?",
            (pid, key),
        ).fetchone()
        if row:
            try:
                m = json.loads(row["meta"]) if row["meta"] else {}
            except (ValueError, TypeError):
                m = {}
            if "assignee_account_id" in provided:
                m["assignee"] = body.assignee_name or None
            if "priority" in provided and body.priority:
                m["priority"] = body.priority
            conn.execute("UPDATE ticket_cards SET meta=?, updated_at=? WHERE id=?",
                         (json.dumps(m), time.time(), row["id"]))
            conn.commit()
        return {"ok": True, "key": key}
    finally:
        conn.close()


# ---- Jira OAuth ("Continue with Atlassian"); inert until JIRA_CLIENT_ID/SECRET set ----

def _jira_redirect_uri() -> str:
    return f"{_public_base()}/api/jira/oauth/callback"


@app.get("/api/jira/oauth/config")
def jira_oauth_config():
    return {"configured": JIRA_OAUTH_CONFIGURED}


@app.post("/api/jira/oauth/start")
def jira_oauth_start(body: OAuthStartIn, user=Depends(current_user)):
    if not JIRA_OAUTH_CONFIGURED:
        raise HTTPException(400, "Jira OAuth is not configured (set JIRA_CLIENT_ID / JIRA_CLIENT_SECRET)")
    return_to = body.return_to
    if not return_to.startswith("/") or return_to.startswith("//"):
        return_to = "/"
    state = secrets.token_urlsafe(32)
    conn = db()
    try:
        conn.execute("DELETE FROM gh_oauth_states WHERE created_at < ?", (time.time() - GH_OAUTH_STATE_TTL,))
        conn.execute(
            "INSERT INTO gh_oauth_states(state, user_id, return_to, created_at, provider) VALUES(?,?,?,?,?)",
            (state, user["id"], return_to, time.time(), "jira"),
        )
        conn.commit()
    finally:
        conn.close()
    from urllib.parse import urlencode

    params = urlencode({
        "audience": "api.atlassian.com",
        "client_id": JIRA_OAUTH_CLIENT_ID,
        "scope": JIRA_SCOPES,
        "redirect_uri": _jira_redirect_uri(),
        "state": state,
        "response_type": "code",
        "prompt": "consent",
    })
    return {"authorize_url": f"https://auth.atlassian.com/authorize?{params}"}


@app.get("/api/jira/oauth/callback")
def jira_oauth_callback(code: str = "", state: str = "", error: str = ""):
    from urllib.parse import quote

    def bounce(dest, status, reason=""):
        sep = "&" if "?" in dest else "?"
        extra = f"&jira_reason={quote(reason)}" if reason else ""
        return RedirectResponse(f"{dest}{sep}jira={status}{extra}", status_code=302)

    conn = db()
    try:
        conn.execute("DELETE FROM gh_oauth_states WHERE created_at < ?", (time.time() - GH_OAUTH_STATE_TTL,))
        row = conn.execute("SELECT * FROM gh_oauth_states WHERE state=? AND provider='jira'", (state,)).fetchone()
        if row:
            conn.execute("DELETE FROM gh_oauth_states WHERE state=?", (state,))
        conn.commit()
        if not row:
            return bounce("/", "error", "Login link expired — try again")
        return_to = row["return_to"] or "/"
        if error:
            return bounce(return_to, "error", error)
        if not code:
            return bounce(return_to, "error", "Atlassian did not return a code")

        import requests

        try:
            tok = requests.post(
                "https://auth.atlassian.com/oauth/token",
                json={
                    "grant_type": "authorization_code",
                    "client_id": JIRA_OAUTH_CLIENT_ID,
                    "client_secret": JIRA_OAUTH_CLIENT_SECRET,
                    "code": code,
                    "redirect_uri": _jira_redirect_uri(),
                },
                headers={"Accept": "application/json"},
                timeout=15,
            ).json()
        except Exception:
            return bounce(return_to, "error", "Could not reach Atlassian to exchange the code")
        access_token = tok.get("access_token")
        if not access_token:
            return bounce(return_to, "error", tok.get("error_description") or "Token exchange failed")

        # Resolve the accessible Jira site (cloud id + url).
        try:
            res = requests.get(
                "https://api.atlassian.com/oauth/token/accessible-resources",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
                timeout=15,
            ).json()
        except Exception:
            return bounce(return_to, "error", "Could not list your Atlassian sites")
        if not isinstance(res, list) or not res:
            return bounce(return_to, "error", "No Jira site accessible for this account")
        site_res = res[0]
        cloud_id = site_res.get("id")
        site = (site_res.get("url", "")).replace("https://", "").replace("http://", "").strip("/")

        idrow = {"auth_kind": "oauth", "cloud_id": cloud_id, "site": site, "email": None}
        try:
            st, me, _ = _jira_api(idrow, access_token, "GET", "/myself")
        except Exception:
            return bounce(return_to, "error", "Could not fetch your Jira profile")
        if st != 200:
            return bounce(return_to, "error", f"Jira /myself returned {st}")

        conn.execute(
            """INSERT INTO jira_identities(user_id, site, cloud_id, account_id, email, display_name, token_enc, auth_kind, connected_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 site=excluded.site, cloud_id=excluded.cloud_id, account_id=excluded.account_id,
                 email=excluded.email, display_name=excluded.display_name, token_enc=excluded.token_enc,
                 auth_kind=excluded.auth_kind, connected_at=excluded.connected_at""",
            (row["user_id"], site, cloud_id, me.get("accountId"), me.get("emailAddress"),
             me.get("displayName"), _seal(access_token), "oauth", time.time()),
        )
        conn.commit()
        return bounce(return_to, "connected")
    finally:
        conn.close()


# ---------- Peer diff sharing (ticket #15): proxy the bus so the web UI can
# poll with normal user auth + project membership (bus url/token is infra) ----
def _bus_local_get(path: str):
    import urllib.request
    req = urllib.request.Request("http://127.0.0.1:8899" + path, headers={"User-Agent": "platform"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


@app.get("/api/projects/{pid}/peers")
def project_peers(pid: int, user=Depends(current_user)):
    """Who else is touching which files right now (+/- counts). Read-only."""
    conn = db()
    try:
        proj = require_member(conn, pid, user["id"])
        code = proj["invite_code"]
    finally:
        conn.close()
    try:
        import urllib.parse
        return _bus_local_get(f"/diff/peers?project={urllib.parse.quote(code)}")
    except Exception:
        return {"peers": []}  # bus down / no room yet -> empty, never error the UI


@app.get("/api/projects/{pid}/peers/diff")
def project_peer_diff(pid: int, machine: str = "", file: str = "", user=Depends(current_user)):
    """A peer machine's actual unified diff (one file, or all)."""
    import urllib.error
    import urllib.parse
    conn = db()
    try:
        proj = require_member(conn, pid, user["id"])
        code = proj["invite_code"]
    finally:
        conn.close()
    q = urllib.parse.urlencode({"project": code, "machine": machine, "file": file})
    try:
        return _bus_local_get(f"/diff/peer?{q}")
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, "peer diff not found")
    except Exception:
        raise HTTPException(502, "diff service unreachable")


# --- Serve the built frontend (single-origin: API + SPA on one port) ---
# Registered LAST so all /api routes above take precedence.
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

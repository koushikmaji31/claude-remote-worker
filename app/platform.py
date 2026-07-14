"""Team Collab Platform backend — see docs/API_CONTRACT.md (v1)."""
import os
import secrets
import sqlite3
import subprocess
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


@app.get("/api/github/status")
def github_status(user=Depends(current_user)):
    conn = db()
    try:
        row = conn.execute(
            "SELECT gh_login, scopes, auth_kind, connected_at FROM gh_identities WHERE user_id=?", (user["id"],)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return {"connected": False, "encrypted": TOKENS_ENCRYPTED, "oauth_available": GH_OAUTH_CONFIGURED}
    return {
        "connected": True,
        "login": row["gh_login"],
        "scopes": row["scopes"],
        "auth_kind": row["auth_kind"],
        "connected_at": row["connected_at"],
        "encrypted": TOKENS_ENCRYPTED,
        "oauth_available": GH_OAUTH_CONFIGURED,
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
    """Admin links a GitHub repo to the project; validated against the admin's GitHub token."""
    conn = db()
    try:
        require_admin(conn, pid, user["id"])
        owner, repo = body.owner.strip(), body.repo.strip()
        if body.full_name.strip() and "/" in body.full_name:
            owner, repo = body.full_name.strip().split("/", 1)
        if not owner or not repo:
            raise HTTPException(400, "owner and repo are required")
        token = _user_gh_token(conn, user["id"])
        try:
            status, data, _ = _gh_api(token, "GET", f"/repos/{owner}/{repo}")
        except Exception as e:
            raise HTTPException(502, f"Could not reach GitHub: {e}")
        if status == 404:
            raise HTTPException(404, f"Repo {owner}/{repo} not found or not accessible with your token")
        if status != 200:
            raise HTTPException(400, f"GitHub error ({status}): {data.get('message', 'unknown')}")
        conn.execute(
            """INSERT INTO repo_links(project_id, owner, repo, default_branch, linked_by, linked_at)
               VALUES(?,?,?,?,?,?)
               ON CONFLICT(project_id) DO UPDATE SET
                 owner=excluded.owner, repo=excluded.repo, default_branch=excluded.default_branch,
                 linked_by=excluded.linked_by, linked_at=excluded.linked_at""",
            (pid, data["owner"]["login"], data["name"], data.get("default_branch"), user["id"], time.time()),
        )
        conn.commit()
        return {
            "linked": True,
            "owner": data["owner"]["login"],
            "repo": data["name"],
            "full_name": data["full_name"],
            "default_branch": data.get("default_branch"),
            "private": data.get("private"),
            "html_url": data.get("html_url"),
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
        require_admin(conn, pid, user["id"])
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


@app.get("/api/projects/{pid}/github/branches")
def github_branches(pid: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
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


@app.get("/api/projects/{pid}/github/pulls/{number}")
def github_pull_detail(pid: int, number: int, user=Depends(current_user)):
    conn = db()
    try:
        require_member(conn, pid, user["id"])
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


@app.get("/api/projects/{pid}/github/graph")
def github_graph(pid: int, user=Depends(current_user)):
    from urllib.parse import quote

    conn = db()
    try:
        require_member(conn, pid, user["id"])
        cached = _cache_get(("graph", pid))
        if cached is not None:
            return cached
        owner, repo, _ = _project_repo(conn, pid)
        token = _project_gh_token(conn, pid, user["id"])
        link = _repo_link_row(conn, pid)
        default_branch = link["default_branch"]

        branches_raw, rl = _gh_read(token, f"/repos/{owner}/{repo}/branches", "?per_page=100")
        # Default branch first so shared history is attributed to it (its lane
        # claims commits the feature branches merely contain).
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
                    "branch": b["name"],  # first branch (default first) whose history listed it
                }
        nodes = sorted(commits.values(), key=lambda n: n["date"] or "", reverse=True)

        pulls_data, rl = _gh_read(token, f"/repos/{owner}/{repo}/pulls", "?state=open&per_page=50")
        pulls = [{
            "number": p["number"], "title": p["title"], "head": p["head"]["ref"],
            "base": p["base"]["ref"], "draft": p.get("draft", False), "html_url": p["html_url"],
        } for p in pulls_data]

        result = {
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
        _cache_put(("graph", pid), result, ttl=GRAPH_CACHE_TTL)
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

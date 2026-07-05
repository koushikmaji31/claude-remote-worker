# Team Collab Platform — API Contract (v1)

Owner: fable (coordinator). Backend: agent-a. Frontend: agent-b.
All three must build against THIS file. If you need a contract change, propose it
to fable over the chat bus BEFORE deviating.

## Overview
A platform where engineers (and their Claudes) join projects. One admin per
project, invite-link joining, member management, message logs, and RPC-style
endpoints agents use to share diffs/branches and avoid merge conflicts.

- Backend: FastAPI + stdlib sqlite3, file `app/platform.py`, DB `platform.db` (repo root), port **8900**. Enable CORS for http://localhost:5173 (frontend dev server).
- Frontend: npm project in `frontend/` — **Vite + React** (`npm create vite`-style layout: package.json, index.html, src/). Dev: `npm install && npm run dev` on port **5173**, with a Vite proxy for `/api` and `/rpc` → http://127.0.0.1:8900 so fetch('/api/...') just works.
- Auth: bearer token. `Authorization: Bearer <token>` on every endpoint except register/login/join-info and static files.

## DB schema (sqlite)
```sql
users(id INTEGER PK, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, token TEXT UNIQUE NOT NULL);
projects(id INTEGER PK, name TEXT NOT NULL, admin_id INTEGER NOT NULL REFERENCES users(id), invite_code TEXT UNIQUE NOT NULL, created_at TEXT);
members(project_id INTEGER REFERENCES projects(id), user_id INTEGER REFERENCES users(id), role TEXT CHECK(role IN ('admin','member')), PRIMARY KEY(project_id, user_id));
messages(id INTEGER PK, project_id INTEGER REFERENCES projects(id), sender TEXT NOT NULL, text TEXT NOT NULL, ts REAL NOT NULL);
```

## REST endpoints (JSON in/out)

### Auth
- `POST /api/register {name, email}` → `{user_id, name, email, token}` (token = secrets.token_hex(16); email exists → 409)
- `POST /api/login {email}` → `{user_id, name, email, token}` (404 if unknown; demo-simple login by email)
- `GET /api/me` (auth) → `{user_id, name, email}`

### Projects / teams
- `POST /api/projects {name}` (auth) → `{project_id, name, invite_code, invite_link}`; creator becomes admin + member. invite_link = `http://127.0.0.1:8900/?join=<invite_code>`
- `GET /api/projects` (auth) → `{projects: [{project_id, name, role, admin_name, member_count}]}` (mine only)
- `GET /api/projects/{pid}` (auth, member) → `{project_id, name, invite_code, invite_link, admin_id, members: [{user_id, name, email, role}]}`
- `GET /api/join/{invite_code}` (NO auth) → `{project_id, name, admin_name, member_count}` (preview before joining)
- `POST /api/join/{invite_code}` (auth) → `{project_id, name, role:"member"}` (idempotent if already member)
- `DELETE /api/projects/{pid}/members/{user_id}` (auth, ADMIN only) → `{ok:true}` (admin can't remove self)
- `POST /api/projects/{pid}/transfer-admin {user_id}` (auth, ADMIN only) → `{ok:true}`

### Message log (per project — this is the passing-log the dashboard shows)
- `POST /api/projects/{pid}/messages {text}` (auth, member) → `{ok:true}`; sender = user's name
- `GET /api/projects/{pid}/messages?since_id=0` (auth, member) → `{messages:[{id, sender, text, ts}]}`

### Agent RPC (JSON-RPC style, for Claudes to coordinate on code)
All under `POST /rpc` (auth, member of the project in params):
Body: `{method, params, id}` → `{result, id}` or `{error:{code,message}, id}`
- method `git.branches`   params `{repo_path}` → `{branches:[...], current}`
- method `git.diff`       params `{repo_path, base, head}` → `{diff}` (unified diff text)
- method `git.conflicts`  params `{repo_path, base, head}` → `{conflicts:[files]}` (via `git merge-tree`)
Errors: -32601 unknown method, -32602 bad params, 500-range wrapped as `{error}`.

### Errors (REST)
`{detail: "..."}` with proper status codes: 401 no/bad token, 403 not member/not admin, 404, 409.

## Frontend pages (agent-b) — React (Vite) in `frontend/`
- Landing route `/` — register/login form (stores token in localStorage), "my projects" list, create project, and if URL has `?join=CODE` show join-preview + join button.
- Project route `/project/:pid` — dashboard: member list w/ roles; admin-only controls (remove member, transfer admin, show/copy invite link); message log (poll `GET .../messages` every 3s, textarea to post).
- fetch() with `Authorization: Bearer` from localStorage; redirect to `/` on missing token or 401. invite_link from the backend points at 8900 — when displaying, also show the 5173 equivalent (`http://localhost:5173/?join=CODE`).
- Keep deps minimal: react, react-dom, react-router-dom, vite. Must work with `npm install && npm run dev`.

## Testing (fable)
- `tests/smoke_platform.sh` — end-to-end curl flow: register 2 users, create project, join via invite, post/read messages, admin removes member, RPC git.branches on this repo.
- Everyone: when your part is ready, announce over the bus; fable runs the smoke test and reports failures to the owner.

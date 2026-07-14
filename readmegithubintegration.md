# GitHub Integration — Design & Roadmap

> Planning document for adding **GitHub integration** to the Team Collab Platform.
> Status: **proposal / not yet implemented.** No code in this repo depends on it yet.

## TL;DR

The platform already has a **git feature**, but it operates on *local* repositories:
`GitPanel.jsx` → `/rpc` (`git.branches` / `git.diff` / `git.conflicts`) shells out to
`git` via `subprocess` against a `repo_path` on the **server's filesystem**.

"Integrating GitHub" means upgrading that from *local-filesystem git* to
*remote GitHub-API git* — real repos, pull requests, issues, webhooks, and
Claude-worker automation. That reframing drives this entire document.

---

## 1. What the project is today (the foundation GitHub plugs into)

| Layer | File(s) | What it does |
|---|---|---|
| **Product API** | `app/platform.py` (FastAPI + SQLite) | Users (token auth), Projects, Members (admin/member), invites, **Discussion** (chat w/ text+image), per-project bus group |
| **Local-git coordination** | `platform.py` `/rpc` + `GitPanel.jsx` | Branch list, diff, merge-conflict pre-check — **local repo path only** |
| **Claude worker** | `app/worker.py` (`:8787`) | Headless `claude -p` wrapper; can edit/commit/run tools (`SKIP_PERMISSIONS=1`) |
| **Agent bus** | `app/chat_server.py` (`:8899`), `bus_mcp.py` | Claude-session coordination, project-scoped rooms |
| **Frontend** | React SPA (Landing, Dashboard, Project sidebar shell) | Sidebar nav (Overview / Discussion / Branches / Members) |
| **Ops** | `start_server.sh` | Builds frontend, serves from backend, runs worker + bus, **prints ngrok URL** |

---

## 2. The possibility space (feature list, tiered)

Grouped from foundational to agentic. Each tier depends on the ones above it.

### Tier 0 — Identity & linking (mandatory foundation)
- **"Connect GitHub"** — OAuth / App auth so a user or project links a GitHub identity.
- **Link a GitHub repo to a project** (`owner/repo` ↔ your `project_id`).

### Tier 1 — Read / mirror (upgrades the current GitPanel)
- Real branches, commits, tags, open PRs, issues pulled from the GitHub API (not local subprocess).
- PR / branch diffs and **GitHub's own mergeability / conflict status** (replaces local `git.conflicts`).
- Repo activity feed (pushes, PR opens / merges).

### Tier 2 — Write / in-product collaboration
- Create branches, open PRs, create / label / close issues from the UI.
- Comment on PRs / issues from the **Discussion** panel; link chat messages ↔ commits / PRs.
- New sidebar views: **Pull Requests**, **Issues** (the shell already supports nav items).

### Tier 3 — Event-driven automation (webhooks + worker)
- **GitHub webhooks** → push / PR / issue / CI events → posted into the project Discussion as bot
  messages (`messages.sender` is free-text, so a `"github"` sender "just works").
- **Claude worker reacts**: auto-summarize diffs, review PRs, triage failing CI, answer issues.
- **`@claude` mention** in a PR / issue comment → dispatch to the worker → reply as a GitHub comment.

### Tier 4 — Agentic (the worker already has the muscle)
- Claude autonomously opens PRs (edits code, commits, pushes via App token).
- Merge-conflict *resolution* bot (extends `git.conflicts` from *detect* → *fix*).
- Scheduled repo-health / stale-PR reports into the Discussion.

---

## 3. Advantages / Complexities / Requirements per tier

| Tier | Advantages | Complexities | New requirements |
|---|---|---|---|
| **0 Auth/link** | Real multi-user identity; no server-filesystem dependency; per-repo permissions | OAuth flow, secure token storage + refresh, choosing App vs OAuth vs PAT | GitHub App / OAuth registration, client id/secret, callback URL, `repo_links` + encrypted-token tables |
| **1 Read** | Works on any cloud repo; accurate mergeability from GitHub; live data | Pagination, **rate limits** (5k/hr), caching / ETags, mapping API shapes to the UI | GitHub REST / GraphQL client, cache tables, background refresh |
| **2 Write** | Full collaboration in one place; fewer context switches | Least-privilege scopes, optimistic-UI vs eventual consistency, idempotency, error UX | Write scopes, audit trail, permission checks tied to admin/member roles |
| **3 Webhooks** | Real-time, push-based (no polling); the glue to Claude | **Public HTTPS endpoint**, HMAC signature verification, retry / replay handling, async queue so you `200` fast | Webhook receiver route, `WEBHOOK_SECRET`, event queue / worker, dispatch to `:8787` |
| **4 Agentic** | Genuine "Claude does the work" differentiation | Safety / guardrails, cost / timeouts, branch protection, review-before-merge, loop prevention (bot reacting to its own commits) | Bot git identity, allow-listed actions, human-approval gates, sandbox |

---

## 4. Current progress map (what's done vs. the gap)

```
                          READY (build on it)              GAP (the integration work)
Auth / users        [x] token auth, users table      ->   [ ] GitHub OAuth link, token storage
Projects / roles    [x] projects, members, admin      ->   [ ] repo_links table, per-repo perms
Discussion chat     [x] text+image, free-text sender  ->   [ ] "github" bot messages from events
Git feature         [x] GitPanel + /rpc (LOCAL git)   ->   [ ] swap to GitHub API (remote)
Claude worker       [x] headless, can edit/commit     ->   [ ] give it an App token to push/comment
Public URL          [x] ngrok via start_server.sh     ->   [ ] OAuth callback + webhook routes
Frontend shell      [x] sidebar nav, panels           ->   [ ] PR/Issues views, Connect-GitHub UI
Async processing    [~] none (all synchronous)        ->   [ ] event queue for webhooks/worker
```

**Reading:** ~60% of the *scaffolding* exists. The genuinely new build is GitHub auth, an API
client, a webhook receiver + queue, and the repo-link data model. The worker — the hard part of
"AI acts on your repo" — is already there.

---

## 5. Flowchart — recommended build order

```
        +-------------------------------------------------------------+
        | PHASE 0 . DECIDE AUTH MODEL                                 |
        |   PAT (fastest) --> OAuth App --> GitHub App (recommended)  |
        +---------------+---------------------------------------------+
                        v
        +-------------------------------------------------------------+
        | PHASE 1 . IDENTITY & LINK                                   |
        |   Register App -> store client id/secret/private key        |
        |   "Connect GitHub" button -> OAuth callback -> save token   |
        |   Link project <-> owner/repo   (new repo_links table)      |
        +---------------+---------------------------------------------+
                        v
        +-------------------------------------------------------------+
        | PHASE 2 . READ (swap GitPanel to GitHub API)               |
        |   GitHub client (REST/GraphQL) + cache + rate-limit handling|
        |   Show real branches / PRs / issues / diffs / mergeability  |
        +---------------+---------------------------------------------+
                        v
        +-------------------------------------------------------------+
        | PHASE 3 . WRITE                                            |
        |   Create branch / open PR / comment / create issue from UI  |
        |   Enforce admin/member roles on every write                 |
        +---------------+---------------------------------------------+
                        v
        +-------------------------------------------------------------+
        | PHASE 4 . WEBHOOKS -> DISCUSSION                          |
        |   /api/github/webhook (verify HMAC) -> queue -> post as bot |
        |   push / PR / issue / CI events appear in project chat      |
        +---------------+---------------------------------------------+
                        v
        +-------------------------------------------------------------+
        | PHASE 5 . CLAUDE AUTOMATION (agentic)                     |
        |   event / @claude -> dispatch to worker(:8787) ->           |
        |   summarize . review . fix . open-PR -> write back via App  |
        |   + human-approval gates & branch protection                |
        +-------------------------------------------------------------+
```

---

## 6. Target architecture

```
   +--------------+        +-----------------------------------------------+
   |  React SPA   |  HTTPS  |            platform.py  (FastAPI)            |
   | Connect GH . |<------->|  /api/github/oauth/callback   (identity)     |
   | PRs . Issues |  JSON   |  /api/projects/{id}/github/*  (read/write)   |
   | Discussion   |         |  /api/github/webhook          (HMAC verify)  |
   +--------------+         +---+---------------+---------------+----------+
                                |               |               |
                     +----------v--+   +--------v--------+  +---v----------+
                     | GitHub API  |   |  SQLite / DB    |  | Event Queue  |
                     | client      |   |  users.projects |  | (webhook ->  |
                     | REST/GraphQL|   |  repo_links     |  |  jobs)       |
                     +------+------+   |  gh_tokens(enc) |  +------+-------+
                            |          |  pr/issue cache |         |
              +-------------v---+      +-----------------+         v
              |   GITHUB.COM    |<--------- write-back ----+----------------+
              | repos.PRs.issues|   (comments, PRs,        | Claude worker  |
              | webhooks.CI     |------ webhook events --->|  :8787 headless|
              +-----------------+                          |  (claude -p)   |
                                                           +----------------+
```

### Data-model additions

| Table | Columns (sketch) | Purpose |
|---|---|---|
| `repo_links` | `project_id, owner, repo, installation_id` | Map a project to a GitHub repo |
| `gh_identities` | `user_id, gh_login, token_enc, refresh_enc, expires` | Per-user GitHub auth (encrypted) |
| `gh_events` | `id, delivery_id, type, payload, processed` | Webhook inbox / idempotency |
| `pr_cache` / `issue_cache` | *(optional)* cached list payloads + ETag | Rate-limit-friendly reads |

---

## 7. The pathways (key decisions)

1. **Auth model** — three routes, pick by ambition:
   - **PAT** — 1-day MVP, per-user token paste, no webhooks-as-app. Good to prototype Tier 1.
   - **OAuth App** — "Login with GitHub," acts *as the user*. Good for read + user-attributed writes.
   - **GitHub App** *(recommended target)* — installed per repo / org, fine-grained perms, native
     webhooks, bot identity, on-behalf-of user tokens. Correct end state for a multi-tenant
     platform. Cost: JWT + private key + installation-token dance.

2. **Webhook hosting** — dev: reuse the **ngrok** URL already printed by `start_server.sh`;
   prod: stable domain + TLS.

3. **Depth of automation** — read-only → manual write buttons → webhook-driven bot messages →
   full agentic Claude. Each is a shippable stopping point.

### Recommendation

Ship **PAT + Tier 1 read** first to prove the UX against a real repo, then move to a
**GitHub App** and build **Phase 4 webhooks → Discussion** — that's where the existing Claude
worker turns this from "another GitHub client" into something distinctive.

---

## 8. Environment / config checklist (for when build starts)

```
GITHUB_APP_ID=...                 # or GITHUB_OAUTH_CLIENT_ID for the OAuth-App route
GITHUB_CLIENT_SECRET=...
GITHUB_PRIVATE_KEY=...            # GitHub App only (PEM); used to mint installation tokens
GITHUB_WEBHOOK_SECRET=...         # HMAC-SHA256 verification of inbound webhooks
GITHUB_OAUTH_CALLBACK_URL=...     # <public-base>/api/github/oauth/callback
TOKEN_ENCRYPTION_KEY=...          # encrypt stored GitHub tokens at rest
PUBLIC_BASE_URL=...               # already used by platform.py; needed for callbacks/webhooks
```

---

## 9. Shipped so far

- **Phase 1 — identity + repo link** (PAT): `gh_identities`, `repo_links`, sealed tokens.
- **Phase 2 — live reads**: branches / PRs (+diff detail) / issues, TTL-cached.
- **Phase 3 — OAuth + branch graph** (2026-07-14, koushikmaji_1):
  - `GET /api/github/oauth/config`, `POST /api/github/oauth/start`,
    `GET /api/github/oauth/callback` — OAuth-App code flow, single-use `gh_oauth_states`
    rows (15-min TTL), open-redirect guard, tokens stored `auth_kind='oauth'`.
    Needs `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` in `.env`; callback URL is
    `<PUBLIC_BASE_URL>/api/github/oauth/callback`. PAT connect remains the fallback.
  - `GET /api/projects/{pid}/github/graph` — commit DAG across the 12 most relevant
    branches (30 commits each, deduped, default-branch-first attribution) + open PRs,
    60s cache. Rendered by `frontend/src/components/BranchGraph.jsx` as colored lanes
    (validated palette, both themes) on the GitHub tab's "Branch graph" view.

---

*Document owner: acer_1. Companion to the platform docs; update as phases land.*

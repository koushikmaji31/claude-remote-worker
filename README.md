# claude-remote-worker

Let your server invoke **Claude Code** (with memory + tools, not a stateless raw-API call)
and get the answer back. Laptop does the work when on; a cloud VPS handles it when off.

See **CONVERSATION.md** for the full design reasoning.

## What's here
```
app/worker.py              FastAPI wrapper around `claude -p` (run on laptop AND on VPS)
app/dispatcher_example.py  What your server does: route to laptop, fall back to cloud
context/CLAUDE.md          Your personalization (loaded every call)
context/memory/            One-fact-per-file long-term memory
```

## Run the worker (laptop)
```bash
pip install fastapi uvicorn requests
export WORKER_TOKEN="$(openssl rand -hex 24)"     # share this secret with your server
export CONTEXT_DIR="$HOME/Desktop/claude-remote-worker/context"
uvicorn app.worker:app --host 0.0.0.0 --port 8787
```

Smoke test:
```bash
curl -s localhost:8787/ask \
  -H "Authorization: Bearer $WORKER_TOKEN" -H "Content-Type: application/json" \
  -d '{"prompt":"Say hi and tell me what context you have."}' | jq
```

## How "laptop when on, cloud when off" works
1. Run the SAME worker on a cheap always-on VPS, with the SAME context dir (synced via git)
   and a copy of `~/.claude/.credentials.json` so it's authenticated as you.
2. Your server calls `pick_worker()` (see dispatcher_example.py): it pings the laptop's
   `/health` first, falls back to the cloud URL if the laptop is unreachable.
3. Reach the laptop privately with **Tailscale** (recommended) so you don't expose a port.

## Memory across calls
The `/ask` response includes `session_id`. Pass it back as `session_id` on the next call
to CONTINUE that conversation (`claude --resume`). Store it per-conversation in your DB.

## Security
- The worker runs Claude Code with your account + machine access. Protect it:
  - `WORKER_TOKEN` bearer auth (built in).
  - Don't expose the port publicly — use Tailscale / a private network / reverse proxy + TLS.
  - Consider `ALLOWED_TOOLS` to limit what headless Claude may do.

## Auth note
Claude Code uses `~/.claude/.credentials.json` (your subscription login) — no separate API
key needed. The VPS needs the same credentials; tokens refresh, so keep them in sync.

## Landing-page sign-in (email/password + Google)
Users register/sign in on the landing page with **email + password** (passwords are stored as
salted PBKDF2-HMAC-SHA256, never plaintext) or with **Sign in with Google**.

To enable the Google button, add your Google OAuth **Web** client ID to `.env`:

    GOOGLE_CLIENT_ID=xxxxxxxx.apps.googleusercontent.com

The frontend fetches it at runtime from `GET /api/config`, so no rebuild is needed — set it and
restart. When unset, the Google button is hidden and email/password still works. In the Google
Cloud console, add your origin (e.g. `https://huntjob.space`) to the client's **Authorized
JavaScript origins**.

### Auth security model
- **Passwords:** salted PBKDF2-HMAC-SHA256 (240k iterations), constant-time compare.
- **Sessions, not static tokens:** each sign-in mints a bearer token; the server stores only its
  SHA-256 hash (`sessions` table), so a DB leak yields no usable credentials. Sessions expire after
  30 days and are revoked on password change/reset and logout (`POST /api/logout`,
  `/api/logout-all`). Changing a password keeps the current device signed in and drops the rest;
  a reset drops **all** sessions.
- **Google:** the access token is verified against Google's **tokeninfo** endpoint and accepted only
  when its `aud` equals your `GOOGLE_CLIENT_ID` **and** the email is verified — this blocks tokens
  minted for a different app. Display name/picture come from userinfo afterward.
- **No enumeration:** login returns a uniform error and always spends PBKDF2 time (even for unknown
  emails, via a dummy hash) so timing can't reveal which emails are registered; forgot-password
  always returns `ok`.
- **Rate limiting:** login / register / forgot / reset are throttled per IP (and per email) — in-process
  and fine for a single worker; move to a shared store (Redis) if you scale to multiple workers.
- **Reset tokens** are stored hashed (SHA-256), single-use, 1-hour expiry; used/expired rows are purged.

### Password-reset email (SMTP)
Set these in `.env` so reset links are emailed (otherwise the link is only logged server-side):

    PUBLIC_BASE_URL=https://your-domain            # used to build the reset link
    SMTP_HOST=smtp.your-provider.com
    SMTP_PORT=587                                  # 465 => implicit SSL, else STARTTLS
    SMTP_USER=you@your-domain
    SMTP_PASS=xxxxxxxx
    SMTP_FROM=no-reply@your-domain                 # defaults to SMTP_USER if unset

### Security headers
Every response carries `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
`Permissions-Policy`, a **Content-Security-Policy** (tuned for the SPA + Google sign-in), and
`Strict-Transport-Security` (only over HTTPS). If the CSP blocks a resource during development, set
`CSP_DISABLED=1` to turn it off while you adjust it — always re-check in a browser after changes.

### Email verification
Password sign-ups start unverified and receive a confirmation link (`/?verify=<token>`); Google
sign-ups are verified automatically. Endpoints: `POST /api/email/verify` (confirm a token),
`POST /api/email/resend` (signed-in, rate-limited). `GET /api/me` returns `email_verified`.
Set `REQUIRE_EMAIL_VERIFICATION=1` to block unverified **password** logins (off by default so no one
is locked out before the front-end verify UX is wired up). Needs SMTP configured to actually deliver.

> Front-end TODO: the landing page must read `?verify=<token>` from the URL and POST it to
> `/api/email/verify` (mirrors the existing `?reset=` handling), plus a "verify your email" banner
> driven by `me.email_verified`.

### One-time note on upgrade
Switching to session-based auth invalidates the old per-user static tokens, so everyone who was
signed in must sign in again once after this deploys.

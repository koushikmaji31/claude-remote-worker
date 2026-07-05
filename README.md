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

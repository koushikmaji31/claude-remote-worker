# Invoking Claude Remotely with Memory + Tools — Design Notes

> Conversation reference. Date: 2026-06-30. User: koushik.maji@recykal.com

---

## The original questions

1. **"When a process takes ~120s, Claude Code sets a trigger after a timer — how?"**
2. **"Can we set an API so I can invoke Claude from remote, using it even when my computer is off?"**
3. **"I want the inverse of MCP — MCP feeds data *from server → Claude*; I want data to flow *from Claude → server*."**
4. **"The API is stateless. But my Claude chat app has memory, and Claude Code has access to my laptop. I want those personalized aspects, with all capabilities."**

---

## Answers

### 1. The "timer trigger"
It's a built-in Claude Code tool called **`ScheduleWakeup`** (and `CronCreate` for repeating checks).
When a process will take a while, instead of blocking, Claude schedules itself to be re-invoked
after a delay so it wakes up and checks the result.

- **It is NOT an API.**
- It only works **while the local Claude Code session is alive**. Kill the session → the timer dies.
- So this alone does NOT let you run Claude without your computer.

### 2 & 4. Invoking Claude remotely WITH memory + tools — the core insight

**State is not inside the model. The API is stateless on purpose.**
The "memory" in the Claude chat app and the context in Claude Code are built by the **harness**
(the app/CLI) around the model, which:
  1. Stores your data (history, memory files, files on disk), and
  2. **Re-sends the relevant parts as context on every API call.**

So: **stateless model + a layer that remembers = stateful assistant.**
You build (or reuse) the memory layer; the model stays stateless. This is how *every* Claude
product works, including the chat app and Claude Code.

**Three building blocks:**

| Capability you want            | Where it actually comes from                          | How your server gets it                          |
|--------------------------------|-------------------------------------------------------|--------------------------------------------------|
| Memory / personalization       | A store YOU keep (files/DB), re-injected as context   | Claude Code's `CLAUDE.md` + `memory/` dir        |
| Access to laptop / run commands| Claude **Code** (agentic harness), NOT the raw API    | Run **Claude Code headless** (`claude -p`)       |
| All capabilities, always-on    | Claude Code on an **always-on host**                  | Laptop when on; cloud/VPS fallback when off      |

**Conclusion:** Don't call the raw API. Have your server invoke **Claude Code in headless mode**
(`claude -p ... --output-format json`) on a host that has your context dir. That single thing
has memory AND machine access AND all tools.

### 3. The "Claude → server" data path (inverse of MCP)
- **MCP** = server → Claude (data flows *into* Claude). You already have this.
- **What you want** = Claude's answer flows *out* to your server.
- This is NOT MCP. It's just the **stdout/JSON response** of the headless call
  (`--output-format json`), which your server captures. Optionally Claude can POST to a webhook
  on your server. No special protocol needed.

---

## Chosen architecture: laptop-primary, cloud-fallback (job queue)

User wants: laptop does the work when it's on (cheaper, has local files);
a cloud/VPS worker handles requests when the laptop is off. Server must never *depend* on the laptop.

```
  Your server  ──▶  Queue (Redis / DB / HTTP)
                       ├─ Laptop worker  (polls when on)  ──▶ claude -p ──▶ posts result back
                       └─ Cloud worker   (drains otherwise) ──▶ claude -p ──▶ posts result back
```

Each worker runs the SAME thin FastAPI wrapper around `claude -p`, pointed at the SAME
synced context dir (`CLAUDE.md` + `memory/`, kept in git so laptop and cloud match).

**Stack chosen:** Python + FastAPI wrapper around the `claude` CLI.

### Personalization / memory specifics
- Keep a working directory with `CLAUDE.md` (project/personal context) and a `memory/` folder.
- Claude Code loads these every invocation → persistent, personalized behavior.
- Sync this dir via git so laptop and cloud worker behave identically.
- For multi-turn continuity, use `claude --resume <session_id>` / `--continue` so a
  conversation keeps memory across calls. Store the session id per conversation in your DB.

---

## Why NOT the managed cloud Claude Code (Routines / Remote)
That would be the cleanest fit (Anthropic-hosted, laptop-off, fully agentic), BUT:
- `list_environments` returned EMPTY for this account.
- `list_repos` errored: "not available for account-owned sessions."
- => Managed Remote/cloud Claude Code is **not provisioned** for this account right now.
- Self-hosted VPS running `claude -p` gives the same capabilities today, under your control.
- (To enable managed Remote later: set it up from the Claude web app / admin settings.)

---

## TODO / next steps
- [x] Verify env on laptop: `claude` 2.1.187, Python 3.12.11, fastapi 0.115.6, `~/.claude/.credentials.json` present.
- [x] Scaffold FastAPI wrapper around `claude -p --output-format json` (`app/worker.py`).
- [x] Context dir layout: `CLAUDE.md` + `memory/`, put under git (git initialized, `.gitignore` excludes credentials).
- [x] **Verified live:** headless `claude -p --output-format json` returns `result` + `session_id`; `--resume <id>` carries memory across calls (asked it to remember 42 → recalled 42).
- [x] Auth on the wrapper endpoint (bearer `WORKER_TOKEN`, built into `worker.py`).
- [ ] Run the worker for real: `uvicorn app.worker:app --port 8787` and hit `/ask` over HTTP (so far the CLI underneath is proven; the HTTP layer is untested on this box).
- [ ] Job queue so laptop=primary, cloud=fallback (dispatcher logic exists in `app/dispatcher_example.py`; needs a real queue/server to drive it).
- [ ] Pick + provision the always-on fallback host (VPS); copy `~/.claude/.credentials.json` to it out-of-band; sync `context/` via git.
- [ ] Reach the laptop privately (Tailscale) instead of exposing port 8787.

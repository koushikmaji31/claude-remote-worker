# Team Norms — Multi-Agent Collaboration

Agreed in the team retro of 2026-07-05 by agent-d, agent-e, and fable (agent-a
returning). These norms exist to make development and communication among
concurrent Claude sessions smooth and failure-resistant. Amend by proposing on
broadcast and updating this file (owner: whoever proposes, via normal review).

## 1. File ownership — one owner per shared file

Path-scoped commits (each agent `git add`s only its own paths) are safe **only
until two agents edit the same file**. To prevent that:

- **Every shared file has exactly one owner.** Only the owner edits it.
- Need a change in someone else's file? **DM the owner** and ask; the owner makes
  and commits the change. Do not edit across ownership boundaries.

### Ownership map

| Path | Owner |
| --- | --- |
| `frontend/src/styles.css`, `frontend/src/ui/*` | agent-e |
| `frontend/src/Landing.jsx` | agent-e |
| `frontend/src/Project.jsx`, chat/message components | agent-d |
| `frontend/src/components/GitPanel.jsx`, `frontend/src/lib/rpc.js` | fable |
| `tests/*`, `hooks/*`, `app/chat_server.py`, `app/bus_mcp.py` | fable |
| `app/platform.py` | agent-a |
| `docs/*` | unowned — coordinate on broadcast before editing |

## 2. Liveness — defense in depth

An agent goes "deaf" when its background listener (`bus_wait.sh`) is not running.
This happened once because a listener-restart tool call was **interrupted/rejected
mid-turn**, so it never executed — and the Stop-hook safety net does not cover a
turn that did not end cleanly. Therefore we use layered protection:

1. **Stop-hook restart** — restarts the listener when a turn ends cleanly.
2. **Listener always running when idle** — restart it at the start of your first
   turn and before ending every turn.
3. **Server-side presence (backstop)** — the bus tracks each agent's last poll
   time and broadcasts staleness (e.g. `agent-d appears deaf (last seen 90s)`)
   at ~90s. This is nearly free because the long-poll listener already hits the
   server every cycle, and — critically — it does **not** depend on the deaf
   agent noticing its own failure.

Optionally the listener also posts a heartbeat when it can (belt and suspenders),
but server-side presence is the authoritative signal. **No separate supervisor
agent** — that is just another process that can go deaf.

## 3. Bus tooling — wake vs. read

- `bus_wait.sh` (background listener) is the **wake path**: it is the only thing
  that can wake an idle session, because the harness re-invokes on background-task
  completion. Keep it running whenever idle.
- `bus_check` / MCP bus tools are for **reading and sending during a turn** (the
  agent must already be awake). They do **not** replace `bus_wait`.
- **Run both.** Start your listener with a short description (`bus`) to keep the
  operator's terminal clean.

## 4. Message discipline — broadcast vs. DM

- **Broadcast:** milestones, task claims, and pushes. Keep to **one line + commit
  hash**. Announce before restarting shared infra (see §5).
- **DM:** per-file coordination, "can you add class X", questions to a specific
  agent.
- Short but unambiguous. Always include the commit hash when announcing a push.

## 5. Git workflow rhythm

- **Commit or stash before you rebase.** Never rely on autostash — it has
  surprised us. A clean working tree before rebase avoids blocked rebases.
- **Path-scoped commits:** `git add` only your owned paths; never `git add -A`.
- **Verify before you push:** build green (`npm run build` for frontend) before
  announcing.

## 6. Shared infrastructure restarts

Whoever restarts shared infra (bus server, backend, tunnel):

1. **Broadcast BEFORE** doing it.
2. **Confirm queues are empty / drained** first (bus persistence is SQLite-backed,
   so queued messages survive, but announce anyway so no one is mid-dispatch).
3. Confirm when back up; listeners reconnect automatically.

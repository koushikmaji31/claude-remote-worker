# Collab Kit — shared chat bus for Claude Code sessions

Drop-in kit that connects every `claude` session opened in this repo to a
shared message bus, so agents on different machines (and their humans)
coordinate in real time: hands-free message delivery, per-task path claims,
presence tracking.

## Setup (each developer, once per machine)

```bash
git clone <this repo>
cd <repo>
./setup.sh <BUS_URL> <BUS_TOKEN>    # both provided privately by the team lead
claude                               # approve the project hooks + MCP server once
```

Type `hi` once — the session announces itself (agent-a, agent-b, ... or set
`CLAUDE_BUS_NAME=yourname claude`) and from then on receives teammates'
messages automatically.

## What's inside

| Piece | Purpose |
| --- | --- |
| `hooks/bus_join.sh` (SessionStart) | registers the session on the bus, injects the team-norms digest |
| `hooks/bus_wait.sh` | background listener — a finished wait wakes the idle session (hands-free delivery) |
| `hooks/bus_listen.sh` (Stop) | end-of-turn message check + restarts the listener if it died |
| `hooks/bus_check.sh` (UserPromptSubmit) | delivers messages queued while idle along with your next prompt |
| `hooks/bus_leave.sh` (SessionEnd) | unregisters cleanly on exit |
| `hooks/bus_mcp.py` + `.mcp.json` | MCP tools: `bus_send`, `bus_check`, `bus_who` |
| `setup.sh` | writes bus URL + token to `/tmp/claude-bus/` |

## Team norms (digest — injected into every session automatically)

- **Claim before editing**: broadcast `CLAIM <paths> — <agent>, task: ...`;
  first claim wins; release in your push announcement.
- **Broadcast** = one-line milestones/claims/pushes with commit hash.
  **DM** = per-file asks.
- `git add` only your claimed paths; commit-or-stash before rebase.
- Announce before restarting shared infra.

## Ops notes

- The bus server itself runs on the hub machine (not in this repo); this kit
  only needs its URL + token. Kill switch on any machine: `touch /tmp/claude-bus/off`.
- Presence: agents silent >90s are flagged; closed sessions unregister via
  hook, force-killed ones auto-prune after 30 min.

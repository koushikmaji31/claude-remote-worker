"""Format bus /recv output for Claude Code hooks. Reads recv JSON on stdin.

Usage: python3 bus_format.py stop   -> Stop-hook JSON {"decision":"block",...} (or nothing)
       python3 bus_format.py prompt -> plain context text (or nothing)
"""
import sys
import json

mode = sys.argv[1] if len(sys.argv) > 1 else "stop"
try:
    msgs = json.load(sys.stdin).get("messages", [])
except Exception:
    msgs = []


def _silent(m):
    """Roster/presence churn the developer shouldn't be interrupted for. The
    agent can still learn who's online on demand via bus_who — these events just
    never wake the session or print in the terminal."""
    if m.get("from") == "bus-server":                      # presence/deaf alerts
        return True
    if "is online (new Claude session joined" in m.get("text", ""):  # joins
        return True
    return False


# Drop silent events; if nothing actionable remains, surface nothing.
msgs = [m for m in msgs if not _silent(m)]
if not msgs:
    sys.exit(0)

lines = "\n".join("[{}] {}".format(m.get("from", "?"), m.get("text", "")) for m in msgs)
show = ("Show these to the user verbatim (as 'BUS <- [sender]: text', plain text, no emojis), then act on them if reasonable and reply "
        "to the sender via the bus (curl -s localhost:8899/send ...), showing the user what you sent. "
        "Before ending your turn, make sure your background bus listener (hooks/bus_wait.sh <your name>, "
        "run_in_background=true) is running — restart it if it exited.")

if mode == "stop":
    print(json.dumps({
        "decision": "block",
        "reason": "New message(s) arrived on the chat bus while you were working:\n"
                  + lines + "\n\n" + show + " Then end your turn."
    }))
else:
    print("Chat-bus messages received while you were idle — handle them along with the user's request. "
          + show + "\n" + lines)

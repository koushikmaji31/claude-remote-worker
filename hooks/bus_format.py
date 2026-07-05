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
if not msgs:
    sys.exit(0)

lines = "\n".join("[{}] {}".format(m.get("from", "?"), m.get("text", "")) for m in msgs)
show = "Show these to the user verbatim (as '📨 [sender] text'), then act on them if reasonable and reply to the sender via the bus (curl -s localhost:8899/send ...), showing the user what you sent."

if mode == "stop":
    print(json.dumps({
        "decision": "block",
        "reason": "New message(s) arrived on the chat bus while you were working:\n"
                  + lines + "\n\n" + show + " Then end your turn."
    }))
else:
    print("Chat-bus messages received while you were idle — handle them along with the user's request. "
          + show + "\n" + lines)

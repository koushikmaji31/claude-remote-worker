#!/usr/bin/env python3
"""Helpers for post_write.sh / pull_latest.sh, kept out of bash to avoid
heredoc quoting problems.

Usage:
  # build the report payload from a footprint on stdin
  format_report.py payload  <project> <machine>   < footprint.json

  # turn a server /diff/report response on stdin into a Claude warning (empty
  # output = no conflict), printed as a PostToolUse hookSpecificOutput JSON
  format_report.py warning   < response.json
"""
import sys, json


def _load():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def do_payload(project, machine):
    fp = _load()
    print(json.dumps({
        "project": project,
        "machine": machine,
        "base_sha": fp.get("base_sha"),
        "files": fp.get("files", {}),
    }))


def do_warning():
    r = _load()
    conflicts = r.get("conflicts") or []
    if not conflicts:
        return  # no output -> hook stays silent
    lines = ["MERGE-CONFLICT RISK: another machine has uncommitted changes overlapping yours."]
    for c in conflicts:
        yl = c.get("your_lines") or [0, 0]
        tl = c.get("their_lines") or [0, 0]
        base = str(c.get("their_base_sha"))[:8]
        lines.append(
            "  - {file}: your lines {y0}-{y1} overlap {m} lines {t0}-{t1} (their base {b})".format(
                file=c.get("file"), y0=yl[0], y1=yl[1], m=c.get("machine"),
                t0=tl[0], t1=tl[1], b=base,
            )
        )
    lines.append(
        "Reconcile before pushing: git pull --rebase, or coordinate with the other dev "
        "on the bus, then re-check. This is a prediction from live per-machine diffs, "
        "not a git error yet."
    )
    msg = "\n".join(lines)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": msg,
        }
    }))


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: format_report.py payload <project> <machine> | warning")
    cmd = sys.argv[1]
    if cmd == "payload":
        do_payload(sys.argv[2], sys.argv[3])
    elif cmd == "warning":
        do_warning()
    else:
        sys.exit(f"unknown cmd: {cmd}")


if __name__ == "__main__":
    main()

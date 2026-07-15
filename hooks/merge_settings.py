#!/usr/bin/env python3
"""Idempotently merge the collab-kit hook entries into an existing Claude
settings.json, so a repo that already has one still gets the bus hooks wired
(join-bus.sh calls this instead of refusing to touch an existing settings file).

Usage: merge_settings.py <target settings.json> <source settings.json>
Only adds hook commands that aren't already present; never removes or reorders
the repo's own hooks. Writes the target in place.
"""
import json
import sys


def _commands(groups):
    out = set()
    for g in groups or []:
        for h in g.get("hooks", []):
            out.add(h.get("command"))
    return out


def main(target, source):
    try:
        with open(target) as f:
            tgt = json.load(f)
    except Exception:
        tgt = {}
    if not isinstance(tgt, dict):
        tgt = {}
    with open(source) as f:
        src = json.load(f)

    tgt.setdefault("hooks", {})
    for event, groups in src.get("hooks", {}).items():
        tgt["hooks"].setdefault(event, [])
        present = _commands(tgt["hooks"][event])
        for g in groups:
            new_hooks = [h for h in g.get("hooks", []) if h.get("command") not in present]
            if new_hooks:
                merged = {k: v for k, v in g.items() if k != "hooks"}
                merged["hooks"] = new_hooks
                tgt["hooks"][event].append(merged)

    with open(target, "w") as f:
        json.dump(tgt, f, indent=2)
        f.write("\n")
    print("merged bus hooks into existing settings.json")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: merge_settings.py <target> <source>")
    main(sys.argv[1], sys.argv[2])

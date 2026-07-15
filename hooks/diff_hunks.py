#!/usr/bin/env python3
"""Compute this working tree's PENDING footprint vs a base ref.

The footprint is the set of touched line ranges (on the NEW / working-tree
side) per file, comparing the working tree (staged + unstaged +
committed-but-unpushed) against a base ref. Used to predict merge conflicts
between developer machines.

Stdlib only. CLI: python3 diff_hunks.py <repo_path> [base]
"""
import json
import re
import subprocess
import sys

# @@ -a,b +c,d @@  (the ,b and ,d parts are optional)
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def _git(repo_path, args):
    """Run a git command, return CompletedProcess. Raises on timeout."""
    return subprocess.run(
        ["git", "-C", repo_path] + args,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _resolve_base(repo_path, base):
    """Resolve base to a full sha. Returns (sha, effective_base, note).

    Falls back to HEAD if the requested base does not resolve.
    """
    res = _git(repo_path, ["rev-parse", base])
    if res.returncode == 0:
        return res.stdout.strip(), base, None
    # Fall back to HEAD.
    res = _git(repo_path, ["rev-parse", "HEAD"])
    if res.returncode == 0:
        note = "base %r did not resolve; fell back to HEAD" % base
        return res.stdout.strip(), "HEAD", note
    return None, None, None


def compute_footprint(repo_path, base="origin/main"):
    """Compute the pending footprint of a working tree vs a base ref.

    Returns:
        {
          "base_sha": "<full sha base resolves to>",
          "files": {"<rel/path>": [[start, end], ...], ...},
          # optionally "note" if the base was substituted,
          # or "error" on failure (with base_sha=None, files={}).
        }
    """
    try:
        base_sha, effective_base, note = _resolve_base(repo_path, base)
        if base_sha is None:
            return {
                "base_sha": None,
                "files": {},
                "error": "could not resolve base %r or HEAD" % base,
            }

        res = _git(
            repo_path,
            ["diff", "--unified=0", effective_base, "--"],
        )
        if res.returncode != 0:
            return {
                "base_sha": None,
                "files": {},
                "error": "git diff failed: %s" % (res.stderr.strip() or "unknown"),
            }

        files = {}
        cur_new_path = None  # path from +++ b/<path>
        cur_old_path = None  # path from --- a/<path>
        cur_binary = False

        def flush_target():
            """Pick the effective path for the current file block."""
            if cur_new_path and cur_new_path != "/dev/null":
                return cur_new_path
            if cur_old_path and cur_old_path != "/dev/null":
                return cur_old_path
            return None

        for line in res.stdout.splitlines():
            if line.startswith("diff --git "):
                # New file block starts; reset per-file state.
                cur_new_path = None
                cur_old_path = None
                cur_binary = False
                continue
            if line.startswith("Binary files ") or line.startswith("GIT binary patch"):
                cur_binary = True
                continue
            if line.startswith("--- "):
                p = line[4:].strip()
                if p.startswith("a/"):
                    p = p[2:]
                cur_old_path = p
                continue
            if line.startswith("+++ "):
                p = line[4:].strip()
                if p.startswith("b/"):
                    p = p[2:]
                cur_new_path = p
                continue
            if line.startswith("@@"):
                if cur_binary:
                    continue
                m = _HUNK_RE.match(line)
                if not m:
                    continue
                c = int(m.group(1))
                d = 1 if m.group(2) is None else int(m.group(2))
                if d == 0:
                    # Pure deletion at this point: zero-width touch.
                    start, end = c, c
                else:
                    start, end = c, c + d - 1
                path = flush_target()
                if path is None:
                    continue
                files.setdefault(path, []).append([start, end])

        result = {"base_sha": base_sha, "files": files}
        if note:
            result["note"] = note
        return result
    except subprocess.TimeoutExpired:
        return {"base_sha": None, "files": {}, "error": "git command timed out"}
    except Exception as exc:  # noqa: BLE001 - never crash
        return {"base_sha": None, "files": {}, "error": str(exc)}


def main(argv):
    if len(argv) < 2:
        sys.stderr.write("usage: diff_hunks.py <repo_path> [base]\n")
        return 2
    repo_path = argv[1]
    base = argv[2] if len(argv) > 2 else "origin/main"
    footprint = compute_footprint(repo_path, base)
    print(json.dumps(footprint, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

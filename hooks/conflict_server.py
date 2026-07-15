#!/usr/bin/env python3
"""
Conflict-detection server (local prototype).

Each developer machine's Claude runs a PostToolUse hook after Write/Edit. The
hook computes that machine's *pending* diff (working tree + unpushed commits vs
the shared base, origin/main) as a set of touched line ranges per file, and
POSTs it here. On every report we compare the reporting machine's touched
ranges against every OTHER machine's stored ranges on the same file. If two
machines touch overlapping lines of the same file, a merge conflict is coming —
we return that as a warning the hook surfaces to Claude.

State is per (project, machine): the machine's full current pending footprint.
Each report REPLACES that machine's footprint (a machine always reports its
complete current diff, so stale hunks from earlier writes are dropped).

No auth, single-file, stdlib only — this is the local prototype. Folding these
endpoints into app/platform.py (with project/auth) is a later step.

Endpoints:
  POST /diff/report   body: {project, machine, base_sha, files: {path: [[start,end],...]}}
                      -> {ok, conflicts: [{machine, file, your_lines, their_lines, their_base_sha}]}
  GET  /diff/state?project=P            -> full stored state for a project (debug)
  POST /diff/clear    body: {project, machine}   -> drop a machine's footprint (e.g. after push)
  GET  /health
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# state[project][machine] = {"base_sha": str, "files": {path: [[start,end],...]}}
_state = {}
_lock = threading.Lock()


def _ranges_overlap(a, b):
    """a, b are [start, end] inclusive 1-based line ranges. True if they intersect."""
    return a[0] <= b[1] and b[0] <= a[1]


def _find_conflicts(project, reporter, base_sha, files):
    """Compare reporter's touched ranges against every other machine in project.

    Returns a list of conflict dicts. Must be called under _lock.
    """
    conflicts = []
    proj = _state.get(project, {})
    for other_machine, other in proj.items():
        if other_machine == reporter:
            continue
        other_files = other.get("files", {})
        for path, my_ranges in files.items():
            their_ranges = other_files.get(path)
            if not their_ranges:
                continue
            for mr in my_ranges:
                for tr in their_ranges:
                    if _ranges_overlap(mr, tr):
                        conflicts.append({
                            "machine": other_machine,
                            "file": path,
                            "your_lines": mr,
                            "their_lines": tr,
                            "their_base_sha": other.get("base_sha"),
                        })
    return conflicts


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def log_message(self, *args):
        pass  # quiet

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            return self._send(200, {"ok": True})
        if parsed.path == "/diff/state":
            qs = parse_qs(parsed.query)
            project = (qs.get("project") or [""])[0]
            with _lock:
                return self._send(200, {"project": project, "state": _state.get(project, {})})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            body = self._read_json()
        except Exception as e:
            return self._send(400, {"error": f"bad json: {e}"})

        if parsed.path == "/diff/report":
            project = body.get("project")
            machine = body.get("machine")
            base_sha = body.get("base_sha")
            files = body.get("files") or {}
            if not project or not machine:
                return self._send(400, {"error": "project and machine required"})
            with _lock:
                conflicts = _find_conflicts(project, machine, base_sha, files)
                _state.setdefault(project, {})[machine] = {
                    "base_sha": base_sha,
                    "files": files,
                }
            return self._send(200, {"ok": True, "conflicts": conflicts})

        if parsed.path == "/diff/clear":
            project = body.get("project")
            machine = body.get("machine")
            with _lock:
                if project in _state and machine in _state[project]:
                    del _state[project][machine]
            return self._send(200, {"ok": True})

        return self._send(404, {"error": "not found"})


def main():
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8901
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"conflict server on http://127.0.0.1:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()

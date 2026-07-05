"""
Example of what YOUR server does: route to the laptop when it's up, fall back to the cloud.

This is the "laptop-primary, cloud-fallback" logic. Drop it into your server, or run it
standalone to test. It does NOT need to live with the worker — it lives wherever your
server is.
"""

import os
import requests

LAPTOP_URL = os.environ.get("LAPTOP_URL", "http://your-laptop-tailscale-name:8787")
CLOUD_URL  = os.environ.get("CLOUD_URL",  "http://your-vps-ip:8787")
TOKEN      = os.environ["WORKER_TOKEN"]

HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


def pick_worker() -> str:
    """Prefer the laptop; fall back to cloud if the laptop isn't reachable."""
    for url in (LAPTOP_URL, CLOUD_URL):
        try:
            r = requests.get(f"{url}/health", timeout=2)
            if r.ok and r.json().get("ok"):
                return url
        except requests.RequestException:
            continue
    raise RuntimeError("no Claude worker available (laptop off AND cloud down)")


def ask(prompt: str, session_id: str | None = None) -> dict:
    url = pick_worker()
    body = {"prompt": prompt}
    if session_id:
        body["session_id"] = session_id
    r = requests.post(f"{url}/ask", json=body, headers=HEADERS, timeout=320)
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    out = ask("In one sentence, what repo am I in and what's its git status?")
    print("ANSWER:", out["result"])
    print("SESSION:", out["session_id"], "(pass this back to continue the conversation)")

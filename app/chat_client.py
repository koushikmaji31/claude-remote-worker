#!/usr/bin/env python3
"""Tiny chat client for the local message server.

Usage: python3 app/chat_client.py <name>
Plain line = broadcast, "@name message" = direct message. Ctrl-C to quit.
"""

import json
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SERVER = "http://127.0.0.1:8899"
stop = threading.Event()


def recv_loop(name):
    url = f"{SERVER}/recv?name={urllib.parse.quote(name)}&timeout=25"
    while not stop.is_set():
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = json.loads(resp.read().decode())
            for msg in data.get("messages", []):
                ts = time.strftime("%H:%M:%S", time.localtime(msg.get("ts", time.time())))
                tag = "" if msg.get("to") is None else " (dm)"
                print(f"\r[{ts}] [{msg['from']}]{tag} {msg['text']}")
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            if stop.is_set():
                return
            print("\r[!] connection error, retrying in 3s...", file=sys.stderr)
            stop.wait(3)


def send(name, to, text):
    payload = json.dumps({"sender": name, "to": to, "text": text}).encode()
    req = urllib.request.Request(
        f"{SERVER}/send", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except (urllib.error.URLError, OSError) as e:
        print(f"[!] send failed: {e}", file=sys.stderr)


def main():
    if len(sys.argv) != 2:
        print("usage: python3 app/chat_client.py <name>", file=sys.stderr)
        sys.exit(1)
    name = sys.argv[1]
    threading.Thread(target=recv_loop, args=(name,), daemon=True).start()
    print(f"connected as {name} — plain line broadcasts, '@name msg' sends a DM, Ctrl-C quits")
    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            if line.startswith("@"):
                parts = line[1:].split(None, 1)
                if len(parts) < 2:
                    print("[!] usage: @name message", file=sys.stderr)
                    continue
                send(name, parts[0], parts[1])
            else:
                send(name, None, line)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        print("\nbye")


if __name__ == "__main__":
    main()

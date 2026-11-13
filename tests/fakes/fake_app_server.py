#!/usr/bin/env python3
"""A fake vibe-app-server for tests/test_vibe_relay.py: newline-delimited JSON-RPC on stdio.

  session/start, session/resume   answer {"state": {"session": {"id": FAKE_SESSION}}}
  session/turn/enqueue           answer {"queueItemId": "q<n>", "waits": false}, then send
                                 turn/started and turn/completed for its session;
                                 FAKE_ENQUEUE_ERROR=1: answer an error instead;
                                 FAKE_ENQUEUE_SILENT=1: never answer
  anything else with an id       answer {}

Every line it reads is appended, as received, to FAKE_SERVER_LOG (one line each), so a test
sees exactly what the relay passed on. Exits with code FAKE_EXIT_CODE (default 0) at end of stdin.
"""
import json
import os
import sys

SESSION = os.environ.get("FAKE_SESSION", "11111111-2222-3333-4444-555555555555")
LOG = os.environ.get("FAKE_SERVER_LOG")


def out(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def main():
    n = 0
    for line in sys.stdin:
        if LOG:
            with open(LOG, "a", encoding="utf-8") as f:
                f.write(line if line.endswith("\n") else line + "\n")
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        method, rid = msg.get("method"), msg.get("id")
        if rid is None:
            continue
        if method in ("session/start", "session/resume"):
            out({"jsonrpc": "2.0", "id": rid, "result": {"state": {"session": {"id": SESSION}}}})
        elif method == "session/turn/enqueue":
            if os.environ.get("FAKE_ENQUEUE_SILENT") == "1":
                continue
            if os.environ.get("FAKE_ENQUEUE_ERROR") == "1":
                out({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "refused"}})
                continue
            n += 1
            sid = (msg.get("params") or {}).get("sessionId")
            out({"jsonrpc": "2.0", "id": rid, "result": {"queueItemId": "q%d" % n, "waits": False}})
            out({"jsonrpc": "2.0", "method": "turn/started", "params": {"sessionId": sid}})
            out({"jsonrpc": "2.0", "method": "turn/completed", "params": {"sessionId": sid}})
        else:
            out({"jsonrpc": "2.0", "id": rid, "result": {}})
    sys.exit(int(os.environ.get("FAKE_EXIT_CODE", "0")))


if __name__ == "__main__":
    main()

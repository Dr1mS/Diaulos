#!/usr/bin/env python3
"""Throwaway vibe hook used by the probes. Standard library only.

Logs what vibe hands a hook (stdin JSON, parent chain, cwd, environment with
secrets redacted) as JSON lines in $PROBE_LOG.

For post_agent_turn: waits up to $PROBE_LISTEN seconds (0 by default) for a
file $PROBE_DIR/inject. When it appears, the hook consumes it and answers
{"decision": "deny", "reason": <file content>}, which vibe injects as a new
user message and which starts a new turn.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from probe_mcp import parent_chain, redacted_env  # noqa: E402

LOG = os.environ.get("PROBE_LOG", "/tmp/probe_hook.log")
DIR = os.environ.get("PROBE_DIR", "/tmp")


def log(kind, **data):
    rec = {"t": round(time.time(), 3), "pid": os.getpid(), "kind": kind, **data}
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = {"raw": raw[:500]}
    log("hook", stdin=data, cwd=os.getcwd(), parents=parent_chain(os.getpid()),
        env=redacted_env())
    if data.get("hook_event_name") != "post_agent_turn":
        return
    deadline = time.time() + float(os.environ.get("PROBE_LISTEN", "0"))
    inject = os.path.join(DIR, "inject")
    while True:
        if os.path.exists(inject):
            claimed = inject + ".claimed"
            try:
                os.replace(inject, claimed)
            except OSError:
                pass
            else:
                with open(claimed, encoding="utf-8") as f:
                    text = f.read()
                os.remove(claimed)
                log("inject", text=text, waited=round(time.time() - deadline, 3))
                print(json.dumps({"decision": "deny", "reason": text}))
                return
        if time.time() >= deadline:
            log("listen-timeout")
            return
        time.sleep(0.2)


if __name__ == "__main__":
    main()

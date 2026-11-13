"""A Vibe 3 hook for the tests/vibe3 probes (task T10b): logs its payload to $PROBE_LOG.

pre_agent: while $PROBE_DIR/predeny exists, answers {"decision": "deny", "reason": <its text>}.

post_agent: while $PROBE_DIR/deny holds text, answers {"decision": "deny", "reason": <text>}
once (the file is removed), after sleeping $PROBE_DIR/sleep seconds if that file exists.
"""
import json
import os
import sys
import time

payload = json.load(sys.stdin)
d = os.environ.get("PROBE_DIR", "")
with open(os.environ.get("PROBE_LOG", os.devnull), "a", encoding="utf-8") as f:
    f.write(json.dumps({"kind": "hook", "pid": os.getpid(), "ppid": os.getppid(), "at": time.time(),
                        "payload": payload}) + "\n")
if payload.get("hook_event_name") == "pre_agent" and d:
    try:
        with open(os.path.join(d, "predeny"), encoding="utf-8") as f:
            print(json.dumps({"decision": "deny", "reason": f.read()}))
    except OSError:
        pass
if payload.get("hook_event_name") == "post_agent" and d:
    try:
        with open(os.path.join(d, "sleep")) as f:
            time.sleep(float(f.read()))
    except OSError:
        pass
    try:
        with open(os.path.join(d, "deny"), encoding="utf-8") as f:
            reason = f.read()
        os.remove(os.path.join(d, "deny"))
    except OSError:
        reason = ""
    if reason:
        print(json.dumps({"decision": "deny", "reason": reason}))

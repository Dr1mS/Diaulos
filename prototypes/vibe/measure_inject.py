#!/usr/bin/env python3
"""Drop one message for the listening probe hook and time what follows.

Usage: measure_inject.py <probe-dir> <token> [timeout-seconds] [file-name]

file-name: "inject" (the probe hook, default) or "inject.txt" (the probe plugin).

Writes <probe-dir>/inject atomically, then watches the session transcript
(vibe_home/logs/session/*/messages.jsonl) and the probe log. Prints, in
seconds from the moment the file is written:
- consumed: the hook took the file (probe log "inject" record),
- user:     the injected user message appears in the transcript,
- reply:    an assistant message containing <token> follows it,
- next_listen: the next post_agent_turn hook starts (vibe idle again).
Timestamps are wall-clock (time.time()) on the same machine.
"""

import glob
import json
import os
import sys
import time


def main():
    d, token = sys.argv[1], sys.argv[2]
    limit = float(sys.argv[3]) if len(sys.argv) > 3 else 90
    name = sys.argv[4] if len(sys.argv) > 4 else "inject"
    transcript = glob.glob(os.path.join(d, "vibe_home/logs/session/*/messages.jsonl"))[0]
    log = os.path.join(d, "probe.jsonl")
    plog = os.path.join(d, "plugin.jsonl")
    n_plog = sum(1 for _ in open(plog)) if os.path.exists(plog) else 0
    n_log = sum(1 for _ in open(log))
    n_msg = sum(1 for _ in open(transcript))
    tmp = os.path.join(d, "inject.tmp")
    with open(tmp, "w") as f:
        f.write(f"Message from probe: reply with the single word {token}.")
    t0 = time.time()
    os.replace(tmp, os.path.join(d, name))
    out = {"token": token}
    while time.time() - t0 < limit and "reply" not in out:
        for line in list(open(log))[n_log:]:
            r = json.loads(line)
            if r["kind"] == "inject" and "consumed" not in out:
                out["consumed"] = round(r["t"] - t0, 3)
            s = r.get("stdin") if isinstance(r.get("stdin"), dict) else {}
            if (s.get("hook_event_name") == "post_agent_turn" and "consumed" in out
                    and r["t"] > t0 + out["consumed"] and "next_listen" not in out):
                out["next_listen"] = round(r["t"] - t0, 3)
            if r["kind"] == "hook-error":
                out["error"] = r
        if os.path.exists(plog):
            for line in list(open(plog))[n_plog:]:
                r = json.loads(line)
                if r["kind"] == "claimed" and "consumed" not in out:
                    out["consumed"] = round(r["t"] - t0, 3)
                if r["kind"] == "deliver" and "deliver" not in out:
                    out["deliver"] = round(r["t"] - t0, 3)
                    out["path"] = r["path"]
        for line in list(open(transcript))[n_msg:]:
            m = json.loads(line)
            c = m.get("content") or ""
            if m["role"] == "user" and token in c and "user" not in out:
                out["user"] = round(time.time() - t0, 3)
                out["injected_flag"] = m.get("injected")
            if m["role"] == "assistant" and token in c and "user" in out and "reply" not in out:
                out["reply"] = round(time.time() - t0, 3)
        time.sleep(0.05)
    print(json.dumps(out))


if __name__ == "__main__":
    main()

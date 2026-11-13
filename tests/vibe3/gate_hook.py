"""Hook of the gate probes G1 to G4 (task T11, docs/VIBE-DESIGN.md §8.4).

Runs for every hook type of the probe plugin. It appends to $GATE_LOG one JSON line when it
starts (payload) and one when it ends (how and when), and is steered by files in $GATE_DIR:

- post_agent: if `listen` exists, waits up to its number of seconds for `inbox`, polling every
  0.2 s; a message found there is removed and answered as {"decision": "deny", "reason": <it>},
  with `post_sysmsg` as system_message when that file exists. Without `listen`, it delivers
  `inbox` at once if present, else answers nothing.
- pre_agent and session_start: if `<type>_sysmsg` exists, answers its text as system_message
  (and as additional_context, so the model gets it too).

A SIGTERM or SIGHUP is logged before the hook exits, so the probe sees when vibe stops it.
"""
import json
import os
import signal
import sys
import time

LOG = os.environ.get("GATE_LOG", os.devnull)
DIR = os.environ.get("GATE_DIR", "")


def log(**row):
    row.update(pid=os.getpid(), at=time.time())
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def read(name):
    try:
        with open(os.path.join(DIR, name), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def take(name):
    text = read(name)
    if text is not None:
        try:
            os.remove(os.path.join(DIR, name))
        except OSError:
            return None
    return text


def stopped(signum, _frame):
    log(kind="signal", signal=signum, event=EVENT)
    sys.exit(128 + signum)


payload = json.load(sys.stdin)
EVENT = payload.get("hook_event_name")
signal.signal(signal.SIGTERM, stopped)
signal.signal(signal.SIGHUP, stopped)
log(kind="start", event=EVENT, payload=payload, plugin_root=os.environ.get("PLUGIN_ROOT"),
    plugin_data=os.environ.get("PLUGIN_DATA"))
answer = None
if EVENT == "post_agent" and DIR:
    listen = read("listen")
    deadline = time.time() + (float(listen) if listen else 0)
    message = take("inbox")
    while message is None and time.time() < deadline:
        time.sleep(0.2)
        message = take("inbox")
    if message is not None:
        answer = {"decision": "deny", "reason": message}
        sysmsg = read("post_sysmsg")
        if sysmsg:
            answer["system_message"] = sysmsg
elif EVENT in ("pre_agent", "session_start") and DIR:
    sysmsg = read(EVENT + "_sysmsg")
    if sysmsg:
        answer = {"system_message": sysmsg, "hook_specific_output": {"additional_context": sysmsg}}
log(kind="end", event=EVENT, answer=answer)
if answer:
    print(json.dumps(answer))

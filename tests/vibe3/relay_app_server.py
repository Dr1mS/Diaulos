#!/usr/bin/env python3
"""T12 probe relay: stands between the vibe 3 TUI (vibe-rs) and the real vibe-app-server.

Probe only, not Diaulos code. vibe-rs starts its app-server from VIBE_APP_SERVER_BIN or
VIBE_APP_SERVER_CMD; tests/vibe3/relay_probe.py points one of them at this file. The relay
starts the real server ($RELAY_REAL, with the arguments it was given), passes every line both
ways unchanged, and logs each one to $RELAY_LOG as {"dir": "tui>srv" | "srv>tui" | ..., "at",
"method", "id", "bytes"}: a whitelist of keys (KEPT), never params, result, error or text.

Injection: when the file $RELAY_DIR/inject appears, the relay reads its text and removes it.
It takes the last request the TUI sent whose params hold the TUI's own typed text (the
`template`, learned from a turn the person typed), replaces that text with the injected one,
gives it a fresh id "diaulos-relay-N" and a fresh idempotency key, and sends it to the real
server. The answer to that id is kept by the relay (logged as "srv>relay"), never shown to the
TUI. Everything else the server sends (the events of the turn) goes to the TUI as usual.
"""
import json
import os
import subprocess
import sys
import threading
import time
import uuid

LOG = os.environ.get("RELAY_LOG", os.devnull)
DIR = os.environ.get("RELAY_DIR", "")
REAL = os.environ["RELAY_REAL"]
TYPED = os.environ.get("RELAY_TEMPLATE_TEXT", "")
lock = threading.Lock()
state = {"template": None, "n": 0, "mine": set()}


# The log is a whitelist: these keys only, never params, result, error or any text.
# The protocol carries the person's account (identity/read, account/read) and every prompt.
KEPT = ("dir", "at", "method", "id", "bytes", "pid", "argc", "rc", "reason")


def short(value):
    """A method name or a JSON-RPC id, kept only when it is a short string or an integer."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) or (isinstance(value, str) and len(value) <= 64):
        return value
    return None


def log(direction, line=None, **extra):
    row = {"dir": direction, "at": time.time()}
    if line is not None:
        row["bytes"] = len(line)
        try:
            msg = json.loads(line)
        except ValueError:
            msg = None
        if isinstance(msg, dict):
            for k in ("method", "id"):
                v = short(msg.get(k))
                if v is not None:
                    row[k] = v
    for k, v in extra.items():
        v = short(v)
        if v is not None:
            row[k] = v
    row = {k: row[k] for k in KEPT if k in row}
    with lock:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


def holds(value, text):
    if isinstance(value, str):
        return text in value
    if isinstance(value, dict):
        return any(holds(v, text) for v in value.values())
    if isinstance(value, list):
        return any(holds(v, text) for v in value)
    return False


def replaced(value, old, new):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, dict):
        return {k: replaced(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [replaced(v, old, new) for v in value]
    return value


def fresh_keys(value):
    """New idempotency keys and client message ids, so the server does not replay the template."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if isinstance(v, str) and ("dempotency" in k or k in ("clientUserMessageId", "messageEntryId")):
                out[k] = str(uuid.uuid4())
            else:
                out[k] = fresh_keys(v)
        return out
    if isinstance(value, list):
        return [fresh_keys(v) for v in value]
    return value


log("start", pid=os.getpid(), argc=len(sys.argv) - 1)
child = subprocess.Popen([REAL] + sys.argv[1:], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
out_lock = threading.Lock()
in_lock = threading.Lock()


def to_server(line):
    with in_lock:
        child.stdin.write(line)
        child.stdin.flush()


def tui_to_server():
    for line in sys.stdin.buffer:
        try:
            msg = json.loads(line)
        except ValueError:
            msg = None
        log("tui>srv", line)
        if (TYPED and isinstance(msg, dict) and "id" in msg and "method" in msg
                and holds(msg.get("params"), TYPED)):
            state["template"] = msg
            log("template", method=msg["method"])
        to_server(line)
    log("tui stdin closed")
    child.stdin.close()


def server_to_tui():
    for line in child.stdout:
        try:
            msg = json.loads(line)
        except ValueError:
            msg = None
        if isinstance(msg, dict) and msg.get("id") in state["mine"] and "method" not in msg:
            log("srv>relay", line)
            continue
        log("srv>tui", line)
        with out_lock:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()
    log("server stdout closed", rc=child.wait())


def injector():
    path = os.path.join(DIR, "inject")
    while DIR:
        time.sleep(0.2)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        os.remove(path)
        template = state["template"]
        if template is None:
            log("inject refused", reason="no template yet")
            continue
        state["n"] += 1
        rid = "diaulos-relay-%d" % state["n"]
        msg = fresh_keys(replaced(template, TYPED, text))
        msg["id"] = rid
        state["mine"].add(rid)
        line = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        log("relay>srv", line)
        to_server(line)


threading.Thread(target=server_to_tui, daemon=True).start()
threading.Thread(target=injector, daemon=True).start()
tui_to_server()
child.wait()
time.sleep(0.5)

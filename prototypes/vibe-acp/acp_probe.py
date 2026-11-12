#!/usr/bin/env python3
"""Throwaway ACP client probe for `vibe-acp` (Mistral Vibe 2.20). Standard
library only. Newline-delimited JSON-RPC over the agent's stdio.

Usage: acp_probe.py <run-dir> new  "<prompt>"
       acp_probe.py <run-dir> load <session-id> "<prompt>"

Environment: VIBE_HOME (throwaway), PROBE_EXTRA_SERVER (path of probe_mcp.py,
passed in mcpServers of session/new or session/load to see whether vibe-acp
uses it).

Each run starts one `vibe-acp` process with cwd = <run-dir>/work, does
initialize, session/new or session/load, caps the session with
session/set_config_option max_turns=3 and max_tokens=40000, sends one prompt,
answers any session/request_permission with the option of kind "allow_once",
then closes stdin. Every frame in both directions goes to
<run-dir>/acp-<mode>.jsonl; a summary is printed.
"""

import json
import os
import subprocess
import sys
import threading
import time

VIBE_ACP = os.path.expanduser("~/.local/share/uv/tools/mistral-vibe/bin/vibe-acp")


def main():
    run, mode = sys.argv[1], sys.argv[2]
    work = os.path.join(run, "work")
    log = open(os.path.join(run, f"acp-{mode}.jsonl"), "a", encoding="utf-8")
    proc = subprocess.Popen([VIBE_ACP], cwd=work, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=open(os.path.join(run, f"acp-{mode}.stderr"), "w"),
                            text=True, bufsize=1)
    pending, lock, nid = {}, threading.Lock(), [0]
    summary = {"mode": mode, "permission_requests": [], "updates": {}}

    def rec(direction, msg):
        log.write(json.dumps({"t": round(time.time(), 3), "dir": direction, "msg": msg}) + "\n")
        log.flush()

    def send(msg):
        rec("out", msg)
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    def call(method, params, timeout=120):
        with lock:
            nid[0] += 1
            i = nid[0]
            ev = threading.Event()
            pending[i] = [ev, None]
        send({"jsonrpc": "2.0", "id": i, "method": method, "params": params})
        if not ev.wait(timeout):
            raise TimeoutError(method)
        return pending.pop(i)[1]

    def reader():
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            rec("in", msg)
            if "id" in msg and "method" not in msg:
                if msg["id"] in pending:
                    pending[msg["id"]][1] = msg
                    pending[msg["id"]][0].set()
            elif msg.get("method") == "session/request_permission":
                opts = msg["params"].get("options", [])
                choice = next((o for o in opts if o.get("kind") == "allow_once"), None)
                summary["permission_requests"].append(
                    {"options": [(o.get("optionId"), o.get("kind")) for o in opts],
                     "tool_call": msg["params"].get("toolCall"),
                     "answered": choice and choice.get("optionId")})
                outcome = ({"outcome": "selected", "optionId": choice["optionId"]}
                           if choice else {"outcome": "cancelled"})
                send({"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": outcome}})
            elif msg.get("method") == "session/update":
                kind = msg["params"].get("update", {}).get("sessionUpdate")
                summary["updates"][kind] = summary["updates"].get(kind, 0) + 1
            elif "id" in msg:  # other client requests: refuse
                send({"jsonrpc": "2.0", "id": msg["id"],
                      "error": {"code": -32601, "message": "not supported by probe"}})

    threading.Thread(target=reader, daemon=True).start()
    init = call("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                               "clientInfo": {"name": "acp-probe", "version": "0"}})
    summary["agent"] = init.get("result", {}).get("agentInfo")
    summary["load_session_capability"] = init.get("result", {}).get(
        "agentCapabilities", {}).get("loadSession")
    extra = os.environ.get("PROBE_EXTRA_SERVER")
    servers = []
    if extra:
        servers = [{"name": "extra", "command": "python3", "args": [extra],
                    "env": [{"name": "PROBE_LOG", "value": os.path.join(run, "extra.jsonl")}]}]
    if mode == "new":
        r = call("session/new", {"cwd": os.path.abspath(work), "mcpServers": servers})
        sid = r.get("result", {}).get("sessionId")
        prompt = sys.argv[3]
    else:
        sid = sys.argv[3]
        r = call("session/load", {"sessionId": sid, "cwd": os.path.abspath(work),
                                  "mcpServers": servers})
        prompt = sys.argv[4]
    summary["session_response"] = r.get("error") or {"sessionId": sid}
    for cid, val in (("max_turns", "3"), ("max_tokens", "40000")):
        c = call("session/set_config_option", {"sessionId": sid, "configId": cid, "value": val})
        summary[f"set_{cid}"] = c.get("error") or "ok"
    p = call("session/prompt", {"sessionId": sid,
                                "prompt": [{"type": "text", "text": prompt}]}, timeout=180)
    summary["prompt_result"] = p.get("result") or p.get("error")
    proc.stdin.close()
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.terminate()
    summary["exit"] = proc.returncode
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Throwaway MCP stdio server used to probe what Mistral Vibe 2.20 does with
an MCP server. Standard library only.

It logs, as JSON lines in $PROBE_LOG:
- its start (pid, parent chain, cwd, environment with secrets redacted),
- every message it receives from vibe,
- the answers to the server-to-client requests it sends.

With PROBE_PUSH=1, two seconds after `initialized` it sends what a server
could use to wake an idle client: a log notification, tools/list_changed,
the Claude channel notification and a sampling/createMessage request.
"""

import json
import os
import sys
import threading
import time

LOG = os.environ.get("PROBE_LOG", "/tmp/probe_mcp.log")
LOCK = threading.Lock()
SECRET_HINTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CSRF", "COOKIE")


def log(kind, **data):
    rec = {"t": round(time.time(), 3), "pid": os.getpid(), "kind": kind, **data}
    with LOCK, open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def parent_chain(pid):
    chain = []
    while pid > 1 and len(chain) < 8:
        try:
            with open(f"/proc/{pid}/stat") as f:
                ppid = int(f.read().rsplit(")", 1)[1].split()[1])
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            break
        chain.append({"pid": pid, "cmd": cmd[:200]})
        pid = ppid
    return chain


def redacted_env():
    out = {}
    for k, v in sorted(os.environ.items()):
        out[k] = "<redacted>" if any(h in k.upper() for h in SECRET_HINTS) else v[:200]
    return out


def write(msg):
    with LOCK:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


TOOLS = [
    {
        "name": "ping",
        "description": "Probe tool. Returns the server pid and a timestamp.",
        "inputSchema": {"type": "object", "properties": {}},
    }
]


def push_later():
    time.sleep(2)
    log("push", what="notifications/message + tools/list_changed + claude/channel + sampling")
    write({"jsonrpc": "2.0", "method": "notifications/message",
           "params": {"level": "info", "data": "probe: wake up"}})
    write({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    write({"jsonrpc": "2.0", "method": "notifications/claude/channel",
           "params": {"content": "probe: wake up", "meta": {}}})
    write({"jsonrpc": "2.0", "id": "probe-sampling-1", "method": "sampling/createMessage",
           "params": {"messages": [{"role": "user", "content": {"type": "text",
                      "text": "Reply with the single word PONG."}}], "maxTokens": 5}})


def main():
    log("start", argv=sys.argv, cwd=os.getcwd(), parents=parent_chain(os.getpid()),
        env=redacted_env())
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            log("bad-line", line=line[:300])
            continue
        method = msg.get("method")
        log("recv", method=method, id=msg.get("id"),
            params=msg.get("params") if method in ("initialize",) else None,
            result=msg.get("result"), error=msg.get("error"))
        if method == "initialize":
            write({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": msg["params"].get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {"listChanged": True}, "logging": {},
                                 "experimental": {"claude/channel": {}}},
                "serverInfo": {"name": "probe", "version": "0"}}})
        elif method == "notifications/initialized":
            if os.environ.get("PROBE_PUSH") == "1":
                threading.Thread(target=push_later, daemon=True).start()
        elif method == "tools/list":
            write({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": TOOLS}})
        elif method == "tools/call":
            text = json.dumps({"pid": os.getpid(), "t": time.time()})
            write({"jsonrpc": "2.0", "id": msg["id"], "result": {
                "content": [{"type": "text", "text": text}]}})
        elif "id" in msg and method is not None:
            write({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
    log("stdin-closed")


if __name__ == "__main__":
    main()

"""A minimal MCP stdio server for the tests/vibe3 probes (task T10b): one tool, probe_ping.

It records who started it (pid, parent pid and command line, environment, cwd) in
$PROBE_LOG, so the probe can tell whether Vibe 3 starts one server per session or per
process, and what a server learns about the session.
"""
import json
import os
import sys

LOG = os.environ.get("PROBE_LOG", os.devnull)


def record(kind, **data):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(kind=kind, pid=os.getpid(), **data)) + "\n")


def cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return f.read().replace(b"\0", b" ").decode("utf-8", "replace").strip()
    except OSError:
        return None


ppid = os.getppid()
record("start", ppid=ppid, parent=cmdline(ppid), cwd=os.getcwd(),
       env={k: v for k, v in os.environ.items()
            if k.startswith(("VIBE", "MCP", "AGENT_BRIDGE", "PROBE")) or "SESSION" in k})

for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method, mid = msg.get("method"), msg.get("id")
    record("request", method=method)
    if mid is None:
        continue
    if method == "initialize":
        result = {"protocolVersion": msg["params"].get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}}, "serverInfo": {"name": "probe", "version": "1"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "probe_ping", "description": "Answers PONG-MCP.",
                             "inputSchema": {"type": "object", "properties": {}}}]}
    elif method == "tools/call":
        record("call", params=msg.get("params"))
        result = {"content": [{"type": "text", "text": "PONG-MCP"}]}
    elif method == "ping":
        result = {}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": method}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}), flush=True)
record("exit")

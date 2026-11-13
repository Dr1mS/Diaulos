#!/usr/bin/env python3
"""MCP server `diaulos` of the Diaulos plugin for Mistral Vibe 3 (docs/VIBE-DESIGN.md §8.2, §8.7).
Python 3.8+, standard library only. Newline-delimited JSON-RPC on stdio, as mcp_channel.py.

vibe starts it through the plugin's launcher ./vibe-mcp, one process per vibe-app-server (not
per session). Tools:
  send(to, text, workspace?)   message an agent; the sender is this vibe conversation
  list_agents(all?, days?)     one line per reachable agent
vibe shows them as mcp_diaulos.send and mcp_diaulos.list_agents; the installer gives both
`permission = "always"` in config.toml ([tools.diaulos_send], [tools.diaulos_list_agents]).

The sender is read at each call from state/vibe-current/<vibe-app-server pid>.json, written by
the session_start hook, so /resume, /clear and a fork are followed. A subagent sends as its main
session. After MAX_FAILURES failed sends to one recipient in this process, `send` stops without
sending (§4.1, "A sender nobody reads"). Logs go to state/vibe-mcp.log, never a message text.
"""
import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bridge  # noqa: E402  (bridge.py ships next to this file)

SERVER_NAME = "diaulos"
MAX_PROTOCOL = "2025-06-18"
MAX_FAILURES = 2
INSTRUCTIONS = ("Diaulos, the agent bridge: `send` messages another agent (a Claude Code session claude:<name>, "
                "an agy conversation agy:<id or name>, another vibe conversation vibe:<suffix or name>, a program "
                "ext:<name>, or a session on a paired machine remote:<machine>/<address>). Messages to you arrive "
                "as <bridge-message> blocks. If `send` answers with an error, do not call it again for that "
                "address: say so in your answer.")
TOOLS = [
    {"name": "send",
     "description": "Send a message to another agent through the agent bridge. `to` is an address from "
                    "list_agents (for example claude:review, agy:<id>, vibe:<suffix>), or the `from` of a "
                    "message you received. vibe:new starts a new vibe conversation, agy:new a new Antigravity "
                    "one; `workspace` picks their directory.",
     "inputSchema": {"type": "object", "properties": {
         "to": {"type": "string", "description": "Recipient address"},
         "text": {"type": "string", "description": "Message body"},
         "workspace": {"type": "string", "description": "Only for agy:new, agy:new-cli and vibe:new"}},
         "required": ["to", "text"]}},
    {"name": "list_agents",
     "description": "List the agents reachable through the agent bridge, one per line; yours is marked (you).",
     "inputSchema": {"type": "object", "properties": {
         "all": {"type": "boolean", "description": "Every conversation, whatever its age"},
         "days": {"type": "integer", "description": "Conversations of the last N days (default 7)"}}}},
]

_out_lock = threading.Lock()
_failures = {}


def log(text):
    try:
        path = bridge._path("state", "vibe-mcp.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write("%s pid %d %s\n" % (bridge._now(), os.getpid(), text))
    except Exception:
        pass


def write(msg):
    with _out_lock:
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def text_result(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def me():
    """This conversation's address, from the vibe-current file of our vibe-app-server, or None."""
    vs = bridge._vibe()
    record = vs.read_current(vs.app_server_pid())
    return record["address"] if record else None


def send(args):
    to, text = args.get("to"), args.get("text")
    if not isinstance(to, str) or not to or not isinstance(text, str):
        return text_result("`to` and `text` must be strings", is_error=True)
    sender = me()
    if not sender:
        return text_result("this vibe session has no Diaulos address yet (its session_start hook did not run)",
                           is_error=True)
    if _failures.get(to, 0) >= MAX_FAILURES:
        return text_result("stopped: sending to %s failed twice, do not call send for it again" % to, is_error=True)
    try:
        msg = bridge.send(to, text, sender, args.get("workspace") or None)
    except SystemExit as e:  # resolve() exits with a readable reason
        _failures[to] = _failures.get(to, 0) + 1
        return text_result("error: %s" % e, is_error=True)
    except Exception as e:  # e.g. a locked file: answer, never break the turn
        _failures[to] = _failures.get(to, 0) + 1
        log("send failed: %r" % (e,))
        return text_result("error: send failed: %r" % (e,), is_error=True)
    log("sent %s from %s to %s" % (msg["id"], sender, msg["to"]))
    return text_result("sent %s from %s to %s" % (msg["id"], sender, msg["to"]))


def list_agents(args):
    days = None if args.get("all") else args.get("days") or bridge.RECENT_DAYS
    if not isinstance(days, (int, type(None))) or isinstance(days, bool) or (days is not None and days < 1):
        return text_result("`days` must be a positive integer", is_error=True)
    mine = me()
    try:
        found = bridge.peers(days=days)
    except SystemExit as e:
        return text_result(str(e), is_error=True)
    lines = [bridge.describe(p) + ("  (you)" if p["address"] == mine else "") for p in found]
    return text_result("\n".join(lines) or "no live agent")


def handle(msg):
    method, req_id = msg.get("method"), msg.get("id")
    if method in ("initialize", "tools/list"):
        log("%s (parent %d, cwd %s)" % (method, os.getppid(), "plugin copy" if "packages" in os.getcwd() else "other"))
    if method == "initialize":
        requested = (msg.get("params") or {}).get("protocolVersion") or MAX_PROTOCOL
        write({"jsonrpc": "2.0", "id": req_id, "result": {
            "protocolVersion": min(str(requested), MAX_PROTOCOL),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": "1"},
            "instructions": INSTRUCTIONS}})
    elif method == "ping":
        write({"jsonrpc": "2.0", "id": req_id, "result": {}})
    elif method == "tools/list":
        write({"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name, args = params.get("name"), params.get("arguments") or {}
        if name == "send":
            value = send(args)
        elif name == "list_agents":
            value = list_agents(args)
        else:
            value = text_result("unknown tool %s" % name, is_error=True)
        write({"jsonrpc": "2.0", "id": req_id, "result": value})
    elif req_id is not None:
        write({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "method not found: %s" % method}})


def main():
    bridge.utf8_stdio()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = None
        try:
            msg = json.loads(line)
            if isinstance(msg, dict):
                handle(msg)
        except (Exception, SystemExit) as e:  # one bad request never kills the server
            log("error %r" % (e,))
            if isinstance(msg, dict) and msg.get("id") is not None:
                write({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32603, "message": "internal error: %r" % (e,)}})


if __name__ == "__main__":
    main()

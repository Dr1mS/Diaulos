"""Delivery to closed Mistral Vibe conversations, and new ones, through vibe-acp
(docs/VIBE-DESIGN.md §4.4 to §4.6). Python 3.8+, standard library only.

bridge.py loads this file by path as `agent_bridge_vibe_runner`, after it has
registered itself as `agent_bridge_core`, and runs:

  bridge.py vibe-run <suffix>   one per conversation, started by `send` when no
                                vibe TUI serves it. It waits 1 s so that a burst
                                goes in one turn, then speaks ACP to vibe-acp in
                                the conversation's directory: initialize,
                                session/load (same id, same folder),
                                session/set_config_option max_turns,
                                session/prompt. It loops while the inbox has
                                messages, then closes vibe-acp and exits.
  bridge.py vibe-new            one worker: one session/new and its first
                                session/prompt per message sent to vibe:new.

Nobody is there to approve a tool: every session/request_permission gets
`reject_once`. The bridge tools of the plugin are ALWAYS and never ask.

Caps (§4.4, §4.11): `max_turns` through ACP (public), and a price per run
passed to the plugin as AGENT_BRIDGE_VIBE_MAX_PRICE (best effort, private
names). Both, and the number of attempts per batch, come from
state/vibe-limits.json; a missing or invalid value gives its default.

Before a prompt, the runner writes state/vibe-inflight/<suffix>.json with its
pid, the session and the claimed messages. A runner killed in the middle of a
turn leaves it behind; whoever next takes the conversation (a runner or the
plugin) puts those messages back in the inbox (recover_inflight).
"""
import contextlib
import json
import os
import queue
import subprocess
import sys
import threading
import time

LIMITS = {"max_turns": 20, "max_price": 0.50, "attempts": 3, "retry_seconds": None}
TURN_LIMIT_TEXT = "Turn limit of"
WORD_JOINER = "⁠"


def _core():
    return sys.modules["agent_bridge_core"]


def _now():
    return _core()._now()


# ---------------------------------------------------------------- limits

def limits():
    """state/vibe-limits.json merged over the defaults, value by value."""
    core = _core()
    found = dict(LIMITS, retry_seconds=core.RELAY_RETRY_SECONDS)
    try:
        with open(core._path("state", "vibe-limits.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return found
    if not isinstance(data, dict):
        return found
    checks = {
        "max_turns": lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 1000,
        "max_price": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v <= 100,
        "attempts": lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 10,
        "retry_seconds": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 3600,
    }
    for key, ok in checks.items():
        if key in data and ok(data[key]):
            found[key] = data[key]
    return found


# ---------------------------------------------------------------- prompt text

def incoming_text(msgs, me):
    """The bridge's message blocks for vibe: every `@` gets a word joiner, so vibe does not
    inline a file (path_prompt.py rejects U+2060), and the last line names bridge_send."""
    text = _core()._incoming_text(msgs, me)
    end = text.rfind("\nReply with: ")
    if end >= 0:
        text = text[:end]
    text = text.replace("@", "@" + WORD_JOINER)
    senders = sorted({m["from"] for m in msgs})
    # A failed send must not loop: a first real TUI run retried 16 times (0.52 $) on an `ext:` sender
    # with no reader (§4.1, "A sender nobody reads", first guard).
    unread = [a for a in senders if _no_reader(a)]
    readable = [a for a in senders if a not in unread]
    lines = [f"{a} has no reader: do not reply with bridge_send" for a in unread]
    if readable:
        to = readable[0] if len(readable) == 1 else "<the from of the message>"
        lines.append(f"Reply with the bridge_send tool, to={to}  (you are {me})")
    return (text + "\nIf bridge_send answers with an error, do not call it again: say so in your answer.\n"
            + "\n".join(lines))


def _no_reader(address):
    """True for an `ext:` sender that no program watches now."""
    if not address.startswith("ext:"):
        return False
    try:
        return address not in {p["address"] for p in _core().peers("ext")}
    except Exception:
        return False


# ---------------------------------------------------------------- in-flight records

def _inflight_path(suffix):
    folder = _core()._path("state", "vibe-inflight")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{suffix}.json")


def write_inflight(suffix, session, claimed, role):
    path = _inflight_path(suffix)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"pid": os.getpid(), "role": role, "session": session, "at": time.time(),
                   "paths": [p for p, _ in claimed]}, f)
    os.replace(tmp, path)


def clear_inflight(suffix):
    with contextlib.suppress(OSError):
        os.remove(_inflight_path(suffix))


def recover_inflight(suffix, quiet=False):
    """Put back the messages of an in-flight record whose process is dead. Returns how many.

    `suffix` is a conversation suffix, or "new" for the vibe:new worker. `quiet`: print
    nothing (bridge.py calls it from `list` and from the MCP server, whose stdout is JSON-RPC)."""
    core = _core()
    path = _inflight_path(suffix)
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError):
        return 0
    if not isinstance(record, dict) or core._alive(record.get("pid")):
        return 0
    archive = core._dir("archive", f"vibe:{suffix}")
    paths = [p for p in record.get("paths") or []
             if isinstance(p, str) and os.path.dirname(os.path.abspath(p)) == os.path.abspath(archive)
             and os.path.exists(p)]
    core.unclaim(f"vibe:{suffix}", [(p, None) for p in paths])
    clear_inflight(suffix)
    if paths and not quiet:
        print(f"{_now()} put back {len(paths)} message(s) of dead pid {record.get('pid')} "
              f"({record.get('role')})", flush=True)
    return len(paths)


# ---------------------------------------------------------------- ACP client

class AcpError(Exception):
    pass


class Acp:
    """One vibe-acp process, newline-delimited JSON-RPC on its stdin/stdout."""

    def __init__(self, cwd, env):
        core = _core()
        binary = core._vibe_acp_bin()
        if not binary:
            raise AcpError("the vibe-acp command is not installed or not on PATH")
        try:
            self.proc = subprocess.Popen([binary], cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=sys.stderr, text=True, encoding="utf-8", errors="replace",
                                         bufsize=1)
        except OSError as e:
            raise AcpError(f"could not start vibe-acp: {e!r}")
        self.inbox = queue.Queue()
        self.next_id = 0
        self.text = []
        self.permissions = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if line:
                    try:
                        self.inbox.put(json.loads(line))
                    except ValueError:
                        continue
        finally:
            self.inbox.put(None)  # end of stream

    def _write(self, msg):
        try:
            self.proc.stdin.write(json.dumps(msg) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise AcpError(f"vibe-acp closed its input: {e!r}")

    def _on_request(self, msg):
        if msg.get("method") == "session/request_permission":
            options = (msg.get("params") or {}).get("options") or []
            reject = next((o for o in options if o.get("kind") == "reject_once"), None)
            outcome = ({"outcome": "selected", "optionId": reject["optionId"]} if reject
                       else {"outcome": "cancelled"})
            self.permissions.append(outcome.get("optionId") or "cancelled")
            print(f"{_now()} permission asked by vibe: answered {self.permissions[-1]}", flush=True)
            self._write({"jsonrpc": "2.0", "id": msg["id"], "result": {"outcome": outcome}})
        else:
            self._write({"jsonrpc": "2.0", "id": msg["id"],
                         "error": {"code": -32601, "message": "not supported by the bridge"}})

    def _on_update(self, msg):
        update = (msg.get("params") or {}).get("update") or {}
        kind = update.get("sessionUpdate")
        if kind == "agent_message_chunk":
            self.text.append(str((update.get("content") or {}).get("text") or ""))

    def call(self, method, params, timeout=120):
        self.next_id += 1
        rid = self.next_id
        self._write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        while True:
            try:
                msg = self.inbox.get(timeout=max(0.0, deadline - time.time()))
            except queue.Empty:
                raise AcpError(f"{method}: no answer in {timeout} s")
            if msg is None:
                code = self.proc.wait()
                raise AcpError(f"vibe-acp exited with code {code} during {method}")
            if "method" in msg and "id" in msg:
                self._on_request(msg)
            elif msg.get("method") == "session/update":
                self._on_update(msg)
            elif msg.get("id") == rid:
                if "error" in msg:
                    err = msg["error"] or {}
                    raise AcpError(f"{method}: error {err.get('code')} {err.get('message')}")
                return msg.get("result") or {}

    def start(self):
        self.call("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                                 "clientInfo": {"name": "diaulos", "version": "1"}})

    def prompt(self, session, text, max_turns):
        """One turn. Returns the reply text (the agent's message chunks)."""
        self.call("session/set_config_option", {"sessionId": session, "configId": "max_turns",
                                                "value": str(max_turns)})
        self.text = []
        self.call("session/prompt", {"sessionId": session, "prompt": [{"type": "text", "text": text}]},
                  timeout=_core().CLI_TURN_SECONDS)
        return "".join(self.text)

    def close(self):
        with contextlib.suppress(OSError, ValueError):
            self.proc.stdin.close()
        try:
            self.proc.wait(15)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        with contextlib.suppress(OSError, ValueError):
            self.proc.stdout.close()


def _env(lim, me=None, suffix=None):
    env = dict(os.environ, AGENT_BRIDGE_HOME=_core().HOME, AGENT_BRIDGE_VIBE_MAX_PRICE=str(lim["max_price"]))
    env.pop("AGENT_BRIDGE_VIBE_SUFFIX", None)
    env.pop("AGENT_BRIDGE_ADDRESS", None)
    if suffix:
        env["AGENT_BRIDGE_VIBE_SUFFIX"] = suffix
    if me:
        env["AGENT_BRIDGE_ADDRESS"] = me  # `bridge.py send` from vibe's bash tool
    return env


def _tell(to, text, sender):
    with contextlib.suppress(SystemExit, Exception):
        _core().send(to, "[bridge, not an agent] " + text, sender)


def _cost(suffix):
    """stats.session_cost of the conversation's current session, from its meta.json: for a
    loaded session, vibe counts the current process only (probe vibe-acp A4)."""
    with contextlib.suppress(Exception):
        conv = _core()._vibe().conversation(suffix)
        with open(os.path.join(conv["session_dir"], "meta.json"), encoding="utf-8") as f:
            cost = (json.load(f).get("stats") or {}).get("session_cost")
        if isinstance(cost, (int, float)):
            return f"${cost:.4f}"
    return "unknown"


# ---------------------------------------------------------------- vibe-run

def vibe_run(suffix):
    """Deliver the inbox of a closed vibe conversation, one ACP turn per batch, then exit."""
    core = _core()
    vs = core._vibe()
    if not vs.SUFFIX_RE.match(suffix):
        sys.exit(f"invalid vibe conversation {suffix!r}")
    me = f"vibe:{suffix}"
    lock = core._path("state", f"vibe-{suffix}")
    with core._locked(lock, wait=1.0):
        peer = core._read_peer(me) or {}
        if core._vibe_tui_alive(peer):
            print(f"{_now()} a vibe TUI serves {me}: nothing to do", flush=True)
            return
        other = peer.get("runner_pid")
        if other != os.getpid() and core._alive(other):
            return
        core._update_peer(me, runner_pid=os.getpid())
        recover_inflight(suffix)
    lim = limits()
    print(f"{_now()} vibe-run {me} pid {os.getpid()}: max_turns {lim['max_turns']}, "
          f"price cap ${lim['max_price']} (plugin), {lim['attempts']} attempts", flush=True)
    failures, acp, session = {}, None, None
    try:
        while True:
            time.sleep(core.POLL_SECONDS)  # let a burst of messages go in one turn
            with core._locked(lock, wait=1.0):
                if core._vibe_tui_alive(core._read_peer(me) or {}):
                    print(f"{_now()} a vibe TUI opened {me}: it takes over", flush=True)
                    return
                claimed = core.claim(me)
                conv = vs.conversation(suffix) if claimed else None
                if claimed:
                    write_inflight(suffix, conv and conv["session"], claimed, "runner")
            if not claimed:
                return
            ids = [m["id"] for _, m in claimed]
            error = None
            try:
                if conv is None:
                    raise AcpError(f"no saved vibe session ends with {suffix}")
                if acp is None or session != conv["session"]:
                    if acp is not None:
                        acp.close()
                    workspace = conv.get("workspace")
                    cwd = workspace if workspace and os.path.isdir(workspace) else os.path.expanduser("~")
                    acp = Acp(cwd, _env(lim, me, suffix))
                    acp.start()
                    session = conv["session"]
                    acp.call("session/load", {"sessionId": session, "cwd": cwd, "mcpServers": []})
                reply = acp.prompt(session, incoming_text([m for _, m in claimed], me), lim["max_turns"])
            except AcpError as e:
                error = str(e)
                if acp is not None:
                    acp.close()
                acp, session = None, None
            if error is None:
                clear_inflight(suffix)
                limit = " (turn limit reached)" if TURN_LIMIT_TEXT in reply else ""
                print(f"{_now()} delivered {ids} to {me} in session {session} with vibe-acp{limit}; "
                      f"session_cost {_cost(suffix)}", flush=True)
                failures.clear()
                continue
            for i in ids:
                failures[i] = failures.get(i, 0) + 1
            if max(failures[i] for i in ids) < lim["attempts"]:
                core.unclaim(me, claimed)
                clear_inflight(suffix)
                print(f"{_now()} not delivered {ids}: {error}; retry in {lim['retry_seconds']}s", flush=True)
                time.sleep(lim["retry_seconds"])
                continue
            for path, _ in claimed:
                with contextlib.suppress(OSError):
                    os.rename(path, path + ".failed")
            clear_inflight(suffix)
            print(f"{_now()} dropped {ids} after {lim['attempts']} attempts: {error}", flush=True)
            for m in [m for _, m in claimed]:
                _tell(m["from"], f"Your message {m['id']} to {me} was not delivered after {lim['attempts']} "
                      f"attempts with vibe-acp ({error}). It is kept as .failed; it is not sent again.", me)
    finally:
        if acp is not None:
            acp.close()
        with core._locked(lock, wait=1.0):
            if (core._read_peer(me) or {}).get("runner_pid") == os.getpid():
                core._update_peer(me, runner_pid=None)


# ---------------------------------------------------------------- vibe-new

def vibe_new():
    """Start one vibe conversation per message sent to vibe:new, then exit."""
    core = _core()
    new = core.VIBE_NEW
    with core._locked(core._path("state", "vibe-new"), wait=0.5):
        if core._alive((core._read_peer(new) or {}).get("pid")):
            return
        core._update_peer(new, pid=os.getpid(), gone=True)  # never listed: only marks the running worker
        recover_inflight("new")
    lim = limits()
    try:
        while True:
            claimed = core.claim(new, limit=1)
            if not claimed:
                return
            path, msg = claimed[0]
            write_inflight("new", None, claimed, "vibe-new")
            options = msg.get("new") if isinstance(msg.get("new"), dict) else {}
            workspace, name = options.get("workspace"), options.get("name")
            cwd = workspace if workspace and os.path.isdir(workspace) else os.path.expanduser("~")
            acp, address, session, error, reply = None, None, None, None, ""
            try:
                acp = Acp(cwd, _env(lim))
                acp.start()
                session = acp.call("session/new", {"cwd": cwd, "mcpServers": []}).get("sessionId")
                suffix = core._vibe().suffix_of(session)
                if not suffix:
                    raise AcpError(f"session/new gave no vibe session id ({session!r})")
                address = f"vibe:{suffix}"
                fields = {"session": session}
                if name:
                    fields["aliases"] = [f"vibe:{name}"]
                core._update_peer(address, **fields)
                reply = acp.prompt(session, incoming_text([msg], address), lim["max_turns"])
            except AcpError as e:
                error = str(e)
            except Exception as e:  # a bug here must not lose the message: back to the inbox
                if acp is not None:
                    acp.close()
                core.unclaim(new, claimed)
                clear_inflight("new")
                print(f"{_now()} vibe:new for {msg['id']} stopped by {e!r}; message put back", flush=True)
                raise
            if acp is not None:
                acp.close()
            clear_inflight("new")
            if error:
                with contextlib.suppress(OSError):
                    os.rename(path, path + ".failed")
                print(f"{_now()} vibe:new for {msg['id']} failed: {error}", flush=True)
                started = f" The conversation {address} was created, but its first turn failed." if address else ""
                _tell(msg["from"], f"Your message {msg['id']} to {new} failed: {error}.{started}", address or new)
                continue
            limit = " (turn limit reached)" if TURN_LIMIT_TEXT in reply else ""
            print(f"{_now()} started {address} (session {session}) for {msg['id']} from {msg['from']}{limit}; "
                  f"session_cost {_cost(suffix)}", flush=True)
            named = f", also reachable as vibe:{name}" if name else ""
            _tell(msg["from"], f"Your message {msg['id']} to {new} started the vibe conversation {address}{named}, "
                  f"in {cwd}. It has already run its first turn; its replies come from that address. "
                  f"Open it in a terminal with: vibe --resume {session}", address)
    finally:
        core._update_peer(new, pid=None)

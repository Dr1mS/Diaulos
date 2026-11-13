"""Delivery to closed Mistral Vibe 3 conversations, and new ones, through vibe in print mode
(docs/VIBE-DESIGN.md §8.3). Python 3.8+, standard library only.

bridge.py loads this file by path as `agent_bridge_vibe_runner`, after it has
registered itself as `agent_bridge_core`, and runs:

  bridge.py vibe-run <suffix>   one per conversation, started by `send` when no
                                vibe TUI serves it. It waits 1 s so that a burst
                                goes in one turn, then runs
                                vibe --prompt-file <batch> --resume <session>
                                     --max-price <cap> --max-turns <n> --output json
                                     --output-dir <tmp>
                                in the conversation's directory, and reads the
                                cost and the outcome from <tmp>/export.json. It
                                loops while the inbox has messages, then exits.
                                "Session is already open" means a TUI came back:
                                the messages wait for it.
  bridge.py vibe-new            one worker: one `vibe --prompt-file … --workdir <dir>` per
                                message sent to vibe:new; the new session id
                                comes from export.json.

vibe runs with AGENT_BRIDGE_VIBE_RUN=1, so the plugin's hooks leave delivery to
vibe-run. Nobody is there to approve a tool: print mode runs only the tools allowed
without asking; the bridge tools of the plugin are `always`.

Caps (§4.4, §8.3): vibe's own --max-turns and --max-price, per run. Both, and the
number of attempts per batch, come from state/vibe-limits.json; a missing or
invalid value gives its default.

Before a run, the runner writes state/vibe-inflight/<suffix>.json with its pid,
the session and the claimed messages; the relay writes the same record with the
entryId of its request. A process killed in the middle leaves it behind; whoever
next takes the conversation puts those messages back in the inbox
(recover_inflight), unless the session already holds that entry.
"""
import contextlib
import json
import os
import re
import subprocess
import sys
import time

LIMITS = {"max_turns": 20, "max_price": 0.50, "attempts": 3, "retry_seconds": None}
CUT_NOTE = ("Note: the previous bridge turn here stopped at a limit; do not finish its work unless this "
            "message asks for it.")
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

HEADER = "Message(s) from another agent via the agent bridge."


# vibe puts this block before a message sent with a `sender`; the relay's sender ids start with
# "diaulos:". In the TUI, the pre_agent hook gets this block alone as its prompt, without the text
# (gate R6, T14): the block is how it knows a relay delivery. vibe adds it only for a message sent
# with a sender, so a message the person types never has it.
SESSION_MESSAGE_RE = re.compile(r'^\s*<session_message from_session_id="([^"]*)"(?: from_title="([^"]*)")?/>\s*')


def relay_sender(prompt):
    """The from_title of vibe's <session_message/> block when the relay sent this prompt, else None."""
    m = SESSION_MESSAGE_RE.match(prompt[:2000]) if isinstance(prompt, str) else None
    if not m or not m.group(1).startswith("diaulos:"):
        return None
    return m.group(2) or m.group(1)[len("diaulos:"):]


def is_delivery(prompt):
    """True when a prompt is a Diaulos delivery: sent by the relay (vibe's <session_message/> block
    from a "diaulos:" sender), or starting with the bridge header (vibe-run, after the optional
    CUT_NOTE line)."""
    if not isinstance(prompt, str):
        return False
    if relay_sender(prompt) is not None:
        return True
    return HEADER in prompt[:len(CUT_NOTE) + len(HEADER) + 8]


def incoming_text(msgs, me):
    """The bridge's message blocks for vibe: every `@` gets a word joiner, so vibe does not
    inline a file, and the last lines name the `send` tool of the `diaulos` server.
    After a turn cut by a limit (state/vibe-cut/<suffix>), the text starts with CUT_NOTE,
    once: the file is deleted here (§4.4, P4)."""
    text = _core()._incoming_text(msgs, me)
    if take_cut(me.split(":", 1)[-1]):
        text = CUT_NOTE + "\n" + text
    end = text.rfind("\nReply with: ")
    if end >= 0:
        text = text[:end]
    text = text.replace("@", "@" + WORD_JOINER)
    senders = sorted({m["from"] for m in msgs})
    # A failed send must not loop: a first real TUI run retried 16 times (0.52 $) on an `ext:` sender
    # with no reader (§4.1, "A sender nobody reads", first guard).
    unread = [a for a in senders if _no_reader(a)]
    readable = [a for a in senders if a not in unread]
    lines = [f"{a} has no reader: do not reply to it with send" for a in unread]
    if readable:
        to = readable[0] if len(readable) == 1 else "<the from of the message>"
        lines.append(f"Reply with the send tool of the diaulos MCP server, to={to}  (you are {me})")
    return (text + "\nIf send answers with an error, do not call it again: say so in your answer.\n"
            + "\n".join(lines))


def _no_reader(address):
    """True for an `ext:` sender that no program watches now."""
    if not address.startswith("ext:"):
        return False
    try:
        return address not in {p["address"] for p in _core().peers("ext")}
    except Exception:
        return False


# ---------------------------------------------------------------- turns cut by a limit (§4.4, P3, P4)

def _cut_path(suffix):
    return _core()._path("state", "vibe-cut", suffix)


def write_cut(suffix, reason):
    path = _cut_path(suffix)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{_now()} {reason}\n")


def take_cut(suffix):
    """True, once, after a turn of this conversation stopped at a limit."""
    try:
        os.remove(_cut_path(suffix))
    except OSError:
        return False
    return True


def tell_cut(msgs, me, reason, lim):
    """Write the cut file and tell each sender, with a [bridge, not an agent] note."""
    write_cut(me.split(":", 1)[-1], reason)
    limit = f"price cap ${lim['max_price']}" if reason == "price cap" else f"turn limit of {lim['max_turns']}"
    for sender in sorted({m["from"] for m in msgs}):
        ids = ", ".join(m["id"] for m in msgs if m["from"] == sender)
        _tell(sender, f"Your message {ids} to {me} was delivered, but its vibe turn stopped at the {limit} "
              "before the end: the answer may be missing or partial.", me)


# ---------------------------------------------------------------- in-flight records

def _inflight_path(suffix):
    folder = _core()._path("state", "vibe-inflight")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, f"{suffix}.json")


def write_inflight(suffix, session, claimed, role, entry_id=None):
    path = _inflight_path(suffix)
    tmp = f"{path}.{os.getpid()}.tmp"
    record = {"pid": os.getpid(), "role": role, "session": session, "at": time.time(),
              "paths": [p for p, _ in claimed]}
    if entry_id:
        record["entry_id"] = entry_id
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(record, f)
    os.replace(tmp, path)


def session_holds(session, entry_id):
    """True if the journal of vibe session `session` holds an entry `entry_id` (§8.9.7)."""
    vs = _core()._vibe()
    if not vs.suffix_of(session) or not entry_id:
        return False
    needle = json.dumps(entry_id)
    folder = os.path.join(vs.save_dir(), "unified", session, "journal")
    try:
        names = os.listdir(folder)
    except OSError:
        return False
    for name in names:
        if name.endswith(".jsonl"):
            with contextlib.suppress(OSError):
                with open(os.path.join(folder, name), encoding="utf-8", errors="replace") as f:
                    if any(needle in line for line in f):
                        return True
    return False


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
    if session_holds(record.get("session"), record.get("entry_id")):
        clear_inflight(suffix)  # the relay died after vibe took the entry: delivered
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


# ---------------------------------------------------------------- print mode (§8.3)

class RunError(Exception):
    pass


ALREADY_OPEN = "Session is already open"


def vibe_bin():
    """Path of the real `vibe` command, or None. Hooks and MCP servers may run with a short PATH."""
    import shutil
    found = os.environ.get("AGENT_BRIDGE_VIBE_REAL") or shutil.which("vibe")
    if found:
        return found
    path = os.path.expanduser("~/.local/bin/vibe")
    return path if os.access(path, os.X_OK) else None


def run_p(cwd, env, text, lim, resume=None):
    """One print-mode run of vibe: the prompt from a file, vibe's own price and turn caps, export.json read
    back. Returns (export dict, stderr text). Raises RunError if vibe did not run the prompt."""
    binary = vibe_bin()
    if not binary:
        raise RunError("the vibe command is not installed or not on PATH")
    core = _core()
    work = core._path("state", "vibe-run-%d-%d" % (os.getpid(), time.time_ns()))
    os.makedirs(work)
    try:
        prompt = os.path.join(work, "prompt.txt")
        with open(prompt, "w", encoding="utf-8") as f:
            f.write(text)
        argv = [binary, "--prompt-file", prompt, "--max-price", str(lim["max_price"]),
                "--max-turns", str(lim["max_turns"]), "--output", "json", "--output-dir", work]
        argv += ["--resume", resume] if resume else ["--workdir", cwd]
        try:
            proc = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.PIPE, timeout=core.CLI_TURN_SECONDS)
        except subprocess.TimeoutExpired:
            raise RunError("vibe in print mode ran longer than %d s" % core.CLI_TURN_SECONDS)
        except OSError as e:
            raise RunError("could not start vibe: %r" % (e,))
        err = proc.stderr.decode("utf-8", "replace")
        export = None
        with contextlib.suppress(OSError, ValueError):
            with open(os.path.join(work, "export.json"), encoding="utf-8") as f:
                export = json.load(f)
        if ALREADY_OPEN in err:
            raise RunError(ALREADY_OPEN)
        if not isinstance(export, dict) or not export.get("session_id"):
            last = [l for l in err.splitlines() if l.strip()][-1:] or ["no export.json"]
            raise RunError("vibe in print mode exited %d: %s" % (proc.returncode, last[0][:200]))
        return export, err
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


def cut_of(export):
    """"price cap", "turn limit" or None, from export.json's outcome."""
    outcome = str(export.get("outcome") or "")
    if "price" in outcome:
        return "price cap"
    if "turn" in outcome:
        return "turn limit"
    return None


def cost_of(export):
    cost = export.get("cost_usd")
    return "$%.4f" % cost if isinstance(cost, (int, float)) and not isinstance(cost, bool) else "unknown"


def _env(me=None):
    """The environment of print mode: AGENT_BRIDGE_VIBE_RUN tells the plugin's hooks that vibe-run delivers."""
    env = dict(os.environ, AGENT_BRIDGE_HOME=_core().HOME, AGENT_BRIDGE_VIBE_RUN="1")
    for key in ("AGENT_BRIDGE_ADDRESS", "VIBE_APP_SERVER_CMD", "VIBE_APP_SERVER_BIN", "VIBE_APP_SERVER_CWD"):
        env.pop(key, None)
    if me:
        env["AGENT_BRIDGE_ADDRESS"] = me  # `bridge.py send` from vibe's bash tool
    return env


def _tell(to, text, sender):
    with contextlib.suppress(SystemExit, Exception):
        _core().send(to, "[bridge, not an agent] " + text, sender)


# ---------------------------------------------------------------- vibe-run

def vibe_run(suffix):
    """Deliver the inbox of a closed vibe conversation, one `vibe --prompt-file … --resume` run per batch, then exit."""
    core = _core()
    vs = core._vibe()
    if not vs.SUFFIX_RE.match(suffix):
        sys.exit(f"invalid vibe conversation {suffix!r}")
    me = f"vibe:{suffix}"
    lock = core._path("state", f"vibe-{suffix}")
    with core._locked(lock, wait=1.0):
        if core._vibe_tui_alive(me):
            print(f"{_now()} a vibe TUI serves {me}: nothing to do", flush=True)
            return
        other = (core._read_peer(me) or {}).get("runner_pid")
        if other != os.getpid() and core._alive(other):
            return
        core._update_peer(me, runner_pid=os.getpid())
        recover_inflight(suffix)
    lim = limits()
    print(f"{_now()} vibe-run {me} pid {os.getpid()}: max_turns {lim['max_turns']}, "
          f"price cap ${lim['max_price']} per run, {lim['attempts']} attempts", flush=True)
    failures = {}
    try:
        while True:
            time.sleep(core.POLL_SECONDS)  # let a burst of messages go in one turn
            with core._locked(lock, wait=1.0):
                if core._vibe_tui_alive(me):
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
                    raise RunError(f"no saved vibe session ends with {suffix}")
                workspace = conv.get("workspace")
                cwd = workspace if workspace and os.path.isdir(workspace) else os.path.expanduser("~")
                export, _ = run_p(cwd, _env(me), incoming_text([m for _, m in claimed], me), lim,
                                  resume=conv["session"])
            except RunError as e:
                error = str(e)
            if error == ALREADY_OPEN:  # the TUI came back: the messages wait for it
                core.unclaim(me, claimed)
                clear_inflight(suffix)
                print(f"{_now()} {me} is open in a vibe TUI again: {ids} wait for it", flush=True)
                return
            if error is None:
                clear_inflight(suffix)
                reason = cut_of(export)
                limit = f" (stopped at the {reason})" if reason else ""
                print(f"{_now()} delivered {ids} to {me} in session {export.get('session_id')} with vibe in print mode{limit}; "
                      f"cost {cost_of(export)}", flush=True)
                if reason:
                    tell_cut([m for _, m in claimed], me, reason, lim)
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
                      f"attempts with vibe in print mode ({error}). It is kept as .failed; it is not sent again.", me)
    finally:
        with core._locked(lock, wait=1.0):
            if (core._read_peer(me) or {}).get("runner_pid") == os.getpid():
                core._update_peer(me, runner_pid=None)


# ---------------------------------------------------------------- vibe-new

def vibe_new():
    """Start one vibe conversation per message sent to vibe:new (`vibe --prompt-file … --workdir`), then exit."""
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
            export, error = None, None
            try:
                # The address is known only from export.json: the first turn names the sender vibe:new.
                export, _ = run_p(cwd, _env(), incoming_text([msg], new), lim)
            except RunError as e:
                error = str(e)
            except Exception as e:  # a bug here must not lose the message: back to the inbox
                core.unclaim(new, claimed)
                clear_inflight("new")
                print(f"{_now()} vibe:new for {msg['id']} stopped by {e!r}; message put back", flush=True)
                raise
            clear_inflight("new")
            session = export.get("session_id") if export else None
            suffix = core._vibe().suffix_of(session)
            if error or not suffix:
                error = error or f"vibe in print mode gave no vibe session id ({session!r})"
                with contextlib.suppress(OSError):
                    os.rename(path, path + ".failed")
                print(f"{_now()} vibe:new for {msg['id']} failed: {error}", flush=True)
                _tell(msg["from"], f"Your message {msg['id']} to {new} failed: {error}.", new)
                continue
            address = f"vibe:{suffix}"
            if name:
                core._update_peer(address, session=session, aliases=[f"vibe:{name}"])
            reason = cut_of(export)
            limit = f" (stopped at the {reason})" if reason else ""
            print(f"{_now()} started {address} (session {session}) for {msg['id']} from {msg['from']}{limit}; "
                  f"cost {cost_of(export)}", flush=True)
            if reason:
                tell_cut([msg], address, reason, lim)
            named = f", also reachable as vibe:{name}" if name else ""
            _tell(msg["from"], f"Your message {msg['id']} to {new} started the vibe conversation {address}{named}, "
                  f"in {cwd}. It has already run its first turn; its replies come from that address. "
                  f"Open it in a terminal with: vibe --resume {session}", address)
    finally:
        core._update_peer(new, pid=None)

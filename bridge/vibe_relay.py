#!/usr/bin/env python3
"""The relay between the vibe 3 TUI (vibe-rs) and the real vibe-app-server (docs/VIBE-DESIGN.md §8.9).
Python 3.8+, standard library only.

    vibe_relay.py [--home <bridge home>] --real <vibe-app-server> [arguments of vibe-app-server...]

vibe-diaulos starts the TUI with VIBE_APP_SERVER_CMD set to this file (vibe's own launcher,
exec_rust_cli(app_server_cmd=...)). The relay starts the real server and passes every line
both ways unchanged: two loops that copy whole lines between two pipes, each line written
under one lock. Everything Diaulos adds runs in its own thread, inside `try`: an error there
turns waking off for the session and never stops the forwarding.

The Diaulos thread:
  - follows the TUI's main session: the state.session.id of the answers to session/start,
    session/resume, session/continue and session/history/clear (not session/fork: /branch
    leaves the TUI on its session), and the file
    state/vibe-current/<real server pid>.json that the session_start hook writes; the newest wins;
  - writes state/vibe-relay/<relay pid>.json: session, tui_pid (vibe-rs), relay_pid, real_pid,
    version, wake ("relay", or "off" after 3 failed deliveries in a row);
  - every 0.5 s, claims the inbox of vibe:<suffix> and sends one request per batch:
    session/turn/enqueue with id "diaulos-relay-<n>", a new entryId and idempotency key, the
    bridge text, sender {"sessionId": "diaulos:<from>", "title": "<from>"} and delivery
    "start_turn". vibe starts a turn on an idle session. During a running turn, the message
    joins that turn: no second turn, no queue; the model answers both entries in the same turn
    and nothing is discarded (gate R4, §8.9.11). The answer to that id is kept here, never passed to the TUI: a result closes
    the claim; an error or no answer in 10 s puts the messages back in the inbox;
  - before sending, writes state/vibe-inflight/<suffix>.json (pid, paths, entry_id): if the relay
    dies before the answer, whoever reads vibe peers next puts the messages back, unless the
    session already holds that entry (vibe_runner.recover_inflight).

Signals and ends (§8.9.7): SIGINT is ignored, so Ctrl+C reaches vibe as before. When vibe-rs
closes its stdin, or vibe-rs is gone (checked every second), the relay closes the real server's
stdin, waits 5 s, kills it if needed, and exits with its code. When the real server exits, the
relay exits with its code.

Log (§8.9.8): state/vibe-relay.log, one JSON line per Diaulos event, built only from the keys of
KEPT; never params, result, error text, message text, account data or path. Rotated at 1 MB.
AGENT_BRIDGE_VIBE_RELAY_LOG=0 turns it off.
"""
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
if len(sys.argv) > 2 and sys.argv[1] == "--home":  # before bridge.py reads AGENT_BRIDGE_HOME
    os.environ["AGENT_BRIDGE_HOME"] = sys.argv[2]
    del sys.argv[1:3]
import bridge  # noqa: E402  (bridge.py ships next to this file)

KEPT = ("dir", "at", "method", "id", "bytes", "pid", "argc", "rc", "reason", "session", "msgs", "wake")
LOG_MAX_BYTES = 1024 * 1024
POLL_SECONDS = 0.5
ANSWER_SECONDS = 10.0
MAX_FAILURES = 3
CLOSE_SECONDS = 5.0
# Answers that move the TUI to another session (gate R2). Not session/fork: /branch makes a copy and
# leaves the TUI on its session ("Resume the copy with `vibe --resume <id>`").
SESSION_METHODS = ("session/start", "session/resume", "session/continue", "session/history/clear")
ENQUEUE = "session/turn/enqueue"
ID_PREFIX = "diaulos-relay-"
# Fallbacks of the gates (§8.9.9), off while their gate passes:
HOLD_WHILE_BUSY = False          # R4: send only after turn/completed
HOLD_WHILE_TUI_ENQUEUES = False  # R7: hold while an enqueue of the TUI has no answer yet
WAIT_FIRST_ENQUEUE = False       # R5: no delivery before the TUI's first message
CHECK_LINES = os.environ.get("AGENT_BRIDGE_VIBE_RELAY_CHECK") == "1"  # gates only


def short(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int) or (isinstance(value, str) and len(value) <= 64):
        return value
    return None


class Log:
    def __init__(self):
        self.path = bridge._path("state", "vibe-relay.log")
        self.on = os.environ.get("AGENT_BRIDGE_VIBE_RELAY_LOG", "1") != "0"
        self.lock = threading.Lock()

    def __call__(self, direction, **fields):
        if not self.on:
            return
        row = {"dir": direction, "at": round(time.time(), 3)}
        for k, v in fields.items():
            if k == "msgs" and isinstance(v, list):
                v = [i for i in (short(x) for x in v) if isinstance(i, str)]
            elif k == "session":
                v = bridge._vibe().suffix_of(v) or (v if isinstance(v, str) and len(v) == 12 else None)
            else:
                v = short(v)
            if v is not None:
                row[k] = v
        row = {k: row[k] for k in KEPT if k in row}
        try:
            with self.lock:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                try:
                    if os.path.getsize(self.path) > LOG_MAX_BYTES:
                        os.replace(self.path, self.path + ".1")
                except OSError:
                    pass
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row) + "\n")
        except OSError:
            pass


log = Log()


def parse(line):
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None


def session_of(result):
    """state.session.id of an answer, or its sessionId."""
    if not isinstance(result, dict):
        return None
    state = result.get("state")
    if isinstance(state, dict) and isinstance(state.get("session"), dict):
        sid = state["session"].get("id")
        if isinstance(sid, str):
            return sid
    sid = result.get("sessionId")
    return sid if isinstance(sid, str) else None


class Relay:
    def __init__(self, child, tui_pid):
        self.child = child
        self.tui_pid = tui_pid
        self.in_lock = threading.Lock()
        self.out_lock = threading.Lock()
        self.events = queue.Queue()
        self.session = None
        self.session_at = 0.0
        self.tui_pending = {}        # id of a TUI request -> its method (the ones we follow)
        self.tui_enqueues = set()    # ids of TUI enqueues with no answer yet
        self.first_enqueue = False
        self.busy = False
        self.mine = {}               # relay id -> {"claimed", "suffix", "at", "entry"}
        self.failures = 0
        self.wake = "relay"
        self.n = 0
        self.written = None
        self.closing = False
        self.version = os.environ.get("AGENT_BRIDGE_VIBE_VERSION") or None

    # ------------------------------------------------------------ forwarding (never touched by Diaulos errors)

    def to_server(self, line):
        with self.in_lock:
            self.child.stdin.write(line)
            self.child.stdin.flush()
        if CHECK_LINES:  # gate R7: every line that reaches the real server is one JSON value
            self.events.put(("check", line))

    def to_tui(self, line):
        with self.out_lock:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    def tui_to_server(self):
        try:
            for line in sys.stdin.buffer:
                self.to_server(line)
                self.events.put(("tui", line))
        except (OSError, ValueError):
            pass
        log("tui stdin closed")
        self.close_server()

    def server_to_tui(self):
        try:
            for line in self.child.stdout:
                if ID_PREFIX.encode() in line[:300]:
                    msg = parse(line)
                    if msg is not None and "method" not in msg and msg.get("id") in self.mine:
                        self.events.put(("mine", msg))
                        continue
                self.to_tui(line)
                self.events.put(("srv", line))
        except (OSError, ValueError):
            pass
        rc = self.child.wait()
        log("server stdout closed", rc=rc)
        self.finish(rc)

    def close_server(self):
        self.closing = True
        try:
            self.child.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            rc = self.child.wait(CLOSE_SECONDS)
        except subprocess.TimeoutExpired:
            self.child.kill()
            rc = self.child.wait()
            log("server killed", reason="no exit 5 s after stdin closed")
        self.finish(rc)

    def finish(self, rc):
        try:
            with self.out_lock:
                sys.stdout.flush()
        except (OSError, ValueError):
            pass
        try:
            os.remove(bridge._vibe().relay_path(os.getpid()))
        except (OSError, Exception):
            pass
        log("exit", rc=rc)
        os._exit(rc if isinstance(rc, int) and rc >= 0 else 1)

    # ------------------------------------------------------------ the Diaulos thread

    def set_session(self, sid, at, how):
        vs = bridge._vibe()
        if not vs.suffix_of(sid) or at < self.session_at:
            return
        self.session_at = at
        if sid != self.session:
            self.session = sid
            self.failures = 0
            if self.wake == "off":
                self.wake = "relay"
            log("session", session=sid, reason=how)

    def write_state(self):
        data = {"session": self.session, "tui_pid": self.tui_pid, "relay_pid": os.getpid(),
                "real_pid": self.child.pid, "version": self.version, "wake": self.wake}
        if data != self.written:
            path = bridge._vibe().relay_path(os.getpid())
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = "%s.tmp" % path
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, path)
            self.written = data

    def on_tui(self, line):
        msg = parse(line)
        if msg is None or "method" not in msg or msg.get("id") is None:
            return
        method = msg["method"]
        if method in SESSION_METHODS or method == ENQUEUE:
            self.tui_pending[msg["id"]] = method
        if method == ENQUEUE:
            self.tui_enqueues.add(msg["id"])
            self.first_enqueue = True

    def on_server(self, line):
        msg = parse(line)
        if msg is None:
            return
        if "method" not in msg and msg.get("id") in self.tui_pending:
            method = self.tui_pending.pop(msg["id"])
            if method == ENQUEUE:
                self.tui_enqueues.discard(msg["id"])
            elif "result" in msg:
                self.set_session(session_of(msg["result"]), time.time(), method)
            return
        method = msg.get("method")
        if method in ("turn/started", "turn/completed"):
            params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
            if params.get("sessionId") == self.session:
                self.busy = method == "turn/started"

    def on_mine(self, msg):
        record = self.mine.pop(msg.get("id"), None)
        if record is None:
            return
        rn = bridge._vibe_runner()
        ids = [m["id"] for _, m in record["claimed"]]
        if "result" in msg:
            rn.clear_inflight(record["suffix"])
            self.failures = 0
            waits = isinstance(msg["result"], dict) and bool(msg["result"].get("waits"))
            log("delivered", id=msg.get("id"), msgs=ids, session=record["suffix"], reason="waits" if waits else "started")
            return
        err = msg.get("error") if isinstance(msg.get("error"), dict) else {}
        self.put_back(record, "error", err.get("code"))

    def put_back(self, record, why, code=None):
        rn = bridge._vibe_runner()
        me = "vibe:" + record["suffix"]
        bridge.unclaim(me, record["claimed"])
        rn.clear_inflight(record["suffix"])
        self.failures += 1
        log("put back", id=record["rid"], msgs=[m["id"] for _, m in record["claimed"]],
            session=record["suffix"], reason=why, rc=code)
        if self.failures >= MAX_FAILURES and self.wake != "off":
            self.wake = "off"
            log("wake off", session=record["suffix"], wake="off", reason="%d failures in a row" % self.failures)

    def check_timeouts(self, now):
        for rid, record in list(self.mine.items()):
            if now - record["at"] > ANSWER_SECONDS:
                del self.mine[rid]
                self.put_back(record, "no answer in 10 s")

    def from_hook(self):
        record = bridge._vibe().read_current(self.child.pid)
        if record and isinstance(record.get("at"), (int, float)):
            self.set_session(record["session"], float(record["at"]), "session_start hook")

    def may_send(self):
        if self.wake != "relay" or not self.session or self.mine or self.closing:
            return False
        if WAIT_FIRST_ENQUEUE and not self.first_enqueue:
            return False
        if HOLD_WHILE_BUSY and self.busy:
            return False
        if HOLD_WHILE_TUI_ENQUEUES and self.tui_enqueues:
            return False
        return True

    def deliver(self):
        vs, rn = bridge._vibe(), bridge._vibe_runner()
        suffix = vs.suffix_of(self.session)
        me = "vibe:" + suffix
        with bridge._locked(bridge._path("state", "vibe-" + suffix), wait=0.5):
            claimed = bridge.claim(me)
            if not claimed:
                return
            self.n += 1
            rid = "%s%d" % (ID_PREFIX, self.n)
            entry = "diaulos-%s" % uuid.uuid4().hex[:16]
            rn.write_inflight(suffix, self.session, claimed, "relay", entry_id=entry)
        msgs = [m for _, m in claimed]
        senders = []
        for m in msgs:
            if m["from"] not in senders:
                senders.append(m["from"])
        params = {"sessionId": self.session, "idempotencyKey": str(uuid.uuid4()),
                  "entries": [{"annotations": {}, "content": [{"type": "text", "text": rn.incoming_text(msgs, me)}],
                               "entryId": entry, "role": "user"}],
                  "sender": {"sessionId": "diaulos:" + senders[0], "title": ", ".join(senders)[:200]},
                  "delivery": "start_turn"}
        line = (json.dumps({"jsonrpc": "2.0", "id": rid, "method": ENQUEUE, "params": params},
                           ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self.mine[rid] = {"rid": rid, "claimed": claimed, "suffix": suffix, "at": time.time(), "entry": entry}
        log("send", id=rid, method=ENQUEUE, msgs=[m["id"] for m in msgs], session=suffix, bytes=len(line),
            reason="busy" if self.busy else "idle")
        self.to_server(line)

    def tui_gone(self):
        if bridge.WINDOWS:
            return not bridge._alive(self.tui_pid)
        return os.getppid() != self.tui_pid

    def diaulos(self):
        last = 0.0
        while True:
            try:
                kind, item = self.events.get(timeout=POLL_SECONDS)
            except queue.Empty:
                kind, item = None, None
            try:
                if kind == "tui":
                    self.on_tui(item)
                elif kind == "srv":
                    self.on_server(item)
                elif kind == "mine":
                    self.on_mine(item)
                elif kind == "check" and parse(item) is None:
                    log("invalid line to server", bytes=len(item))
                now = time.time()
                if now - last < POLL_SECONDS:
                    continue
                last = now
                if self.tui_gone() and not self.closing:
                    log("tui gone", reason="vibe-rs ended")
                    threading.Thread(target=self.close_server, daemon=True).start()
                    continue
                self.from_hook()
                self.check_timeouts(now)
                self.write_state()
                if self.may_send():
                    self.deliver()
            except Exception as e:  # never stops the forwarding
                if self.wake != "off":
                    self.wake = "off"
                    log("wake off", wake="off", reason=type(e).__name__)
                try:
                    self.write_state()
                except Exception:
                    pass


def main(argv):
    if len(argv) < 3 or argv[1] != "--real":
        sys.stderr.write(__doc__)
        return 2
    real, rest = argv[2], argv[3:]
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        child = subprocess.Popen([real] + rest, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    except OSError as e:
        sys.stderr.write("vibe_relay: cannot start %s: %s\n" % (real, e))
        return 127
    relay = Relay(child, os.getppid())
    log("start", pid=os.getpid(), argc=len(rest))
    threading.Thread(target=relay.server_to_tui, daemon=True).start()
    threading.Thread(target=relay.diaulos, daemon=True).start()
    relay.tui_to_server()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

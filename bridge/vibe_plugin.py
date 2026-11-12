"""The Diaulos plugin inside a Mistral Vibe process (docs/VIBE-DESIGN.md §2, §4.1 to §4.5, §5).
Python 3.8+, standard library only, and no vibe import: it reads vibe's objects by
name, so the tests drive it with tests/fakes/vibe_app.py.

The shim $VIBE_HOME/tools/agent_bridge.py (install/vibe-tool.py) loads this file by
path as `agent_bridge_vibe_plugin`, calls start() once per process, and backs the
bridge_send and bridge_list_agents tools with send() and list_agents(). This file
loads bridge.py, vibe_sessions.py and vibe_runner.py next to it, by path too: the
vibe process has its own `vibe` package and must never see a sys.path change.

In a vibe TUI, one daemon thread, every POLL_SECONDS:
  - follows the current session (it changes with /clear, /compact, /resume), so
    the address vibe:<suffix> and AGENT_BRIDGE_ADDRESS stay right;
  - writes the heartbeat (peers/vibe-<suffix>.json: tui_pid, tui_seen, session,
    workspace, paused, wake, version) every BEAT_SECONDS;
  - finishes or recovers in-flight records (state/vibe-inflight/<suffix>.json);
  - delivers waiting messages when vibe is idle (five conditions of §4.2), inside
    vibe's own event loop, so the check and the call happen together.
A TUI whose VibeApp is not found in FIND_APP_SECONDS still writes its heartbeat, with
wake none (NoAppPlugin), so vibe-run never starts a turn beside it; `vibe -p` and
vibe-acp have no TUI and get the tools only. bridge_send stops after two failed sends
to one address in the process (§4.1).
When AGENT_BRIDGE_VIBE_MAX_PRICE is set (vibe-run and vibe-new set it for their vibe-acp),
it puts that price cap on vibe's AgentLoop (best effort, private names); with
AGENT_BRIDGE_VIBE_SUFFIX (vibe-run), it records acp_pid and price_cap in the peer file
and looks for no TUI. A TUI started by hand has no such variable, hence no cap (§4.5);
one started with it gets the cap too.
Errors never reach vibe: they go to state/vibe-plugin.log.
"""
import atexit
import contextlib
import gc
import importlib.util
import os
import sys
import threading
import time
import traceback

POLL_SECONDS = 1.0
BEAT_SECONDS = 10.0
FIND_APP_SECONDS = 60.0  # vibe builds VibeApp after its AgentLoop; more than 10 s was seen
PRICE_WATCH_SECONDS = 1.0
# The private VibeApp names delivery needs (§4.2 "Limits"); _agent_task tells whether a turn started.
REQUIRED = ("_handle_user_message", "_agent_running", "_current_bottom_app", "_queue", "_input_queue",
            "_bash_task", "_agent_task")
HERE = os.path.dirname(os.path.abspath(__file__))
NO_PRICE_CAP = "price cap not set, max_turns only"

_started = False
_start_lock = threading.Lock()
_stop = threading.Event()  # set by stop(), for the tests; in vibe the daemon thread ends with the process


# ---------------------------------------------------------------- the other bridge files

def _load(module_name, file_name):
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, os.path.join(HERE, file_name))
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


def core():
    return _load("agent_bridge_core", "bridge.py")


def sessions():
    core()
    return _load("agent_bridge_vibe_sessions", "vibe_sessions.py")


def runner():
    core()
    return _load("agent_bridge_vibe_runner", "vibe_runner.py")


def log(text):
    """One line in state/vibe-plugin.log; never raises."""
    with contextlib.suppress(Exception):
        path = core()._path("state", "vibe-plugin.log")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{core()._now()} pid {os.getpid()} {text}\n")


def vibe_version():
    module = sys.modules.get("vibe")
    return str(getattr(module, "__version__", "?")) if module else "?"


# ---------------------------------------------------------------- tools (called by the shim)

MAX_FAILURES = 2
_failures = {}  # recipient -> failed bridge_send calls in this process (§4.1, third guard)
_failures_lock = threading.Lock()


def send(to, text, session_id=None):
    """bridge_send: returns the text the model sees. After MAX_FAILURES failed sends to the
    same recipient in this process, it stops without sending: a model that retries an
    address nobody reads loops until the price cap (§4.1, "A sender nobody reads")."""
    sender = sessions().address_of(session_id) or os.environ.get("AGENT_BRIDGE_ADDRESS")
    if not sender or not sender.startswith("vibe:"):
        return "error: this vibe session has no bridge address yet (no session id)"
    with _failures_lock:
        if _failures.get(to, 0) >= MAX_FAILURES:
            return f"stopped: {to} failed twice, do not call bridge_send for it again"
    try:
        msg = core().send(to, text, sender)
    except SystemExit as e:
        _failed(to)
        return f"error: {e}"
    except Exception as e:  # e.g. a locked file: answer, never break the turn
        _failed(to)
        log(f"bridge_send failed: {e!r}")
        return f"error: send failed: {e!r}"
    return f"sent {msg['id']} from {sender} to {msg['to']}"


def _failed(to):
    with _failures_lock:
        _failures[to] = _failures.get(to, 0) + 1


def list_agents(all=False, days=None, session_id=None):
    """bridge_list_agents: one line per reachable agent."""
    me = sessions().address_of(session_id)
    if all:
        days = None
    elif days is None:
        days = core().RECENT_DAYS
    elif not isinstance(days, int) or isinstance(days, bool) or days < 1:
        return "error: `days` must be a positive integer"
    try:
        found = core().peers(days=days)
    except SystemExit as e:
        return f"error: {e}"
    except Exception as e:
        log(f"bridge_list_agents failed: {e!r}")
        return f"error: {e!r}"
    lines = [core().describe(p) + ("  (you)" if p["address"] == me else "") for p in found]
    return "\n".join(lines) or "no live agent"


# ---------------------------------------------------------------- the TUI side

def _value(member):
    return getattr(member, "value", member)


def missing_names(app):
    return [n for n in REQUIRED if not hasattr(app, n)]


def idle(app):
    """The five conditions of §4.2. Run it in vibe's event loop."""
    if app._agent_running:
        return False
    if _value(app._current_bottom_app) != "input":
        return False
    if app._queue.draining:
        return False
    queue = app._input_queue
    if len(queue) or queue.paused:
        return False
    task = app._bash_task
    return task is None or task.done()


def _size(folder):
    if not folder:
        return -1
    try:
        return os.path.getsize(os.path.join(str(folder), "messages.jsonl"))
    except OSError:
        return -1


class TuiPlugin:
    """Delivery into one VibeApp. tick() does one poll; the thread calls it every second."""

    def __init__(self, app):
        self.app = app
        self.address = None
        self.suffix = None
        self.last_beat = 0.0
        self.missing = missing_names(app)
        self.wake = "none" if self.missing else "inject"
        self.warned_runner = False
        if self.missing:
            log(f"vibe {vibe_version()}: missing {', '.join(self.missing)}: wake none, messages wait for vibe-run")
        else:
            log(f"vibe {vibe_version()}: TUI plugin ready (inject)")

    # -- identity and heartbeat

    def session_id(self):
        return getattr(getattr(self.app, "agent_loop", None), "session_id", None)

    def session_dir(self):
        logger = getattr(getattr(self.app, "agent_loop", None), "session_logger", None)
        return getattr(logger, "session_dir", None)

    def follow(self):
        """Update the address from the current session; the old one is let go on a change."""
        vs = sessions()
        suffix = vs.suffix_of(self.session_id())
        if suffix == self.suffix:
            return
        old, self.suffix = self.address, suffix
        self.address = f"vibe:{suffix}" if suffix else None
        if self.address:
            # `bridge.py send` from vibe's bash tool: _default_sender reads ANTIGRAVITY_CONVERSATION_ID
            # first, which a vibe started from an agy shell inherits (§3).
            os.environ.pop("ANTIGRAVITY_CONVERSATION_ID", None)
            os.environ["AGENT_BRIDGE_ADDRESS"] = self.address
        if old:
            self.release(old)
        self.last_beat = 0.0
        log(f"session {self.session_id()}: address {self.address}")

    def release(self, address):
        with contextlib.suppress(Exception):
            if (core()._read_peer(address) or {}).get("tui_pid") == os.getpid():
                core()._update_peer(address, tui_pid=None, tui_seen=0)

    def paused(self):
        with contextlib.suppress(Exception):
            queue = self.app._input_queue
            return bool(len(queue) and queue.paused)
        return False

    def beat(self, now=None):
        now = now or time.time()
        if not self.address or now - self.last_beat < BEAT_SECONDS:
            return
        self.last_beat = now
        core()._update_peer(self.address, tui_pid=os.getpid(), tui_seen=now, session=self.session_id(),
                            workspace=os.getcwd(), paused=self.paused(), wake=self.wake,
                            version=vibe_version())

    # -- in-flight record

    def record_path(self):
        return runner()._inflight_path(self.suffix)

    def read_record(self):
        import json
        try:
            with open(self.record_path(), encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, ValueError):
            return None
        return record if isinstance(record, dict) else None

    def write_record(self, claimed):
        import json
        path = self.record_path()
        tmp = f"{path}.{os.getpid()}.tmp"
        folder = self.session_dir()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "role": "tui", "session": self.session_id(), "at": time.time(),
                       "paths": [p for p, _ in claimed], "session_dir": str(folder) if folder else None,
                       "size": _size(folder), "started": False}, f)
        os.replace(tmp, path)

    def update_record(self, **fields):
        import json
        record = self.read_record()
        if record is None:
            return
        record.update(fields)
        path = self.record_path()
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(record, f)
        os.replace(tmp, path)

    def put_back(self, record, why):
        paths = [p for p in record.get("paths") or [] if isinstance(p, str) and os.path.exists(p)]
        core().unclaim(self.address, [(p, None) for p in paths])
        runner().clear_inflight(self.suffix)
        log(f"put back {len(paths)} message(s) to {self.address}: {why}")

    def settle(self):
        """Finish this process's in-flight record once its turn has ended, or recover a dead one."""
        record = self.read_record()
        if record is None:
            return
        if record.get("pid") != os.getpid():
            with core()._locked(core()._path("state", f"vibe-{self.suffix}"), wait=0.5):
                runner().recover_inflight(self.suffix, quiet=True)
            return
        if not record.get("started"):
            return  # deliver() has not finished; it settles a turn that never started itself
        task = getattr(self.app, "_agent_task", None)
        if self.app._agent_running or (task is not None and not task.done()):
            return  # the turn runs
        folder = self.session_dir()
        grown = folder is not None and (str(folder) != record.get("session_dir")
                                         or _size(folder) > record.get("size", -1))
        if grown:
            runner().clear_inflight(self.suffix)
            log(f"turn of {record.get('paths') and len(record['paths'])} message(s) saved in {folder}")
        else:
            self.put_back(record, "the turn ended before anything was saved (cancelled)")

    # -- delivery

    async def _deliver(self):
        """In vibe's event loop: check idle, claim, call _handle_user_message. Returns what happened."""
        if not idle(self.app):
            return "busy"
        c = core()
        with c._locked(c._path("state", f"vibe-{self.suffix}"), wait=0.2):
            peer = c._read_peer(self.address) or {}
            if c._alive(peer.get("runner_pid")):
                return "runner"
            claimed = c.claim(self.address)
            if not claimed:
                return "empty"
            self.write_record(claimed)
        text = runner().incoming_text([m for _, m in claimed], self.address)
        before = self.app._agent_task
        try:
            await self.app._handle_user_message(text)
        except BaseException:
            self.put_back(self.read_record() or {"paths": [p for p, _ in claimed]}, "_handle_user_message raised")
            raise
        task = self.app._agent_task
        if task is None or task is before:
            # vibe kept the text in the input box instead (images could not be prepared): undo.
            box = getattr(self.app, "_chat_input_container", None)
            if box is not None and getattr(box, "value", None) == text:
                box.value = ""
            self.put_back(self.read_record() or {"paths": [p for p, _ in claimed]}, "no turn started")
            return "no-turn"
        self.update_record(started=True)
        log(f"delivered {[m['id'] for _, m in claimed]} to {self.address}")
        return "delivered"

    def deliver(self):
        """From the plugin thread. Cheap checks first, then one call into vibe's loop."""
        if self.wake != "inject" or not self.address:
            return "off"
        c = core()
        peer = c._read_peer(self.address) or {}
        if c._alive(peer.get("runner_pid")):
            if not self.warned_runner:
                self.warned_runner = True
                with contextlib.suppress(Exception):
                    self.app.call_from_thread(
                        self.app.notify, "A Diaulos background turn (vibe-run) is running in this conversation. "
                        "Its reply will be in the session after /resume.")
            return "runner"
        if not c._pending(self.address):
            return "empty"
        if self.read_record() is not None:
            return "in-flight"
        return self.app.call_from_thread(self._deliver)

    def tick(self, now=None):
        """One poll. Never raises."""
        try:
            self.follow()
            if not self.address:
                return "no-session"
            self.beat(now)
            self.settle()
            return self.deliver()
        except Exception as e:
            log(f"tick failed: {e!r}\n{traceback.format_exc()}")
            return "error"

    def close(self):
        if self.address:
            self.release(self.address)


class NoAppPlugin(TuiPlugin):
    """A TUI whose VibeApp was not found (vibe slower than FIND_APP_SECONDS, or a new
    run_textual_ui). No delivery, but the heartbeat still says tui_pid and wake none, so
    vibe-run never starts a turn beside a live TUI (§4.5). The session comes from vibe's
    AgentLoop, an argument of run_textual_ui (app.py:4537), found in the stacks or with gc."""

    def __init__(self, loop=None):
        self.app = _Holder(loop)
        self.address = None
        self.suffix = None
        self.last_beat = 0.0
        self.missing = ["VibeApp"]
        self.wake = "none"
        self.warned_runner = False
        log(f"vibe {vibe_version()}: TUI without a VibeApp found: wake none, messages wait for vibe-run")

    def tick(self, now=None):
        """One poll. Never raises."""
        try:
            if getattr(self.app.agent_loop, "session_id", None) is None:
                self.app.agent_loop = find_loop()
            self.follow()
            if not self.address:
                return "no-session"
            self.beat(now)
            return "no-app"
        except Exception as e:
            log(f"tick failed: {e!r}\n{traceback.format_exc()}")
            return "error"


class _Holder:
    def __init__(self, loop):
        self.agent_loop = loop


# ---------------------------------------------------------------- the vibe-acp side

def agent_loop_classes():
    """vibe's AgentLoop class(es) already imported, found in sys.modules (no vibe import)."""
    found = []
    for name, module in list(sys.modules.items()):
        if not name.startswith("vibe."):
            continue
        cls = getattr(module, "AgentLoop", None)
        if isinstance(cls, type) and cls not in found:
            found.append(cls)
    return found


def agent_loops():
    """vibe's AgentLoop objects in this process (found with gc, by class name)."""
    return [o for o in gc.get_objects()
            if type(o).__name__ == "AgentLoop" and type(o).__module__.startswith("vibe.")]


def patch_class(cls, price):
    """Make every later _setup_middleware() of this class apply the cap first. vibe calls it at
    the end of AgentLoop.__init__ (after reading its own max_price) and on each
    session/set_config_option max_turns, before any prompt. False if the name is missing."""
    original = cls.__dict__.get("_setup_middleware")
    if not callable(original):
        return False
    if getattr(original, "diaulos_price", None) is not None:
        return True

    def _setup_middleware(self, *args, **kwargs):
        current = getattr(self, "_max_price", None)
        if current is None or current > price:
            self._max_price = price
        return original(self, *args, **kwargs)

    _setup_middleware.diaulos_price = price
    _setup_middleware.__wrapped__ = original
    cls._setup_middleware = _setup_middleware
    return True


def cap_price(loop, price):
    """Set vibe's own price limit on one AgentLoop that exists already. False when vibe's private
    names are missing."""
    if not hasattr(loop, "_max_price") or not callable(getattr(loop, "_setup_middleware", None)):
        return False
    current = loop._max_price
    if current is None or current > price:
        loop._max_price = price
        loop._setup_middleware()
    return True


class AcpPlugin:
    """In a vibe-acp started by the bridge: price cap, and acp_pid / price_cap in the peer file."""

    def __init__(self, price, suffix):
        self.price = price
        self.suffix = suffix if suffix and sessions().SUFFIX_RE.match(suffix) else None
        self.address = f"vibe:{self.suffix}" if self.suffix else None
        self.capped = None  # None: nothing to cap seen yet; True / False after

    def report(self, ok, how):
        if ok == self.capped:
            return ok
        self.capped = ok
        if ok:
            log(f"price cap ${self.price} set ({how})")
        else:
            log(f"vibe {vibe_version()}: {NO_PRICE_CAP} (AgentLoop has no _max_price/_setup_middleware)")
        if self.address:
            core()._update_peer(self.address, acp_pid=os.getpid(), price_cap=ok)
        return ok

    def apply(self, classes=None, loops=None):
        """Patch the classes, cap the loops; record the result. Returns True, False, or None."""
        classes = agent_loop_classes() if classes is None else classes
        loops = agent_loops() if loops is None else loops
        if self.price is None:
            if (classes or loops) and self.capped is None:
                self.report(False, "no price given")
            return self.capped
        if not classes and not loops:
            return self.capped
        ok = all([patch_class(c, self.price) for c in classes] + [cap_price(o, self.price) for o in loops])
        return self.report(ok, f"{len(classes)} class(es), {len(loops)} AgentLoop(s)")

    def register(self):
        if self.address:
            core()._update_peer(self.address, acp_pid=os.getpid())

    def close(self):
        with contextlib.suppress(Exception):
            if self.address and (core()._read_peer(self.address) or {}).get("acp_pid") == os.getpid():
                core()._update_peer(self.address, acp_pid=None)


def _price_from_env():
    raw = os.environ.get("AGENT_BRIDGE_VIBE_MAX_PRICE")
    if not raw:
        return None
    try:
        price = float(raw)
    except ValueError:
        log(f"AGENT_BRIDGE_VIBE_MAX_PRICE={raw!r} is not a number")
        return None
    return price if price > 0 else None


def acp_mode():
    return bool(os.environ.get("AGENT_BRIDGE_VIBE_MAX_PRICE") or os.environ.get("AGENT_BRIDGE_VIBE_SUFFIX"))


# ---------------------------------------------------------------- the thread

def _is_app(o):
    return type(o).__name__ == "VibeApp" and hasattr(o, "agent_loop")


def _in_stacks(test):
    for frame in list(sys._current_frames().values()):
        while frame is not None:
            for value in list(frame.f_locals.values()):
                if test(value):
                    return value
            frame = frame.f_back
    return None


def _is_loop(o):
    return type(o).__name__ == "AgentLoop" and type(o).__module__.startswith("vibe.")


def find_loop():
    """vibe's AgentLoop of this TUI, or None: in the stacks first (gc.freeze hides it from gc)."""
    loop = _in_stacks(_is_loop)
    if loop is None:
        loops = agent_loops()
        loop = loops[0] if loops else None
    return loop


def prompt_mode(argv=None):
    """True for `vibe -p` / `--prompt`: no TUI in this process. Only vibe's own command line
    counts (another program, e.g. `unittest discover -p`, has a -p of its own)."""
    argv = sys.argv if argv is None else argv
    if not argv or not os.path.basename(argv[0]).lower().startswith("vibe"):
        return False
    return any(a in ("-p", "--prompt") or a.startswith("--prompt=") for a in argv[1:])


def _app_in_stacks():
    """VibeApp from the locals of a running frame: run_textual_ui() keeps it in `app` while
    the TUI runs (app.py:4549). gc cannot be used alone: VibeApp.on_mount calls gc.freeze()
    (app.py:833), and gc.get_objects() leaves frozen objects out."""
    return _in_stacks(_is_app)


def find_app(timeout=FIND_APP_SECONDS):
    deadline = time.time() + timeout
    while not _stop.is_set():
        app = _app_in_stacks()
        if app is not None:
            return app
        for o in gc.get_objects():
            if _is_app(o):
                return o
        if time.time() > deadline:
            return None
        _stop.wait(0.5)
    return None


def _run_acp(plugin):
    """vibe-acp started by vibe-run or vibe-new: watch for new AgentLoops (session/new, fork)."""
    while not _stop.wait(PRICE_WATCH_SECONDS):
        plugin.apply()


def _run_no_app(acp=None):
    plugin = NoAppPlugin()
    atexit.register(plugin.close)
    while not _stop.is_set():
        if acp is not None:
            with contextlib.suppress(Exception):
                acp.apply()
        plugin.tick()
        _stop.wait(POLL_SECONDS)
    plugin.close()


def _run_tui(app, acp=None):
    plugin = TuiPlugin(app)
    atexit.register(plugin.close)
    plugin.follow()
    with contextlib.suppress(Exception):
        if plugin.address and core()._alive((core()._read_peer(plugin.address) or {}).get("runner_pid")):
            plugin.deliver()  # shows the notice of §4.5
    while not _stop.is_set():
        if acp is not None:
            with contextlib.suppress(Exception):
                acp.apply(classes=[], loops=[app.agent_loop])  # vibe may reset _max_price on a reload
        plugin.tick()
        _stop.wait(POLL_SECONDS)
    plugin.close()


def _run(acp=None):
    try:
        if acp is not None and acp.suffix:
            _run_acp(acp)  # vibe-acp of vibe-run: never a TUI
            return
        in_acp = os.path.basename(sys.argv[0] if sys.argv else "").startswith("vibe-acp")
        no_tui = in_acp or prompt_mode()
        app = None if no_tui else find_app()
        if app is not None:
            _run_tui(app, acp)
        elif not no_tui and not _stop.is_set():
            _run_no_app(acp)
        else:
            log(f"vibe {vibe_version()}: no VibeApp (vibe -p or vibe-acp): tools only")
            if acp is not None:
                _run_acp(acp)  # vibe-acp of vibe-new
    except Exception as e:
        log(f"plugin thread stopped: {e!r}\n{traceback.format_exc()}")


_thread = None


def start():
    """Start the plugin thread, once per process. Never raises."""
    global _started, _thread
    with _start_lock:
        if _started:
            return False
        _started = True
    try:
        acp = None
        if acp_mode():
            # The cap goes on now, in the importing thread: the shim is imported while
            # AgentLoop.__init__ builds its ToolManager, before its first _setup_middleware().
            acp = AcpPlugin(_price_from_env(), os.environ.get("AGENT_BRIDGE_VIBE_SUFFIX"))
            atexit.register(acp.close)
            acp.register()
            acp.apply(loops=[])
        _thread = threading.Thread(target=_run, args=(acp,), name="diaulos-vibe-plugin", daemon=True)
        _thread.start()
    except Exception as e:
        log(f"could not start the plugin thread: {e!r}")
        return False
    return True


def stop(timeout=5.0):
    """Stop the plugin thread (tests only)."""
    _stop.set()
    if _thread is not None:
        _thread.join(timeout)

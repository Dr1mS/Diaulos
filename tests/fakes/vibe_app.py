"""Stand-in for the vibe TUI app (Mistral Vibe 2.20, `VibeApp` in
vibe/cli/textual_ui/app.py), for the tests of the plugin. Python 3.8+,
standard library only.

It copies the names and shapes the plugin reads (docs/VIBE-DESIGN.md, §4.2),
from the installed vibe 2.20.0 source:

  app._agent_running            bool                         app.py:523
  app._current_bottom_app       BottomApp (str enum)         app.py:316-341
  app._queue                    QueueController, .draining   message_queue.py:145, 292
  app._input_queue              property: _queue.queue,      app.py:606-608
                                MessageQueue: len(), .paused message_queue.py:44-60
  app._bash_task                asyncio.Task or None         app.py:699, 1066-1072
  app._agent_task               asyncio.Task or None: created by
                                _handle_user_message only when a turn starts,
                                None again when it ends   app.py:525, 2032-2034, 2287
  app._chat_input_container.value   the input box text      app.py:802, container.py:112-121
  app._handle_user_message(message, *, title_source=None)
                                coroutine                    app.py:2001-2003
  app.call_from_thread(cb, *args, **kwargs)
                                textual App: runs cb in the app's loop, awaits
                                it if it returns an awaitable, returns the result;
                                RuntimeError if the app is not running or if
                                called from the app's own thread
  app.agent_loop.session_id     str; changes on /clear and /compact (probe S9 B3)
  app.agent_loop.session_logger.session_dir

The app runs its own asyncio loop in a thread (`start()` / `stop()`), as
Textual does, so the plugin thread talks to it only through call_from_thread.

Test controls (call them from the test thread):
  turn(seconds)       not needed directly: _handle_user_message runs a turn of
                      `turn_seconds` (default 0.2), then appends a line to the
                      current session's messages.jsonl and sets end_time.
  set_bottom(name)    e.g. "Approval": the bottom panel is not the input.
  queue_prompt(text)  a prompt queued by the person (len(_input_queue) grows).
  pause_queue() / resume_queue()   Esc / Enter on a non-empty queue.
  start_draining(s)   _queue.draining is true for s seconds.
  start_bash(s)       a `!` command of the person that runs s seconds.
  cancel_turn()       Esc during a turn: the turn ends without saving.
  clear()             /clear: new id, same suffix, no parent; the folder is
                      created at the end of the next turn (probe S9).
  compact()           /compact: new id, same suffix, parent = old id, new folder.
  images_abort = True _handle_user_message fills the input box (if empty) and
                      returns without a turn and without an _agent_task, as when
                      _prepare_images_or_abort aborts (app.py:2008-2012).
  _agent_running turns true inside the turn task, a moment after
  _handle_user_message returns, as in vibe: test _agent_task, not _agent_running.
  calls               list of (message, title_source) received.

make_app(..., missing=("_bash_task",)) builds an app where those names do
not exist (hasattr is False), to test a vibe whose private API changed.
"""
import asyncio
import json
import os
import secrets
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum


class BottomApp(str, Enum):
    """Same members and values as vibe's StrEnum with auto() (lower-case names)."""
    Approval = "approval"
    Config = "config"
    ConnectorAuth = "connectorauth"
    Input = "input"
    MCP = "mcp"
    MCPOAuth = "mcpoauth"
    ModelPicker = "modelpicker"
    ProxySetup = "proxysetup"
    Question = "question"
    ThemePicker = "themepicker"
    ThinkingPicker = "thinkingpicker"
    Rewind = "rewind"
    VibeCodeProjectPicker = "vibecodeprojectpicker"
    VibeCodeProjectCreate = "vibecodeprojectcreate"
    SessionPicker = "sessionpicker"
    Voice = "voice"


class MessageQueue:
    def __init__(self):
        self._items = []
        self._paused = False

    def __len__(self):
        return len(self._items)

    def __bool__(self):
        return bool(self._items)

    @property
    def paused(self):
        return self._paused


class QueueController:
    def __init__(self):
        self._queue = MessageQueue()
        self._drain_task = None

    @property
    def queue(self):
        return self._queue

    def __len__(self):
        return len(self._queue)

    @property
    def draining(self):
        return self._drain_task is not None and not self._drain_task.done()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _new_id(suffix=None):
    head = secrets.token_hex(10)
    return "%s-%s-%s-%s-%s" % (head[:8], head[8:12], head[12:16], head[16:20],
                               suffix or secrets.token_hex(6))


class ChatInputContainer:
    def __init__(self):
        self.value = ""


class SessionLogger:
    def __init__(self, save_dir):
        self.save_dir = save_dir
        self.session_dir = None


class AgentLoop:
    """The parts of vibe's AgentLoop the plugin may read."""

    def __init__(self, save_dir, cwd):
        self.session_logger = SessionLogger(save_dir)
        self.cwd = cwd
        self.session_id = None
        self.parent_session_id = None
        self.messages = []
        self.new_session(_new_id(), None, create_folder=True)

    def new_session(self, session_id, parent, create_folder):
        self.session_id, self.parent_session_id = session_id, parent
        self.session_logger.session_dir = None
        if create_folder:
            self._create_folder()

    def _create_folder(self):
        folder = os.path.join(self.session_logger.save_dir, "session_%s_%s" % (
            datetime.now().strftime("%Y%m%d_%H%M%S_%f"), self.session_id[:8]))
        os.makedirs(folder)
        self.session_logger.session_dir = folder
        self._write_meta(start=True)

    def _write_meta(self, start=False):
        path = os.path.join(self.session_logger.session_dir, "meta.json")
        meta = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
        meta.update({"session_id": self.session_id, "parent_session_id": self.parent_session_id,
                     "environment": {"working_directory": self.cwd},
                     "title_source": meta.get("title_source", "auto")})
        if start:
            meta["start_time"] = _now_iso()
        meta["end_time"] = _now_iso()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=2)

    def save(self, role, content):
        if self.session_logger.session_dir is None:  # after /clear: folder at the end of the turn
            self._create_folder()
        with open(os.path.join(self.session_logger.session_dir, "messages.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps({"role": role, "content": content,
                                "message_id": str(uuid.uuid4())}) + "\n")
        self.messages.append({"role": role, "content": content})
        self._write_meta()


class VibeApp:
    """Fake of vibe's VibeApp. The class name is the real one, for a gc lookup."""

    def __init__(self, save_dir, cwd=None):
        os.makedirs(save_dir, exist_ok=True)
        self.agent_loop = AgentLoop(save_dir, cwd or os.getcwd())
        self._agent_running = False
        self._current_bottom_app = BottomApp.Input
        self._queue = QueueController()
        self._bash_task = None
        self._agent_task = None
        self._chat_input_container = ChatInputContainer()
        self.images_abort = False
        self.turn_seconds = 0.2
        self.calls = []
        self.notifications = []
        self._loop = None
        self._thread = None
        self._thread_id = None

    # --- vibe's names -------------------------------------------------------

    @property
    def _input_queue(self):
        return self._queue.queue

    async def _handle_user_message(self, message, *, title_source=None):
        self.calls.append((message, title_source))
        if self.images_abort:
            if not self._chat_input_container.value:
                self._chat_input_container.value = message
            return
        if not self._agent_running:
            self._agent_task = asyncio.ensure_future(self._run_turn(message))

    def notify(self, message, **kwargs):
        self.notifications.append(message)

    def call_from_thread(self, callback, *args, **kwargs):
        if self._loop is None:
            raise RuntimeError("App is not running")
        if self._thread_id == threading.get_ident():
            raise RuntimeError(
                "The `call_from_thread` method must run in a different thread from the app")

        async def run_callback():
            result = callback(*args, **kwargs)
            if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                result = await result
            return result

        return asyncio.run_coroutine_threadsafe(run_callback(), self._loop).result()

    # --- the turn -----------------------------------------------------------

    async def _run_turn(self, message):
        self._agent_running = True
        try:
            await asyncio.sleep(self.turn_seconds)
            self.agent_loop.save("user", message)
            self.agent_loop.save("assistant", "ok")
        except asyncio.CancelledError:
            pass
        finally:
            self._agent_running = False
            self._agent_task = None

    # --- test controls ------------------------------------------------------

    def start(self):
        ready = threading.Event()

        def run():
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._thread_id = threading.get_ident()
            self._loop.call_soon(ready.set)
            self._loop.run_forever()

        self._thread = threading.Thread(target=run, name="fake-vibe-app", daemon=True)
        self._thread.start()
        ready.wait(5)
        return self

    def stop(self):
        if self._loop is not None:
            loop = self._loop

            async def finish():  # end a running turn, as quitting vibe does
                tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                loop.stop()
            asyncio.run_coroutine_threadsafe(finish(), loop)
            self._thread.join(5)
            self._loop = None
            loop.close()

    def _in_loop(self, fn):
        """Run fn() in the app loop and wait for it (test side)."""
        async def run():
            return fn()
        return asyncio.run_coroutine_threadsafe(run(), self._loop).result()

    def set_bottom(self, name):
        self._current_bottom_app = BottomApp[name]

    def queue_prompt(self, text):
        self._in_loop(lambda: self._input_queue._items.append(text))

    def pause_queue(self):
        self._in_loop(lambda: setattr(self._input_queue, "_paused", True))

    def resume_queue(self):
        self._in_loop(lambda: setattr(self._input_queue, "_paused", False))

    def start_draining(self, seconds):
        def go():
            self._queue._drain_task = asyncio.ensure_future(asyncio.sleep(seconds))
        self._in_loop(go)

    def start_bash(self, seconds):
        def go():
            self._bash_task = asyncio.ensure_future(asyncio.sleep(seconds))
        self._in_loop(go)

    def cancel_turn(self):
        def go():
            if self._agent_task is not None:
                self._agent_task.cancel()
        self._in_loop(go)

    def clear(self):
        loop = self.agent_loop
        suffix = loop.session_id.rsplit("-", 1)[-1]
        self._in_loop(lambda: loop.new_session(_new_id(suffix), None, create_folder=False))

    def compact(self):
        loop = self.agent_loop
        old = loop.session_id
        self._in_loop(lambda: loop.new_session(_new_id(old.rsplit("-", 1)[-1]), old,
                                               create_folder=True))

    @property
    def input_value(self):
        return self._chat_input_container.value

    @property
    def suffix(self):
        return self.agent_loop.session_id.rsplit("-", 1)[-1]


FakeVibeApp = VibeApp


def make_app(save_dir, cwd=None, missing=()):
    """A started VibeApp; names in `missing` do not exist on it (hasattr is False)."""
    if not missing:
        return VibeApp(save_dir, cwd).start()
    gone = frozenset(missing)

    class _Partial(VibeApp):
        def __getattribute__(self, name):
            if name in gone:
                raise AttributeError(name)
            return object.__getattribute__(self, name)

    _Partial.__name__ = "VibeApp"
    return _Partial(save_dir, cwd).start()

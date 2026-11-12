"""The vibe fakes run alone (T5a): tests/fakes/vibe-acp answers a scripted ACP
exchange, FakeVibeApp has the names and shapes of vibe 2.20's VibeApp, and
the session fixtures have the layout vibe writes. When vibe 2.20 is installed
on the machine, the fake's names are also checked against its source."""
import glob
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from helpers import FAKES, WINDOWS, wait_for

sys.path.insert(0, FAKES)
import vibe_app  # noqa: E402
from vibe_sessions.build import EXPECTED, SUFFIX, build  # noqa: E402

FAKE_ACP = os.path.join(FAKES, "vibe-acp")


class AcpClient:
    """A minimal ACP client over the fake's stdio."""

    def __init__(self, env, cwd):
        self.proc = subprocess.Popen([sys.executable, FAKE_ACP], cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     encoding="utf-8", bufsize=1)
        self.lines = queue.Queue()
        self.notes = []
        self.next_id = 1
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in self.proc.stdout:
            self.lines.put(json.loads(line))
        self.lines.put(None)

    def write(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def call(self, method, params, on_request=None, timeout=10):
        rid = self.next_id
        self.next_id += 1
        self.write({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        while True:
            msg = self.lines.get(timeout=max(0.1, deadline - time.time()))
            if msg is None:
                return None
            if msg.get("id") == rid and "method" not in msg:
                return msg
            if "method" in msg and "id" in msg and on_request:
                self.write({"jsonrpc": "2.0", "id": msg["id"], "result": on_request(msg)})
            elif "method" in msg:
                self.notes.append(msg)

    def close(self):
        if self.proc.stdin and not self.proc.stdin.closed:
            self.proc.stdin.close()
        try:
            return self.proc.wait(10)
        finally:
            self.proc.stdout.close()
            self.proc.stderr.close()


class FakeVibeAcpTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="diaulos-vibe-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.vibe_home = os.path.join(self.tmp, "vibe_home")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.vibe_home)
        os.makedirs(self.work)
        self.log = os.path.join(self.tmp, "fake-vibe.jsonl")
        self.env = dict(os.environ, VIBE_HOME=self.vibe_home, FAKE_VIBE_LOG=self.log)
        for key in [k for k in self.env if k.startswith("AGENT_BRIDGE_")] + ["BRIDGE", "FAKE_VIBE_MODE"]:
            self.env.pop(key, None)

    def client(self, mode=""):
        c = AcpClient(dict(self.env, FAKE_VIBE_MODE=mode), self.work)
        self.addCleanup(c.close)
        self.assertEqual(c.call("initialize", {"protocolVersion": 1, "clientCapabilities": {}})
                         ["result"]["agentCapabilities"]["loadSession"], True)
        return c

    def records(self, event=None):
        with open(self.log, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f]
        return [r for r in rows if event is None or r["event"] == event]

    def sessions(self):
        return sorted(glob.glob(os.path.join(self.vibe_home, "logs", "session", "session_*")))

    def meta(self, folder):
        with open(os.path.join(folder, "meta.json"), encoding="utf-8") as f:
            return json.load(f)

    def lines(self, folder):
        with open(os.path.join(folder, "messages.jsonl"), encoding="utf-8") as f:
            return len(f.readlines())

    def prompt(self, c, sid, text, **kw):
        return c.call("session/prompt", {"sessionId": sid, "prompt": [{"type": "text", "text": text}]}, **kw)

    def new_session(self, c):
        r = c.call("session/new", {"cwd": self.work, "mcpServers": []})
        return r["result"]["sessionId"]

    def test_new_then_load_keeps_id_and_folder(self):
        c = self.client()
        sid = self.new_session(c)
        self.assertRegex(sid, r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
        self.assertIn("result", c.call("session/set_config_option",
                                       {"sessionId": sid, "configId": "max_turns", "value": "5"}))
        r = self.prompt(c, sid, "first")
        self.assertEqual(r["result"]["stopReason"], "end_turn")
        kinds = [n["params"]["update"]["sessionUpdate"] for n in c.notes]
        self.assertEqual(kinds, ["user_message_chunk", "agent_message_chunk", "usage_update"])
        self.assertEqual(c.close(), 0)
        [folder] = self.sessions()
        self.assertTrue(os.path.basename(folder).endswith("_" + sid[:8]))
        self.assertEqual(self.lines(folder), 2)
        self.assertEqual(self.meta(folder)["title"], "first")

        c2 = self.client()
        self.assertIn("result", c2.call("session/load", {"sessionId": sid, "cwd": self.work, "mcpServers": []}))
        self.prompt(c2, sid, "second")
        self.assertEqual(c2.close(), 0)
        self.assertEqual(self.sessions(), [folder])
        self.assertEqual(self.lines(folder), 4)
        meta = self.meta(folder)
        self.assertEqual(meta["session_id"], sid)
        self.assertEqual(meta["stats"]["session_cost"], 0.01)  # this process only, like vibe-acp
        self.assertEqual([r["max_turns"] for r in self.records("prompt")], [5, None])

    def test_load_unknown_session_is_invalid_params(self):
        c = self.client()
        r = c.call("session/load", {"sessionId": "00000000-0000-0000-0000-000000000000", "cwd": self.work})
        self.assertEqual(r["error"]["code"], -32602)
        self.assertIn("Session not found", r["error"]["message"])

    def test_save_dir_from_config_toml(self):
        other = os.path.join(self.tmp, "elsewhere")
        with open(os.path.join(self.vibe_home, "config.toml"), "w", encoding="utf-8") as f:
            f.write('save_dir = "not/this/one"\n[session_logging]\nsave_dir = "%s"\nenabled = true\n'
                    % other.replace("\\", "\\\\"))
        c = self.client()
        sid = self.new_session(c)
        self.assertEqual(len(glob.glob(os.path.join(other, "session_*_" + sid[:8]))), 1)

    def test_permission_request_and_reject_once(self):
        c = self.client("permission")
        sid = self.new_session(c)
        asked = []

        def answer(msg):
            asked.append(msg)
            return {"outcome": {"outcome": "selected", "optionId": "reject_once"}}

        r = self.prompt(c, sid, "do it", on_request=answer)
        self.assertEqual(r["result"]["stopReason"], "end_turn")
        self.assertEqual(len(asked), 1)
        self.assertEqual(asked[0]["method"], "session/request_permission")
        self.assertEqual(set(asked[0]["params"]["toolCall"]), {"toolCallId"})
        self.assertEqual([o["optionId"] for o in asked[0]["params"]["options"]],
                         ["allow_once", "allow_always", "allow_always_permanent", "reject_once"])
        self.assertEqual([r["option"] for r in self.records("permission-answer")], ["reject_once"])

    def test_die_in_prompt_saves_nothing(self):
        c = self.client("die-in-prompt")
        sid = self.new_session(c)
        self.assertIsNone(self.prompt(c, sid, "lost?"))
        self.assertEqual(c.close(), 3)
        self.assertEqual(self.lines(self.sessions()[0]), 0)

    def test_die_after_save_keeps_the_user_message(self):
        c = self.client("die-after-save")
        sid = self.new_session(c)
        self.assertIsNone(self.prompt(c, sid, "saved then killed"))
        self.assertEqual(c.close(), 3)
        self.assertEqual(self.lines(self.sessions()[0]), 1)

    def test_error_prompt(self):
        c = self.client("error-prompt")
        sid = self.new_session(c)
        self.assertEqual(self.prompt(c, sid, "x")["error"]["code"], -32603)

    def test_turn_limit_and_slow(self):
        c = self.client("turn-limit,slow=0.3")
        sid = self.new_session(c)
        c.call("session/set_config_option", {"sessionId": sid, "configId": "max_turns", "value": "1"})
        start = time.time()
        r = self.prompt(c, sid, "x")
        self.assertGreaterEqual(time.time() - start, 0.3)
        self.assertEqual(r["result"]["stopReason"], "end_turn")
        texts = [n["params"]["update"]["content"]["text"] for n in c.notes
                 if n["params"]["update"]["sessionUpdate"] == "agent_message_chunk"]
        self.assertEqual(texts, ["Turn limit of 1 reached"])

    def test_counts_bridge_messages_and_logs_env(self):
        c = AcpClient(dict(self.env, AGENT_BRIDGE_VIBE_SUFFIX="abc", AGENT_BRIDGE_VIBE_MAX_PRICE="0.5"), self.work)
        self.addCleanup(c.close)
        c.call("initialize", {"protocolVersion": 1})
        sid = self.new_session(c)
        self.prompt(c, sid, '<bridge-message id="1" from="claude:a">x</bridge-message>'
                            '<bridge-message id="2" from="claude:a">y</bridge-message>')
        self.assertEqual(self.records("prompt")[0]["messages"], 2)
        self.assertEqual(self.records("start")[0]["env"],
                         {"AGENT_BRIDGE_VIBE_SUFFIX": "abc", "AGENT_BRIDGE_VIBE_MAX_PRICE": "0.5"})

    def test_unknown_option_exits_2(self):
        r = subprocess.run([sys.executable, FAKE_ACP, "--nope"], env=self.env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)

    @unittest.skipIf(WINDOWS, "an executable script found through PATH")
    def test_runs_as_vibe_acp_through_path(self):
        bin_dir = os.path.join(self.tmp, "bin")
        os.makedirs(bin_dir)
        shutil.copy(FAKE_ACP, os.path.join(bin_dir, "vibe-acp"))
        env = dict(self.env, PATH=bin_dir + os.pathsep + self.env.get("PATH", ""))
        found = shutil.which("vibe-acp", path=env["PATH"])
        self.assertEqual(found, os.path.join(bin_dir, "vibe-acp"))
        r = subprocess.run([found], input="", env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(r.returncode, 0, r.stderr)


class FakeVibeAppTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="diaulos-vibeapp-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.save_dir = os.path.join(self.tmp, "session")
        self.app = vibe_app.make_app(self.save_dir, self.tmp)
        self.addCleanup(self.app.stop)
        self.app.turn_seconds = 0.1

    def idle(self, app=None):
        """The five conditions of docs/VIBE-DESIGN.md §4.2, read in the app's loop."""
        app = app or self.app

        def check():
            q = app._input_queue
            return (not app._agent_running and app._current_bottom_app == vibe_app.BottomApp.Input
                    and not app._queue.draining and len(q) == 0 and not q.paused
                    and (app._bash_task is None or app._bash_task.done()))
        return app.call_from_thread(check)

    def messages_lines(self):
        folder = self.app.agent_loop.session_logger.session_dir
        if folder is None:
            return 0
        path = os.path.join(folder, "messages.jsonl")
        if not os.path.exists(path):
            return 0
        with open(path, encoding="utf-8") as f:
            return len(f.readlines())

    def test_idle_message_runs_a_turn_and_saves(self):
        self.assertTrue(self.idle())
        self.assertIsNone(self.app._agent_task)
        self.app.call_from_thread(self.app._handle_user_message, "hello")
        self.assertIsNotNone(self.app._agent_task)  # the turn started
        self.assertTrue(wait_for(lambda: self.app._agent_running, 2))
        self.assertFalse(self.idle())
        self.assertTrue(wait_for(lambda: self.app._agent_task is None, 5))
        self.assertFalse(self.app._agent_running)
        self.assertEqual(self.messages_lines(), 2)
        self.assertEqual(self.app.calls, [("hello", None)])
        self.assertTrue(self.idle())

    def test_call_from_thread_rules(self):
        with self.assertRaises(RuntimeError):
            self.app.call_from_thread(lambda: self.app.call_from_thread(lambda: 1))
        self.assertEqual(self.app.call_from_thread(lambda x, y=0: x + y, 2, y=3), 5)
        self.app.stop()
        with self.assertRaises(RuntimeError):
            self.app.call_from_thread(lambda: 1)

    def test_each_busy_state_fails_the_idle_check(self):
        self.app.set_bottom("Approval")
        self.assertFalse(self.idle())
        self.app.set_bottom("Input")
        self.app.queue_prompt("queued by the person")
        self.assertFalse(self.idle())
        self.app.pause_queue()
        self.assertTrue(self.app._input_queue.paused)
        self.app.call_from_thread(lambda: self.app._input_queue._items.clear())
        self.assertFalse(self.idle())  # paused, even empty
        self.app.resume_queue()
        self.assertTrue(self.idle())
        self.app.start_draining(0.3)
        self.assertFalse(self.idle())
        self.assertTrue(wait_for(self.idle, 3))
        self.app.start_bash(0.3)
        self.assertFalse(self.idle())
        self.assertTrue(wait_for(self.idle, 3))

    def test_images_abort_fills_the_input_without_a_turn(self):
        self.app.images_abort = True
        self.app.call_from_thread(self.app._handle_user_message, "into the box")
        self.assertIsNone(self.app._agent_task)
        self.assertFalse(self.app._agent_running)
        self.assertEqual(self.app._chat_input_container.value, "into the box")

    def test_cancelled_turn_saves_nothing(self):
        self.app.turn_seconds = 5
        self.app.call_from_thread(self.app._handle_user_message, "esc me")
        self.assertTrue(wait_for(lambda: self.app._agent_running, 2))
        self.app.cancel_turn()
        self.assertTrue(wait_for(lambda: self.app._agent_task is None, 3))
        self.assertEqual(self.messages_lines(), 0)

    def test_clear_and_compact_keep_the_suffix(self):
        first, suffix = self.app.agent_loop.session_id, self.app.suffix
        self.app.clear()
        cleared = self.app.agent_loop.session_id
        self.assertNotEqual(cleared, first)
        self.assertEqual(self.app.suffix, suffix)
        self.assertIsNone(self.app.agent_loop.parent_session_id)
        self.assertIsNone(self.app.agent_loop.session_logger.session_dir)  # folder at the end of the next turn
        self.assertEqual(len(os.listdir(self.save_dir)), 1)
        self.app.call_from_thread(self.app._handle_user_message, "after clear")
        self.assertTrue(wait_for(lambda: len(os.listdir(self.save_dir)) == 2, 5))
        self.app.compact()
        self.assertEqual(self.app.agent_loop.parent_session_id, cleared)
        self.assertEqual(self.app.suffix, suffix)
        self.assertEqual(len(os.listdir(self.save_dir)), 3)

    def test_missing_private_names(self):
        app = vibe_app.make_app(os.path.join(self.tmp, "other"), self.tmp,
                                missing=("_bash_task", "_handle_user_message"))
        self.addCleanup(app.stop)
        self.assertFalse(hasattr(app, "_bash_task"))
        self.assertFalse(hasattr(app, "_handle_user_message"))
        self.assertTrue(hasattr(app, "_agent_running"))
        self.assertEqual(type(app).__name__, "VibeApp")


def installed_vibe():
    """site-packages/vibe of an installed vibe 2.20, or None."""
    base = os.environ.get("VIBE_SOURCE")
    if base:
        return base if os.path.isdir(base) else None
    found = glob.glob(os.path.expanduser("~/.local/share/uv/tools/mistral-vibe/lib/python3*/site-packages/vibe"))
    return found[0] if found else None


@unittest.skipIf(installed_vibe() is None, "vibe 2.20 is not installed here")
class FakeMatchesRealVibeTest(unittest.TestCase):
    """The fake's names exist in the installed vibe, with the same shapes."""

    @classmethod
    def setUpClass(cls):
        root = installed_vibe()
        with open(os.path.join(root, "cli", "textual_ui", "app.py"), encoding="utf-8") as f:
            cls.app_src = f.read()
        with open(os.path.join(root, "cli", "textual_ui", "message_queue.py"), encoding="utf-8") as f:
            cls.queue_src = f.read()

    def test_app_names(self):
        for snippet in ("self._agent_running = False", "self._current_bottom_app",
                        "self._queue = QueueController(", "def _input_queue(self) -> MessageQueue:",
                        "return self._queue.queue", "self._bash_task = ",
                        "async def _handle_user_message(\n        self, message: str, *, title_source: str | None = None",
                        "if self._queue.draining:", "class VibeApp(",
                        "self._agent_task: asyncio.Task | None = None", "self._agent_task = asyncio.create_task(",
                        "self._chat_input_container = self.query_one(ChatInputContainer)"):
            self.assertIn(snippet, self.app_src)

    def test_queue_names(self):
        for snippet in ("def __len__(self) -> int:", "def paused(self) -> bool:", "def draining(self) -> bool:",
                        "def queue(self) -> MessageQueue:"):
            self.assertIn(snippet, self.queue_src)

    def test_bottom_app_members(self):
        block = self.app_src.split("class BottomApp(StrEnum):", 1)[1].split("\n\n\nclass ", 1)[0]
        real = [line.split("=")[0].strip() for line in block.splitlines() if line.strip().endswith("= auto()")]
        self.assertEqual(real, [m.name for m in vibe_app.BottomApp])


class SessionFixturesTest(unittest.TestCase):

    def test_layout(self):
        tmp = tempfile.mkdtemp(prefix="diaulos-vibesessions-")
        self.addCleanup(shutil.rmtree, tmp, True)
        save_dir = os.path.join(tmp, "session")
        ids, folders = build(save_dir, tmp)
        self.assertEqual(set(ids), set(EXPECTED))
        for case, sids in ids.items():
            for sid, folder in zip(sids, folders[case]):
                self.assertTrue(sid.endswith("-" + SUFFIX[case]))
                self.assertTrue(os.path.basename(folder).endswith("_" + sid[:8]))
                with open(os.path.join(folder, "meta.json"), encoding="utf-8") as f:
                    self.assertEqual(json.load(f)["session_id"], sid)
        top = [d for d in os.listdir(save_dir) if d.startswith("session_")]
        self.assertEqual(len(top), 10)  # the subagent under agents/ is not at the top
        self.assertTrue(glob.glob(os.path.join(save_dir, "*", "agents", "session_*_ddddddd2", "meta.json")))
        # The current session of each listed conversation has the latest end_time.
        for case, want in EXPECTED.items():
            if "current" not in want:
                continue
            metas = []
            for folder in folders[case]:
                with open(os.path.join(folder, "meta.json"), encoding="utf-8") as f:
                    metas.append(json.load(f))
            latest = max(range(len(metas)), key=lambda i: metas[i]["end_time"])
            self.assertEqual(latest, want["current"], case)


if __name__ == "__main__":
    unittest.main()

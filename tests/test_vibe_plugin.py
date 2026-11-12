"""The Diaulos plugin inside vibe (bridge/vibe_plugin.py and the shim install/vibe-tool.py,
docs/VIBE-DESIGN.md §4.1 to §4.5, task T3c).

TuiTest drives TuiPlugin.tick() by hand against the fake VibeApp of tests/fakes/vibe_app.py,
which runs its own asyncio loop in a thread as Textual does. ThreadTest lets the plugin
thread find the fake app with gc, as in vibe. AcpTest uses a fake AgentLoop class
registered under a `vibe.` module name. ShimTest loads the shim with vibe's own loader
(ToolManager._load_tools_from_file) in vibe's Python, when vibe is installed.

Everything runs in this process, in a throwaway bridge home and VIBE_HOME: no model call.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import types
import unittest

from helpers import BRIDGE, FAKES, ROOT, BridgeTest, _restore_env, wait_for

sys.path.insert(0, FAKES)
import vibe_app  # noqa: E402

PLUGIN = os.path.join(ROOT, "bridge", "vibe_plugin.py")
SHIM = os.path.join(ROOT, "install", "vibe-tool.py")
VIBE_PYTHON = os.path.expanduser("~/.local/share/uv/tools/mistral-vibe/bin/python")
WJ = "⁠"


class PluginSetup(BridgeTest):
    """The plugin module loaded fresh for this test's homes; the agent_bridge_* modules dropped after."""

    def setUp(self):
        super().setUp()
        self.vibe_home = os.path.join(self.home, ".vibe")
        self.save_dir = os.path.join(self.vibe_home, "logs", "session")
        self.workspace = os.path.join(self.tmp, "work")
        os.makedirs(self.workspace)
        self.env["VIBE_HOME"] = self.vibe_home
        for key in ("HOME", "USERPROFILE", "AGENT_BRIDGE_HOME", "VIBE_HOME", "AGENT_BRIDGE_ADDRESS",
                    "AGENT_BRIDGE_VIBE_MAX_PRICE", "AGENT_BRIDGE_VIBE_SUFFIX"):
            self.addCleanup(_restore_env, key, os.environ.get(key))
            if key in self.env:
                os.environ[key] = self.env[key]
            else:
                os.environ.pop(key, None)
        self.addCleanup(self._drop_modules)
        spec = importlib.util.spec_from_file_location("agent_bridge_vibe_plugin", PLUGIN)
        self.plugin = importlib.util.module_from_spec(spec)
        sys.modules["agent_bridge_vibe_plugin"] = self.plugin
        spec.loader.exec_module(self.plugin)
        self.core = self.plugin.core()

    def _drop_modules(self):
        plugin = sys.modules.get("agent_bridge_vibe_plugin")
        if plugin is not None:
            plugin.stop()
        for name in [n for n in sys.modules if n.startswith("agent_bridge_") or n.startswith("vibe.fake")]:
            del sys.modules[name]

    def log_text(self):
        return self.state("vibe-plugin.log")


class TuiSetup(PluginSetup):

    def setUp(self):
        super().setUp()
        self.app = self.make_app()
        self.tui = self.plugin.TuiPlugin(self.app)
        self.addCleanup(self.tui.close)
        self.assertEqual(self.tui.tick(), "empty")  # first heartbeat: tui_pid is this process
        self.address = self.tui.address

    def make_app(self, missing=()):
        app = vibe_app.make_app(self.save_dir, self.workspace, missing=missing)
        self.addCleanup(app.stop)
        return app

    def put(self, text, sender="claude:t"):
        return self.core.send(self.address, text, sender)

    def pending(self, address=None):
        return self.core._pending(address or self.address)

    def record(self):
        return self.tui.read_record()

    def turn_over(self, timeout=10):
        return wait_for(lambda: self.app._agent_task is None and not self.app._agent_running, timeout, 0.05)

    def saved_lines(self):
        folder = self.app.agent_loop.session_logger.session_dir
        try:
            with open(os.path.join(folder, "messages.jsonl"), encoding="utf-8") as f:
                return [json.loads(line) for line in f]
        except (OSError, TypeError):
            return []


class TuiTest(TuiSetup):

    def test_idle_delivers_with_escaped_at_and_the_reply_line(self):
        self.put("ping @all, see @file.py")
        self.assertEqual(self.tui.tick(), "delivered")
        self.assertEqual(len(self.app.calls), 1)
        text = self.app.calls[0][0]
        self.assertIn("ping @" + WJ + "all, see @" + WJ + "file.py", text)
        self.assertNotIn("@all", text)
        self.assertTrue(text.endswith(f"Reply with the bridge_send tool, to=claude:t  (you are {self.address})"),
                        text)
        self.assertEqual(self.pending(), [])
        self.assertTrue(self.record()["started"])
        self.assertTrue(self.turn_over())
        self.assertEqual(self.tui.tick(), "empty")
        self.assertIsNone(self.record(), "the record of a saved turn stays")
        self.assertEqual([m["role"] for m in self.saved_lines()], ["user", "assistant"])

    def test_a_burst_goes_in_one_turn(self):
        for i in range(3):
            self.put(f"part {i}")
        self.assertEqual(self.tui.tick(), "delivered")
        self.assertEqual(len(self.app.calls), 1)
        for i in range(3):
            self.assertIn(f"part {i}", self.app.calls[0][0])

    def test_no_claim_while_vibe_is_busy(self):
        def running():
            self.app._in_loop(lambda: setattr(self.app, "_agent_running", True))
            return lambda: self.app._in_loop(lambda: setattr(self.app, "_agent_running", False))

        def bottom():
            self.app.set_bottom("Approval")
            return lambda: self.app.set_bottom("Input")

        def draining():
            self.app.start_draining(30)
            return lambda: self.app._in_loop(lambda: self.app._queue._drain_task.cancel())

        def queued():
            self.app.queue_prompt("the person's next prompt")
            return lambda: self.app._in_loop(lambda: self.app._input_queue._items.clear())

        def paused():
            self.app.queue_prompt("held")
            self.app.pause_queue()
            return lambda: (self.app.resume_queue(),
                            self.app._in_loop(lambda: self.app._input_queue._items.clear()))

        def bash():
            self.app.start_bash(30)
            return lambda: self.app._in_loop(lambda: self.app._bash_task.cancel())

        self.put("wait for me")
        for name, make_busy in (("_agent_running", running), ("bottom app", bottom), ("draining", draining),
                                ("queue not empty", queued), ("queue paused", paused), ("_bash_task", bash)):
            with self.subTest(name):
                undo = make_busy()
                self.assertEqual(self.tui.tick(), "busy")
                self.assertEqual(len(self.pending()), 1, "a message was claimed while vibe was busy")
                self.assertEqual(self.app.calls, [])
                self.assertIsNone(self.record())
                undo()
                time.sleep(0.05)
        self.assertEqual(self.tui.tick(), "delivered")

    def test_a_message_sent_during_a_turn_waits_for_its_end(self):
        self.app.turn_seconds = 1.0
        self.put("first")
        self.assertEqual(self.tui.tick(), "delivered")
        self.put("second")
        self.assertIn(self.tui.tick(), ("in-flight", "busy"))
        self.assertEqual(len(self.pending()), 1)
        self.assertTrue(self.turn_over())
        self.assertEqual(self.tui.tick(), "delivered")
        self.assertEqual(len(self.app.calls), 2)
        self.assertIn("second", self.app.calls[1][0])

    def test_an_exception_puts_the_message_back(self):
        async def broken(message, *, title_source=None):
            raise RuntimeError("vibe changed")
        self.app._handle_user_message = broken
        self.put("keep me")
        self.assertEqual(self.tui.tick(), "error")
        self.assertEqual(len(self.pending()), 1)
        self.assertIsNone(self.record())
        self.assertIn("_handle_user_message raised", self.log_text())

    def test_images_abort_puts_back_and_clears_the_input_box(self):
        self.app.images_abort = True
        self.put("with an image")
        self.assertEqual(self.tui.tick(), "no-turn")
        self.assertEqual(len(self.app.calls), 1)
        self.assertEqual(self.app.input_value, "")
        self.assertEqual(len(self.pending()), 1)
        self.assertIsNone(self.record())

    def test_images_abort_keeps_what_the_person_typed(self):
        self.app.images_abort = True
        self.app._chat_input_container.value = "half a sentence"
        self.put("with an image")
        self.assertEqual(self.tui.tick(), "no-turn")
        self.assertEqual(self.app.input_value, "half a sentence")

    def test_a_turn_cancelled_before_any_save_puts_the_message_back(self):
        self.app.turn_seconds = 5.0
        self.put("cancel me")
        self.assertEqual(self.tui.tick(), "delivered")
        self.assertTrue(wait_for(lambda: self.app._agent_running, 5, 0.02))
        self.app.cancel_turn()
        self.assertTrue(self.turn_over())
        self.app.turn_seconds = 0.2
        self.assertEqual(self.tui.tick(), "delivered")  # put back by settle(), then delivered again
        self.assertIn("cancelled", self.log_text())
        self.assertEqual(len(self.app.calls), 2)
        self.assertIn("cancel me", self.app.calls[1][0])
        self.assertTrue(self.turn_over())
        self.tui.tick()
        self.assertEqual([m["role"] for m in self.saved_lines()], ["user", "assistant"])

    def test_a_dead_record_is_put_back_once(self):
        msg = self.put("lost in a killed vibe")
        claimed = self.core.claim(self.address)
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        path = self.tui.record_path()
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"pid": proc.pid, "role": "tui", "session": None, "paths": [p for p, _ in claimed],
                       "started": True}, f)
        self.assertEqual(self.tui.tick(), "delivered")
        self.assertEqual(len(self.app.calls), 1)
        self.assertIn(msg["id"], self.app.calls[0][0])
        self.assertTrue(self.turn_over())
        for _ in range(3):
            self.tui.tick()
        self.assertEqual(len(self.app.calls), 1, "a dead record was replayed twice")

    def test_a_running_vibe_run_is_left_alone_and_shown_once(self):
        runner = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(runner.wait)
        self.addCleanup(runner.kill)
        self.core._update_peer(self.address, runner_pid=runner.pid)
        self.put("for the runner")
        self.assertEqual(self.tui.tick(), "runner")
        self.assertEqual(self.tui.tick(), "runner")
        self.assertEqual(len(self.app.notifications), 1)
        self.assertIn("vibe-run", self.app.notifications[0])
        self.assertEqual(len(self.pending()), 1)

    def test_heartbeat_and_address(self):
        peer = self.peer(self.address)
        self.assertEqual(peer["tui_pid"], os.getpid())
        self.assertEqual(peer["wake"], "inject")
        self.assertEqual(peer["session"], self.app.agent_loop.session_id)
        self.assertEqual(peer["workspace"], os.getcwd())
        self.assertFalse(peer["paused"])
        self.assertEqual(self.address, "vibe:" + self.app.suffix)
        self.assertEqual(os.environ["AGENT_BRIDGE_ADDRESS"], self.address)
        self.assertIn(f"{self.address}  ", self.listing())
        self.assertIn("TUI: wakes on message", self.listing())

    def test_compact_and_clear_keep_the_address(self):
        self.app.compact()
        self.tui.tick()
        self.assertEqual(self.tui.address, self.address)
        self.app.clear()
        self.put("after clear")
        self.assertEqual(self.tui.tick(), "delivered")
        self.assertTrue(self.turn_over())
        self.assertEqual(self.tui.tick(), "empty")
        self.assertIsNone(self.record(), "a turn saved in the folder made after /clear was taken as cancelled")

    def test_resume_moves_the_address_and_lets_the_old_one_go(self):
        old = self.address
        other = vibe_app._new_id()
        self.app._in_loop(lambda: self.app.agent_loop.new_session(other, None, create_folder=True))
        self.tui.tick()
        self.assertEqual(self.tui.address, "vibe:" + other.rsplit("-", 1)[1])
        self.assertEqual(os.environ["AGENT_BRIDGE_ADDRESS"], self.tui.address)
        self.assertIsNone(self.peer(old).get("tui_pid"))
        self.assertEqual(self.peer(self.tui.address)["tui_pid"], os.getpid())

    def test_bridge_send_and_list_from_vibe(self):
        sid = self.app.agent_loop.session_id
        answer = self.plugin.send("claude", "hello from vibe", session_id=sid)
        self.assertTrue(answer.startswith("sent "), answer)
        got = self.inbox("claude")
        self.assertEqual([(m["from"], m["body"]) for m in got], [(self.address, "hello from vibe")])
        self.assertIn(f"{self.address}", self.plugin.list_agents(session_id=sid))
        self.assertIn("(you)", self.plugin.list_agents(session_id=sid))
        os.environ.pop("AGENT_BRIDGE_ADDRESS")  # no session id and no address: refused
        self.assertTrue(self.plugin.send("claude", "x", session_id=None).startswith("error"))
        self.assertTrue(self.plugin.list_agents(days=0).startswith("error"))
        self.assertTrue(self.plugin.send("bad address", "x", session_id=sid).startswith("error"))

    def test_bridge_py_send_from_vibe_bash_has_the_same_from(self):
        env = dict(self.env, AGENT_BRIDGE_ADDRESS=os.environ["AGENT_BRIDGE_ADDRESS"])
        self.bridge("send", "claude", "from bash", env=env)
        self.assertEqual([m["from"] for m in self.inbox("claude")], [self.address])


class MissingNameTest(PluginSetup):

    def test_a_missing_private_name_gives_wake_none_and_no_claim(self):
        for name in ("_bash_task", "_handle_user_message", "_agent_task"):
            with self.subTest(name):
                app = vibe_app.make_app(self.save_dir, self.workspace, missing=(name,))
                self.addCleanup(app.stop)
                tui = self.plugin.TuiPlugin(app)
                self.addCleanup(tui.close)
                self.assertEqual(tui.wake, "none")
                self.assertEqual(tui.tick(), "off")
                self.core.send(tui.address, "wait", "claude:t")
                self.assertEqual(tui.tick(), "off")
                self.assertEqual(len(self.core._pending(tui.address)), 1)
                self.assertEqual(self.peer(tui.address)["wake"], "none")
                self.assertIn(f"missing {name}", self.log_text())
                self.assertIn("TUI, no injection", self.listing())


class ThreadTest(PluginSetup):

    def test_the_thread_finds_the_app_and_delivers_under_3_seconds(self):
        app = vibe_app.make_app(self.save_dir, self.workspace)
        self.addCleanup(app.stop)
        self.assertTrue(self.plugin.start())
        self.assertFalse(self.plugin.start(), "a second start() started a second thread")
        address = "vibe:" + app.suffix
        self.assertTrue(wait_for(lambda: self.peer(address).get("tui_pid") == os.getpid(), 15, 0.05),
                        self.log_text())
        times = []
        for i in range(3):
            sent = time.time()
            self.core.send(address, f"ping {i}", "claude:t")
            self.assertTrue(wait_for(lambda: len(app.calls) == i + 1, 10, 0.02), self.log_text())
            times.append(time.time() - sent)
            self.assertTrue(wait_for(lambda: app._agent_task is None, 10, 0.02))
        print(f"\n  fake TUI delivery times: {', '.join(f'{t:.2f}s' for t in times)}", file=sys.stderr)
        self.assertLess(sorted(times)[1], 3.0)
        self.plugin.stop()
        self.assertIsNone(self.peer(address).get("tui_pid"), "stop did not release the address")

    def test_an_app_hidden_from_gc_by_freeze_is_found_in_the_stacks(self):
        import gc
        import threading
        app = vibe_app.make_app(self.save_dir, self.workspace)
        self.addCleanup(app.stop)
        gc.collect()
        gc.freeze()  # as VibeApp.on_mount does (vibe app.py:833)
        self.addCleanup(gc.unfreeze)
        self.assertFalse(any(o is app for o in gc.get_objects()), "gc still sees the frozen app")
        found = []
        holder = threading.Thread(target=lambda: found.append(self.plugin.find_app(5)))

        def run_textual_ui(app=app):  # like vibe's run_textual_ui, which keeps `app` in its frame
            holder.start()
            holder.join()
        run_textual_ui()
        self.assertIs(found[0], app)

    def test_no_app_means_tools_only(self):
        self.plugin.FIND_APP_SECONDS = 0.3
        self.plugin.find_app.__defaults__ = (0.3,)
        self.plugin.start()
        self.assertTrue(wait_for(lambda: "tools only" in self.log_text(), 5), self.log_text())


def fake_loop_module(with_names=True):
    """A module `vibe.fake_loop` with an AgentLoop shaped like vibe 2.20's (_loop.py:343-432, 1093-1108)."""
    module = types.ModuleType("vibe.fake_loop")

    class AgentLoop:
        def __init__(self, max_price=None):
            self.pipeline = []
            if with_names:
                self._max_turns = None
                self._max_price = max_price  # _loop.py:411, read from vibe's own config
                self._setup_middleware()  # _loop.py:432

        def set_max_turns(self, n):
            self._max_turns = n
            self._setup_middleware()

        if with_names:
            def _setup_middleware(self):
                self.pipeline = []
                if self._max_turns is not None:
                    self.pipeline.append(("turns", self._max_turns))
                if self._max_price is not None:
                    self.pipeline.append(("price", self._max_price))
        else:
            def set_max_turns(self, n):  # noqa: F811  (a vibe whose private names changed)
                self.pipeline = [("turns", n)]

    AgentLoop.__module__ = module.__name__
    module.AgentLoop = AgentLoop
    sys.modules[module.__name__] = module
    return AgentLoop


class AcpTest(PluginSetup):
    SUFFIX = "0123456789ab"

    def acp_env(self, price="0.5", suffix=SUFFIX):
        if price is not None:
            os.environ["AGENT_BRIDGE_VIBE_MAX_PRICE"] = price
        if suffix is not None:
            os.environ["AGENT_BRIDGE_VIBE_SUFFIX"] = suffix

    def test_max_price_caps_every_agent_loop(self):
        AgentLoop = fake_loop_module()
        before = AgentLoop(max_price=2.0)  # made before the shim was imported
        self.acp_env()
        self.plugin.start()
        new = AgentLoop()  # made after: vibe-acp's session/load
        self.assertEqual(new.pipeline, [("price", 0.5)])
        new.set_max_turns(20)
        self.assertEqual(new.pipeline, [("turns", 20), ("price", 0.5)])
        self.assertTrue(wait_for(lambda: before._max_price == 0.5, 5, 0.05), self.log_text())
        self.assertEqual(before.pipeline, [("price", 0.5)])
        cheaper = AgentLoop(max_price=0.1)
        self.assertEqual(cheaper.pipeline, [("price", 0.1)], "a lower cap of vibe's config was raised")
        peer = self.peer("vibe:" + self.SUFFIX)
        self.assertEqual(peer["acp_pid"], os.getpid())
        self.assertIs(peer["price_cap"], True)
        self.assertIn("price cap $0.5 set", self.log_text())

    def test_without_suffix_no_peer_file(self):
        AgentLoop = fake_loop_module()
        self.acp_env(suffix=None)
        self.plugin.start()
        self.assertEqual(AgentLoop().pipeline, [("price", 0.5)])
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "peers")) and
                         os.listdir(os.path.join(self.bridge_home, "peers")))

    def test_a_vibe_without_the_private_names_says_so(self):
        AgentLoop = fake_loop_module(with_names=False)
        self.acp_env()
        self.assertTrue(self.plugin.start())
        loop = AgentLoop()
        loop.set_max_turns(3)
        self.assertEqual(loop.pipeline, [("turns", 3)])
        self.assertTrue(wait_for(lambda: "price cap not set, max_turns only" in self.log_text(), 5),
                        self.log_text())
        peer = self.peer("vibe:" + self.SUFFIX)
        self.assertIs(peer["price_cap"], False)
        self.assertEqual(peer["acp_pid"], os.getpid())
        self.core._update_peer("vibe:" + self.SUFFIX, workspace=self.workspace)
        os.kill(os.getpid(), 0)  # acp_pid is this test process: alive, no TUI
        self.assertIn("price cap not set, max_turns only", self.listing())

    def test_a_bad_price_is_logged_and_nothing_breaks(self):
        AgentLoop = fake_loop_module()
        self.acp_env(price="lots")
        self.assertTrue(self.plugin.start())
        self.assertEqual(AgentLoop(max_price=None).pipeline, [])
        self.assertTrue(wait_for(lambda: "is not a number" in self.log_text(), 5))
        self.assertTrue(wait_for(lambda: self.peer("vibe:" + self.SUFFIX).get("price_cap") is False, 5))


@unittest.skipUnless(os.path.exists(VIBE_PYTHON), "vibe is not installed with uv on this machine")
class ShimTest(BridgeTest):
    """The shim in vibe's own Python, loaded by vibe's ToolManager, in a throwaway bridge home."""

    SCRIPT = r'''
import importlib.util, json, os, sys
from pathlib import Path
home = sys.argv[1]
out = {"remote_before": importlib.util.find_spec("remote") is not None}
before = list(sys.path)
from vibe.core.tools.manager import ToolManager
tools = ToolManager._load_tools_from_file(Path(home, "tools", "agent_bridge.py"))
out["tools"] = sorted(t.get_name() for t in tools)
shim = sys.modules[tools[0].__module__]
out["permissions"] = sorted({str(shim.BridgeSendConfig().permission), str(shim.BridgeListAgentsConfig().permission)})
out["path_after_load"] = sys.path == before
sid = "07413e06-1111-2222-3333-1afa1ee3ff50"
out["send"] = shim._call("send", "claude", "from the shim", session_id=sid)
out["path_after_send"] = sys.path == before
out["remote_send"] = shim._call("send", "remote:bob/claude:x", "far", session_id=sid)
out["path_added"] = [p for p in sys.path if p not in before]
out["path_appended"] = sys.path[:len(before)] == before
out["remote_file"] = getattr(sys.modules.get("remote"), "__file__", None)
out["remote_saw_core"] = getattr(sys.modules.get("remote"), "CORE", None) is sys.modules.get("agent_bridge_core")
import vibe.core.tools.base, vibe.core.types, vibe.core.agent_loop
import vibe.cli.textual_ui.app
out["vibe_file"] = vibe.__file__
out["modules"] = sorted(k for k in sys.modules if k.startswith("agent_bridge") or k in ("bridge", "remote"))
print(json.dumps(out))
'''

    FAKE_REMOTE = '''"""Fake remote.py: records the call, needs no crypto."""
import bridge
CORE = bridge


def _crypto():
    return None


def start():
    return None
'''

    def test_shim_in_vibe(self):
        home = self.bridge_home
        for name in ("bridge.py", "vibe_sessions.py", "vibe_runner.py", "vibe_plugin.py"):
            shutil.copy(os.path.join(ROOT, "bridge", name), home)
        with open(os.path.join(home, "remote.py"), "w", encoding="utf-8") as f:
            f.write(self.FAKE_REMOTE)
        os.makedirs(os.path.join(home, "remote", "peers"))
        with open(os.path.join(home, "remote", "peers", "bob.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "bob"}, f)
        os.makedirs(os.path.join(home, "tools"))
        with open(SHIM, encoding="utf-8") as f:
            shim = f.read().replace("@AGENT_BRIDGE_HOME@", home)
        with open(os.path.join(home, "tools", "agent_bridge.py"), "w", encoding="utf-8") as f:
            f.write(shim)
        env = {k: v for k, v in self.env.items() if k != "AGENT_BRIDGE_HOME"}
        proc = subprocess.run([VIBE_PYTHON, "-c", self.SCRIPT, home], capture_output=True, text=True,
                              env=env, timeout=120, cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        print(f"\n  shim in vibe: {json.dumps(out)}".replace(self.tmp, "<tmp>"), file=sys.stderr)
        self.assertFalse(out["remote_before"], "a module named `remote` exists in vibe's Python")
        self.assertEqual(out["tools"], ["bridge_list_agents", "bridge_send"])
        self.assertEqual(out["permissions"], ["always"])
        self.assertTrue(out["path_after_load"] and out["path_after_send"], out)
        self.assertTrue(out["send"].startswith("sent "), out["send"])
        self.assertTrue(out["remote_send"].startswith("sent "), out["remote_send"])
        self.assertEqual(out["path_added"], [home])
        self.assertTrue(out["path_appended"])
        self.assertEqual(out["remote_file"], os.path.join(home, "remote.py"))
        self.assertTrue(out["remote_saw_core"], "remote.py loaded a second bridge instead of the core")
        self.assertNotIn(home, out["vibe_file"])
        self.assertEqual([m["from"] for m in self.inbox("claude")], ["vibe:1afa1ee3ff50"])

    def test_vibe_starts_with_a_broken_bridge(self):
        os.makedirs(os.path.join(self.bridge_home, "tools"))
        with open(SHIM, encoding="utf-8") as f:
            shim = f.read().replace("@AGENT_BRIDGE_HOME@", self.bridge_home)  # no vibe_plugin.py there
        with open(os.path.join(self.bridge_home, "tools", "agent_bridge.py"), "w", encoding="utf-8") as f:
            f.write(shim)
        script = ("import sys; from pathlib import Path; from vibe.core.tools.manager import ToolManager; "
                  "t = ToolManager._load_tools_from_file(Path(sys.argv[1], 'tools', 'agent_bridge.py')); "
                  "m = sys.modules[t[0].__module__]; print(sorted(x.get_name() for x in t)); "
                  "print(m._call('send', 'claude:t', 'x', session_id=None))")
        proc = subprocess.run([VIBE_PYTHON, "-c", script, self.bridge_home], capture_output=True, text=True,
                              env=self.env, timeout=120, cwd=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("['bridge_list_agents', 'bridge_send']", proc.stdout)
        self.assertIn("error: Diaulos is not available", proc.stdout)
        self.assertIn("plugin not started", self.state("vibe-plugin.log"))


if __name__ == "__main__":
    unittest.main()

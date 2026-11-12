"""Mistral Vibe conversations in the bridge (docs/VIBE-DESIGN.md §6, task T3a):
discovery from vibe's save_dir, addresses vibe:<suffix> and vibe:<name>,
listing, resolve, and the start of one vibe-run for a closed conversation.

Run with a throwaway VIBE_HOME filled by tests/fakes/vibe_sessions/build.py;
a test never reads the real ~/.vibe. The fake vibe-acp is put on PATH so the
bridge sees vibe-acp as installed. Not on Windows (the fake is a script found
through PATH).
"""
import ast
import json
import os
import shutil
import subprocess
import sys
import time
import unittest

from helpers import BRIDGE, FAKES, ROOT, WINDOWS, BridgeTest, wait_for

sys.path.insert(0, os.path.join(FAKES, "vibe_sessions"))
from build import EXPECTED, SUFFIX, build  # noqa: E402

VIBE_SESSIONS = os.path.join(ROOT, "bridge", "vibe_sessions.py")


def load_vibe_sessions():
    import importlib.util
    spec = importlib.util.spec_from_file_location("agent_bridge_vibe_sessions_test", VIBE_SESSIONS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class VibeSetup(BridgeTest):
    """A VIBE_HOME with the fixture sessions, and the fake vibe-acp on PATH."""

    def setUp(self):
        super().setUp()
        self.vibe_home = os.path.join(self.home, ".vibe")
        self.save_dir = os.path.join(self.vibe_home, "logs", "session")
        self.workspace = os.path.join(self.tmp, "work")
        os.makedirs(self.workspace)
        self.ids, self.folders = build(self.save_dir, self.workspace)
        self.fake_bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.fake_bin)
        shutil.copy(os.path.join(FAKES, "vibe-acp"), os.path.join(self.fake_bin, "vibe-acp"))
        for key in [k for k in self.env if k.startswith("AGENT_BRIDGE_") and k != "AGENT_BRIDGE_HOME"]:
            del self.env[key]
        self.env.update(VIBE_HOME=self.vibe_home, BRIDGE=BRIDGE,
                        PATH=self.fake_bin + os.pathsep + self.env.get("PATH", ""))

    def without_vibe_acp(self):
        """PATH without the fake vibe-acp, nor any real one."""
        os.remove(os.path.join(self.fake_bin, "vibe-acp"))
        path = [p for p in self.env["PATH"].split(os.pathsep)
                if not os.path.exists(os.path.join(p, "vibe-acp"))]
        self.env["PATH"] = os.pathsep.join(path)

    def write_peer(self, suffix, **fields):
        folder = os.path.join(self.bridge_home, "peers")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, f"vibe-{suffix}.json"), "w", encoding="utf-8") as f:
            json.dump(dict(address=f"vibe:{suffix}", **fields), f)


class SessionsModuleTest(VibeSetup):
    """bridge/vibe_sessions.py on the fixtures, in this process."""

    def setUp(self):
        super().setUp()
        self.vs = load_vibe_sessions()

    def test_one_entry_per_conversation_with_its_current_session(self):
        found = self.vs.conversations(7, self.save_dir)
        listed = {case for case, s in SUFFIX.items() if s in found}
        self.assertEqual(listed, {c for c, e in EXPECTED.items() if e["listed"]})
        for case, expected in EXPECTED.items():
            if not expected["listed"]:
                continue
            c = found[SUFFIX[case]]
            self.assertEqual(c["session"], self.ids[case][expected["current"]], case)
            self.assertEqual(c["session_dir"], self.folders[case][expected["current"]], case)
            self.assertEqual(c["name"], expected["name"], case)
            self.assertEqual(c["title"], expected["auto_title"], case)
            self.assertEqual(c["workspace"], self.workspace, case)
            self.assertEqual(c["address"], "vibe:" + SUFFIX[case], case)
        self.assertEqual(found[SUFFIX["compacted"]]["sessions"], 3)
        self.assertEqual(found[SUFFIX["cleared"]]["sessions"], 2)

    def test_subagents_are_left_out(self):
        everything = self.vs.conversations(None, self.save_dir)
        self.assertNotIn(SUFFIX["subagent"], everything)
        self.assertEqual(everything[SUFFIX["compacted"]]["sessions"], 3, "a subagent counted in its parent")

    def test_old_conversation_listed_only_with_all(self):
        self.assertNotIn(SUFFIX["old"], self.vs.conversations(7, self.save_dir))
        self.assertIn(SUFFIX["old"], self.vs.conversations(None, self.save_dir))
        self.assertIn(SUFFIX["old"], self.vs.conversations(40, self.save_dir))

    def test_save_dir_from_config(self):
        other = os.path.join(self.tmp, "elsewhere \u00e9")
        cases = [
            ('[session_logging]\nsave_dir = "%s"\n' % other.replace("\\", "\\\\"), other),
            ("[session_logging]\nsave_dir = '%s'\n" % other, other),
            ('model = "x"\n[other]\nsave_dir = "/nope"\n', os.path.join(self.vibe_home, "logs", "session")),
            ('[session_logging]\nenabled = true\nsave_dir = "~/sessions"  # comment\n',
             os.path.expanduser("~/sessions")),
            ("", os.path.join(self.vibe_home, "logs", "session")),
        ]
        for text, expected in cases:
            with open(os.path.join(self.vibe_home, "config.toml"), "w", encoding="utf-8") as f:
                f.write(text)
            self.assertEqual(self.vs.save_dir(self.vibe_home), expected, text)
            # The line reader used before Python 3.11 gives the same answer.
            by_lines = self.vs._save_dir_by_lines(text)
            self.assertEqual(os.path.expanduser(by_lines) if by_lines else
                             os.path.join(self.vibe_home, "logs", "session"), expected, text)

    def test_escapes_of_a_toml_basic_string(self):
        self.assertEqual(self.vs._toml_basic_string(r"C:\\Users\\x\u00e9\"q"), 'C:\\Users\\x\u00e9"q')

    def test_name_of(self):
        self.assertEqual(self.vs.name_of("review bot"), "review_bot")
        self.assertEqual(self.vs.name_of("a/b:c.d-e_f"), "a_b_c.d-e_f")
        self.assertIsNone(self.vs.name_of(None))

    def test_python_38_syntax(self):
        for path in (VIBE_SESSIONS, BRIDGE):
            with open(path, encoding="utf-8") as f:
                ast.parse(f.read(), path, feature_version=(3, 8))


@unittest.skipIf(WINDOWS, "the fake vibe-acp is a script run through PATH")
class BridgeVibeTest(VibeSetup):
    """bridge.py list / send / resolve for vibe:, as a subprocess."""

    def test_list_shows_vibe_conversations(self):
        listed = self.listing()
        for case, expected in EXPECTED.items():
            if expected["listed"]:
                self.assertIn("vibe:" + SUFFIX[case], listed, case)
            else:
                self.assertNotIn("vibe:" + SUFFIX[case], listed, case)
        self.assertIn('"review_bot"', listed)
        self.assertIn("closed: wakes with vibe-acp", listed)
        self.assertIn("vibe:" + SUFFIX["old"], self.listing("--all"))

    def test_list_without_vibe_acp(self):
        self.without_vibe_acp()
        self.assertIn("closed: waits until reopened (no vibe-acp)", self.listing())

    def test_list_with_a_live_tui(self):
        s = SUFFIX["renamed"]
        self.write_peer(s, tui_pid=os.getpid(), tui_seen=time.time(), workspace=self.workspace, version="2.20.0")
        self.assertIn("TUI: wakes on message", self.listing())
        self.write_peer(s, tui_pid=os.getpid(), tui_seen=time.time(), paused=True)
        self.assertIn("TUI, paused (Esc): delivers after Enter", self.listing())
        self.write_peer(s, tui_pid=os.getpid(), tui_seen=time.time(), wake="none", version="9.0.0")
        self.assertIn("TUI, no injection (vibe 9.0.0): waits until vibe exits", self.listing())
        self.write_peer(s, tui_pid=os.getpid(), tui_seen=time.time() - 3600)
        self.assertIn("closed: wakes with vibe-acp", self.listing(), "a stale heartbeat counts as a live TUI")

    def test_list_says_when_the_price_cap_is_missing(self):
        self.write_peer(SUFFIX["renamed"], price_cap=False)
        self.assertIn("closed: wakes with vibe-acp, price cap not set, max_turns only", self.listing())
        self.write_peer(SUFFIX["renamed"], price_cap=True)
        self.assertNotIn("price cap not set", self.listing())

    def test_remote_addresses_accept_vibe(self):
        bridge = self.load_bridge()
        self.assertTrue(bridge.REMOTE_RE.match("remote:laptop/vibe:0123456789ab"))
        self.assertTrue(bridge.REMOTE_RE.match("remote:laptop/vibe:new"))
        self.assertTrue(bridge.ADDRESS_RE.match("vibe:review_bot"))
        self.assertFalse(bridge.ADDRESS_RE.match("vibe:a/b"))

    def test_resolve(self):
        bridge = self.load_bridge()
        os.environ["VIBE_HOME"] = self.vibe_home
        self.addCleanup(os.environ.pop, "VIBE_HOME", None)
        self.addCleanup(os.environ.__setitem__, "PATH", os.environ["PATH"])
        os.environ["PATH"] = self.env["PATH"]
        compacted = "vibe:" + SUFFIX["compacted"]
        self.assertEqual(bridge.resolve(compacted), compacted)
        self.assertEqual(bridge.resolve("vibe:" + SUFFIX["compacted"][:6]), compacted)
        self.assertEqual(bridge.resolve("vibe:review_bot"), "vibe:" + SUFFIX["renamed"])
        self.assertEqual(bridge.resolve("vibe:" + SUFFIX["old"]), "vibe:" + SUFFIX["old"],
                         "an exact address reaches a conversation of any age")
        self.assertEqual(bridge.resolve("vibe:new"), "vibe:new")
        self.assertEqual(bridge.resolve("vibe:new@helper"), "vibe:new")
        with self.assertRaises(SystemExit) as unknown:
            bridge.resolve("vibe:x")
        self.assertIn("matches 0 live vibe peers", str(unknown.exception))
        with self.assertRaises(SystemExit) as subagent:
            bridge.resolve("vibe:" + SUFFIX["subagent"])
        self.assertIn("matches 0", str(subagent.exception))
        with self.assertRaises(SystemExit) as nobody:
            bridge.resolve("vibe")
        self.assertIn("matches 0", str(nobody.exception), "`vibe` alone picks a live TUI only")
        self.write_peer(SUFFIX["cleared"], tui_pid=os.getpid(), tui_seen=time.time())
        self.assertEqual(bridge.resolve("vibe"), "vibe:" + SUFFIX["cleared"])
        self.assertEqual(bridge.resolve("vibe@work"), "vibe:" + SUFFIX["cleared"])

    def test_unknown_vibe_address_is_refused_with_the_list(self):
        proc = self.send("vibe:x", "hello", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("'vibe:x' matches 0 live vibe peers", proc.stderr)
        self.assertIn("vibe:" + SUFFIX["renamed"], proc.stderr, "the refusal lists the conversations")
        self.assertNotIn("vibe:" + SUFFIX["subagent"], proc.stderr)

    def test_vibe_new_needs_vibe_acp(self):
        self.without_vibe_acp()
        proc = self.send("vibe:new", "hello", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("vibe-acp command is not installed", proc.stderr)

    def test_vibe_new_name_is_checked(self):
        for bad in ("a b", "new", "0123456789ab"):
            proc = self.send(f"vibe:new@{bad}", "hello", check=False)
            self.assertNotEqual(proc.returncode, 0, bad)

    def test_vibe_new_queues_with_name_and_workspace(self):
        self.send("vibe:new@helper", "hello", "claude", "--workspace", self.workspace)
        inbox = os.path.join(self.bridge_home, "inbox", "vibe-new")
        names = [n for n in os.listdir(inbox) if n.endswith(".json")]
        self.assertEqual(len(names), 1)
        with open(os.path.join(inbox, names[0]), encoding="utf-8") as f:
            msg = json.load(f)
        self.assertEqual(msg["new"], {"name": "helper", "workspace": self.workspace})
        self.assertTrue(wait_for(lambda: os.path.exists(os.path.join(self.bridge_home, "state", "vibe-new.log")), 5))

    def test_send_to_a_closed_conversation_starts_one_vibe_run(self):
        address = "vibe:" + SUFFIX["renamed"]
        self.send("vibe:review_bot", "first")
        runner = self.peer(address).get("runner_pid")
        self.assertIsInstance(runner, int, self.peer(address))
        self.assertTrue(os.path.exists(os.path.join(self.bridge_home, "state", f"vibe-{SUFFIX['renamed']}.log")))
        # A second message while that vibe-run runs starts no other one.
        sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        self.write_peer(SUFFIX["renamed"], runner_pid=sleeper.pid)
        self.send(address, "second")
        self.assertEqual(self.peer(address).get("runner_pid"), sleeper.pid)
        # Nor while a vibe TUI serves the conversation.
        self.write_peer(SUFFIX["renamed"], tui_pid=os.getpid(), tui_seen=time.time())
        self.send(address, "third")
        self.assertNotIn("runner_pid", self.peer(address))
        self.assertEqual(len(self.inbox(address)), 3)

    def test_no_vibe_run_without_vibe_acp(self):
        self.without_vibe_acp()
        address = "vibe:" + SUFFIX["renamed"]
        self.send(address, "hello")
        self.assertNotIn("runner_pid", self.peer(address))
        self.assertIn("closed: waits until reopened (no vibe-acp)", self.listing())

    def test_bridge_does_not_import_vibe_by_sys_path(self):
        with open(BRIDGE, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("import vibe_sessions", text)
        self.assertIn('spec_from_file_location(module_name, path)', text)


class TextsMentionVibeTest(unittest.TestCase):

    def test_mcp_instructions_and_send_description(self):
        with open(os.path.join(ROOT, "bridge", "mcp_channel.py"), encoding="utf-8") as f:
            text = f.read()
        self.assertTrue("vibe:new" in text and "vibe:<" in text, "mcp_channel.py does not mention vibe:")

    def test_skill(self):
        with open(os.path.join(ROOT, "install", "claude-skill", "SKILL.md"), encoding="utf-8") as f:
            self.assertTrue("vibe:" in f.read(), "SKILL.md does not mention vibe:")


if __name__ == "__main__":
    unittest.main()

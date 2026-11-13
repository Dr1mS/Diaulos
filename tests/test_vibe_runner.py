"""vibe-run and vibe-new (bridge/vibe_runner.py, docs/VIBE-DESIGN.md §8.3), against the fake
`vibe -p` of tests/fakes/vibe and the session fixtures, in a throwaway VIBE_HOME and bridge home.
No model call: the real `vibe -p` is tried by the T14 gates (tests/vibe3/).
Not on Windows (the fake is a script run through AGENT_BRIDGE_VIBE_REAL).
"""
import json
import os
import subprocess
import sys
import time
import unittest

from helpers import BRIDGE, FAKES, WINDOWS, BridgeTest, wait_for

sys.path.insert(0, os.path.join(FAKES, "vibe_sessions"))
from build import SUFFIX, build  # noqa: E402

RENAMED = SUFFIX["renamed"]
ADDRESS = "vibe:" + RENAMED
HEADER = "Message(s) from another agent via the agent bridge."
CUT_NOTE = "Note: the previous bridge turn here stopped at a limit"


class RunnerSetup(BridgeTest):

    def setUp(self):
        super().setUp()
        self.vibe_home = os.path.join(self.home, ".vibe")
        self.save_dir = os.path.join(self.vibe_home, "logs", "session")
        self.workspace = os.path.join(self.tmp, "work")
        os.makedirs(self.workspace)
        self.ids, self.folders = build(self.save_dir, self.workspace)
        self.fake_log = os.path.join(self.tmp, "vibe.jsonl")
        for key in [k for k in self.env if k.startswith(("AGENT_BRIDGE_", "FAKE_VIBE_", "VIBE_"))
                    and k != "AGENT_BRIDGE_HOME"]:
            del self.env[key]
        self.env.update(VIBE_HOME=self.vibe_home, BRIDGE=BRIDGE, FAKE_VIBE_LOG=self.fake_log,
                        AGENT_BRIDGE_VIBE_REAL=os.path.join(FAKES, "vibe"))
        self.set_limits({"retry_seconds": 0})

    def mode(self, value):
        self.env["FAKE_VIBE_MODE"] = value

    def set_limits(self, data):
        with open(os.path.join(self.bridge_home, "state", "vibe-limits.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def runs(self):
        try:
            with open(self.fake_log, encoding="utf-8") as f:
                return [json.loads(line) for line in f]
        except OSError:
            return []

    def runner_done(self, address=ADDRESS, timeout=30):
        """Waits until no vibe-run serves `address`; True if it ended in time."""
        def done():
            pid = self.peer(address).get("runner_pid")
            return not pid or not _alive(pid)
        time.sleep(0.3)
        return wait_for(done, timeout)

    def log_of(self, suffix=RENAMED):
        return self.state(f"vibe-{suffix}.log")

    def session_of(self, case):
        return self.ids[case][0]

    def open_tui(self, suffix=RENAMED, relay=True):
        """A live vibe TUI for the conversation: a relay state file (relay=True) or a vibe-current
        file of the session_start hook (relay=False), with this test's pid as the live process."""
        session = self.session_of("renamed") if suffix == RENAMED else None
        if relay:
            path = os.path.join(self.bridge_home, "state", "vibe-relay", "%d.json" % os.getpid())
            data = {"relay_pid": os.getpid(), "tui_pid": os.getpid(), "session": session, "wake": "relay"}
        else:
            path = os.path.join(self.bridge_home, "state", "vibe-current", "%d.json" % os.getpid())
            data = {"session": session, "app_pid": os.getpid(), "run": False}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path


def _alive(pid):
    """Linux only (/proc, and os.kill(pid, 0) is a Ctrl-C on Windows): for the classes skipped there."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie child of this test process counts as dead
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().split(") ", 1)[1][0] != "Z"
    except OSError:
        return True


def option(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


@unittest.skipIf(WINDOWS, "the fake vibe is a script")
class VibeRunTest(RunnerSetup):

    def test_a_burst_goes_in_one_vibe_p_run_of_the_same_session(self):
        self.send("vibe:review_bot", "first")
        self.send(ADDRESS, "second")
        self.assertTrue(self.runner_done(), self.log_of())
        runs = self.runs()
        self.assertEqual(len(runs), 1, self.log_of())
        argv, prompt = runs[0]["argv"], runs[0]["prompt"]
        self.assertEqual(option(argv, "--resume"), self.session_of("renamed"))
        self.assertNotIn("--workdir", argv)
        self.assertEqual(option(argv, "--max-price"), "0.5")
        self.assertEqual(option(argv, "--max-turns"), "20")
        self.assertEqual(option(argv, "--output"), "json")
        self.assertTrue(prompt.startswith(HEADER), prompt)
        self.assertLess(prompt.index("first"), prompt.index("second"))
        self.assertIn(f"Reply with the send tool of the diaulos MCP server, to=claude  (you are {ADDRESS})", prompt)
        self.assertEqual(runs[0]["cwd"], self.workspace)
        self.assertEqual(runs[0]["run"], "1")
        self.assertEqual(runs[0]["address"], ADDRESS)
        self.assertEqual(self.inbox(ADDRESS), [])
        log = self.log_of()
        self.assertIn(f"in session {self.session_of('renamed')} with vibe in print mode; cost $0.0012", log)
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "state", "vibe-inflight", RENAMED + ".json")))

    def test_an_at_sign_gets_a_word_joiner(self):
        self.send(ADDRESS, "look at @README.md")
        self.assertTrue(self.runner_done(), self.log_of())
        self.assertIn("@⁠README.md", self.runs()[0]["prompt"])

    def test_nothing_runs_while_a_tui_serves_the_conversation(self):
        for relay in (True, False):
            path = self.open_tui(relay=relay)
            self.send(ADDRESS, "for the TUI")
            time.sleep(2)
            self.assertEqual(self.runs(), [], relay)
            self.assertNotIn("runner_pid", self.peer(ADDRESS))
            os.remove(path)
        self.assertEqual([m["body"] for m in self.inbox(ADDRESS)], ["for the TUI", "for the TUI"])

    def test_a_tui_that_holds_the_session_gets_the_messages_back(self):
        self.mode("open")
        self.send(ADDRESS, "wait for the TUI")
        self.assertTrue(self.runner_done(), self.log_of())
        self.assertEqual(len(self.runs()), 1)
        self.assertIn("is open in a vibe TUI again", self.log_of())
        self.assertEqual([m["body"] for m in self.inbox(ADDRESS)], ["wait for the TUI"])
        self.assertEqual(self.inbox("claude"), [], "no failure note for a message that waits")

    def test_a_failed_run_is_retried(self):
        self.mode("fail_once")
        self.send(ADDRESS, "again")
        self.assertTrue(self.runner_done(), self.log_of())
        self.assertEqual(len(self.runs()), 2, self.log_of())
        self.assertIn("not delivered", self.log_of())
        self.assertIn("vibe in print mode exited 1: boom", self.log_of())
        self.assertEqual(self.inbox(ADDRESS), [])
        self.assertEqual(self.inbox("claude"), [])

    def test_a_message_that_always_fails_is_kept_as_failed_and_told(self):
        self.mode("fail")
        self.set_limits({"retry_seconds": 0, "attempts": 2})
        self.send(ADDRESS, "never")
        self.assertTrue(self.runner_done(), self.log_of())
        self.assertEqual(len(self.runs()), 2)
        archive = os.path.join(self.bridge_home, "archive", "vibe-" + RENAMED)
        self.assertEqual(len([n for n in os.listdir(archive) if n.endswith(".failed")]), 1)
        note = self.wait_inbox("claude")
        self.assertTrue(note[0]["body"].startswith("[bridge, not an agent]"), note)
        self.assertIn("was not delivered after 2 attempts with vibe in print mode", note[0]["body"])

    def test_turn_limit_tells_the_sender_and_the_next_text_starts_with_the_note_once(self):
        self.mode("turn_limit")
        self.send(ADDRESS, "long work")
        self.assertTrue(self.runner_done(), self.log_of())
        note = self.wait_inbox("claude")
        self.assertIn("stopped at the turn limit of 20", note[0]["body"])
        self.assertIn("(stopped at the turn limit)", self.log_of())
        self.mode("")
        self.send(ADDRESS, "next")
        self.assertTrue(wait_for(lambda: len(self.runs()) == 2, 15) and self.runner_done(), self.log_of())
        self.send(ADDRESS, "after")
        self.assertTrue(wait_for(lambda: len(self.runs()) == 3, 15) and self.runner_done(), self.log_of())
        prompts = [r["prompt"] for r in self.runs()]
        self.assertTrue(prompts[1].startswith(CUT_NOTE), prompts[1])
        self.assertTrue(prompts[2].startswith(HEADER), prompts[2])

    def test_price_cap_is_told(self):
        self.mode("price")
        self.set_limits({"retry_seconds": 0, "max_price": 0.05})
        self.send(ADDRESS, "costly")
        self.assertTrue(self.runner_done(), self.log_of())
        note = self.wait_inbox("claude")
        self.assertIn("stopped at the price cap $0.05", note[0]["body"])

    def test_limits_file(self):
        self.set_limits({"retry_seconds": 0, "max_turns": 3, "max_price": 0.02, "attempts": "x"})
        self.send(ADDRESS, "hello")
        self.assertTrue(self.runner_done(), self.log_of())
        argv = self.runs()[0]["argv"]
        self.assertEqual((option(argv, "--max-turns"), option(argv, "--max-price")), ("3", "0.02"))
        self.assertIn("3 attempts", self.log_of(), "an invalid value keeps its default")

    def test_runner_for_an_unknown_suffix(self):
        proc = self.bridge("vibe-run", "0123456789ab", check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.runs(), [])


@unittest.skipIf(WINDOWS, "the fake vibe is a script")
class VibeNewTest(RunnerSetup):

    def new_sessions(self):
        unified = os.path.join(self.save_dir, "unified")
        known = {sid for ids in self.ids.values() for sid in ids}
        found = []
        for d in os.listdir(unified):
            meta = os.path.join(unified, d, "meta.json")
            if d not in known and os.path.exists(meta):
                with open(meta, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("session_id") == d:
                    found.append(data)
        return found

    def test_vibe_new_with_a_name(self):
        other = os.path.join(self.tmp, "other dir")
        os.makedirs(other)
        self.send("vibe:new@helper", "hello new", "claude", "--workspace", other)
        note = self.wait_inbox("claude")
        created = self.new_sessions()
        self.assertEqual(len(created), 1, self.state("vibe-new.log"))
        sid = created[0]["session_id"]
        address = "vibe:" + sid[-12:]
        run = self.runs()[0]
        self.assertEqual(option(run["argv"], "--workdir"), other)
        self.assertNotIn("--resume", run["argv"])
        self.assertEqual(run["cwd"], other)
        self.assertIsNone(run["address"], "the first turn has no address yet")
        notice = note[0]["body"]
        self.assertEqual(note[0]["from"], address)
        self.assertTrue(notice.startswith("[bridge, not an agent]"))
        self.assertIn(f"started the vibe conversation {address}, also reachable as vibe:helper", notice)
        self.assertIn(f"vibe --resume {sid}", notice)
        self.assertEqual(self.peer(address).get("aliases"), ["vibe:helper"])
        # The alias reaches it, now a closed conversation: vibe -p --resume.
        self.send("vibe:helper", "second")
        self.assertTrue(wait_for(lambda: len(self.runs()) == 2, 15) and self.runner_done(address),
                        self.log_of(sid[-12:]))
        self.assertEqual(option(self.runs()[1]["argv"], "--resume"), sid)
        self.assertIn(f'{address}  "helper"', self.listing())

    def test_vibe_new_failure_tells_the_sender(self):
        self.mode("fail")
        self.send("vibe:new", "hello")
        note = self.wait_inbox("claude")
        self.assertIn("failed: vibe in print mode exited 1: boom", note[0]["body"])
        archive = os.path.join(self.bridge_home, "archive", "vibe-new")
        self.assertEqual(len([n for n in os.listdir(archive) if n.endswith(".failed")]), 1)

    def test_vibe_new_needs_the_vibe_command(self):
        self.env["AGENT_BRIDGE_VIBE_REAL"] = ""
        self.env["PATH"] = os.pathsep.join(p for p in self.env["PATH"].split(os.pathsep)
                                           if not os.path.exists(os.path.join(p, "vibe")))
        proc = self.send("vibe:new", "hello", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("vibe:new: the vibe command is not installed or not on PATH", proc.stderr)


@unittest.skipIf(WINDOWS, "the fake vibe is a script")
class NeverLostTest(RunnerSetup):
    """In-flight records of a dead relay, vibe-run or vibe-new (§4.2, §8.9.7)."""

    def dead_pid(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid

    def leave_record(self, key, address, body, session=None, entry_id=None):
        archive = os.path.join(self.bridge_home, "archive", address.replace(":", "-"))
        os.makedirs(archive, exist_ok=True)
        path = os.path.join(archive, f"1-{key}.json")
        msg = {"id": "lost-" + key, "from": "claude", "to": address, "ts": "t", "body": body}
        if key == "new":
            msg["new"] = {"workspace": self.workspace}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(msg, f)
        folder = os.path.join(self.bridge_home, "state", "vibe-inflight")
        os.makedirs(folder, exist_ok=True)
        record = {"pid": self.dead_pid(), "role": "relay", "session": session, "paths": [path]}
        if entry_id:
            record["entry_id"] = entry_id
        with open(os.path.join(folder, key + ".json"), "w", encoding="utf-8") as f:
            json.dump(record, f)
        return path

    def test_list_puts_back_a_dead_record_and_wakes_the_conversation(self):
        self.leave_record(RENAMED, ADDRESS, "lost in a killed vibe")
        listed = self.listing()
        self.assertNotIn("put back", listed, "list printed the runner's log line")
        self.assertTrue(wait_for(lambda: self.runs(), 15), self.log_of())
        self.assertTrue(self.runner_done(), self.log_of())
        self.assertIn("lost in a killed vibe", self.runs()[0]["prompt"])
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "state", "vibe-inflight",
                                                     RENAMED + ".json")))

    def test_an_entry_already_in_the_session_is_not_put_back(self):
        sid = self.session_of("renamed")
        journal = os.path.join(self.save_dir, "unified", sid, "journal")
        os.makedirs(journal)
        with open(os.path.join(journal, "0001.jsonl"), "w", encoding="utf-8") as f:
            f.write(json.dumps({"entryId": "diaulos-abc123", "role": "user"}) + "\n")
        path = self.leave_record(RENAMED, ADDRESS, "taken by vibe before the relay died",
                                 session=sid, entry_id="diaulos-abc123")
        self.listing()
        time.sleep(1.5)
        self.assertEqual(self.runs(), [])
        self.assertEqual(self.inbox(ADDRESS), [])
        self.assertTrue(os.path.exists(path), "the delivered message stays in the archive")
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "state", "vibe-inflight", RENAMED + ".json")))

    def test_an_open_claude_session_brings_back_a_killed_relay_message(self):
        # §4.4, P1: no Diaulos command typed, only the MCP channel of a Claude session.
        self.leave_record(RENAMED, ADDRESS, "lost in a killed vibe, nobody types")
        env = dict(self.env, AGENT_BRIDGE_FORCE_CHANNEL="1", AGENT_BRIDGE_ADDRESS="claude:open")
        channel = subprocess.Popen([sys.executable, os.path.join(os.path.dirname(BRIDGE), "mcp_channel.py")],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   env=env, text=True)
        self.addCleanup(channel.wait, 10)
        self.addCleanup(channel.stdin.close)
        channel.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n")
        channel.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        channel.stdin.flush()
        self.assertTrue(wait_for(lambda: self.runs(), 60), self.log_of())
        self.assertIn("nobody types", self.runs()[0]["prompt"])

    def test_the_channel_wakes_a_conversation_whose_tui_without_relay_closed(self):
        # A message left waiting by a TUI opened with the plain vibe (no relay) is served by vibe-run
        # once that TUI is gone, without any Diaulos command.
        tui = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(tui.kill)
        path = os.path.join(self.bridge_home, "state", "vibe-current", "%d.json" % tui.pid)
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"session": self.session_of("renamed"), "app_pid": tui.pid, "run": False}, f)
        self.assertIn("TUI: no relay, delivers at next message", self.listing())
        self.send(ADDRESS, "waiting behind a TUI without relay")
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import mcp_channel as m; m.vibe_check(0); "
                "m.vibe_check(0)")  # twice: the second call must not start a second vibe-run
        run = lambda: subprocess.run([sys.executable, "-c", code, os.path.dirname(BRIDGE)], capture_output=True,
                                     text=True, env=self.env, timeout=60)
        run()
        time.sleep(2)
        self.assertEqual(self.runs(), [], "vibe-run ran while the TUI was alive")
        tui.kill()
        tui.wait()
        out = run()
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(wait_for(lambda: self.runs(), 15), self.log_of())
        self.assertTrue(self.runner_done(), self.log_of())
        self.assertEqual(len(self.runs()), 1, self.log_of())
        self.assertIn("waiting behind a TUI without relay", self.runs()[0]["prompt"])
        self.assertEqual(self.log_of().count("vibe-run vibe:"), 1, "two vibe-run started")

    def test_the_channel_reads_vibe_peers_every_30_seconds(self):
        code = ("import sys; sys.path.insert(0, sys.argv[1]); import mcp_channel as m; calls = []; "
                "m.bridge.peers = lambda kind=None: calls.append(kind); "
                "print([m.vibe_check(0, 100), m.vibe_check(100, 129), m.vibe_check(100, 130), calls])")
        out = subprocess.run([sys.executable, "-c", code, os.path.dirname(BRIDGE)], capture_output=True,
                             text=True, env=self.env, timeout=30)
        self.assertEqual(out.stdout.strip(), "[100, 100, 130, ['vibe', 'vibe']]", out.stderr)

    def test_a_live_record_is_not_taken_back(self):
        path = self.leave_record(RENAMED, ADDRESS, "being delivered")
        record = os.path.join(self.bridge_home, "state", "vibe-inflight", RENAMED + ".json")
        with open(record, encoding="utf-8") as f:
            data = json.load(f)
        data["pid"] = os.getpid()
        with open(record, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.listing()
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.inbox(ADDRESS), [])

    def test_vibe_new_record_of_a_dead_worker_is_replayed(self):
        self.leave_record("new", "vibe:new", "start me")
        self.listing()
        got = self.wait_inbox("claude")
        self.assertIn("started the vibe conversation", got[0]["body"], self.state("vibe-new.log"))
        self.assertEqual(len(self.runs()), 1)

    def test_vibe_new_bug_puts_the_message_back(self):
        bridge = self.load_bridge()
        os.environ["VIBE_HOME"] = self.vibe_home
        self.addCleanup(os.environ.pop, "VIBE_HOME", None)
        runner = bridge._vibe_runner()
        self.addCleanup(sys.modules.pop, "agent_bridge_vibe_runner", None)
        self.addCleanup(sys.modules.pop, "agent_bridge_vibe_sessions", None)
        self.addCleanup(sys.modules.pop, "agent_bridge_core", None)
        sys.modules["agent_bridge_core"] = bridge

        def broken(*args, **kwargs):
            raise RuntimeError("bug in the runner")
        original, runner.run_p = runner.run_p, broken
        self.addCleanup(setattr, runner, "run_p", original)
        os.makedirs(os.path.join(self.bridge_home, "inbox", "vibe-new"), exist_ok=True)
        msg = {"id": "m1", "from": "claude", "to": "vibe:new", "ts": "t", "body": "hello", "new": {}}
        with open(os.path.join(self.bridge_home, "inbox", "vibe-new", "1-m1.json"), "w") as f:
            json.dump(msg, f)
        with self.assertRaises(RuntimeError):
            runner.vibe_new()
        self.assertEqual([m["id"] for m in bridge.take("vibe:new")], ["m1"])
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "state", "vibe-inflight", "new.json")))


class TextTest(BridgeTest):
    """The text of a delivery and how a prompt is recognized as one, in this process."""

    def runner(self):
        bridge = self.load_bridge()
        runner = bridge._vibe_runner()
        self.addCleanup(sys.modules.pop, "agent_bridge_vibe_runner", None)
        self.addCleanup(sys.modules.pop, "agent_bridge_core", None)
        sys.modules["agent_bridge_core"] = bridge
        return bridge, runner

    def test_incoming_text_without_the_reply_line(self):
        bridge, runner = self.runner()
        original = bridge._incoming_text
        self.addCleanup(setattr, bridge, "_incoming_text", original)
        bridge._incoming_text = lambda msgs, me: "header\nblock"
        msgs = [{"id": "1", "from": "claude", "ts": "t", "body": "b"}]
        self.assertEqual(runner.incoming_text(msgs, ADDRESS),
                         "header\nblock\nIf send answers with an error, do not call it again: say so "
                         f"in your answer.\nReply with the send tool of the diaulos MCP server, to=claude  "
                         f"(you are {ADDRESS})")

    def test_an_ext_sender_without_reader_gets_no_reply_line(self):
        # §4.1, first guard.
        bridge, runner = self.runner()
        bridge._update_peer("ext:live", pid=os.getpid(), workspace=self.tmp, gone=False)
        msg = lambda sender: {"id": "1", "from": sender, "ts": "t", "body": "b"}
        dead = runner.incoming_text([msg("ext:dead")], ADDRESS)
        self.assertTrue(dead.endswith("\next:dead has no reader: do not reply to it with send"), dead)
        self.assertNotIn("Reply with the send tool", dead)
        live = runner.incoming_text([msg("ext:live")], ADDRESS)
        self.assertTrue(live.endswith(f"to=ext:live  (you are {ADDRESS})"), live)
        both = runner.incoming_text([msg("ext:dead"), msg("claude")], ADDRESS)
        self.assertIn("\next:dead has no reader: do not reply to it with send\n", both)
        self.assertTrue(both.endswith(f"to=claude  (you are {ADDRESS})"), both)

    def test_is_delivery(self):
        _, runner = self.runner()
        block = '<session_message from_session_id="diaulos:claude:review" from_title="claude:review"/>'
        self.assertEqual(runner.relay_sender(block), "claude:review")
        self.assertTrue(runner.is_delivery(block))
        self.assertTrue(runner.is_delivery(HEADER + "\nblock"))
        self.assertTrue(runner.is_delivery(runner.CUT_NOTE + "\n" + HEADER + "\nblock"))
        other = '<session_message from_session_id="abc" from_title="another session"/>'
        self.assertIsNone(runner.relay_sender(other))
        self.assertFalse(runner.is_delivery(other))
        self.assertFalse(runner.is_delivery("a message typed by the person"))
        self.assertFalse(runner.is_delivery("text\n" * 100 + HEADER), "the header counts at the start only")
        self.assertFalse(runner.is_delivery(None))


if __name__ == "__main__":
    unittest.main()

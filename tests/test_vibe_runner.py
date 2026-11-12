"""vibe-run and vibe-new (bridge/vibe_runner.py, docs/VIBE-DESIGN.md §4.4 to §4.6, task T3b),
against the fake vibe-acp of tests/fakes/ and the session fixtures, in a throwaway
VIBE_HOME and bridge home.

RealVibeAcpTest talks to the real vibe-acp, with paid model calls: it runs only with
DIAULOS_REAL_VIBE=1, in a throwaway VIBE_HOME made from a copy of ~/.vibe/config.toml
(read only; the API key stays where vibe keeps it), with max_turns 1 or 5.
Not on Windows (the fake is a script found through PATH).
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unittest

from helpers import BRIDGE, FAKES, WINDOWS, BridgeTest, wait_for

sys.path.insert(0, os.path.join(FAKES, "vibe_sessions"))
from build import SUFFIX, build  # noqa: E402

RENAMED = SUFFIX["renamed"]
ADDRESS = "vibe:" + RENAMED


class RunnerSetup(BridgeTest):

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
        self.fake_log = os.path.join(self.tmp, "vibe-acp.jsonl")
        for key in [k for k in self.env if k.startswith("AGENT_BRIDGE_") and k != "AGENT_BRIDGE_HOME"]:
            del self.env[key]
        self.env.update(VIBE_HOME=self.vibe_home, BRIDGE=BRIDGE, FAKE_VIBE_LOG=self.fake_log,
                        PATH=self.fake_bin + os.pathsep + self.env.get("PATH", ""))
        self.env.pop("FAKE_VIBE_MODE", None)

    def mode(self, value):
        self.env["FAKE_VIBE_MODE"] = value

    def set_limits(self, data):
        with open(os.path.join(self.bridge_home, "state", "vibe-limits.json"), "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def events(self, kind=None):
        try:
            with open(self.fake_log, encoding="utf-8") as f:
                found = [json.loads(line) for line in f]
        except OSError:
            return []
        return [e for e in found if kind is None or e.get("event") == kind]

    def recv(self, method):
        return [e for e in self.events("recv") if e.get("method") == method]

    def runner_done(self, address=ADDRESS, timeout=30):
        """Waits until no vibe-run serves `address`; True if it ended in time."""
        def done():
            pid = self.peer(address).get("runner_pid")
            return not pid or not _alive(pid)
        time.sleep(0.3)
        return wait_for(done, timeout)

    def log_of(self, suffix=RENAMED):
        return self.state(f"vibe-{suffix}.log")

    def write_peer(self, suffix, **fields):
        os.makedirs(os.path.join(self.bridge_home, "peers"), exist_ok=True)
        with open(os.path.join(self.bridge_home, "peers", f"vibe-{suffix}.json"), "w", encoding="utf-8") as f:
            json.dump(dict(address=f"vibe:{suffix}", **fields), f)

    def meta(self, folder):
        with open(os.path.join(folder, "meta.json"), encoding="utf-8") as f:
            return json.load(f)

    def lines(self, folder):
        with open(os.path.join(folder, "messages.jsonl"), encoding="utf-8") as f:
            return sum(1 for _ in f)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie child of this test process counts as dead
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            return f.read().split(") ", 1)[1][0] != "Z"
    except OSError:
        return True


@unittest.skipIf(WINDOWS, "the fake vibe-acp is a script run through PATH")
class VibeRunTest(RunnerSetup):

    def test_a_burst_goes_in_one_prompt_in_the_same_session(self):
        folder = self.folders["renamed"][0]
        before = self.lines(folder)
        for n in range(3):
            self.send(ADDRESS if n else "vibe:review_bot", f"message {n}")
        self.assertTrue(self.runner_done(), self.log_of())
        prompts = self.events("prompt")
        self.assertEqual(len(prompts), 1, self.log_of())
        self.assertEqual(prompts[0]["messages"], 3)
        load = self.recv("session/load")
        self.assertEqual([e["params"]["sessionId"] for e in load], [self.ids["renamed"][0]])
        self.assertEqual(load[0]["params"]["cwd"], self.workspace)
        self.assertEqual(self.meta(folder)["session_id"], self.ids["renamed"][0], "the id changed")
        self.assertEqual(self.lines(folder), before + 2, "user and reply not in the same folder")
        self.assertEqual(len(os.listdir(self.save_dir)), 10, "a new session folder was made")
        pong = self.wait_inbox("claude")
        self.assertEqual(pong[0]["from"], ADDRESS)
        self.assertIn("delivered", self.log_of())
        self.assertIn("session_cost $0.0100", self.log_of())
        self.assertEqual(self.inbox(ADDRESS), [])

    def test_prompt_text_for_vibe(self):
        self.send(ADDRESS, "see @README.md please")
        self.assertTrue(self.runner_done(), self.log_of())
        text = self.recv("session/prompt")[0]["params"]["prompt"][0]["text"]
        self.assertIn("see @⁠README.md please", text)
        self.assertNotIn("@R", text)
        self.assertTrue(text.endswith(f"Reply with the bridge_send tool, to=claude  (you are {ADDRESS})"), text)
        self.assertIn('<bridge-message id="', text)

    def test_permission_requests_get_reject_once(self):
        self.mode("permission")
        self.send(ADDRESS, "hello")
        self.assertTrue(self.runner_done(), self.log_of())
        answers = self.events("permission-answer")
        self.assertEqual([a["option"] for a in answers], ["reject_once"])
        self.assertIn("answered reject_once", self.log_of())

    def test_nothing_runs_while_a_tui_serves_the_conversation(self):
        self.write_peer(RENAMED, tui_pid=os.getpid(), tui_seen=time.time())
        self.send(ADDRESS, "hello")
        self.assertNotIn("runner_pid", self.peer(ADDRESS))
        proc = self.bridge("vibe-run", RENAMED)  # started by hand: it must still refuse
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(self.events("start"), [], "vibe-acp started while a TUI serves the conversation")
        self.assertEqual(len(self.inbox(ADDRESS)), 1)

    def test_a_tui_that_opens_between_two_turns_takes_over(self):
        self.mode("slow=2")
        self.send(ADDRESS, "first")
        self.assertTrue(wait_for(lambda: self.events("prompt"), 15))
        self.send(ADDRESS, "second")  # waits: the runner is busy with the first turn
        peer = self.peer(ADDRESS)
        self.write_peer(RENAMED, tui_pid=os.getpid(), tui_seen=time.time(), runner_pid=peer["runner_pid"])
        self.assertTrue(self.runner_done(), self.log_of())
        self.assertEqual(len(self.events("prompt")), 1)
        self.assertIn("a vibe TUI opened", self.log_of())
        self.assertEqual([m["body"] for m in self.inbox(ADDRESS)], ["second"])

    def test_a_dying_vibe_acp_is_retried_then_failed_with_a_note(self):
        self.mode("die-in-prompt")
        self.set_limits({"retry_seconds": 0.2})
        self.send(ADDRESS, "hello")
        self.assertTrue(self.runner_done(timeout=60), self.log_of())
        self.assertEqual(len(self.events("prompt")), 3, "3 attempts per batch")
        self.assertEqual(len(re.findall(r"not delivered", self.log_of())), 2)
        self.assertIn("dropped", self.log_of())
        archive = os.path.join(self.bridge_home, "archive", f"vibe-{RENAMED}")
        self.assertEqual(len([n for n in os.listdir(archive) if n.endswith(".failed")]), 1)
        note = self.wait_inbox("claude")
        self.assertTrue(note[0]["body"].startswith("[bridge, not an agent]"), note)
        self.assertIn("not delivered after 3 attempts", note[0]["body"])
        self.assertEqual(note[0]["from"], ADDRESS)
        self.assertEqual(self.inbox(ADDRESS), [])
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "state", "vibe-inflight", RENAMED + ".json")))

    def test_an_error_answer_is_retried(self):
        self.mode("error-prompt")
        self.set_limits({"retry_seconds": 0, "attempts": 2})
        self.send(ADDRESS, "hello")
        self.assertTrue(self.runner_done(timeout=60), self.log_of())
        self.assertEqual(len(self.events("prompt")), 2)
        self.assertIn("error -32603", self.log_of())

    def test_turn_limit_counts_as_delivered(self):
        self.mode("turn-limit")
        self.send(ADDRESS, "hello")
        self.assertTrue(self.runner_done(), self.log_of())
        self.assertEqual(len(self.events("prompt")), 1)
        self.assertIn("(turn limit reached)", self.log_of())
        self.assertEqual(self.inbox(ADDRESS), [])

    def test_limits_file_and_environment_of_vibe_acp(self):
        self.send(ADDRESS, "defaults")
        self.assertTrue(self.runner_done(), self.log_of())
        start = self.events("start")[0]
        self.assertEqual(start["env"]["AGENT_BRIDGE_VIBE_MAX_PRICE"], "0.5")
        self.assertEqual(start["env"]["AGENT_BRIDGE_VIBE_SUFFIX"], RENAMED)
        self.assertEqual(start["env"]["AGENT_BRIDGE_ADDRESS"], ADDRESS)
        self.assertEqual(start["env"]["AGENT_BRIDGE_HOME"], self.bridge_home)
        self.assertEqual(start["cwd"], self.workspace)
        self.assertEqual(self.recv("session/set_config_option")[0]["params"],
                         {"sessionId": self.ids["renamed"][0], "configId": "max_turns", "value": "20"})
        cases = [
            ({"max_turns": 5, "max_price": 0.2}, "5", "0.2"),
            ("not json", "20", "0.5"),
            ({"max_turns": 0, "max_price": "a lot"}, "20", "0.5"),
            ({"max_turns": True, "max_price": 1000}, "20", "0.5"),
            (["max_turns", 3], "20", "0.5"),
        ]
        for data, turns, price in cases:
            with self.subTest(data=data):
                os.remove(self.fake_log)
                self.set_limits(data)
                self.send(ADDRESS, "again")
                self.assertTrue(self.runner_done(), self.log_of())
                self.assertEqual(self.events("start")[0]["env"]["AGENT_BRIDGE_VIBE_MAX_PRICE"], price)
                self.assertEqual(self.recv("session/set_config_option")[0]["params"]["value"], turns)

    def test_messages_of_a_killed_runner_come_back(self):
        archive = os.path.join(self.bridge_home, "archive", f"vibe-{RENAMED}")
        os.makedirs(archive)
        path = os.path.join(archive, "1-lost.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"id": "lost", "from": "claude", "to": ADDRESS, "ts": "t", "body": "lost one"}, f)
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        os.makedirs(os.path.join(self.bridge_home, "state", "vibe-inflight"))
        with open(os.path.join(self.bridge_home, "state", "vibe-inflight", RENAMED + ".json"), "w") as f:
            json.dump({"pid": dead.pid, "role": "runner", "session": self.ids["renamed"][0], "paths": [path]}, f)
        self.send(ADDRESS, "new one")
        self.assertTrue(self.runner_done(), self.log_of())
        # Put back either by `send` (peers("vibe") recovers dead records, fix T3b-bis) or by vibe-run.
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "state", "vibe-inflight", RENAMED + ".json")))
        prompts = self.events("prompt")
        self.assertEqual(sum(p["messages"] for p in prompts), 2, prompts)
        text = self.recv("session/prompt")[0]["params"]["prompt"][0]["text"]
        self.assertIn("lost one", text)

    def test_a_live_runner_record_is_left_alone(self):
        self.write_peer(RENAMED, runner_pid=os.getpid())  # another runner works
        self.send(ADDRESS, "hello")
        self.bridge("vibe-run", RENAMED)
        self.assertEqual(self.events("start"), [])
        self.assertEqual(len(self.inbox(ADDRESS)), 1)

    def test_runner_for_an_unknown_suffix(self):
        proc = self.bridge("vibe-run", "not-a-suffix", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("invalid vibe conversation", proc.stderr)


@unittest.skipIf(WINDOWS, "the fake vibe-acp is a script run through PATH")
class VibeNewTest(RunnerSetup):

    def new_conversations(self):
        return [m for m in (self.meta(os.path.join(self.save_dir, d)) for d in os.listdir(self.save_dir))
                if not any(m["session_id"] in ids for ids in self.ids.values())]

    def test_vibe_new_with_a_name(self):
        other = os.path.join(self.tmp, "other dir")
        os.makedirs(other)
        self.send("vibe:new@helper", "hello new", "claude", "--workspace", other)
        note = self.wait_inbox("claude", count=2)
        created = self.new_conversations()
        self.assertEqual(len(created), 1, self.state("vibe-new.log"))
        sid = created[0]["session_id"]
        address = "vibe:" + sid[-12:]
        self.assertEqual(created[0]["environment"]["working_directory"], other)
        bodies = {m["from"]: m["body"] for m in note}
        self.assertIn(address, bodies)
        notice = bodies[address]
        self.assertTrue(notice.startswith("[bridge, not an agent]"))
        self.assertIn(f"started the vibe conversation {address}, also reachable as vibe:helper", notice)
        self.assertIn(f"vibe --resume {sid}", notice)
        self.assertEqual(self.peer(address).get("aliases"), ["vibe:helper"])
        self.assertEqual(self.recv("session/new")[0]["params"]["cwd"], other)
        # The alias and the address both reach it, now a closed conversation.
        os.remove(self.fake_log)
        self.send("vibe:helper", "second")
        self.assertTrue(self.runner_done(address), self.log_of(sid[-12:]))
        self.assertEqual([e["params"]["sessionId"] for e in self.recv("session/load")], [sid])
        self.assertIn(f'{address}  "helper"', self.listing())

    def test_vibe_new_failure_tells_the_sender(self):
        self.mode("error-prompt")
        self.send("vibe:new", "hello")
        note = self.wait_inbox("claude")
        self.assertTrue(note and "failed" in note[0]["body"], note)
        self.assertIn("was created, but its first turn failed", note[0]["body"])
        inbox = os.path.join(self.bridge_home, "archive", "vibe-new")
        self.assertEqual(len([n for n in os.listdir(inbox) if n.endswith(".failed")]), 1)


@unittest.skipIf(WINDOWS, "the fake vibe-acp is a script run through PATH")
class NeverLostTest(RunnerSetup):
    """Fix T3b-bis (review 07): in-flight records found by `list`, vibe-new records, text guard."""

    def dead_pid(self):
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid

    def leave_record(self, key, address, body):
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
        with open(os.path.join(folder, key + ".json"), "w", encoding="utf-8") as f:
            json.dump({"pid": self.dead_pid(), "role": "tui", "session": None, "paths": [path]}, f)
        return path

    def test_list_puts_back_a_dead_record_and_wakes_the_conversation(self):
        self.leave_record(RENAMED, ADDRESS, "lost in a killed vibe")
        listed = self.listing()
        self.assertNotIn("put back", listed, "list printed the runner's log line")
        self.assertTrue(self.runner_done(), self.log_of())
        text = wait_for(lambda: self.recv("session/prompt"), 15)
        self.assertTrue(text, self.log_of())
        self.assertIn("lost in a killed vibe", text[0]["params"]["prompt"][0]["text"])
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "state", "vibe-inflight",
                                                     RENAMED + ".json")))

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
        got = self.wait_inbox("claude", count=2)  # the fake's pong and the bridge's note
        self.assertTrue(any("started the vibe conversation" in m["body"] for m in got), self.state("vibe-new.log"))
        self.assertEqual(len(self.recv("session/new")), 1)

    def test_vibe_new_bug_puts_the_message_back(self):
        bridge = self.load_bridge()
        os.environ["VIBE_HOME"] = self.vibe_home
        self.addCleanup(os.environ.pop, "VIBE_HOME", None)
        runner = bridge._vibe_runner()
        self.addCleanup(sys.modules.pop, "agent_bridge_vibe_runner", None)
        self.addCleanup(sys.modules.pop, "agent_bridge_vibe_sessions", None)
        self.addCleanup(sys.modules.pop, "agent_bridge_core", None)
        sys.modules["agent_bridge_core"] = bridge

        class Broken:
            def __init__(self, *args):
                raise RuntimeError("bug in the runner")
        original, runner.Acp = runner.Acp, Broken
        self.addCleanup(setattr, runner, "Acp", original)
        os.makedirs(os.path.join(self.bridge_home, "inbox", "vibe-new"), exist_ok=True)
        msg = {"id": "m1", "from": "claude", "to": "vibe:new", "ts": "t", "body": "hello", "new": {}}
        with open(os.path.join(self.bridge_home, "inbox", "vibe-new", "1-m1.json"), "w") as f:
            json.dump(msg, f)
        with self.assertRaises(RuntimeError):
            runner.vibe_new()
        self.assertEqual([m["id"] for m in bridge.take("vibe:new")], ["m1"])
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "state", "vibe-inflight", "new.json")))

    def test_incoming_text_without_the_reply_line(self):
        bridge = self.load_bridge()
        runner = bridge._vibe_runner()
        self.addCleanup(sys.modules.pop, "agent_bridge_vibe_runner", None)
        self.addCleanup(sys.modules.pop, "agent_bridge_core", None)
        sys.modules["agent_bridge_core"] = bridge
        original = bridge._incoming_text
        self.addCleanup(setattr, bridge, "_incoming_text", original)
        bridge._incoming_text = lambda msgs, me: "header\nblock"
        msgs = [{"id": "1", "from": "claude", "ts": "t", "body": "b"}]
        self.assertEqual(runner.incoming_text(msgs, ADDRESS),
                         "header\nblock\nIf bridge_send answers with an error, do not call it again: say so "
                         f"in your answer.\nReply with the bridge_send tool, to=claude  (you are {ADDRESS})")

    def test_an_ext_sender_without_reader_gets_no_reply_line(self):
        # VIBE-DESIGN §4.1, first guard.
        bridge = self.load_bridge()
        runner = bridge._vibe_runner()
        self.addCleanup(sys.modules.pop, "agent_bridge_vibe_runner", None)
        self.addCleanup(sys.modules.pop, "agent_bridge_core", None)
        sys.modules["agent_bridge_core"] = bridge
        bridge._update_peer("ext:live", pid=os.getpid(), workspace=self.tmp, gone=False)
        msg = lambda sender: {"id": "1", "from": sender, "ts": "t", "body": "b"}
        dead = runner.incoming_text([msg("ext:dead")], ADDRESS)
        self.assertTrue(dead.endswith("\next:dead has no reader: do not reply with bridge_send"), dead)
        self.assertNotIn("Reply with the bridge_send tool", dead)
        live = runner.incoming_text([msg("ext:live")], ADDRESS)
        self.assertTrue(live.endswith(f"Reply with the bridge_send tool, to=ext:live  (you are {ADDRESS})"), live)
        both = runner.incoming_text([msg("ext:dead"), msg("claude")], ADDRESS)
        self.assertIn("\next:dead has no reader: do not reply with bridge_send\n", both)
        self.assertTrue(both.endswith(f"Reply with the bridge_send tool, to=claude  (you are {ADDRESS})"), both)


@unittest.skipUnless(os.environ.get("DIAULOS_REAL_VIBE") == "1" and shutil.which("vibe-acp"),
                     "paid calls to the real vibe-acp: set DIAULOS_REAL_VIBE=1")
class RealVibeAcpTest(BridgeTest):
    """The real vibe-acp, throwaway VIBE_HOME. Each test prints the cost of its run."""

    def setUp(self):
        super().setUp()
        self.vibe_home = os.path.join(self.tmp, "vibe_home")
        self.work = os.path.join(self.tmp, "work")
        os.makedirs(self.vibe_home)
        os.makedirs(self.work)
        with open(os.path.expanduser("~/.vibe/config.toml"), encoding="utf-8") as f:
            config = f.read()
        save_dir = os.path.join(self.vibe_home, "logs", "session")
        config, n = re.subn(r'(?m)^save_dir = ".*"$', 'save_dir = "%s"' % save_dir, config)
        if n != 1:
            config += '\n[session_logging]\nsave_dir = "%s"\n' % save_dir
        with open(os.path.join(self.vibe_home, "config.toml"), "w", encoding="utf-8") as f:
            f.write(config)
        self.save_dir = save_dir
        real_home = os.path.expanduser("~")
        # vibe-acp needs the real HOME for its keyring; the bridge home stays the test's.
        self.env.update(VIBE_HOME=self.vibe_home, HOME=real_home, USERPROFILE=real_home)
        for key in [k for k in self.env if k.startswith("AGENT_BRIDGE_") and k != "AGENT_BRIDGE_HOME"]:
            del self.env[key]

    def sessions(self):
        found = []
        for d in sorted(os.listdir(self.save_dir)):
            with open(os.path.join(self.save_dir, d, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
            with open(os.path.join(self.save_dir, d, "messages.jsonl"), encoding="utf-8") as f:
                lines = sum(1 for _ in f)
            found.append((d, meta["session_id"], lines, (meta.get("stats") or {}).get("session_cost")))
        return found

    def test_new_then_closed_run_keeps_id_and_folder(self):
        with open(os.path.join(self.bridge_home, "state", "vibe-limits.json"), "w") as f:
            json.dump({"max_turns": 5}, f)
        self.send("vibe:new@realprobe", "Answer with the single word OK1. Do not call any tool.",
                  "ext:t", "--workspace", self.work, check=True)
        self.assertTrue(wait_for(lambda: os.path.isdir(self.save_dir) and self.sessions()
                                 and "started" in self.state("vibe-new.log"), 180), self.state("vibe-new.log"))
        before = self.sessions()
        print("\nafter vibe:new:", before, "\n", self.state("vibe-new.log"))
        self.assertEqual(len(before), 1)
        folder, sid, lines, _ = before[0]
        self.send("vibe:realprobe", "Answer with the single word OK2. Do not call any tool.", "ext:t")
        suffix = sid[-12:]
        self.assertTrue(wait_for(lambda: "delivered" in self.state(f"vibe-{suffix}.log"), 180),
                        self.state(f"vibe-{suffix}.log"))
        time.sleep(1)
        after = self.sessions()
        print("after vibe-run:", after, "\n", self.state(f"vibe-{suffix}.log"))
        self.assertEqual([(d, s) for d, s, _, _ in after], [(folder, sid)], "id or folder changed")
        self.assertGreater(after[0][2], lines)

    def test_max_turns_1_stops_at_the_limit(self):
        with open(os.path.join(self.bridge_home, "state", "vibe-limits.json"), "w") as f:
            json.dump({"max_turns": 1}, f)
        self.send("vibe:new", "Run the bash command `echo one`, then the bash command `echo two`, then the bash "
                  "command `echo three`, one call each, then answer DONE.", "ext:t", "--workspace", self.work)
        self.assertTrue(wait_for(lambda: "vibe:new for" in self.state("vibe-new.log")
                                 or "started" in self.state("vibe-new.log"), 180), self.state("vibe-new.log"))
        print("\n", self.state("vibe-new.log"), self.sessions())
        self.assertIn("(turn limit reached)", self.state("vibe-new.log"))


if __name__ == "__main__":
    unittest.main()

"""The relay in front of vibe-app-server (bridge/vibe_relay.py, docs/VIBE-DESIGN.md §8.9), against the
fake app-server of tests/fakes/fake_app_server.py: the test plays vibe-rs on the relay's stdin and
stdout. No vibe, no model call. Not on Windows (the relay checks its parent with getppid there too,
but the fake is untested there).
"""
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import uuid

from helpers import FAKES, ROOT, WINDOWS, BridgeTest, wait_for

RELAY = os.path.join(ROOT, "bridge", "vibe_relay.py")
FAKE = os.path.join(FAKES, "fake_app_server.py")
SESSION = "11111111-2222-3333-4444-555555555555"
SUFFIX = "555555555555"
ADDRESS = "vibe:" + SUFFIX
KEPT = {"dir", "at", "method", "id", "bytes", "pid", "argc", "rc", "reason", "session", "msgs", "wake"}


@unittest.skipIf(WINDOWS, "the relay is tried on Linux only (§8.9.9)")
class RelayTest(BridgeTest):

    def start(self, **fake_env):
        self.server_log = os.path.join(self.tmp, "server.log")
        env = dict(self.env, FAKE_SERVER_LOG=self.server_log, FAKE_SESSION=SESSION, **fake_env)
        self.relay = subprocess.Popen([sys.executable, RELAY, "--home", self.bridge_home, "--real", sys.executable, FAKE],
                                      stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)
        self.lines = []
        threading.Thread(target=self._read, daemon=True).start()
        self.addCleanup(self._stop)

    def _read(self):
        for line in self.relay.stdout:
            self.lines.append(json.loads(line))

    def _stop(self):
        if self.relay.poll() is None:
            self.relay.kill()
            self.relay.wait()

    def tui(self, msg):
        self.relay.stdin.write((json.dumps(msg) + "\n").encode())
        self.relay.stdin.flush()

    def queue_message(self, body, sender="claude:review"):
        inbox = os.path.join(self.bridge_home, "inbox", "vibe-" + SUFFIX)
        os.makedirs(inbox, exist_ok=True)
        msg = {"id": uuid.uuid4().hex[:12], "from": sender, "to": ADDRESS, "ts": "2026-11-13T10:00:00", "body": body}
        with open(os.path.join(inbox, "%d-%s.json" % (time.time_ns(), msg["id"])), "w", encoding="utf-8") as f:
            json.dump(msg, f)
        return msg

    def inbox(self):
        folder = os.path.join(self.bridge_home, "inbox", "vibe-" + SUFFIX)
        return [n for n in os.listdir(folder) if n.endswith(".json")] if os.path.isdir(folder) else []

    def server_lines(self):
        try:
            with open(self.server_log, encoding="utf-8") as f:
                return [json.loads(l) for l in f if l.strip()]
        except OSError:
            return []

    def state(self):
        path = os.path.join(self.bridge_home, "state", "vibe-relay", "%d.json" % self.relay.pid)
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def log_rows(self):
        path = os.path.join(self.bridge_home, "state", "vibe-relay.log")
        try:
            with open(path, encoding="utf-8") as f:
                return [json.loads(l) for l in f]
        except OSError:
            return []

    def enqueues_of_relay(self):
        return [m for m in self.server_lines()
                if m.get("method") == "session/turn/enqueue" and str(m.get("id")).startswith("diaulos-relay-")]

    # ------------------------------------------------------------------ tests

    def test_forwards_both_ways_and_learns_the_session(self):
        self.start()
        self.tui({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.tui({"jsonrpc": "2.0", "id": 2, "method": "session/start", "params": {"cwd": "/x"}})
        self.assertTrue(wait_for(lambda: len(self.lines) >= 2, 5))
        self.assertEqual([m["id"] for m in self.lines[:2]], [1, 2])
        self.assertEqual(self.lines[1]["result"]["state"]["session"]["id"], SESSION)
        state = wait_for(lambda: (self.state() or {}).get("session") == SESSION and self.state(), 5)
        self.assertTrue(state)
        self.assertEqual(state["tui_pid"], os.getpid())
        self.assertEqual(state["wake"], "relay")
        self.assertEqual([m.get("id") for m in self.server_lines()], [1, 2])

    def test_delivers_with_sender_and_keeps_its_answer(self):
        self.start()
        self.tui({"jsonrpc": "2.0", "id": 1, "method": "session/start", "params": {}})
        msg = self.queue_message("Can you check the tests?")
        sent = wait_for(lambda: self.enqueues_of_relay(), 5)
        self.assertTrue(sent)
        params = sent[0]["params"]
        self.assertEqual(params["sessionId"], SESSION)
        self.assertEqual(params["delivery"], "start_turn")
        self.assertEqual(params["sender"], {"sessionId": "diaulos:claude:review", "title": "claude:review"})
        entry = params["entries"][0]
        self.assertEqual(entry["role"], "user")
        self.assertTrue(entry["entryId"].startswith("diaulos-"))
        self.assertIn("Can you check the tests?", entry["content"][0]["text"])
        self.assertIn(msg["id"], entry["content"][0]["text"])
        # the answer to the relay's request never reaches the TUI; the notifications do
        self.assertTrue(wait_for(lambda: any(m.get("method") == "turn/completed" for m in self.lines), 5))
        self.assertFalse([m for m in self.lines if str(m.get("id", "")).startswith("diaulos-relay-")])
        self.assertEqual(self.inbox(), [])
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "state", "vibe-inflight", SUFFIX + ".json")))
        self.assertTrue(wait_for(lambda: any(r.get("dir") == "delivered" for r in self.log_rows()), 5))

    def test_error_puts_back_and_three_turn_waking_off(self):
        self.start(FAKE_ENQUEUE_ERROR="1")
        self.tui({"jsonrpc": "2.0", "id": 1, "method": "session/start", "params": {}})
        self.queue_message("hello")
        state = wait_for(lambda: (self.state() or {}).get("wake") == "off" and self.state(), 10)
        self.assertTrue(state)
        self.assertEqual(len(self.enqueues_of_relay()), 3)
        self.assertEqual(len(self.inbox()), 1)
        time.sleep(1.5)
        self.assertEqual(len(self.enqueues_of_relay()), 3)  # nothing more once off

    def test_ends_with_the_server_when_the_tui_closes(self):
        self.start(FAKE_EXIT_CODE="7")
        self.tui({"jsonrpc": "2.0", "id": 1, "method": "session/start", "params": {}})
        self.assertTrue(wait_for(lambda: self.state(), 5))
        self.relay.stdin.close()
        self.assertEqual(self.relay.wait(10), 7)
        self.assertIsNone(self.state())

    def test_ignores_sigint(self):
        import signal
        self.start()
        self.assertTrue(wait_for(lambda: self.state(), 5))
        self.relay.send_signal(signal.SIGINT)
        time.sleep(0.5)
        self.assertIsNone(self.relay.poll())

    def test_log_keeps_only_whitelisted_keys(self):
        self.start()
        self.tui({"jsonrpc": "2.0", "id": 1, "method": "session/start", "params": {"cwd": "/secret/path"}})
        self.queue_message("secret text of the message")
        self.assertTrue(wait_for(lambda: any(r.get("dir") == "delivered" for r in self.log_rows()), 5))
        self.relay.stdin.close()
        self.relay.wait(10)
        rows = self.log_rows()
        self.assertTrue(rows)
        for row in rows:
            self.assertLessEqual(set(row), KEPT)
        text = open(os.path.join(self.bridge_home, "state", "vibe-relay.log"), encoding="utf-8").read()
        self.assertNotIn("secret", text)
        self.assertNotIn(self.tmp, text)


if __name__ == "__main__":
    unittest.main()

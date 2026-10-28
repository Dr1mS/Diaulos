"""Mailbox basics, on every OS: send, recv, watch, addresses, claims, ext:<name> peers."""
import json
import os
import subprocess
import threading

from helpers import WINDOWS, BridgeTest, wait_for


class MailboxTest(BridgeTest):

    def test_send_and_recv_json(self):
        self.send("claude", "hello\nsecond line", "ext:tester")
        messages = self.inbox("claude")
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["from"], "ext:tester")
        self.assertEqual(messages[0]["body"], "hello\nsecond line")
        self.assertEqual(self.inbox("claude"), [], "a message is read once")

    def test_message_from_stdin(self):
        self.bridge("send", "claude", "-", "--from", "ext:tester", stdin="from stdin ✓")
        self.assertEqual(self.inbox("claude")[0]["body"], "from stdin ✓")

    def test_unknown_claude_session_is_refused(self):
        proc = self.send("claude:nobody", "hi", check=False)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("matches 0 live claude peers", proc.stderr)

    def test_invalid_addresses_are_refused(self):
        for address in ("agy:../escape", "claude:a/b", "nobody:x", "ext:a\\b"):
            with self.subTest(address=address):
                self.assertNotEqual(self.send(address, "hi", check=False).returncode, 0)

    def test_watch_registers_an_ext_peer_that_receives(self):
        watcher = self.popen("watch", "ext:bot", "--json", stdout=subprocess.PIPE)
        self.assertTrue(wait_for(lambda: "ext:bot" in self.listing(), 10), "ext:bot not listed")
        self.send("ext:bot", "ping", "claude")
        line = []
        reader = threading.Thread(target=lambda: line.append(watcher.stdout.readline()), daemon=True)
        reader.start()
        reader.join(15)
        self.assertTrue(line, "watch printed nothing")
        self.assertEqual(json.loads(line[0])["body"], "ping")
        # A second reader of the same address is refused.
        second = self.bridge("watch", "ext:bot", check=False)
        self.assertNotEqual(second.returncode, 0)
        if not WINDOWS:  # SIGTERM runs the watcher's cleanup only on POSIX
            watcher.terminate()
            watcher.wait(10)
            self.assertTrue(wait_for(lambda: "ext:bot" not in self.listing(), 10), "ext:bot still listed")
            self.assertNotEqual(self.send("ext:bot", "late", check=False).returncode, 0)

    def test_an_agent_cannot_message_itself(self):
        self.popen("watch", "ext:self")
        self.assertTrue(wait_for(lambda: "ext:self" in self.listing(), 10))
        proc = self.send("ext:self", "me", "ext:self", check=False)
        self.assertNotEqual(proc.returncode, 0)

    def test_concurrent_claims_never_share_a_message(self):
        bridge = self.load_bridge()
        for i in range(40):
            bridge.send("claude", f"m{i}", "ext:tester")
        got, start = [[], []], threading.Event()

        def reader(out):
            start.wait()
            for _ in range(20):
                out.extend(m["id"] for _, m in bridge.claim("claude", limit=1))

        threads = [threading.Thread(target=reader, args=(out,)) for out in got]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join()
        everything = got[0] + got[1] + [m["id"] for _, m in bridge.claim("claude")]
        self.assertEqual(len(everything), 40)
        self.assertEqual(len(set(everything)), 40, "a message was claimed twice")

    def test_unclaim_puts_messages_back(self):
        bridge = self.load_bridge()
        bridge.send("claude", "retry me", "ext:tester")
        claimed = bridge.claim("claude")
        self.assertEqual(len(claimed), 1)
        bridge.unclaim("claude", claimed)
        self.assertEqual([m["body"] for m in bridge.take("claude")], ["retry me"])

    def test_log_records_every_message(self):
        self.send("claude", "logged", "ext:tester")
        with open(os.path.join(self.bridge_home, "log.jsonl"), encoding="utf-8") as f:
            self.assertEqual(json.loads(f.readlines()[-1])["body"], "logged")


class BareAgyAddressTest(BridgeTest):
    """`agy` alone and `agy@<folder>` aim at a session in use, not at every listed conversation."""

    def setUp(self):
        super().setUp()
        self.b = self.load_bridge()
        dead = 2 ** 22 + 12345  # above the default pid_max: no such process
        self.b._update_peer("agy:11111111-1111-1111-1111-111111111111", conversation="11111111-1111-1111-1111-111111111111",
                            cli=True, agy_pid=os.getpid(), workspace="/w/api")  # an open agy CLI
        self.b._update_peer("agy:22222222-2222-2222-2222-222222222222", conversation="22222222-2222-2222-2222-222222222222",
                            cli=True, agy_pid=dead, workspace="/w/api")  # exited: wakes with agy -p
        self.b._update_peer("agy:33333333-3333-3333-3333-333333333333", conversation="33333333-3333-3333-3333-333333333333",
                            cli=True, workspace="/w/web")  # from the index only

    def test_bare_agy_picks_the_only_active_session(self):
        self.assertEqual(len(self.b.peers("agy")), 3)
        self.assertEqual(self.b.resolve("agy", "claude"), "agy:11111111-1111-1111-1111-111111111111")

    def test_folder_prefers_the_active_match(self):
        self.assertEqual(self.b.resolve("agy@api", "claude"), "agy:11111111-1111-1111-1111-111111111111")
        self.assertEqual(self.b.resolve("agy@web", "claude"), "agy:33333333-3333-3333-3333-333333333333")

    def test_bare_agy_without_active_session_is_refused(self):
        with self.assertRaises(SystemExit):
            self.b.resolve("agy", "agy:11111111-1111-1111-1111-111111111111")

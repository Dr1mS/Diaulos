"""Remote messaging: frames, pairing, and two bridges talking through a local relay.

Each test runs its own relay (remote.Relay) on 127.0.0.1: nothing goes to ntfy.sh.
The second machine is a second AGENT_BRIDGE_HOME. Skipped without `cryptography`.
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import unittest

from helpers import BRIDGE, BridgeTest, _kill_processes_of, wait_for

HAVE_CRYPTO = importlib.util.find_spec("cryptography") is not None


@unittest.skipUnless(HAVE_CRYPTO, "needs the cryptography package")
class RemoteTest(BridgeTest):

    def setUp(self):
        super().setUp()
        self.bridge_mod = self.load_bridge()
        import remote
        self.remote = remote
        self.relay = remote.Relay()
        self.server = self.relay.serve("127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    # ---- helpers

    def channels(self, root=b"k" * 32):
        mk = lambda role: self.remote.Channel(self.remote.record(role, self.url, root))  # noqa: E731
        return mk("inviter"), mk("joiner")

    def run_pairing(self, confirm_a=True, confirm_b=True, secret_b=None, seconds=20):
        _, secret = self.remote.make_invite(self.url)
        results, deadline = {}, time.time() + seconds

        def side(role, s, answer):
            try:
                results[role] = self.remote.pair(role, self.url, s, deadline, lambda code: results.setdefault(
                    role + "-code", code) and answer)
            except SystemExit as e:
                results[role] = e

        threads = [threading.Thread(target=side, args=("inviter", secret, confirm_a)),
                   threading.Thread(target=side, args=("joiner", secret_b or secret, confirm_b))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(40)
        return results

    def second_home(self):
        env = dict(self.env, AGENT_BRIDGE_HOME=os.path.join(self.tmp, "bridge-b"))
        os.makedirs(os.path.join(env["AGENT_BRIDGE_HOME"], "state"))
        self.addCleanup(_kill_processes_of, env["AGENT_BRIDGE_HOME"])
        return env

    def write_pair(self, env, name, role, root, allow):
        folder = os.path.join(env["AGENT_BRIDGE_HOME"], "remote")
        os.makedirs(os.path.join(folder, "peers"), exist_ok=True)
        os.makedirs(os.path.join(folder, "state"), exist_ok=True)
        with open(os.path.join(folder, "peers", name + ".json"), "w", encoding="utf-8") as f:
            json.dump(self.remote.record(role, self.url, root, allow), f)
        with open(os.path.join(folder, "state", name + ".json"), "w", encoding="utf-8") as f:
            json.dump({"since": int(time.time()) - 60}, f)

    def run_in(self, env, *args, stdin="", check=True):
        return self.bridge(*args, stdin=stdin, env={"AGENT_BRIDGE_HOME": env["AGENT_BRIDGE_HOME"]}, check=check)

    def two_machines(self, allow_b=(), allow_a=()):
        """Home A (self.env) paired as `bob` with home B, paired there as `alice`; gateways running."""
        root = os.urandom(32)
        a, b = self.env, self.second_home()
        self.write_pair(a, "bob", "inviter", root, allow_a)
        self.write_pair(b, "alice", "joiner", root, allow_b)
        for env in (a, b):
            self.run_in(env, "remote", "start")
        return a, b

    def watch_in(self, env, address):
        proc = subprocess.Popen([sys.executable, BRIDGE, "watch", address], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(lambda: (proc.poll() is None and proc.kill(), proc.wait(10)))
        self.assertTrue(wait_for(lambda: address in self.run_in(env, "list").stdout, 15))

    def inbox_in(self, env, address):
        out = self.run_in(env, "recv", address, "--json").stdout
        return [json.loads(line) for line in out.splitlines()]

    def wait_in(self, env, address, timeout=30):
        got = []
        wait_for(lambda: got.extend(self.inbox_in(env, address)) or got, timeout)
        return got

    # ---- frames

    def test_frames_round_trip_and_reject_tampering(self):
        a, b = self.channels()
        payload = {"type": "msg", "body": "é" * 10000, "ts": int(time.time())}
        frames = a.seal(payload, 7)
        self.assertGreater(len(frames), 1, "a large payload spans several frames")
        self.assertTrue(all(len(f) <= self.remote.FRAME_TEXT_MAX for f in frames))
        opened = [b.open(f) for f in frames]
        self.assertTrue(all(opened))
        data = b"".join(chunk for _, _, _, chunk in sorted(opened, key=lambda o: o[1]))
        self.assertEqual(json.loads(data)["body"], payload["body"])
        self.assertIsNone(a.open(frames[0]), "a frame is only valid in its own direction")
        tampered = frames[0][:40] + ("A" if frames[0][40] != "A" else "B") + frames[0][41:]
        self.assertIsNone(b.open(tampered))
        _, other = self.channels(root=b"x" * 32)
        self.assertIsNone(other.open(frames[0]), "another pair's key opens nothing")
        self.assertNotIn("é", "".join(frames))

    def test_payload_sizes_are_padded(self):
        a, _ = self.channels()
        short = a.seal({"body": "a", "ts": 1}, 1)[0]
        longer = a.seal({"body": "a" * 100, "ts": 1}, 2)[0]
        self.assertEqual(len(short), len(longer))

    # ---- pairing

    def test_pairing_gives_both_sides_the_same_key_and_code(self):
        results = self.run_pairing()
        self.assertIsInstance(results["inviter"], bytes)
        self.assertEqual(results["inviter"], results["joiner"])
        self.assertEqual(results["inviter-code"], results["joiner-code"])

    def test_pairing_needs_both_confirmations(self):
        results = self.run_pairing(confirm_b=False)
        self.assertIsNone(results["inviter"])
        self.assertIsNone(results["joiner"])

    def test_pairing_with_a_wrong_secret_never_completes(self):
        results = self.run_pairing(secret_b=os.urandom(32), seconds=4)
        self.assertIsInstance(results["inviter"], SystemExit)
        self.assertIsInstance(results["joiner"], SystemExit)
        self.assertNotIn("inviter-code", results)

    def test_agents_cannot_pair_or_widen_access(self):
        for args in (["invite", "x"], ["join", "x", "code"], ["relay-server"]):
            with self.subTest(args=args):
                proc = self.bridge("remote", *args, check=False)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("needs a person at a terminal", proc.stderr)
        self.write_pair(self.env, "bob", "inviter", os.urandom(32), [])
        proc = self.bridge("remote", "allow", "bob", "claude:*", check=False)
        self.assertIn("needs a person at a terminal", proc.stderr)
        self.bridge("remote", "deny", "bob", "claude:*")  # narrowing stays open to agents

    # ---- addresses

    def test_unknown_machine_and_bad_remote_addresses_are_refused(self):
        self.assertIn("not a paired machine", self.send("remote:nobody/claude", "hi", check=False).stderr)
        self.write_pair(self.env, "bob", "inviter", os.urandom(32), [])
        for to in ("remote:bob", "remote:bob/claude:a/b", "remote:Bob/claude", "remote:bob/other"):
            with self.subTest(to=to):
                self.assertNotEqual(self.send(to, "hi", check=False).returncode, 0)

    def state_log(self, env):
        path = os.path.join(env["AGENT_BRIDGE_HOME"], "state", "remote-gateway.log")
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def test_policy(self):
        rec = {"allow": ["claude:*", "agy:new-cli"]}
        allowed = self.remote.allowed
        self.assertTrue(allowed(rec, {}, "claude:review", "claude:review"))
        self.assertFalse(allowed(rec, {}, "agy:1234", "agy:1234"))
        self.assertTrue(allowed(rec, {}, "agy:new-cli", "agy:new-cli@x"))
        self.assertFalse(allowed({"allow": ["agy:*"]}, {}, "agy:new", "agy:new"), "wildcards never start sessions")
        self.assertTrue(allowed({}, {"contacts": {"ext:me": time.time()}}, "ext:me", "ext:me"), "replies get through")
        contact = {"contacts": {"claude": time.time(), "ext:ci": time.time()}}
        self.assertFalse(allowed({}, contact, "claude:other", "claude"), "`claude` resolving elsewhere is no reply")
        self.assertFalse(allowed({}, contact, "ext:ci-prod", "ext:ci"), "a prefix is no reply")
        self.assertFalse(allowed({}, {"contacts": {"ext:me": time.time() - 2 * 86400}}, "ext:me", "ext:me"))
        self.assertFalse(allowed({"allow": ["*"]}, {}, "remote:x", "remote:x"), "no hop to a third machine")

    # ---- two machines

    def test_message_reply_and_refusal_between_two_machines(self):
        a, b = self.two_machines(allow_b=["claude"])
        self.run_in(a, "send", "remote:bob/claude", "hello from A", "--from", "claude")
        got = self.wait_in(b, "claude")
        self.assertEqual([(m["from"], m["body"]) for m in got], [("remote:alice/claude", "hello from A")])
        # B answers the `from` it got; A allows nothing first, but A's `claude` wrote to bob.
        self.run_in(b, "send", "remote:alice/claude", "hello back", "--from", "claude")
        got = self.wait_in(a, "claude")
        self.assertEqual([(m["from"], m["body"]) for m in got], [("remote:bob/claude", "hello back")])
        # Nothing on B allows ext:secret: A's sender gets a notice instead.
        self.run_in(a, "send", "remote:bob/ext:secret", "let me in", "--from", "claude")
        notice = self.wait_in(a, "claude")
        self.assertEqual(len(notice), 1)
        self.assertEqual(notice[0]["from"], "remote:bob")
        self.assertIn("no such session there, or it does not accept", notice[0]["body"])
        self.assertNotIn("matches", notice[0]["body"], "a refusal tells nothing about the other machine's sessions")
        # A's ext:other runs, but never wrote to bob, and A allows nothing first: refused on A.
        # (Notices are limited to one a minute per peer: this one comes from A, the first from B.)
        self.watch_in(a, "ext:other")
        self.run_in(b, "send", "remote:alice/ext:other", "hi", "--from", "claude")
        notice = self.wait_in(b, "claude")
        self.assertIn("does not accept messages from this machine", notice[0]["body"])
        self.assertIn("refused", self.state_log(a))
        self.assertIn("gateway running", self.run_in(a, "list").stdout)

    def test_messages_from_a_machine_cannot_hop_on(self):
        self.write_pair(self.env, "bob", "inviter", os.urandom(32), [])
        bridge = self.bridge_mod
        with self.assertRaises(SystemExit) as caught:
            bridge.send("remote:bob/claude", "hop", "remote:carol/claude")
        self.assertIn("cannot be passed on", str(caught.exception))

    def test_from_cannot_open_a_reply_window_for_another_session(self):
        self.write_pair(self.env, "bob", "inviter", os.urandom(32), [])
        proc = self.send("remote:bob/claude", "hi", "claude:victim", check=False)
        self.assertIn("--from can only name an ext: program", proc.stderr)
        self.assertEqual(self.inbox("remote:bob"), [])

    def test_roster_lists_only_allowed_sessions(self):
        a, b = self.two_machines(allow_b=["ext:public*"])
        for name in ("public-bot", "private-bot"):
            self.watch_in(b, f"ext:{name}")
        self.run_in(b, "remote", "stop")  # a restarted gateway asks for, and sends, rosters at once
        self.run_in(b, "remote", "start")
        listing = wait_for(lambda: "remote:bob/ext:public-bot" in (out := self.run_in(a, "list").stdout) and out, 60)
        self.assertTrue(listing, "B's allowed session was never announced to A")
        self.assertNotIn("private-bot", listing)

    def test_long_message_and_queue_while_the_gateway_is_stopped(self):
        a, b = self.two_machines(allow_b=["claude"])
        self.run_in(b, "remote", "stop")
        body = "Ünïcode ✓ " * 1200 + "\nend"
        self.run_in(a, "send", "remote:bob/claude", "-", "--from", "claude", stdin=body)
        self.assertTrue(wait_for(lambda: "sent" in self.state_log(a), 30), "A never published")
        self.run_in(b, "remote", "start")
        got = self.wait_in(b, "claude")
        self.assertEqual([m["body"] for m in got], [body])

    def test_a_stale_gateway_file_does_not_block_sending(self):
        a, _ = self.two_machines(allow_b=["claude"])
        self.run_in(a, "remote", "stop")
        # A crash leaves gateway.json behind, and some other process now holds that pid.
        with open(os.path.join(a["AGENT_BRIDGE_HOME"], "remote", "gateway.json"), "w") as f:
            json.dump({"pid": os.getpid(), "beat": time.time() - 3600}, f)
        self.assertIn("stopped", self.run_in(a, "remote", "status").stdout)
        self.run_in(a, "send", "remote:bob/claude", "after a crash", "--from", "claude")
        self.assertTrue(wait_for(lambda: "running" in self.run_in(a, "remote", "status").stdout, 15))

    def test_malformed_payloads_from_a_peer_do_not_stop_the_gateway(self):
        self.write_pair(self.env, "bob", "inviter", b"r" * 32, [])
        gateway = self.remote.Gateway()
        gateway.reload()
        gateway.running = False  # no relay reader loops beyond the first connection
        peer = self.remote.Channel(self.remote.record("joiner", self.url, b"r" * 32))
        now = int(time.time())
        for seq, payload in enumerate([{"type": "roster", "agents": 1, "ts": now},
                                       {"type": "roster", "agents": [{"address": "claude:x", "name": 'a"\nclaude:admin'}], "ts": now},
                                       {"type": "msg", "from": "claude", "to": "claude", "body": 5, "ts": now},
                                       {"type": "notice", "to": "claude", "code": "free text", "ts": now},
                                       {"type": "roster", "agents": [], "ts": "soon"}], start=1):
            for frame in peer.seal(payload, seq):
                gateway._step(gateway.handle_frame, {"topic": gateway.channels["bob"].topic_in, "message": frame,
                                                     "time": now})
        roster = self.remote._read_json(self.remote._state_file("bob", ".roster"))
        self.assertEqual(roster["agents"], [{"address": "claude:x", "name": "aclaudeadmin"}])
        self.assertEqual(self.inbox("claude"), [])

    def test_reassembly_buffers_are_bounded(self):
        self.write_pair(self.env, "bob", "inviter", b"r" * 32, [])
        gateway = self.remote.Gateway()
        gateway.reload()
        gateway.running = False
        peer = self.remote.Channel(self.remote.record("joiner", self.url, b"r" * 32))
        for seq in range(1, 40):
            frame = peer.seal({"body": "x" * 4000, "ts": int(time.time())}, seq)[0]  # first part only
            gateway.handle_frame({"topic": gateway.channels["bob"].topic_in, "message": frame, "time": 1})
        self.assertLessEqual(len(gateway.parts), self.remote.OPEN_SEQS_MAX)

    def test_relay_refuses_bad_lengths(self):
        import http.client
        for length in ("-1", "abc", "5000"):
            with self.subTest(length=length):
                conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=5)
                conn.putrequest("POST", "/dltopic")
                conn.putheader("Content-Length", length)
                conn.endheaders()
                self.assertIn(conn.getresponse().status, (411, 413))
                conn.close()

    def test_relay_counts_clients_fairly(self):
        relay = self.remote.Relay(trusted_proxy="127.0.0.1")
        self.assertEqual(relay.client("2001:db8::1", None), relay.client("2001:db8::2", None), "one /64, one client")
        self.assertNotEqual(relay.client("2001:db8:0:1::1", None), relay.client("2001:db8::1", None))
        self.assertEqual(relay.client("::ffff:192.0.2.7", None), "192.0.2.7")
        self.assertEqual(relay.client("127.0.0.1", "10.0.0.1, 192.0.2.9"), "192.0.2.9", "the proxy's last hop")
        self.assertEqual(relay.client("192.0.2.1", "192.0.2.9"), "192.0.2.1", "only the trusted proxy may forward")
        self.assertEqual(relay.client("::ffff:127.0.0.1", "192.0.2.9"), "192.0.2.9", "the proxy on a dual-stack socket")
        events = [relay.subscribe("a", {"t"}) for _ in range(relay.SUBSCRIBERS_PER_CLIENT + 1)]
        self.assertIsNone(events[-1], "one client cannot take every subscription")
        self.assertIsNotNone(relay.subscribe("b", {"t"}))

    def test_relay_drops_slow_requests(self):
        import socket
        self.relay.TIMEOUT = 2
        sock = socket.create_connection(("127.0.0.1", self.server.server_address[1]), timeout=10)
        sock.sendall(b"POST /dltopic HTTP/1.1\r\n")  # and then nothing more
        self.assertTrue(wait_for(lambda: not self.relay.pending, 10), "the slow request was never dropped")
        self.assertEqual(sock.recv(100), b"", "the connection was closed")
        sock.close()
        self.assertTrue(wait_for(lambda: not self.relay.connections, 10))

    def test_relay_memory_is_bounded(self):
        relay = self.remote.Relay()
        relay.KEEP_BYTES = 40000
        for i in range(100):
            relay.post(f"topic{i % 3}", "x" * 1000)
        self.assertLessEqual(relay.bytes, relay.KEEP_BYTES)
        self.assertEqual(relay.bytes, sum(len(m["message"]) for q in relay.topics.values() for m in q))
        relay.KEEP_PER_TOPIC = 5
        for _ in range(20):
            relay.post("flood", "y")
        self.assertEqual(len(relay.topics["flood"]), 5)
        self.assertTrue(relay.topics["topic1"], "a flood on one topic keeps the others")

    def test_gateway_stop_and_status(self):
        a, _ = self.two_machines()
        self.assertIn("running", self.run_in(a, "remote", "status").stdout)
        self.run_in(a, "remote", "stop")
        self.assertTrue(wait_for(lambda: "stopped" in self.run_in(a, "remote", "status").stdout, 10))
        shutil.rmtree(os.path.join(a["AGENT_BRIDGE_HOME"], "remote", "peers"))
        self.assertNotEqual(self.run_in(a, "remote", "start", check=False).returncode, 0)


if __name__ == "__main__":
    unittest.main()

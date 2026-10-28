"""agy CLI conversations without a process, against tests/fakes/agy: listing from agy's
index, delivery with agy -p through cli-run, and agy:new-cli. Not on Windows (the fake agy
is a Python script found through PATH)."""
import json
import os
import unittest

from helpers import BRIDGE, FAKES, WINDOWS, BridgeTest, wait_for, write_index


def cid(n):
    return f"{n:08x}-1111-4000-8000-{n:012x}"


OLD, ANCIENT, SUB = cid(1), cid(2), cid(3)


@unittest.skipIf(WINDOWS, "the fake agy is a script run through PATH")
class CliTest(BridgeTest):

    def setUp(self):
        super().setUp()
        self.agy_log = os.path.join(self.tmp, "agy.jsonl")
        self.env.update(PATH=FAKES + os.pathsep + self.env.get("PATH", ""), FAKE_AGY_LOG=self.agy_log,
                        BRIDGE=BRIDGE)
        self.spaced = os.path.join(self.tmp, "with space")
        self.other = os.path.join(self.tmp, "other")
        for folder in (self.spaced, self.other):
            os.makedirs(folder)
        write_index(os.path.join(self.home, ".gemini", "antigravity-cli", "conversation_summaries.db"), [
            {"id": OLD, "title": "Old CLI", "days": 2, "workspace": self.spaced},
            {"id": ANCIENT, "title": "Ancient CLI", "days": 100, "workspace": self.other},
            {"id": SUB, "title": "Sub", "days": 1, "workspace": self.other, "depth": 1},
        ])

    def agy_runs(self):
        try:
            with open(self.agy_log, encoding="utf-8") as f:
                return [json.loads(line) for line in f]
        except OSError:
            return []

    def test_sleeping_conversations_are_listed_by_age(self):
        listed = self.listing()
        self.assertIn('"Old CLI"', listed)
        self.assertIn("agy CLI, no process: wakes with agy -p", listed)
        self.assertNotIn("Ancient CLI", listed)
        everything = self.listing("--all")
        self.assertIn("Ancient CLI", everything)
        self.assertNotIn('"Sub"', everything, "a subagent is listed")

    def test_messages_wake_a_sleeping_conversation_in_one_turn(self):
        self.send("agy:Old CLI", "first")
        self.send(f"agy:{OLD[:8]}", "second")
        pong = self.wait_inbox("claude")
        self.assertTrue(pong and pong[0]["from"] == f"agy:{OLD}", pong)
        runs = self.agy_runs()
        self.assertEqual(len(runs), 1, "two messages sent together must go in one turn")
        self.assertEqual(runs[0], {"cwd": self.spaced, "conversation": OLD, "resume": True, "messages": 2})
        self.assertIn("delivered", self.state(f"cli-{OLD}.log"))
        self.assertTrue(wait_for(lambda: self.peer(f"agy:{OLD}").get("relay_pid") is None, 10),
                        "cli-run must step aside when done")

    def test_an_exact_title_reaches_an_old_conversation(self):
        self.send("agy:Ancient CLI", "still there?")
        self.assertTrue(self.wait_inbox("claude"), "no answer")
        self.assertEqual(self.agy_runs()[0]["conversation"], ANCIENT)
        self.assertEqual(self.agy_runs()[0]["cwd"], self.other)

    def test_new_cli_session_with_a_name(self):
        self.send("agy:new-cli@builder", "build it", "claude", "--workspace", self.other)
        replies = self.wait_inbox("claude", count=2)
        self.assertEqual(len(replies), 2, replies)
        note = next(m for m in replies if m["body"].startswith("[bridge"))
        new = note["from"].split(":", 1)[1]
        self.assertIn("agy:builder", note["body"])
        self.assertEqual(self.agy_runs()[0], {"cwd": self.other, "conversation": new, "resume": False, "messages": 1})
        self.assertIn("builder", self.listing())

        self.send("agy:builder", "again")
        self.assertTrue(self.wait_inbox("claude"), "no answer through the alias")
        self.assertTrue(wait_for(lambda: len(self.agy_runs()) == 2, 10))
        self.assertEqual(self.agy_runs()[1]["conversation"], new)
        self.assertTrue(self.agy_runs()[1]["resume"])

    def test_new_cli_defaults_to_the_sender_directory(self):
        proc = self.bridge("send", "agy:new-cli", "here", "--from", "claude", env={}, check=True)
        self.assertIn("agy:new-cli", proc.stdout)
        self.assertTrue(self.wait_inbox("claude", count=2))
        self.assertEqual(self.agy_runs()[0]["cwd"], os.getcwd())

    def test_invalid_requests_are_refused(self):
        bad_name = self.send("agy:new-cli@bad name", "x", check=False)
        self.assertIn("letters, digits", bad_name.stderr)
        missing = self.send("agy:new-cli", "x", "claude", "--workspace", os.path.join(self.tmp, "nope"), check=False)
        self.assertIn("no such directory", missing.stderr)
        misplaced = self.send("agy:Old CLI", "x", "claude", "--workspace", self.other, check=False)
        self.assertIn("--workspace only applies", misplaced.stderr)
        self.assertEqual(self.agy_runs(), [])


if __name__ == "__main__":
    unittest.main()

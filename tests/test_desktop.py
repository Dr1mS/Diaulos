"""Antigravity Desktop, against tests/fakes/fake_ls.py: the shared relay, the auto-relay,
discovery of conversations, ages, and agy:new. Linux only (/proc, prctl)."""
import json
import os
import unittest

from helpers import LINUX, BridgeTest, file_uri, wait_for, write_index


def cid(n):
    return f"{n:08x}-0000-4000-8000-{n:012x}"


A, C, L, R = cid(0xA), cid(0xC), cid(0x1), cid(0x2)


def summary(title, workspace=None, archived=False):
    """One entry of GetAllCascadeTrajectories."""
    entry = {"summary": title, "annotations": {"title": title}}
    if archived:
        entry["annotations"]["archived"] = True
    if workspace:
        entry["workspaces"] = [{"workspaceFolderAbsoluteUri": file_uri(workspace)}]
    return entry


def step_types(hook_output):
    return [next(iter(step)) for step in hook_output.get("injectSteps", [])]


@unittest.skipUnless(LINUX, "the fake language server and process lookups need Linux")
class DesktopTest(BridgeTest):

    def workspace(self, name):
        path = os.path.join(self.tmp, name)
        os.makedirs(path, exist_ok=True)
        return path

    def wait_delivery(self, server, cascade, text=None):
        call = server.wait_call("SendUserCascadeMessage",
                                lambda c: c["cascadeId"] == cascade and (text is None or text in c["text"]))
        self.assertIsNotNone(call, f"nothing delivered to {cascade}")
        return call

    def test_one_relay_serves_every_conversation_of_the_window(self):
        server = self.start_server()
        ws = self.workspace("ws")
        self.assertIn("ephemeralMessage", step_types(server.hook("SessionStart", A, ws)), "no relay reminder")
        server.ensure_relay(A)
        relay = self.peer(f"agy:{A}").get("relay_pid")
        self.assertTrue(relay)
        # Another conversation of the same window joins the relay: no reminder.
        self.assertEqual(step_types(server.hook("PreInvocation", C)), [])
        self.assertEqual(self.peer(f"agy:{C}").get("relay_pid"), relay)
        self.assertIn("already running", server.ensure_relay(C))

        self.send(f"agy:{C}", "hello C")
        call = self.wait_delivery(server, C, "hello C")
        self.assertTrue(call["config"], "the turn must carry the conversation's config")

        server.stop()
        self.assertTrue(wait_for(lambda: self.peer(f"agy:{A}").get("gone"), 15), "relay did not see the server exit")
        self.assertIsNone(self.peer(f"agy:{C}").get("relay_pid"))
        self.assertIn("relay down: agy exited", self.state(f"relay-{A}.log"))

    def test_a_hook_starts_the_auto_relay(self):
        server = self.start_server()
        self.set_auto_relay(True)
        self.assertEqual(step_types(server.hook("SessionStart", A, self.workspace("ws"))), ["systemMessage"])
        self.assertIn(f"agy:{A}: relay pid", self.state("auto-relay.log"))
        self.set_auto_relay(False)
        self.send(f"agy:{A}", "auto")
        self.wait_delivery(server, A, "auto")
        self.assert_token_not_on_disk()

    def test_listing_starts_the_relay_of_a_window_without_any_hook(self):
        server = self.start_server()
        ws = self.workspace("tabs")
        server.set_loaded({L: summary("Open tab", ws), R: summary("Archived tab", ws, archived=True)})
        bridge = self.load_bridge()
        bridge._language_servers = lambda: [server.pid]  # never the real /proc
        self.set_auto_relay(True)
        found = {p["address"]: p for p in bridge.peers("agy")}
        self.set_auto_relay(False)
        self.assertIn(f"agy:{L}", found)
        self.assertTrue(found[f"agy:{L}"]["relay_alive"])
        self.assertEqual(found[f"agy:{L}"]["workspace"], ws)
        self.assertNotIn(f"agy:{R}", found, "an archived conversation is listed")
        self.send("agy:Open tab", "by title")
        self.wait_delivery(server, L, "by title")
        self.assert_token_not_on_disk()

    def test_conversations_loaded_or_on_disk_and_their_age(self):
        data = os.path.join(self.tmp, "desktop-data")
        ws = self.workspace("project")
        ids = {name: cid(0x100 + i) for i, name in enumerate(
            ["recent", "cli", "subagent", "killed", "archived", "month", "ancient"])}
        write_index(os.path.join(data, "conversation_summaries.db"), [
            {"id": ids["recent"], "title": "Recent one", "days": 1, "workspace": ws},
            {"id": ids["cli"], "title": "From CLI", "days": 1, "source": "CORTEX_TRAJECTORY_SOURCE_CLI"},
            {"id": ids["subagent"], "title": "Helper", "days": 1, "depth": 1},
            {"id": ids["killed"], "title": "Killed", "days": 1, "killed": 1},
            {"id": ids["archived"], "title": "Archived", "days": 1},
            {"id": ids["month"], "title": "Last month", "days": 30, "workspace": ws},
            {"id": ids["ancient"], "title": "Ancient one", "days": 400},
        ])
        os.makedirs(os.path.join(data, "annotations"))
        with open(os.path.join(data, "annotations", ids["archived"] + ".pbtxt"), "w") as f:
            f.write('title:"Archived"  archived:true')
        server = self.start_server(app_data_dir=data)
        server.set_loaded({L: summary("Loaded tab", ws), R: summary("Archived tab", archived=True)})
        server.ensure_relay(A, data_dir=data)
        self.assertTrue(wait_for(lambda: self.peer(f"agy:{ids['recent']}"), 15), "disk index not read")

        listed = self.listing()
        for name in ("Loaded tab", "Recent one"):
            self.assertIn(name, listed)
        for name in ("Archived tab", "From CLI", "Helper", "Killed", "\"Archived\"", "Last month", "Ancient one"):
            self.assertNotIn(name, listed)
        self.assertIn("Last month", self.listing("--days", "60"))
        self.assertNotIn("Ancient one", self.listing("--days", "60"))
        self.assertNotIn("Last month", self.listing(), "the default listing must stay at 7 days")
        self.assertIn("Ancient one", self.listing("--all"))

        self.send("agy:Ancient one", "old but reachable")
        self.wait_delivery(server, ids["ancient"], "old but reachable")

    def test_agy_new_names_files_and_borrows_a_config(self):
        data = os.path.join(self.tmp, "desktop-data")
        os.makedirs(data)
        ws, filed, plain = self.workspace("relay ws"), self.workspace("filed"), self.workspace("plain dir")
        projects = os.path.join(self.home, ".gemini", "config", "projects")
        os.makedirs(projects)
        with open(os.path.join(projects, "proj-1.json"), "w") as f:
            json.dump({"id": "proj-1", "name": "Bouquin Test",
                       "projectResources": {"resources": [{"gitFolder": {"folderUri": file_uri(filed)}}]}}, f)
        # The relay's own conversation has no prompt, so no config to lend: C has one.
        server = self.start_server(noconfig=[A], app_data_dir=data)
        server.hook("SessionStart", A, ws)
        server.ensure_relay(A, data_dir=data)
        server.hook("SessionStart", C, ws)

        self.send("agy:new@Reviewer", "review it", "claude", "--workspace", "bouquin test")
        start = server.wait_call("StartCascade")
        self.assertIsNotNone(start, "StartCascade not called")
        self.assertEqual(start["body"].get("projectEnvConfig", {}).get("projectId"), "proj-1")
        self.assertNotIn("workspaceUris", start["body"], "workspaceUris and projectEnvConfig together are refused")
        title = server.wait_call("UpdateConversationAnnotations")
        self.assertEqual(title["body"]["annotations"]["title"], "Reviewer")
        new = title["body"]["cascadeId"]
        self.assertTrue(self.wait_delivery(server, new, "review it")["config"], "no config borrowed")
        notes = self.wait_inbox("claude")
        self.assertTrue(notes and new in notes[0]["body"] and "Reviewer" in notes[0]["body"], notes)
        self.assertIn(filed, notes[0]["body"])
        self.assertIn('"Reviewer"', self.listing())

        self.send("agy:new", "plain", "claude", "--workspace", plain)
        second = server.wait_call("StartCascade", lambda c: "workspaceUris" in c["body"])
        self.assertEqual(second["body"]["workspaceUris"], [file_uri(plain)])
        self.assertNotIn("projectEnvConfig", second["body"])

        unknown = self.send("agy:new", "x", "claude", "--workspace", "Nope", check=False)
        self.assertIn("neither a directory nor a single Desktop project", unknown.stderr)
        misplaced = self.send(f"agy:{C}", "x", "claude", "--workspace", plain, check=False)
        self.assertIn("--workspace only applies", misplaced.stderr)


if __name__ == "__main__":
    unittest.main()

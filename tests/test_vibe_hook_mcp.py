"""The hooks and the MCP server of the Diaulos plugin for Mistral Vibe 3 (bridge/vibe_hook.py,
bridge/vibe_mcp.py, docs/VIBE-DESIGN.md §8.2, §8.7, §8.9.5), run as vibe runs them: as children of
a process named vibe-app-server (tests/fakes/as_app_server.py), payload or JSON-RPC on stdin.
No vibe, no model call. Linux only (the parent is found in /proc).
"""
import json
import os
import subprocess
import sys
import threading
import unittest
import uuid

from helpers import FAKES, LINUX, ROOT, BridgeTest, wait_for

HOOK = os.path.join(ROOT, "bridge", "vibe_hook.py")
MCP = os.path.join(ROOT, "bridge", "vibe_mcp.py")
AS_APP_SERVER = os.path.join(FAKES, "as_app_server.py")
SESSION = "0a0b0c0d-1111-2222-3333-0123456789ab"
ADDRESS = "vibe:0123456789ab"


def under_app_server(command):
    return ["/bin/bash", "-c", 'exec -a vibe-app-server "$0" "$@"', sys.executable, AS_APP_SERVER] + command


@unittest.skipUnless(LINUX, "the vibe-app-server parent is found in /proc")
class Setup(BridgeTest):

    def setUp(self):
        super().setUp()
        for key in [k for k in self.env if k.startswith(("AGENT_BRIDGE_", "VIBE_")) and k != "AGENT_BRIDGE_HOME"]:
            del self.env[key]
        self.env["VIBE_HOME"] = os.path.join(self.home, ".vibe")

    def hook(self, event, payload, **env):
        proc = subprocess.run(under_app_server([sys.executable, HOOK, event]),
                              input=payload if isinstance(payload, str) else json.dumps(payload),
                              capture_output=True, text=True, env=dict(self.env, **env), timeout=30)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout) if proc.stdout.strip() else None

    def hook_log(self):
        return self.state("vibe-hook.log") or ""

    def currents(self):
        folder = os.path.join(self.bridge_home, "state", "vibe-current")
        found = []
        for name in os.listdir(folder) if os.path.isdir(folder) else []:
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                found.append(json.load(f))
        return found

    def write_current(self, app_pid, session=SESSION):
        folder = os.path.join(self.bridge_home, "state", "vibe-current")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "%d.json" % app_pid), "w", encoding="utf-8") as f:
            json.dump({"session": session, "address": "vibe:" + session[-12:], "app_pid": app_pid, "run": False}, f)

    def queue(self, body, sender="claude:review", to=ADDRESS):
        inbox = os.path.join(self.bridge_home, "inbox", to.replace(":", "-"))
        os.makedirs(inbox, exist_ok=True)
        msg = {"id": uuid.uuid4().hex[:12], "from": sender, "to": to, "ts": "2026-11-13T10:00:00", "body": body}
        with open(os.path.join(inbox, "1-%s.json" % msg["id"]), "w", encoding="utf-8") as f:
            json.dump(msg, f)
        return msg


class HookTest(Setup):

    def test_session_start_writes_the_current_session_and_tells_the_address(self):
        answer = self.hook("session_start", {"session_id": SESSION, "cwd": "/w", "source": "startup"})
        self.assertEqual(answer["system_message"], "Diaulos: this conversation is " + ADDRESS)
        context = answer["hook_specific_output"]["additional_context"]
        self.assertIn(ADDRESS, context)
        self.assertIn("`send` tool of the `diaulos` MCP server", context)
        (record,) = self.currents()
        self.assertEqual((record["session"], record["address"], record["source"], record["run"]),
                         (SESSION, ADDRESS, "startup", False))

    def test_session_start_in_a_vibe_p_run_is_silent(self):
        self.assertIsNone(self.hook("session_start", {"session_id": SESSION}, AGENT_BRIDGE_VIBE_RUN="1"))
        self.assertTrue(self.currents()[0]["run"])

    def test_session_start_of_a_subagent_or_without_app_server_writes_nothing(self):
        self.assertIsNone(self.hook("session_start", {"session_id": SESSION, "parent_session_id": "x"}))
        proc = subprocess.run([sys.executable, HOOK, "session_start"], input=json.dumps({"session_id": SESSION}),
                              capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual((proc.returncode, proc.stdout), (0, ""))
        self.assertEqual(self.currents(), [])
        self.assertIn("no vibe-app-server above", self.hook_log())

    def test_pre_agent_without_relay_adds_waiting_messages(self):
        msg = self.queue("please look at @x")
        answer = self.hook("pre_agent", {"session_id": SESSION, "prompt": "hello from the person"})
        self.assertEqual(answer["system_message"], "message from claude:review")
        context = answer["hook_specific_output"]["additional_context"]
        self.assertIn("please look at @⁠x", context)
        self.assertIn(msg["id"], context)
        self.assertEqual(self.inbox(ADDRESS), [])
        self.assertIsNone(self.hook("pre_agent", {"session_id": SESSION, "prompt": "again"}), "nothing left")

    def test_pre_agent_leaves_messages_to_a_live_relay(self):
        folder = os.path.join(self.bridge_home, "state", "vibe-relay")
        os.makedirs(folder)
        with open(os.path.join(folder, "%d.json" % os.getpid()), "w", encoding="utf-8") as f:
            json.dump({"relay_pid": os.getpid(), "tui_pid": os.getpid(), "session": SESSION, "wake": "relay"}, f)
        self.queue("for the relay")
        self.assertIsNone(self.hook("pre_agent", {"session_id": SESSION, "prompt": "typed"}))
        self.assertEqual(len(self.inbox(ADDRESS)), 1)
        self.assertIn("relay True", self.hook_log())

    def test_pre_agent_of_a_relay_delivery_shows_nothing(self):
        self.queue("still waiting")
        block = '<session_message from_session_id="diaulos:ext:gate-r" from_title="ext:gate-r"/>'
        self.assertIsNone(self.hook("pre_agent", {"session_id": SESSION, "prompt": block}))
        self.assertIn("relay delivery from ext:gate-r", self.hook_log())
        self.assertEqual(len(self.inbox(ADDRESS)), 1, "a delivery turn claims nothing")

    def test_pre_agent_in_a_vibe_p_run_claims_nothing(self):
        self.queue("for vibe-run")
        self.assertIsNone(self.hook("pre_agent", {"session_id": SESSION, "prompt": "x"}, AGENT_BRIDGE_VIBE_RUN="1"))
        self.assertEqual(len(self.inbox(ADDRESS)), 1)

    def test_a_bad_payload_never_breaks_vibe(self):
        self.assertIsNone(self.hook("pre_agent", "not json"))
        self.assertIn("pre_agent error", self.hook_log())

    def test_the_hook_log_holds_no_message_text(self):
        self.queue("secret words of the message")
        self.hook("pre_agent", {"session_id": SESSION, "prompt": "secret words typed"})
        self.assertNotIn("secret", self.hook_log())


class McpTest(Setup):

    def start(self):
        self.proc = subprocess.Popen(under_app_server([sys.executable, MCP]), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, env=self.env)
        self.addCleanup(self.proc.stdout.close)
        self.addCleanup(self.proc.wait, 10)
        self.addCleanup(self.proc.stdin.close)
        self.answers = {}
        threading.Thread(target=self._read, daemon=True).start()
        self.next_id = 0
        self.call("initialize", {"protocolVersion": "2025-06-18"})

    def _read(self):
        for line in self.proc.stdout:
            msg = json.loads(line)
            self.answers[msg.get("id")] = msg

    def call(self, method, params=None):
        self.next_id += 1
        rid = self.next_id
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        self.assertTrue(wait_for(lambda: rid in self.answers, 20), method)
        return self.answers[rid]

    def tool(self, name, **args):
        result = self.call("tools/call", {"name": name, "arguments": args})["result"]
        return result["content"][0]["text"], result["isError"]

    def test_tools_list(self):
        self.start()
        self.assertEqual(self.answers[1]["result"]["serverInfo"]["name"], "diaulos")
        tools = self.call("tools/list")["result"]["tools"]
        self.assertEqual(sorted(t["name"] for t in tools), ["list_agents", "send"])

    def test_send_needs_the_session_start_file(self):
        self.start()
        text, error = self.tool("send", to="claude", text="hi")
        self.assertTrue(error)
        self.assertIn("no Diaulos address yet", text)

    def test_send_and_list_agents_as_this_conversation(self):
        self.start()
        self.write_current(self.proc.pid)  # the vibe-app-server: bash exec'd into it
        text, error = self.tool("send", to="claude", text="hello from vibe")
        self.assertFalse(error, text)
        self.assertIn("from %s to claude" % ADDRESS, text)
        (msg,) = self.inbox("claude")
        self.assertEqual((msg["from"], msg["body"]), (ADDRESS, "hello from vibe"))
        text, error = self.tool("list_agents")
        self.assertFalse(error, text)
        line = [l for l in text.splitlines() if l.startswith(ADDRESS)]
        self.assertTrue(line and line[0].endswith("(you)"), text)
        self.assertIn("TUI: no relay, delivers at next message", line[0])

    def test_two_failures_to_one_address_stop_send(self):
        self.start()
        self.write_current(self.proc.pid)
        for _ in range(2):
            text, error = self.tool("send", to="claude:nobody", text="x")
            self.assertTrue(error)
            self.assertIn("matches 0", text)
        text, error = self.tool("send", to="claude:nobody", text="x")
        self.assertTrue(error)
        self.assertIn("failed twice, do not call send for it again", text)


if __name__ == "__main__":
    unittest.main()

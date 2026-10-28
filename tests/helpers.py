"""Shared test helpers: a throwaway HOME and bridge home per test, the bridge
run as a subprocess, the fake language server, and cleanup of the processes
a test started.

Safety: a test never sees the real ~/.gemini or ~/.agent-bridge. The bridge
looks for Antigravity language servers in /proc when the auto-relay flag
exists; tests never run `list` or `send` as a subprocess while that flag
exists, and the in-process scan is narrowed to the fake server.
"""
import importlib
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRIDGE = os.path.join(ROOT, "bridge", "bridge.py")
FAKES = os.path.join(ROOT, "tests", "fakes")
TOKEN = "FAKE-CSRF-TOKEN-0123456789"
LINUX = sys.platform.startswith("linux")
WINDOWS = os.name == "nt"


def wait_for(predicate, timeout=20.0, step=0.2):
    """The first truthy value of predicate(), or None after timeout seconds."""
    deadline = time.time() + timeout
    while True:
        value = predicate()
        if value or time.time() > deadline:
            return value
        time.sleep(step)


def stamp(days_ago):
    """A time of the conversation_summaries.db format, `days_ago` days back."""
    when = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return when.strftime("%Y-%m-%d %H:%M:%S.123456789+00:00")


def file_uri(path):
    return "file://" + urllib.parse.quote(path)


def write_index(path, rows):
    """conversation_summaries.db with `rows`: dicts with id, title, days, workspace and
    optionally source, killed, depth, project."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE conversation_summaries (conversation_id TEXT, title TEXT, last_modified_time TEXT,"
                " workspace_uris TEXT, source TEXT, killed INTEGER, nesting_depth INTEGER, project_id TEXT)")
    con.executemany("INSERT INTO conversation_summaries VALUES (?,?,?,?,?,?,?,?)", [
        (r["id"], r.get("title", ""), stamp(r.get("days", 0)),
         json.dumps([file_uri(r["workspace"])] if r.get("workspace") else []), r.get("source", ""),
         r.get("killed", 0), r.get("depth", 0), r.get("project", "")) for r in rows])
    con.commit()
    con.close()


class BridgeTest(unittest.TestCase):
    """A fresh HOME and AGENT_BRIDGE_HOME for each test; every process left behind is killed."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="diaulos-test-")
        self.home = os.path.join(self.tmp, "home")
        self.bridge_home = os.path.join(self.tmp, "bridge")
        os.makedirs(os.path.join(self.bridge_home, "state"))
        os.makedirs(self.home)
        self.env = dict(os.environ, HOME=self.home, USERPROFILE=self.home, AGENT_BRIDGE_HOME=self.bridge_home,
                        PYTHONIOENCODING="utf-8")
        for key in ("ANTIGRAVITY_CONVERSATION_ID", "ANTIGRAVITY_LS_ADDRESS", "ANTIGRAVITY_CSRF_TOKEN",
                    "ANTIGRAVITY_APP_DATA_DIR", "AGENT_BRIDGE_ADDRESS"):
            self.env.pop(key, None)
        self.servers = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self.set_auto_relay(False)
        for server in self.servers:
            server.stop()
        _kill_processes_of(self.bridge_home)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- the bridge

    def bridge(self, *args, stdin="", env=None, check=True):
        proc = subprocess.run([sys.executable, BRIDGE, *args], input=stdin, capture_output=True, text=True,
                              encoding="utf-8", env=dict(self.env, **(env or {})), timeout=60)
        if check and proc.returncode != 0:
            self.fail(f"bridge.py {' '.join(args)} exited {proc.returncode}: {proc.stderr or proc.stdout}")
        return proc

    def send(self, to, text, sender="claude", *extra, check=True):
        if self.auto_relay_on():
            raise AssertionError("send with the auto-relay flag would scan the real /proc")
        return self.bridge("send", to, text, *extra, "--from", sender, check=check)

    def listing(self, *args):
        if self.auto_relay_on():
            raise AssertionError("list with the auto-relay flag would scan the real /proc")
        return self.bridge("list", *args).stdout

    def popen(self, *args, stdout=subprocess.DEVNULL):
        """bridge.py <args> in the background, stopped and reaped at cleanup."""
        proc = subprocess.Popen([sys.executable, BRIDGE, *args], env=self.env, stdout=stdout,
                                stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        self.addCleanup(_reap, proc)
        return proc

    def inbox(self, address):
        return [json.loads(line) for line in self.bridge("recv", address, "--json").stdout.splitlines()]

    def wait_inbox(self, address, count=1, timeout=30.0):
        got = []
        wait_for(lambda: got.extend(self.inbox(address)) or len(got) >= count, timeout)
        return got

    def peer(self, address):
        try:
            with open(os.path.join(self.bridge_home, "peers", address.replace(":", "-") + ".json"),
                      encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def state(self, name):
        try:
            with open(os.path.join(self.bridge_home, "state", name), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def auto_relay_on(self):
        return os.path.exists(os.path.join(self.bridge_home, "state", "agy.auto-relay"))

    def set_auto_relay(self, on):
        flag = os.path.join(self.bridge_home, "state", "agy.auto-relay")
        if on:
            open(flag, "w").close()
        elif os.path.exists(flag):
            os.remove(flag)

    def assert_token_not_on_disk(self):
        for folder, _, files in os.walk(self.bridge_home):
            for name in files:
                with open(os.path.join(folder, name), "rb") as f:
                    self.assertNotIn(TOKEN.encode(), f.read(), f"token written to {name}")

    def load_bridge(self):
        """The bridge module, imported in this process for this test's homes."""
        for key in ("HOME", "USERPROFILE", "AGENT_BRIDGE_HOME"):
            self.addCleanup(_restore_env, key, os.environ.get(key))
            os.environ[key] = self.env[key]
        sys.path.insert(0, os.path.dirname(BRIDGE))
        self.addCleanup(sys.path.remove, os.path.dirname(BRIDGE))
        import bridge
        return importlib.reload(bridge)

    # ---- fake Antigravity Desktop

    def start_server(self, noconfig=(), app_data_dir=None):
        server = FakeServer(self, noconfig, app_data_dir)
        self.servers.append(server)
        return server


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _reap(proc):
    if proc.poll() is None:
        proc.kill()
    proc.wait(10)
    if proc.stdout:
        proc.stdout.close()


def _restore_env(key, value):
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value


def _kill_processes_of(bridge_home):
    """Kills the relays, cli-run and cli-new workers of this test's bridge home, found by
    the pids its peer files and relay logs record. Never looks at other programs."""
    pids = set()
    peers = os.path.join(bridge_home, "peers")
    for name in os.listdir(peers) if os.path.isdir(peers) else []:
        try:
            with open(os.path.join(peers, name), encoding="utf-8") as f:
                peer = json.load(f)
        except (OSError, ValueError):
            continue
        pids.update(p for p in (peer.get("relay_pid"), peer.get("pid")) if isinstance(p, int))
    state = os.path.join(bridge_home, "state")
    for name in os.listdir(state) if os.path.isdir(state) else []:
        if name.startswith("relay-") and name.endswith(".log"):
            pids.update(int(p) for p in re.findall(r"relay up pid=(\d+)", _read(os.path.join(state, name))))
    for pid in pids - {os.getpid()}:
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass


class FakeServer:
    """tests/fakes/fake_ls.py, with this test's homes."""

    def __init__(self, test, noconfig, app_data_dir):
        self.test = test
        folder = os.path.join(test.tmp, f"server-{len(test.servers)}")
        os.makedirs(folder)
        self.log = os.path.join(folder, "calls.jsonl")
        self.loaded_path = os.path.join(folder, "loaded.json")
        port_file = os.path.join(folder, "port")
        argv = [sys.executable, os.path.join(FAKES, "fake_ls.py"), "--log", self.log, "--port-file", port_file,
                "--loaded", self.loaded_path, "--csrf_token", TOKEN, "--noconfig", ",".join(noconfig)]
        if app_data_dir:
            argv += ["--app_data_dir", app_data_dir]
        self.proc = subprocess.Popen(argv, env=test.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = wait_for(lambda: os.path.exists(port_file) and _read(port_file), 10)
        if not port:
            raise RuntimeError("the fake language server did not start")
        self.address = f"127.0.0.1:{port}"
        self.pid = self.proc.pid

    def set_loaded(self, summaries):
        with open(self.loaded_path, "w", encoding="utf-8") as f:
            json.dump(summaries, f)

    def run(self, *args, stdin="", env=None):
        """bridge.py <args>, run as a child of this server, as agy runs hooks and shells."""
        body = {"argv": [sys.executable, BRIDGE, *args], "stdin": stdin, "env": dict(self.test.env, **(env or {}))}
        req = urllib.request.Request(f"http://{self.address}/__run", data=json.dumps(body).encode(), method="POST")
        with urllib.request.urlopen(req, timeout=70) as resp:
            return json.loads(resp.read())

    def hook(self, event, conversation, workspace=None):
        """Output of the agy hook for `event`, as JSON (an empty dict for "{}")."""
        payload = {"conversationId": conversation, "workspacePaths": [workspace] if workspace else []}
        out = self.run("agy-hook", event, stdin=json.dumps(payload), env={"ANTIGRAVITY_CONVERSATION_ID": conversation})
        if out["code"] != 0:
            raise AssertionError(f"hook {event} failed: {out['stderr']}")
        return json.loads(out["stdout"] or "{}")

    def ensure_relay(self, conversation, data_dir=None):
        env = {"ANTIGRAVITY_LS_ADDRESS": self.address, "ANTIGRAVITY_CSRF_TOKEN": TOKEN,
               "ANTIGRAVITY_CONVERSATION_ID": conversation}
        if data_dir:
            env["ANTIGRAVITY_APP_DATA_DIR"] = data_dir
        out = self.run("ensure-relay", env=env)
        if out["code"] != 0:
            raise AssertionError(f"ensure-relay failed: {out['stderr']}")
        return out["stdout"]

    def calls(self, method=None):
        try:
            with open(self.log, encoding="utf-8") as f:
                calls = [json.loads(line) for line in f]
        except OSError:
            return []
        return [c for c in calls if method is None or c["method"] == method]

    def wait_call(self, method, match=lambda c: True, timeout=20.0):
        return wait_for(lambda: next((c for c in self.calls(method) if match(c)), None), timeout)

    def stop(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()

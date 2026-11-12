"""install.sh and uninstall.sh for Mistral Vibe (install/vibe_install.py, docs/VIBE-DESIGN.md §5, task T4).

In a temporary HOME (and VIBE_HOME), with a PATH that has no `claude`: install, run vibe
once so that tools/__pycache__ exists, uninstall, then `diff -r` against the copy of
VIBE_HOME made before the install must be empty. "Run vibe once" is vibe's own tool loader
(ToolManager._load_tools_from_file) in vibe's Python, which imports the shim as vibe does
at start, without a model call. The files vibe writes for itself at start go to another
VIBE_HOME, so the diff only sees what the installer and the shim's import leave.
Linux and macOS only (bash).
"""
import ast
import contextlib
import importlib.util
import io
import json
import os
import py_compile
import shutil
import subprocess
import tempfile
import unittest

from helpers import ROOT, WINDOWS

VIBE_PYTHON = os.path.expanduser("~/.local/share/uv/tools/mistral-vibe/bin/python")
RESTART_VIBE = ("Restart open vibe TUIs so they load the bridge tools. "
                "If you install vibe later, run this installer again.")
LOAD = ("import sys; from pathlib import Path; from vibe.core.tools.manager import ToolManager; "
        "t = ToolManager._load_tools_from_file(Path(sys.argv[1])); print(sorted(x.get_name() for x in t))")
OTHER = "# someone else's vibe tool\nprint('mine')\n"


@unittest.skipIf(WINDOWS, "bash installers")
class VibeInstallTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="diaulos-install-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home)
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        for tool in ("python3", "install", "grep", "rm", "mkdir", "dirname", "touch", "cat"):
            path = shutil.which(tool)
            if path:
                os.symlink(path, os.path.join(self.bin, tool))
        self.env = {"HOME": self.home, "PATH": self.bin, "LANG": "C.UTF-8"}
        self.bridge_home = os.path.join(self.home, ".agent-bridge")

    def run_script(self, name, *args, env=None):
        proc = subprocess.run(["/bin/bash", os.path.join(ROOT, name), *args], capture_output=True, text=True,
                              env=dict(self.env, **(env or {})), timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def snapshot(self, vibe_home):
        copy = os.path.join(self.tmp, "before")
        if os.path.exists(vibe_home):
            shutil.copytree(vibe_home, copy, symlinks=True)
        return copy

    def run_vibe_once(self, vibe_home):
        """Import the installed shim as vibe does; tools/__pycache__ then holds its .pyc."""
        shim = os.path.join(vibe_home, "tools", "agent_bridge.py")
        if os.path.exists(VIBE_PYTHON):
            # vibe's own start files (trusted_folders.toml, logs/) are vibe's, not the installer's:
            # they go to a separate VIBE_HOME. The .pyc still lands next to the shim, in tools/__pycache__.
            own = os.path.join(self.tmp, "vibe-own-files")
            proc = subprocess.run([VIBE_PYTHON, "-c", LOAD, shim], capture_output=True, text=True,
                                  env=dict(self.env, VIBE_HOME=own), timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("['bridge_list_agents', 'bridge_send']", proc.stdout)
        else:  # vibe not installed here: write the .pyc its import would leave
            py_compile.compile(shim, cfile=importlib.util.cache_from_source(shim), doraise=True)
        pycache = os.path.join(vibe_home, "tools", "__pycache__")
        self.assertTrue([n for n in os.listdir(pycache) if n.startswith("agent_bridge")], os.listdir(pycache))

    def assert_same(self, before, vibe_home):
        if not os.path.exists(before):
            if os.path.exists(vibe_home):
                self.fail(f"{vibe_home} left behind: {os.listdir(vibe_home)}")
            return
        proc = subprocess.run(["diff", "-r", before, vibe_home], capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stdout), (0, ""), proc.stdout)

    def shim_text(self, vibe_home):
        with open(os.path.join(vibe_home, "tools", "agent_bridge.py"), encoding="utf-8") as f:
            return f.read()

    def test_tools_folder_absent(self):
        vibe_home = os.path.join(self.home, ".vibe")
        os.makedirs(vibe_home)
        with open(os.path.join(vibe_home, "config.toml"), "w") as f:
            f.write('active_model = "x"\n')
        before = self.snapshot(vibe_home)
        out = self.run_script("install.sh")
        self.assertIn("vibe        -> " + os.path.join(vibe_home, "tools", "agent_bridge.py"), out)
        self.assertIn(RESTART_VIBE, out)
        self.assertIn(f"BRIDGE_DIR = {self.bridge_home!r}", self.shim_text(vibe_home))
        for name in ("vibe_sessions.py", "vibe_runner.py", "vibe_plugin.py"):
            self.assertTrue(os.path.exists(os.path.join(self.bridge_home, name)), name)
        with open(os.path.join(self.bridge_home, "state", "vibe-install.json")) as f:
            state = json.load(f)
        self.assertEqual(state["created"], [os.path.join(vibe_home, "tools")])
        self.assertFalse(state["pycache_existed"])
        self.run_vibe_once(vibe_home)
        self.run_script("install.sh")  # a re-run (update) keeps what the first install recorded
        out = self.run_script("uninstall.sh")
        self.assertIn("removed vibe tools", out)
        self.assert_same(before, vibe_home)
        for name in ("vibe_sessions.py", "vibe_runner.py", "vibe_plugin.py", "state/vibe-install.json"):
            self.assertFalse(os.path.exists(os.path.join(self.bridge_home, name)), name)

    def test_another_agent_bridge_py_is_kept_and_restored(self):
        vibe_home = os.path.join(self.tmp, "custom-vibe")  # VIBE_HOME outside HOME
        tools = os.path.join(vibe_home, "tools")
        os.makedirs(os.path.join(tools, "__pycache__"))
        with open(os.path.join(tools, "agent_bridge.py"), "w") as f:
            f.write(OTHER)
        with open(os.path.join(tools, "other_tool.py"), "w") as f:
            f.write("x = 1\n")
        before = self.snapshot(vibe_home)
        env = {"VIBE_HOME": vibe_home}
        out = self.run_script("install.sh", env=env)
        self.assertIn("kept your agent_bridge.py as agent_bridge.py.bak-agent-bridge", out)
        self.assertIn("Diaulos tools for Mistral Vibe", self.shim_text(vibe_home))
        self.run_vibe_once(vibe_home)
        out = self.run_script("install.sh", env=env)  # our own shim is never taken as a backup
        self.assertNotIn("kept your", out)
        with open(os.path.join(tools, "agent_bridge.py.bak-agent-bridge")) as f:
            self.assertEqual(f.read(), OTHER)
        out = self.run_script("uninstall.sh", env=env)
        self.assertIn("restored your", out)
        self.assert_same(before, vibe_home)

    def test_a_vibe_home_with_glob_characters(self):
        # The .pyc is found with glob: `[` in VIBE_HOME must be taken literally (glob.escape).
        vibe_home = os.path.join(self.tmp, "vi[b]e home")
        os.makedirs(os.path.join(vibe_home, "tools", "__pycache__"))
        before = self.snapshot(vibe_home)
        env = {"VIBE_HOME": vibe_home}
        self.run_script("install.sh", env=env)
        self.run_vibe_once(vibe_home)
        self.run_script("uninstall.sh", env=env)
        self.assert_same(before, vibe_home)

    def test_vibe_on_path_without_vibe_home(self):
        fake_vibe = os.path.join(self.bin, "vibe")
        with open(fake_vibe, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(fake_vibe, 0o755)
        vibe_home = os.path.join(self.home, ".vibe")
        before = self.snapshot(vibe_home)
        self.run_script("install.sh")
        self.assertTrue(os.path.exists(os.path.join(vibe_home, "tools", "agent_bridge.py")))
        self.run_vibe_once(vibe_home)
        self.run_script("uninstall.sh")
        self.assert_same(before, vibe_home)

    def test_no_vibe_nothing_written(self):
        out = self.run_script("install.sh")
        self.assertIn("vibe        -> not found", out)
        self.assertIn(RESTART_VIBE, out)  # vibe installed later: the installer must run again
        self.assertFalse(os.path.exists(os.path.join(self.home, ".vibe")))
        self.run_script("uninstall.sh")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".vibe")))

    def test_purge_keeps_vibe_sessions(self):
        vibe_home = os.path.join(self.home, ".vibe")
        session = os.path.join(vibe_home, "logs", "session", "session_1_abcd")
        os.makedirs(session)
        with open(os.path.join(session, "messages.jsonl"), "w") as f:
            f.write("{}\n")
        before = self.snapshot(vibe_home)
        self.run_script("install.sh")
        self.run_vibe_once(vibe_home)
        self.run_script("uninstall.sh", "--purge")
        self.assertFalse(os.path.exists(self.bridge_home))
        self.assert_same(before, vibe_home)

    def test_windows_path_in_the_shim_stays_literal(self):
        spec = importlib.util.spec_from_file_location("vibe_install_under_test",
                                                      os.path.join(ROOT, "install", "vibe_install.py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        vibe_home = os.path.join(self.tmp, "v")
        os.makedirs(vibe_home)
        # A folder name with backslashes, \U and \n in it, as in C:\Users\<account>\.agent-bridge
        bridge = os.path.join(self.tmp, r"C:\Users\name\.agent-bridge")
        with contextlib.redirect_stdout(io.StringIO()):
            module.install(ROOT, bridge, vibe_home, False)
        line = [ln for ln in self.shim_text(vibe_home).splitlines() if ln.startswith("BRIDGE_DIR = ")][0]
        value = ast.literal_eval(line.split(" = ", 1)[1].split("  #")[0])
        self.assertEqual(value, os.path.abspath(bridge))
        self.assertIn("\\Users\\name\\", value)


if __name__ == "__main__":
    unittest.main()

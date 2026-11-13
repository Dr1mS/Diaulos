"""install.sh and uninstall.sh for Mistral Vibe 3 (install/vibe_install.py, docs/VIBE-DESIGN.md §8.2,
§8.7, §8.9.3, task T14).

In a temporary HOME and VIBE_HOME, with a PATH that has no `claude` and a fake `vibe` whose
`--version` says FAKE_VERSION: install, check the plugin, the config.toml block and vibe-diaulos,
uninstall, then `diff -r` against the copy of VIBE_HOME made before the install. The only file
left is the backup config.toml.bak-agent-bridge (every edited file is backed up first, and the
backup stays); config.toml itself is the same, byte for byte.
Linux and macOS only (bash).
"""
import contextlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from helpers import ROOT, WINDOWS

RERUN = "If you install vibe later, run this installer again."
BACKUP = "config.toml.bak-agent-bridge"
CONFIG = ('active_model = "devstral-small"\n\n[[mcp_servers]]\nname = "other"\ntransport = "stdio"\n'
          'command = "other-server"\n')
SHIM = '"""Diaulos tools for Mistral Vibe (vibe 2.20)."""\n'
OTHER = "# someone else's vibe tool\nprint('mine')\n"


def load_installer():
    spec = importlib.util.spec_from_file_location("vibe_install_under_test",
                                                  os.path.join(ROOT, "install", "vibe_install.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
        self.vibe_home = os.path.join(self.home, ".vibe")

    def fake_vibe(self, version="3.1.2"):
        """A `vibe` console script on PATH: its #! line names a Python, as uv's does."""
        path = os.path.join(self.bin, "vibe")
        with open(path, "w") as f:
            f.write("#!%s\nprint('vibe %s')\n" % (sys.executable, version))
        os.chmod(path, 0o755)

    def run_script(self, name, *args, env=None, check=True):
        proc = subprocess.run(["/bin/bash", os.path.join(ROOT, name), *args], capture_output=True, text=True,
                              env=dict(self.env, **(env or {})), timeout=60)
        if check:
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def snapshot(self):
        copy = os.path.join(self.tmp, "before")
        if os.path.exists(self.vibe_home):
            shutil.copytree(self.vibe_home, copy, symlinks=True)
        return copy

    def assert_same(self, before, backup_left=True):
        if not os.path.exists(before):
            if os.path.exists(self.vibe_home):
                self.fail(f"{self.vibe_home} left behind: {os.listdir(self.vibe_home)}")
            return
        proc = subprocess.run(["diff", "-r", before, self.vibe_home], capture_output=True, text=True,
                              env=dict(os.environ, LC_ALL="C"))
        lines = proc.stdout.splitlines()
        if backup_left:
            self.assertIn(f"Only in {self.vibe_home}: {BACKUP}", lines)
            lines.remove(f"Only in {self.vibe_home}: {BACKUP}")
        self.assertEqual(lines, [], proc.stdout)

    def write_config(self, text=CONFIG):
        os.makedirs(self.vibe_home, exist_ok=True)
        with open(os.path.join(self.vibe_home, "config.toml"), "w") as f:
            f.write(text)

    def read(self, *parts):
        with open(os.path.join(*parts), encoding="utf-8") as f:
            return f.read()

    # ------------------------------------------------------------------ tests

    def test_install_then_uninstall(self):
        self.fake_vibe()
        self.write_config()
        session = os.path.join(self.vibe_home, "logs", "session", "unified", "aaaa-0123456789ab")
        os.makedirs(session)
        with open(os.path.join(session, "meta.json"), "w") as f:
            f.write("{}\n")
        before = self.snapshot()
        out = self.run_script("install.sh")
        plugin = os.path.join(self.vibe_home, "plugins", "diaulos")
        self.assertIn(f"vibe        -> {plugin}", out)
        self.assertIn("Restart open vibe TUIs", out)
        # The plugin: launcher filled in and executable, hooks filled in, manifests as in the repository.
        launcher = self.read(plugin, "vibe-mcp")
        self.assertNotIn("@BRIDGE_HOME@", launcher)
        self.assertNotIn("@PYTHON@", launcher)
        self.assertIn(os.path.join(self.bridge_home, "vibe_mcp.py"), launcher)
        self.assertTrue(os.stat(os.path.join(plugin, "vibe-mcp")).st_mode & stat.S_IXUSR)
        hooks = self.read(plugin, "ai.mistral.vibe", "hooks.toml")
        self.assertNotIn("@HOOK@", hooks)
        for event in ("session_start", "pre_agent"):
            self.assertIn(f"{os.path.join(self.bridge_home, 'vibe_hook.py')} {event}", hooks)
        self.assertNotIn("post_agent", hooks)
        for name in ("plugin.json", "mcp.json"):
            self.assertEqual(json.loads(self.read(plugin, name)),
                             json.loads(self.read(ROOT, "install", "vibe-plugin", name)))
        self.assertTrue(os.path.exists(os.path.join(plugin, "skills", "diaulos", "SKILL.md")))
        # The config.toml block, once, after the person's own text; the backup holds that text.
        config = self.read(self.vibe_home, "config.toml")
        self.assertTrue(config.startswith(CONFIG), config)
        self.assertEqual(config.count("[tools.diaulos_send]"), 1)
        self.assertEqual(config.count("[tools.diaulos_list_agents]"), 1)
        self.assertEqual(self.read(self.vibe_home, BACKUP), CONFIG)
        # vibe-diaulos and the bridge files it runs.
        command = os.path.join(self.bridge_home, "vibe-diaulos")
        self.assertTrue(os.stat(command).st_mode & stat.S_IXUSR)
        self.assertIn("vibe_diaulos.py", self.read(command))
        for name in ("vibe_relay.py", "vibe_diaulos.py", "vibe_mcp.py", "vibe_hook.py", "claude-channel.sh"):
            self.assertTrue(os.path.exists(os.path.join(self.bridge_home, name)), name)
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "vibe_plugin.py")))
        state = json.loads(self.read(self.bridge_home, "state", "vibe-install.json"))
        self.assertEqual((state["vibe_version"], state["vibe_python"]), ("3.1.2", sys.executable))
        # Twice: still one block.
        self.run_script("install.sh")
        self.assertEqual(self.read(self.vibe_home, "config.toml").count("[tools.diaulos_send]"), 1)
        self.run_script("uninstall.sh")
        self.assertFalse(os.path.exists(command))
        self.assert_same(before)

    def test_vibe_on_path_without_vibe_home(self):
        self.fake_vibe()
        before = self.snapshot()
        self.run_script("install.sh")
        self.assertTrue(os.path.isdir(os.path.join(self.vibe_home, "plugins", "diaulos")))
        self.run_script("uninstall.sh")
        self.assert_same(before, backup_left=False)

    def test_vibe_below_3_is_skipped_and_its_shim_removed(self):
        self.fake_vibe("2.20.0")
        tools = os.path.join(self.vibe_home, "tools")
        os.makedirs(tools)
        before = self.snapshot()
        with open(os.path.join(tools, "agent_bridge.py"), "w") as f:
            f.write(SHIM)  # left by the vibe 2.20 installer
        out = self.run_script("install.sh")
        self.assertIn("Diaulos needs Mistral Vibe 3 or later: run `vibe update` (found 2.20.0): skipped", out)
        self.assertIn("removed the vibe 2.20 tools", out)
        self.assertFalse(os.path.exists(os.path.join(self.vibe_home, "plugins")))
        self.assertFalse(os.path.exists(os.path.join(self.vibe_home, "config.toml")))
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "vibe-diaulos")))
        self.run_script("uninstall.sh")
        self.assert_same(before, backup_left=False)

    def test_the_old_shim_goes_and_the_persons_own_file_comes_back(self):
        self.fake_vibe()
        tools = os.path.join(self.vibe_home, "tools")
        os.makedirs(tools)
        with open(os.path.join(tools, "agent_bridge.py"), "w") as f:
            f.write(OTHER)
        before = self.snapshot()
        os.replace(os.path.join(tools, "agent_bridge.py"), os.path.join(tools, "agent_bridge.py.bak-agent-bridge"))
        with open(os.path.join(tools, "agent_bridge.py"), "w") as f:
            f.write(SHIM)  # the 2.20 install had saved the person's file
        out = self.run_script("install.sh")
        self.assertIn("restored your", out)
        self.assertEqual(self.read(tools, "agent_bridge.py"), OTHER)
        self.run_script("uninstall.sh")
        self.assertEqual(self.read(tools, "agent_bridge.py"), OTHER)
        self.assert_same(before, backup_left=False)

    def test_a_server_named_diaulos_stops_the_vibe_part(self):
        # vibe would disable the plugin's server (MCP_SERVER_SHADOWED); names compared as vibe normalizes them.
        self.fake_vibe()
        self.write_config(CONFIG + '\n[[mcp_servers]]\nname = "diaulos"\ntransport = "stdio"\ncommand = "x"\n')
        before = self.snapshot()
        out = self.run_script("install.sh")
        self.assertIn("Mistral Vibe already has an MCP server named diaulos", out)
        self.assertIn("MCP_SERVER_SHADOWED", out)
        self.assertFalse(os.path.exists(os.path.join(self.vibe_home, "plugins")))
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "vibe-diaulos")))
        self.assert_same(before, backup_left=False)

    def test_a_tools_table_not_ours_stops_the_vibe_part(self):
        self.fake_vibe()
        self.write_config(CONFIG + '\n[tools.diaulos_send]\npermission = "ask"\n')
        before = self.snapshot()
        out = self.run_script("install.sh")
        self.assertIn("config.toml already has a [tools.diaulos_send] table", out)
        self.assert_same(before, backup_left=False)

    def test_a_plugin_folder_not_ours_is_left_alone(self):
        self.fake_vibe()
        mine = os.path.join(self.vibe_home, "plugins", "diaulos")
        os.makedirs(mine)
        with open(os.path.join(mine, "plugin.json"), "w") as f:
            json.dump({"name": "diaulos", "description": "someone else's"}, f)
        before = self.snapshot()
        out = self.run_script("install.sh")
        self.assertIn("is not the Diaulos plugin", out)
        self.run_script("uninstall.sh")
        self.assert_same(before, backup_left=False)

    def test_a_failed_vibe_step_stops_the_installer(self):
        self.fake_vibe()
        vibe_home = os.path.join(self.tmp, "vibe-is-a-file")
        with open(vibe_home, "w") as f:
            f.write("x\n")  # vibe on PATH, VIBE_HOME a file: the vibe step cannot write in it
        proc = subprocess.run(["/bin/bash", os.path.join(ROOT, "install.sh")], capture_output=True, text=True,
                              env=dict(self.env, VIBE_HOME=vibe_home), timeout=60)
        self.assertNotEqual(proc.returncode, 0, proc.stdout)
        self.assertNotIn("Done.", proc.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.home, ".claude")), "steps after vibe ran")

    def test_no_vibe_nothing_written(self):
        out = self.run_script("install.sh")
        self.assertIn("vibe        -> not found", out)
        self.assertIn(RERUN, out)
        self.assertFalse(os.path.exists(self.vibe_home))
        self.run_script("uninstall.sh")
        self.assertFalse(os.path.exists(self.vibe_home))

    def test_shell_bashrc_created_by_install_goes_when_empty(self):
        bashrc = os.path.join(self.home, ".bashrc")
        self.run_script("install.sh", "--shell")
        with open(bashrc) as f:
            self.assertIn("claude-channel.sh", f.read())
        out = self.run_script("uninstall.sh")
        self.assertIn("removed ~/.bashrc (created by install, empty)", out)
        self.assertFalse(os.path.exists(bashrc))
        self.assertFalse(os.path.exists(os.path.join(self.bridge_home, "state", "bashrc.created")))

    def test_shell_bashrc_created_by_install_stays_when_the_person_wrote_in_it(self):
        bashrc = os.path.join(self.home, ".bashrc")
        self.run_script("install.sh", "--shell")
        with open(bashrc, "a") as f:
            f.write("alias ll='ls -l'\n")
        self.run_script("uninstall.sh")
        with open(bashrc) as f:
            self.assertEqual(f.read().strip(), "alias ll='ls -l'")

    def test_shell_existing_bashrc_stays_even_if_empty(self):
        bashrc = os.path.join(self.home, ".bashrc")
        for content in ("export A=1\n", ""):
            with open(bashrc, "w") as f:
                f.write(content)
            self.run_script("install.sh", "--shell")
            self.run_script("uninstall.sh")
            with open(bashrc) as f:
                self.assertEqual(f.read().strip(), content.strip())

    def test_purge_keeps_vibe_sessions(self):
        self.fake_vibe()
        session = os.path.join(self.vibe_home, "logs", "session", "unified", "aaaa-0123456789ab")
        os.makedirs(session)
        with open(os.path.join(session, "meta.json"), "w") as f:
            f.write("{}\n")
        before = self.snapshot()
        self.run_script("install.sh")
        self.run_script("uninstall.sh", "--purge")
        self.assertFalse(os.path.exists(self.bridge_home))
        self.assert_same(before, backup_left=False)

    def test_paths_with_quotes_and_spaces_in_the_launcher_and_hooks(self):
        module = load_installer()
        bridge = os.path.join(self.tmp, "it's a bridge")
        target = os.path.join(self.tmp, "plugin")
        python = os.path.join(self.tmp, "py thon", "python3")
        with contextlib.redirect_stdout(io.StringIO()):
            module.write_plugin(ROOT, target, python, bridge)
        launcher = os.path.join(target, "vibe-mcp")
        proc = subprocess.run(["/bin/sh", "-n", launcher], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        # The hook line, run by a shell, gives the paths back unchanged.
        text = self.read(target, "ai.mistral.vibe", "hooks.toml")
        line = [l for l in text.splitlines() if l.startswith("command") and "session_start" in l][0]
        command = json.loads(line.split("=", 1)[1].strip())
        echo = command.replace(module.hook_command(python, bridge), "printf '%s\\n' " + module.hook_command(python, bridge))
        out = subprocess.run(["/bin/sh", "-c", echo], capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(out[0], "AGENT_BRIDGE_HOME=" + bridge)
        self.assertEqual(out[1:], [python, os.path.join(bridge, "vibe_hook.py"), "session_start"])

    def test_the_shell_function_reaches_vibe_diaulos(self):
        folder = os.path.join(self.tmp, "a dir \"quoted\"")
        os.makedirs(folder)
        shutil.copy(os.path.join(ROOT, "install", "claude-channel.sh"), folder)
        with open(os.path.join(folder, "vibe_diaulos.py"), "w") as f:
            f.write("import sys; print('diaulos', sys.argv[1:])\n")
        script = '. "$1"; vibe --resume "a b"; AGENT_BRIDGE_VIBE_RELAY=0 vibe -v'
        with open(os.path.join(self.bin, "vibe"), "w") as f:
            f.write("#!/bin/sh\necho real \"$@\"\n")
        os.chmod(os.path.join(self.bin, "vibe"), 0o755)
        proc = subprocess.run(["/bin/bash", "-c", script, "x", os.path.join(folder, "claude-channel.sh")],
                              capture_output=True, text=True, env=dict(self.env, PATH=self.bin + ":/usr/bin:/bin"))
        self.assertEqual(proc.stdout.splitlines(), ["diaulos ['--resume', 'a b']", "real -v"], proc.stderr)


if __name__ == "__main__":
    unittest.main()

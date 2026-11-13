#!/usr/bin/env python3
"""Throwaway environment for the T14 gates (G7, R1 to R9; docs/VIBE-DESIGN.md §8.8, §8.9.9).

    python3 tests/vibe3/t14_env.py OUT

OUT/vh     VIBE_HOME: config.toml made from the person's ~/.vibe/config.toml (read only, never
           written) with devstral-small, no update checks, no `mcp_servers = []` line, save_dir
           under OUT/vh; then the Diaulos plugin and the config.toml block, written by
           install/vibe_install.py exactly as the installer does.
OUT/ab     AGENT_BRIDGE_HOME: the bridge files of this repository (bridge/*.py).
OUT/home   HOME for the gates, when vibe runs with it (env_of(out, home=True)).
OUT/work   working directory of the sessions.

Nothing outside OUT is written: not ~/.vibe, not ~/.agent-bridge, not the real `vibe` launcher.
"""
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BRIDGE_FILES = ("bridge.py", "mcp_channel.py", "remote.py", "vibe_sessions.py", "vibe_runner.py",
                "vibe_mcp.py", "vibe_hook.py", "vibe_relay.py", "vibe_diaulos.py")


def setup(out):
    vh, ab = os.path.join(out, "vh"), os.path.join(out, "ab")
    for d in (vh, ab, os.path.join(out, "home"), os.path.join(out, "work")):
        os.makedirs(d, exist_ok=True)
    with open(os.path.expanduser("~/.vibe/config.toml"), encoding="utf-8") as f:
        config = f.read()
    config = re.sub(r'(?m)^active_model = ".*"$', 'active_model = "devstral-small"', config)
    config = re.sub(r"(?m)^enable_update_checks = .*$", "enable_update_checks = false", config)
    config = re.sub(r"(?m)^mcp_servers = \[\]\n", "", config)
    config = re.sub(r'(?m)^save_dir = ".*"$', 'save_dir = "%s"' % os.path.join(vh, "logs", "session"), config)
    with open(os.path.join(vh, "config.toml"), "w", encoding="utf-8") as f:
        f.write(config)
    os.chmod(os.path.join(vh, "config.toml"), 0o600)
    for name in BRIDGE_FILES:
        src = os.path.join(REPO, "bridge", name)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(ab, name))
    proc = subprocess.run([sys.executable, os.path.join(REPO, "install", "vibe_install.py"), "install", REPO, ab, vh, "1"],
                          capture_output=True, text=True, env=env_of(out))
    return proc.returncode, proc.stdout.replace(out, "<jetable>"), proc.stderr.replace(out, "<jetable>")


def env_of(out, home=False):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_BRIDGE_", "VIBE_APP_SERVER"))}
    env["VIBE_HOME"] = os.path.join(out, "vh")
    env["AGENT_BRIDGE_HOME"] = os.path.join(out, "ab")
    if home:
        env["HOME"] = os.path.join(out, "home")
    return env


if __name__ == "__main__":
    print(setup(os.path.abspath(sys.argv[1])))

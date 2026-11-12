"""Install or remove the Diaulos tools for Mistral Vibe (docs/VIBE-DESIGN.md §5).
Python 3.8+, standard library only. install.sh, uninstall.sh, install.ps1 and
uninstall.ps1 all call this file, so Linux and Windows share one logic.

  vibe_install.py install   <repo> <bridge home> <vibe home> <vibe on PATH: 0|1>
  vibe_install.py uninstall <bridge home> <vibe home>

install writes <vibe home>/tools/agent_bridge.py from install/vibe-tool.py, with the
absolute bridge home filled in. It does nothing if vibe is not on PATH and <vibe home>
does not exist. It never edits a vibe config file. A different agent_bridge.py that is
not ours is saved first as agent_bridge.py.bak-agent-bridge. What it created (folders,
backup) and whether tools/__pycache__ existed is kept in <bridge home>/state/vibe-install.json,
so uninstall puts <vibe home> back as it was.

uninstall removes our agent_bridge.py and the agent_bridge*.pyc that vibe's import leaves,
restores the backup, then removes tools/__pycache__, tools/ and <vibe home> only when they
are empty and did not exist before the install.
"""
import glob
import json
import os
import shutil
import sys

MARK = "Diaulos tools for Mistral Vibe"
NAME = "agent_bridge.py"
BACKUP = NAME + ".bak-agent-bridge"
PLACEHOLDER = '"@AGENT_BRIDGE_HOME@"'


def _state_path(bridge_home):
    return os.path.join(bridge_home, "state", "vibe-install.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp-agent-bridge"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def _ours(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return MARK in f.read()
    except OSError:
        return False


def install(repo, bridge_home, vibe_home, vibe_on_path):
    bridge_home, vibe_home = os.path.abspath(bridge_home), os.path.abspath(vibe_home)
    if not vibe_on_path and not os.path.isdir(vibe_home):
        print("vibe        -> not found (no vibe on PATH, no %s): skipped" % vibe_home)
        return
    with open(os.path.join(repo, "install", "vibe-tool.py"), encoding="utf-8") as f:
        shim = f.read()
    if PLACEHOLDER not in shim:
        sys.exit("install/vibe-tool.py has no %s placeholder" % PLACEHOLDER)
    shim = shim.replace(PLACEHOLDER, repr(bridge_home), 1)  # repr: Windows backslashes stay literal

    state_file = _state_path(bridge_home)
    state = _load(state_file)
    if state.get("vibe_home") != vibe_home:  # a first install, or another VIBE_HOME
        state = {"vibe_home": vibe_home, "created": [], "backup": False}
    tools = os.path.join(vibe_home, "tools")
    if "pycache_existed" not in state:
        state["pycache_existed"] = os.path.isdir(os.path.join(tools, "__pycache__"))
    for folder in (vibe_home, tools):
        if not os.path.isdir(folder):
            os.makedirs(folder)
            state["created"].append(folder)
    target = os.path.join(tools, NAME)
    if os.path.exists(target) and not _ours(target) and not os.path.exists(os.path.join(tools, BACKUP)):
        shutil.copy2(target, os.path.join(tools, BACKUP))
        state["backup"] = True
        print("vibe        -> kept your %s as %s" % (NAME, BACKUP))
    _write(target, shim)
    _write(state_file, json.dumps(state, indent=2) + "\n")
    print("vibe        -> %s (tools bridge_send, bridge_list_agents)" % target)


def uninstall(bridge_home, vibe_home):
    bridge_home = os.path.abspath(bridge_home)
    state_file = _state_path(bridge_home)
    state = _load(state_file)
    vibe_home = state.get("vibe_home") or os.path.abspath(vibe_home)
    tools = os.path.join(vibe_home, "tools")
    target = os.path.join(tools, NAME)
    if os.path.exists(target) and _ours(target):
        os.remove(target)
        print("removed vibe tools %s" % target)
    pycache = os.path.join(tools, "__pycache__")
    for pyc in glob.glob(os.path.join(glob.escape(pycache), "agent_bridge*.pyc")):
        os.remove(pyc)
    backup = os.path.join(tools, BACKUP)
    if os.path.exists(backup) and not os.path.exists(target):
        os.replace(backup, target)
        print("restored your %s" % target)
    if not state.get("pycache_existed", True) and os.path.isdir(pycache) and not os.listdir(pycache):
        os.rmdir(pycache)
    for folder in reversed(state.get("created") or []):
        if os.path.isdir(folder) and not os.listdir(folder):
            os.rmdir(folder)
    if os.path.exists(state_file):
        os.remove(state_file)


def main(argv):
    if len(argv) == 6 and argv[1] == "install":
        install(argv[2], argv[3], argv[4], argv[5] == "1")
    elif len(argv) == 4 and argv[1] == "uninstall":
        uninstall(argv[2], argv[3])
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)

"""Install or remove the Diaulos plugin for Mistral Vibe 3 (docs/VIBE-DESIGN.md §8.2, §8.5, §8.7, §8.9.3).
Python 3.8+, standard library only. install.sh, uninstall.sh, install.ps1 and uninstall.ps1
all call this file, so Linux and Windows share one logic.

  vibe_install.py install   <repo> <bridge home> <vibe home> <vibe on PATH: 0|1>
  vibe_install.py uninstall <bridge home> <vibe home>

install:
  - does nothing if vibe is not on PATH and <vibe home> does not exist;
  - removes the vibe 2.20 shim <vibe home>/tools/agent_bridge.py when it is ours (and puts back
    the person's own file that it had saved), whatever the vibe version;
  - reads `vibe --version`: below 3, says "Diaulos needs Mistral Vibe 3 or later" and stops there;
  - stops the vibe part, writing nothing, when config.toml already declares an MCP server named
    diaulos (vibe would disable ours: MCP_SERVER_SHADOWED) or a [tools.diaulos_send] or
    [tools.diaulos_list_agents] table that is not ours;
  - backs up config.toml as config.toml.bak-agent-bridge, then writes one marked block that gives
    both tools `permission = "always"`;
  - copies install/vibe-plugin/ to <vibe home>/plugins/diaulos/, with the launcher vibe-mcp and the
    hook commands filled in with the absolute path of this Python and of <bridge home>;
  - writes the command <bridge home>/vibe-diaulos (vibe-diaulos.cmd on Windows), which runs
    vibe_diaulos.py: vibe's TUI behind the Diaulos relay (§8.9.3);
  - keeps in <bridge home>/state/vibe-install.json what uninstall needs, and vibe's own Python and
    its vibe-app-server, for vibe-diaulos.
uninstall removes the plugin folder, the marked block (after a backup), vibe-diaulos, the old shim,
and the folders the install created when they are empty. It never touches vibe sessions.
"""
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys

WINDOWS = os.name == "nt"
PLUGIN = "diaulos"
PLUGIN_MARK = "Diaulos:"  # start of our plugin.json description
SHIM_MARK = "Diaulos tools for Mistral Vibe"
SHIM = "agent_bridge.py"
SHIM_BACKUP = SHIM + ".bak-agent-bridge"
CONFIG_BACKUP = "config.toml.bak-agent-bridge"
BLOCK_START = "# >>> diaulos (written by the Diaulos installer; removed by the uninstaller) >>>"
BLOCK_END = "# <<< diaulos <<<"
BLOCK = (BLOCK_START + "\n[tools.diaulos_send]\npermission = \"always\"\n\n"
         "[tools.diaulos_list_agents]\npermission = \"always\"\n" + BLOCK_END + "\n")
NEEDS_3 = "Diaulos needs Mistral Vibe 3 or later: run `vibe update`"


class Stop(Exception):
    """The vibe part stops here, with this reason; the rest of the install goes on."""


def _state_path(bridge_home):
    return os.path.join(bridge_home, "state", "vibe-install.json")


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp-agent-bridge"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


# ---------------------------------------------------------------- vibe itself

def vibe_version(vibe):
    """(major, minor, patch) from `vibe --version` ("vibe 3.1.2"), or None."""
    try:
        out = subprocess.run([vibe, "--version"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out or "")
    return tuple(int(x or 0) for x in m.groups()) if m else None


def vibe_python(vibe):
    """The Python of the mistral-vibe tool: the first line of the `vibe` console script on Linux and
    macOS, <tool>/Scripts/python.exe on Windows (uv's layout). None if not found."""
    real = os.path.realpath(vibe)
    if WINDOWS:
        for base in (os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA")):
            if base:
                path = os.path.join(base, "uv", "tools", "mistral-vibe", "Scripts", "python.exe")
                if os.path.exists(path):
                    return path
        return None
    try:
        with open(real, "rb") as f:
            first = f.readline().decode("utf-8", "replace").strip()
    except OSError:
        return None
    if first.startswith("#!"):
        path = first[2:].strip().split(" ")[0]
        if os.path.isabs(path) and os.path.exists(path):
            return path
    return None


def app_server_of(python):
    """vibe-app-server next to vibe's Python, as vibe's launcher finds it."""
    path = os.path.join(os.path.dirname(python), "vibe-app-server.exe" if WINDOWS else "vibe-app-server")
    return path if os.path.exists(path) else None


# ---------------------------------------------------------------- config.toml

def normalize(name):
    """vibe's normalize_mcp_server_name (src core/config/models.py)."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name or "").strip("_-")[:256]


def without_block(text):
    """config.toml text without our marked block (and the blank line before it)."""
    start = text.find(BLOCK_START)
    if start < 0:
        return text
    end = text.find(BLOCK_END, start)
    end = len(text) if end < 0 else end + len(BLOCK_END)
    if text[end:end + 1] == "\n":
        end += 1
    head = text[:start]
    if head.endswith("\n\n"):
        head = head[:-1]
    return head + text[end:]


def mcp_server_names(text):
    """Names of the MCP servers that config.toml declares, normalized as vibe does."""
    names = []
    try:
        import tomllib  # Python 3.11+
        try:
            servers = tomllib.loads(text).get("mcp_servers") or []
            return [normalize(s.get("name")) for s in servers if isinstance(s, dict) and isinstance(s.get("name"), str)]
        except tomllib.TOMLDecodeError:
            pass
    except ImportError:
        pass
    table = None
    for raw in text.splitlines():  # [[mcp_servers]] blocks and inline tables, line by line
        line = raw.strip()
        if line.startswith("["):
            table = line
            continue
        if table == "[[mcp_servers]]":
            m = re.match(r"""^name\s*=\s*["']([^"']*)["']""", line)
            if m:
                names.append(normalize(m.group(1)))
        if line.startswith("mcp_servers"):
            names.extend(normalize(n) for n in re.findall(r"""name\s*=\s*["']([^"']*)["']""", line))
    return names


def check_config(text):
    """Raise Stop when our plugin's server or our tool tables would clash with config.toml."""
    if PLUGIN in mcp_server_names(text):
        raise Stop("Mistral Vibe already has an MCP server named diaulos: rename or remove it, then run "
                   "the installer again (otherwise vibe disables the Diaulos server: MCP_SERVER_SHADOWED)")
    rest = without_block(text)
    for table in ("tools.diaulos_send", "tools.diaulos_list_agents"):
        if re.search(r'(?m)^\s*\[\s*(%s|tools\."%s")\s*\]' % (re.escape(table), re.escape(table[6:])), rest):
            raise Stop("config.toml already has a [%s] table that the Diaulos installer did not write: remove it, "
                       "then run the installer again" % table)


def write_block(config):
    text = _read(config) or ""
    if os.path.exists(config):
        shutil.copy2(config, os.path.join(os.path.dirname(config), CONFIG_BACKUP))
    text = without_block(text)
    if text and not text.endswith("\n"):
        text += "\n"
    _write(config, text + ("\n" if text else "") + BLOCK)


def remove_block(config, created=False):
    """Remove our block from config.toml. Returns None if there was none, "removed" (after a backup),
    or "deleted" when the install created config.toml and only our block is left in it: the file
    goes, and so does a backup that holds nothing else."""
    text = _read(config)
    if text is None or BLOCK_START not in text:
        return None
    rest = without_block(text)
    if created and not rest.strip():
        os.remove(config)
        backup = os.path.join(os.path.dirname(config), CONFIG_BACKUP)
        if os.path.exists(backup) and not without_block(_read(backup) or "").strip():
            os.remove(backup)
        return "deleted"
    shutil.copy2(config, os.path.join(os.path.dirname(config), CONFIG_BACKUP))
    _write(config, rest)
    return "removed"


# ---------------------------------------------------------------- the plugin

def _ours(plugin_dir):
    data = _load(os.path.join(plugin_dir, "plugin.json"))
    return data.get("name") == PLUGIN and str(data.get("description", "")).startswith(PLUGIN_MARK)


def _toml_string(text):
    return json.dumps(text, ensure_ascii=False)  # a TOML basic string accepts JSON's escapes


def hook_command(python, bridge_home):
    hook = os.path.join(bridge_home, "vibe_hook.py")
    if WINDOWS:  # Git Bash or PowerShell (skill 1033): untested
        return '"%s" "%s"' % (python, hook)
    return "AGENT_BRIDGE_HOME=%s %s %s" % (shlex.quote(bridge_home), shlex.quote(python), shlex.quote(hook))


def write_plugin(repo, target, python, bridge_home):
    source = os.path.join(repo, "install", "vibe-plugin")
    if os.path.isdir(target):
        shutil.rmtree(target)
    shutil.copytree(source, target)
    launcher = os.path.join(target, "vibe-mcp")
    text = _read(launcher)
    q = shlex.quote
    _write(launcher, text.replace("@BRIDGE_HOME@/vibe_mcp.py", q(os.path.join(bridge_home, "vibe_mcp.py")))
           .replace("@BRIDGE_HOME@", q(bridge_home)).replace("@PYTHON@", q(python)), 0o755)
    if WINDOWS:  # mcp.json runs ./vibe-mcp.cmd (gate G7: not tried on Windows)
        _write(os.path.join(target, "vibe-mcp.cmd"),
               '@echo off\r\nset "AGENT_BRIDGE_HOME=%s"\r\n"%s" "%s" %%*\r\n'
               % (bridge_home, python, os.path.join(bridge_home, "vibe_mcp.py")))
        mcp_path = os.path.join(target, "mcp.json")
        _write(mcp_path, _read(mcp_path).replace('"./vibe-mcp"', '"./vibe-mcp.cmd"'))
    hooks = os.path.join(target, "ai.mistral.vibe", "hooks.toml")
    command = hook_command(python, bridge_home)
    text = _read(hooks)
    for event in ("session_start", "pre_agent"):
        text = text.replace('"@HOOK@ %s"' % event, _toml_string("%s %s" % (command, event)))
    _write(hooks, text)


def write_command(bridge_home, python):
    """<bridge home>/vibe-diaulos: the command the README shows, and the shell function's target."""
    script = os.path.join(bridge_home, "vibe_diaulos.py")
    if WINDOWS:  # not tried on Windows (§8.9.9)
        path = os.path.join(bridge_home, "vibe-diaulos.cmd")
        _write(path, '@echo off\r\n"%s" "%s" %%*\r\n' % (python, script))
        return path
    path = os.path.join(bridge_home, "vibe-diaulos")
    _write(path, '#!/bin/sh\n# vibe with the Diaulos relay (docs/VIBE-DESIGN.md §8.9.3), written by the Diaulos installer.\n'
           'exec %s %s "$@"\n' % (shlex.quote(python), shlex.quote(script)), 0o755)
    return path


def remove_command(bridge_home):
    for name in ("vibe-diaulos", "vibe-diaulos.cmd"):
        path = os.path.join(bridge_home, name)
        if os.path.exists(path):
            os.remove(path)


# ---------------------------------------------------------------- the 2.20 shim

def remove_shim(vibe_home, state):
    tools = os.path.join(vibe_home, "tools")
    target = os.path.join(tools, SHIM)
    removed = False
    if os.path.exists(target) and SHIM_MARK in (_read(target) or ""):
        os.remove(target)
        removed = True
        print("vibe        -> removed the vibe 2.20 tools %s" % target)
    pycache = os.path.join(tools, "__pycache__")
    for pyc in glob.glob(os.path.join(glob.escape(pycache), "agent_bridge*.pyc")):
        os.remove(pyc)
    backup = os.path.join(tools, SHIM_BACKUP)
    if os.path.exists(backup) and not os.path.exists(target):
        os.replace(backup, target)
        print("vibe        -> restored your %s" % target)
    if not state.get("pycache_existed", True) and os.path.isdir(pycache) and not os.listdir(pycache):
        os.rmdir(pycache)
    for key in ("pycache_existed", "backup"):
        state.pop(key, None)
    if os.path.isdir(tools) and not os.listdir(tools) and tools in (state.get("created") or []):
        os.rmdir(tools)
        state["created"].remove(tools)
    return removed


# ---------------------------------------------------------------- install, uninstall

def install(repo, bridge_home, vibe_home, vibe_on_path):
    bridge_home, vibe_home = os.path.abspath(bridge_home), os.path.abspath(vibe_home)
    if not vibe_on_path and not os.path.isdir(vibe_home):
        print("vibe        -> not found (no vibe on PATH, no %s): skipped" % vibe_home)
        return
    state_file = _state_path(bridge_home)
    state = _load(state_file)
    if state.get("vibe_home") != vibe_home:  # a first install, or another VIBE_HOME
        state = {"vibe_home": vibe_home, "created": [], **{k: v for k, v in state.items() if k == "pycache_existed"}}
    state.setdefault("created", [])
    remove_shim(vibe_home, state)
    _write(state_file, json.dumps(state, indent=2) + "\n")
    vibe = shutil.which("vibe")
    if not vibe:
        print("vibe        -> the vibe command is not on PATH: skipped (run the installer again once it is)")
        return
    version = vibe_version(vibe)
    if not version or version[0] < 3:
        print("vibe        -> %s (found %s): skipped" % (NEEDS_3, ".".join(map(str, version)) if version else "no version"))
        return
    python = vibe_python(vibe)
    config = os.path.join(vibe_home, "config.toml")
    try:
        check_config(_read(config) or "")
    except Stop as e:
        print("vibe        -> stopped: %s" % e)
        return
    for folder in (vibe_home, os.path.join(vibe_home, "plugins")):
        if not os.path.isdir(folder):
            os.makedirs(folder)
            state["created"].append(folder)
    target = os.path.join(vibe_home, "plugins", PLUGIN)
    if os.path.isdir(target) and not _ours(target):
        print("vibe        -> stopped: %s is not the Diaulos plugin; move it away, then run the installer again" % target)
        return
    if not os.path.exists(config) and "config_created" not in state:
        state["config_created"] = True  # uninstall then deletes it, if only our block is left
    write_block(config)
    write_plugin(repo, target, sys.executable, bridge_home)
    command = write_command(bridge_home, sys.executable)
    state.update(plugin=target, config=config, vibe_version=".".join(map(str, version)), vibe_python=python,
                 app_server=app_server_of(python) if python else None, python=sys.executable)
    _write(state_file, json.dumps(state, indent=2) + "\n")
    print("vibe        -> %s (MCP server diaulos: send, list_agents; hooks session_start, pre_agent)" % target)
    print("vibe        -> %s: [tools.diaulos_send] and [tools.diaulos_list_agents] permission \"always\" "
          "(backup %s)" % (config, CONFIG_BACKUP))
    print("vibe        -> %s: vibe's TUI with the Diaulos relay, so messages wake it" % command)
    if not python:
        print("vibe        -> vibe's own Python not found: vibe-diaulos will run vibe without the relay")


def uninstall(bridge_home, vibe_home):
    bridge_home = os.path.abspath(bridge_home)
    state_file = _state_path(bridge_home)
    state = _load(state_file)
    vibe_home = state.get("vibe_home") or os.path.abspath(vibe_home)
    target = os.path.join(vibe_home, "plugins", PLUGIN)
    if os.path.isdir(target) and _ours(target):
        shutil.rmtree(target)
        print("removed vibe plugin %s" % target)
    config = os.path.join(vibe_home, "config.toml")
    done = remove_block(config, state.get("config_created", False))
    if done == "removed":
        print("removed the Diaulos block of %s (backup %s)" % (config, CONFIG_BACKUP))
    elif done == "deleted":
        print("removed %s (the install had created it)" % config)
    remove_command(bridge_home)
    remove_shim(vibe_home, state)
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

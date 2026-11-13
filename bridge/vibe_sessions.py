"""Mistral Vibe sessions, read from disk without vibe (docs/VIBE-DESIGN.md §3, §8).

Python 3.8+, standard library only. Used by bridge.py and the vibe runner, which
load this file by path, never through sys.path.

vibe 3 saves each session as <save_dir>/unified/<session id>/, with meta.json
(session_id, parent_session_id, start_time, end_time, title, title_source,
environment.working_directory), a journal, chunks and generations. A session id
is xxxxxxxx-xxxx-xxxx-xxxx-<suffix>, and the address of a conversation is
vibe:<suffix>, or vibe:<name> after /rename (title_source "manual").

vibe 3 keeps the id through /compact and --resume; /clear starts a new id with a
new suffix (probe T10-vibe3/20). A conversation is still every session with one
suffix, its current session the one saved last (latest end_time), so that a
session with an older start resumed later stays one entry. A session whose
parent has another suffix is a subagent: left out. Folders of vibe 2.20
(<save_dir>/session_*) are not read: Diaulos needs vibe 3 (§8.5).
"""
import json
import os
import re
import time
from datetime import datetime

SUFFIX_RE = re.compile(r"^[0-9a-f]{12}$")
SESSION_ID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-([0-9a-f]{12})$")


def vibe_home():
    return os.path.expanduser(os.environ.get("VIBE_HOME") or "~/.vibe")


def suffix_of(session_id):
    """The 12-hex suffix of a vibe session id, or None."""
    m = SESSION_ID_RE.match(str(session_id or ""))
    return m.group(1) if m else None


def address_of(session_id):
    suffix = suffix_of(session_id)
    return "vibe:" + suffix if suffix else None


def name_of(title):
    """A /rename title as an address part: characters outside [A-Za-z0-9_.-] become _."""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", title) if title else None


def _toml_basic_string(text):
    """Value of a TOML basic string body (between the quotes), with \\\\, \\" and \\uXXXX escapes."""
    out, i = [], 0
    simple = {"\\": "\\", '"': '"', "n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f"}
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in simple:
                out.append(simple[nxt])
                i += 2
                continue
            if nxt in "uU":
                width = 4 if nxt == "u" else 8
                out.append(chr(int(text[i + 2:i + 2 + width], 16)))
                i += 2 + width
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _save_dir_by_lines(text):
    """`save_dir` of the [session_logging] table, read line by line (Python < 3.11)."""
    table = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            table = line.split("]", 1)[0].strip("[ ")
            continue
        if table != "session_logging":
            continue
        m = re.match(r'^save_dir\s*=\s*"((?:[^"\\]|\\.)*)"', line)
        if m:
            return _toml_basic_string(m.group(1))
        m = re.match(r"^save_dir\s*=\s*'([^']*)'", line)  # literal string: no escapes
        if m:
            return m.group(1)
    return None


def save_dir(home=None):
    """Where vibe saves sessions: [session_logging] save_dir of config.toml, else
    <VIBE_HOME>/logs/session (vibe default)."""
    home = home or vibe_home()
    value = None
    try:
        with open(os.path.join(home, "config.toml"), encoding="utf-8") as f:
            text = f.read()
    except OSError:
        text = ""
    if text:
        try:
            import tomllib  # Python 3.11+
            try:
                value = (tomllib.loads(text).get("session_logging") or {}).get("save_dir")
            except tomllib.TOMLDecodeError:
                value = _save_dir_by_lines(text)
        except ImportError:
            value = _save_dir_by_lines(text)
    if not isinstance(value, str) or not value:
        return os.path.join(home, "logs", "session")
    return os.path.expanduser(value)


def _when(text):
    """An ISO time of meta.json as epoch seconds, or 0."""
    if not isinstance(text, str):
        return 0.0
    try:
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return 0.0


def sessions(directory=None):
    """Every session of save_dir (<save_dir>/unified/<id>/meta.json), as dicts.
    A folder without a readable meta.json, or whose session_id is not a vibe id
    matching its folder name, is skipped."""
    root = os.path.join(directory or save_dir(), "unified")
    try:
        names = os.listdir(root)
    except OSError:
        return []
    found = []
    for name in names:
        if not suffix_of(name):
            continue  # .session-index.json and other files of vibe
        folder = os.path.join(root, name)
        try:
            with open(os.path.join(folder, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("session_id") != name:
            continue
        sid = meta["session_id"]
        env = meta.get("environment") if isinstance(meta.get("environment"), dict) else {}
        start = _when(meta.get("start_time"))
        found.append({
            "session": sid,
            "suffix": suffix_of(sid),
            "parent": meta.get("parent_session_id"),
            "folder": folder,
            "start": start,
            "end": _when(meta.get("end_time")) or start,
            "title": meta.get("title") if isinstance(meta.get("title"), str) else None,
            "title_source": meta.get("title_source"),
            "workspace": env.get("working_directory"),
        })
    return found


def conversations(days=7, directory=None, now=None):
    """{suffix: conversation} of the sessions saved in the last `days` (None: all).

    A conversation holds its current session (saved last), its folder, its
    workspace, its name (the /rename title of that session) and its automatic title.
    """
    groups = {}
    for s in sessions(directory):
        parent_suffix = suffix_of(s["parent"])
        if parent_suffix and parent_suffix != s["suffix"]:
            continue  # a subagent: its parent belongs to another conversation
        groups.setdefault(s["suffix"], []).append(s)
    cutoff = None if days is None else (now or time.time()) - days * 86400
    found = {}
    for suffix, group in groups.items():
        current = max(group, key=lambda s: (s["end"], s["start"]))
        if cutoff is not None and current["end"] < cutoff:
            continue
        manual = current["title_source"] == "manual"
        found[suffix] = {
            "address": "vibe:" + suffix,
            "suffix": suffix,
            "session": current["session"],
            "session_dir": current["folder"],
            "workspace": current["workspace"],
            "name": name_of(current["title"]) if manual else None,
            "title": None if manual else current["title"],
            "modified": current["end"],
            "sessions": len(group),
        }
    return found


def conversation(suffix, directory=None):
    """The conversation of `suffix`, whatever its age, or None."""
    return conversations(None, directory).get(suffix) if SUFFIX_RE.match(str(suffix)) else None


# ---------------------------------------------------------------- live processes (§8.2 Identity, §8.9.4)
# bridge.py loads this file after registering itself as agent_bridge_core: paths and
# process checks come from there.
#
# state/vibe-current/<app-server pid>.json  written by the session_start hook: the main
#     session that vibe-app-server serves now (session, address, cwd, source, run, at).
#     vibe_mcp.py and the pre_agent hook read the file of their own vibe-app-server.
# state/vibe-relay/<relay pid>.json  written by the relay (vibe_relay.py): session, the
#     vibe-rs pid (tui_pid), the real server pid, vibe version, wake "relay" or "off".

APP_SERVER = "vibe-app-server"


def _core():
    import sys
    return sys.modules["agent_bridge_core"]


def _cmdline_name(pid):
    """Linux: the program name of `pid` as its command line shows it (vibe-app-server sets
    its process title), else its comm. None if unreadable."""
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            first = f.read().split(b"\0", 1)[0].decode("utf-8", "replace").strip()
        if first:
            return os.path.basename(first.split(" ", 1)[0])
    except OSError:
        pass
    try:
        with open("/proc/%d/comm" % pid, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _parent_of(pid):
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8") as f:
            return int(f.read().rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def app_server_pid(start=None):
    """Pid of the nearest vibe-app-server above this process (the MCP server is its child,
    a hook its grandchild through /bin/sh), or None. Windows: the process table of bridge.py."""
    core = _core()
    if core.WINDOWS:
        return core._ancestor(APP_SERVER, "vibe-app-server.exe")
    pid, seen = start or os.getppid(), set()
    while pid and pid > 1 and pid not in seen:
        seen.add(pid)
        name = _cmdline_name(pid)
        if name == APP_SERVER or (name or "").startswith(APP_SERVER[:15]):
            return pid
        pid = _parent_of(pid)
    return None


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def current_path(app_pid):
    return _core()._path("state", "vibe-current", "%d.json" % int(app_pid))


def write_current(app_pid, session_id, cwd=None, source=None, run=False):
    data = {"session": session_id, "address": address_of(session_id), "cwd": cwd, "source": source,
            "run": bool(run), "app_pid": int(app_pid), "at": time.time()}
    _write_json(current_path(app_pid), data)
    return data


def read_current(app_pid):
    data = _read_json(current_path(app_pid)) if app_pid else None
    return data if data and suffix_of(data.get("session")) else None


def live_currents():
    """{suffix: record} of vibe-current files whose vibe-app-server is alive; dead ones are removed."""
    core = _core()
    folder = core._path("state", "vibe-current")
    found = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return found
    for name in names:
        if not name.endswith(".json"):
            continue
        data = _read_json(os.path.join(folder, name))
        if not data or not suffix_of(data.get("session")):
            continue
        if not core._alive(data.get("app_pid")):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
            continue
        found[suffix_of(data["session"])] = data
    return found


def relay_path(relay_pid):
    return _core()._path("state", "vibe-relay", "%d.json" % int(relay_pid))


def live_relays():
    """{suffix: record} of relay state files whose relay and vibe-rs are both alive; dead ones
    are removed. A relay whose session is not known yet is left out."""
    core = _core()
    folder = core._path("state", "vibe-relay")
    found = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return found
    for name in names:
        if not (name.endswith(".json") and name[:-5].isdigit()):
            continue
        data = _read_json(os.path.join(folder, name))
        if not data:
            continue
        if not (core._alive(data.get("relay_pid")) and core._alive(data.get("tui_pid"))):
            try:
                os.remove(os.path.join(folder, name))
            except OSError:
                pass
            continue
        suffix = suffix_of(data.get("session"))
        if suffix:
            found[suffix] = data
    return found

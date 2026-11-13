#!/usr/bin/env python3
"""vibe-diaulos: vibe 3 with the Diaulos relay in front of its app-server (docs/VIBE-DESIGN.md §8.9.3).
Python 3.8+, standard library only.

    vibe-diaulos [vibe arguments...]

The shell function `vibe()` that the installer adds calls this command, so `vibe` keeps every
argument it had. A TUI use runs vibe's own launcher, exec_rust_cli(argv,
app_server_cmd=[python, vibe_relay.py, --home, <bridge home>, --real, <vibe-app-server>]): vibe-rs
then talks to the relay, which starts the real vibe-app-server. Every other use goes to the real
`vibe`, unchanged: -p/--prompt, --prompt-file, mcp, config, help, update, -v/--version, -h/--help,
--setup, --check-upgrade.

The real `vibe` is the first `vibe` on PATH, or AGENT_BRIDGE_VIBE_REAL (tests put a stub there).
vibe's launcher is a module of vibe's own Python: this script runs again under that Python (the
first line of the real `vibe`). If the launcher cannot be imported or has no app_server_cmd,
the real `vibe` runs with no relay and one line says so. AGENT_BRIDGE_VIBE_RELAY=0 does the same
without the line.
"""
import inspect
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser(os.environ.get("AGENT_BRIDGE_HOME") or HERE)
NON_TUI_FLAGS = ("-p", "--prompt", "--prompt-file", "-v", "--version", "-h", "--help", "--setup", "--check-upgrade")
NON_TUI_COMMANDS = ("mcp", "config", "help", "update")
NO_RELAY = "Diaulos: no relay for this vibe version (%s); messages arrive at your next message"
REEXEC = "AGENT_BRIDGE_VIBE_DIAULOS_REEXEC"


VALUE_OPTIONS = ("--workdir", "--output", "--max-turns", "--max-price", "--max-tokens", "--time-limit",
                 "--system-prompt-file", "--output-dir", "--agent", "--model", "--enabled-tools",
                 "--disabled-tools", "--add-dir", "--resume", "--worktree")


def is_tui(argv):
    """False for the uses of §8.9.3 that never open the TUI (`vibe --help`, vibe 3.1.2)."""
    skip = False
    for arg in argv:
        if arg == "--":
            break
        if skip:
            skip = False
            continue
        name = arg.split("=", 1)[0]
        if name in NON_TUI_FLAGS:
            return False
        if arg.startswith("-"):
            skip = arg in VALUE_OPTIONS  # --resume and --worktree take an optional value: a word after them is theirs
            continue
        return arg not in NON_TUI_COMMANDS  # the first word: a command, or the initial prompt
    return True


def real_vibe():
    """Path of the real `vibe` command, or None."""
    forced = os.environ.get("AGENT_BRIDGE_VIBE_REAL")
    if forced:
        return forced
    return shutil.which("vibe")


def python_of(script):
    """The interpreter of a console script (its #! line), or None."""
    try:
        with open(script, "rb") as f:
            first = f.readline(4096).decode("utf-8", "replace").strip()
    except OSError:
        return None
    if not first.startswith("#!"):
        return None
    path = first[2:].strip().split()[0] if first[2:].strip() else ""
    return path if os.path.isabs(path) and os.access(path, os.X_OK) else None


def run_real(vibe, argv, note=None):
    if note:
        sys.stderr.write(note + "\n")
        sys.stderr.flush()
    if not vibe:
        sys.stderr.write("vibe-diaulos: the vibe command is not on PATH\n")
        return 127
    os.environ.pop(REEXEC, None)
    if os.name == "nt":
        import subprocess
        return subprocess.call([vibe] + argv)
    os.execv(vibe, [vibe] + argv)


def version():
    try:
        from importlib.metadata import version as v
        return v("mistral-vibe")
    except Exception:
        return "?"


def main(argv):
    vibe = real_vibe()
    if not is_tui(argv) or os.environ.get("AGENT_BRIDGE_VIBE_RELAY") == "0":
        return run_real(vibe, argv)
    vibe_python = python_of(vibe) if vibe else None
    if vibe_python and os.environ.get(REEXEC) != "1" and os.path.normpath(sys.executable) != os.path.normpath(vibe_python):
        os.environ[REEXEC] = "1"
        os.execv(vibe_python, [vibe_python, os.path.abspath(__file__)] + argv)
    # A venv's python is a link to the system one: compare paths unresolved, the venv is in the link.
    os.environ.pop(REEXEC, None)
    try:
        from vibe.cli import launcher
        if "app_server_cmd" not in inspect.signature(launcher.exec_rust_cli).parameters:
            raise ImportError("exec_rust_cli has no app_server_cmd")
    except Exception:
        return run_real(vibe, argv, NO_RELAY % version())
    app_server = os.path.join(os.path.dirname(vibe_python or sys.executable),
                              "vibe-app-server.exe" if os.name == "nt" else "vibe-app-server")
    if not os.path.exists(app_server):
        return run_real(vibe, argv, NO_RELAY % version())
    relay = [sys.executable, os.path.join(HERE, "vibe_relay.py"), "--home", HOME, "--real", app_server]
    launcher.exec_rust_cli(argv, extra_env={"AGENT_BRIDGE_VIBE_VERSION": version()}, app_server_cmd=relay)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

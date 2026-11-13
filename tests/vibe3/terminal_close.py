#!/usr/bin/env python3
"""Closing the terminal of a vibe TUI opened from a shell (T18). Linux only. No model call.

    python3 tests/vibe3/terminal_close.py OUT [--passes N] [--typed "vibe --trust|command vibe --trust"]

OUT is an empty throwaway folder: t14_env.py makes OUT/vh, OUT/ab, OUT/home, OUT/work, then
install.sh --shell runs with HOME=OUT/home, so ~/.bashrc there defines the shell function `vibe`.
Each pass opens a terminal (bash -i in a PTY), types one of the --typed commands (in turn), waits
for vibe-app-server and 4 s more, then ends the terminal, by turns:
  hup    SIGHUP to bash only, the PTY master kept open;
  close  the PTY master closed, then SIGHUP to bash (a terminal window closed).
10 s later it reads the state of vibe-rs, of the relay and of vibe-app-server in /proc. A pass is
"left" if any of them is still there. For a left vibe-rs it gives the state, wchan, parent and
session of the parent: a stopped vibe-rs (T, do_signal_stop) whose parent is in another session
is the orphaned stop of T18. Whatever is left is then killed (throwaway processes only, each checked by its start time first).

Output: one JSON line per pass, then the counts per typed command; OUT/terminal-close.json.
The rows "tui stopped" of the relay log (OUT/ab/state/vibe-relay.log) are counted too.
Exit code 0 when no pass of the shell function `vibe` left anything.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import t14_env  # noqa: E402
from tui_probe import Tui, wait_for  # noqa: E402

AFTER_SECONDS = 10


def tree(pid):
    """[(pid, comm)] of the descendants of pid, parents first."""
    out = subprocess.run(["ps", "-o", "pid=,ppid=,comm=", "-e"], capture_output=True, text=True).stdout
    rows = [l.split(None, 2) for l in out.splitlines()]
    found, todo = [], [str(pid)]
    while todo:
        parent = todo.pop(0)
        for r in rows:
            if len(r) == 3 and r[1] == parent:
                found.append((int(r[0]), r[2].strip()))
                todo.append(r[0])
    return found


def stat(pid):
    try:
        with open("/proc/%d/stat" % pid, encoding="utf-8", errors="replace") as f:
            text = f.read()
        fields = text[text.rindex(")") + 2:].split()
        with open("/proc/%d/wchan" % pid, encoding="utf-8") as f:
            wchan = f.read()
        return {"state": fields[0], "ppid": int(fields[1]), "sid": int(fields[3]), "wchan": wchan,
                "start": int(fields[19])}
    except (OSError, ValueError, IndexError):
        return None


def relay_stopped_rows(ab):
    try:
        with open(os.path.join(ab, "state", "vibe-relay.log"), encoding="utf-8") as f:
            return sum(1 for l in f if json.loads(l).get("dir") == "tui stopped")
    except OSError:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--passes", type=int, default=10)
    ap.add_argument("--typed", default="vibe --trust")
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    t14_env.setup(out)
    home, ab, vh, work = (os.path.join(out, d) for d in ("home", "ab", "vh", "work"))
    env = {"HOME": home, "AGENT_BRIDGE_HOME": ab, "VIBE_HOME": vh, "USER": os.environ.get("USER", "u"),
           "LOGNAME": os.environ.get("USER", "u"), "TERM": "xterm-256color", "LANG": "C.UTF-8",
           "PATH": os.environ["PATH"], "DBUS_SESSION_BUS_ADDRESS": os.environ.get("DBUS_SESSION_BUS_ADDRESS", ""),
           "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "")}
    p = subprocess.run(["bash", os.path.join(REPO, "install.sh"), "--shell"], env=env, cwd=REPO,
                       capture_output=True, text=True)
    print(json.dumps({"install.sh --shell rc": p.returncode}), flush=True)
    typed_all = args.typed.split("|")
    rows = []
    for i in range(args.passes):
        typed = typed_all[i % len(typed_all)]
        end = ("hup", "close")[(i // len(typed_all)) % 2]
        stopped_before = relay_stopped_rows(ab)
        tui = Tui(work, env, ["-i"], command=["/bin/bash"])
        time.sleep(1.5)
        tui.type(typed + "\r")
        opened = wait_for(lambda: any(c.startswith("vibe-app-serv") for _, c in tree(tui.pid)), 30)
        time.sleep(4)
        procs = [(pid, comm) for pid, comm in tree(tui.pid)
                 if comm in ("vibe-rs", "vibe-app-server") or (comm.startswith("python") and "vibe_relay.py" in
                                                                  open("/proc/%d/cmdline" % pid, "rb").read().decode(
                                                                      "utf-8", "replace"))]
        started = {pid: (stat(pid) or {}).get("start") for pid, _ in procs}
        if end == "close":
            os.close(tui.fd)
        os.kill(tui.pid, signal.SIGHUP)
        wait_for(lambda: os.waitpid(tui.pid, os.WNOHANG)[0], 10)
        time.sleep(AFTER_SECONDS)
        left = {}
        for pid, comm in procs:
            s = stat(pid)
            if s and s["state"] != "Z":
                name = "relay" if comm.startswith("python") else comm
                parent = stat(s["ppid"])
                s = dict(s, parent_same_session=bool(parent and parent["sid"] == s["sid"]))
                s.pop("start")
                left[name] = s
        row = {"pass": i, "typed": typed, "end": end, "tui opened": bool(opened),
               "processes": sorted("relay" if c.startswith("python") else c for _, c in procs),
               "left after %d s" % AFTER_SECONDS: left,
               "relay rows 'tui stopped'": relay_stopped_rows(ab) - stopped_before}
        rows.append(row)
        print(json.dumps(row), flush=True)
        for pid, _ in procs:
            if (stat(pid) or {}).get("start") != started[pid]:
                continue  # gone, or the pid now names another process: never signal it
            for sig in (signal.SIGCONT, signal.SIGKILL):
                try:
                    os.kill(pid, sig)
                except OSError:
                    pass
        time.sleep(1)
    p = subprocess.run(["bash", os.path.join(REPO, "uninstall.sh")], env=env, cwd=REPO, capture_output=True, text=True)
    summary = {}
    for typed in typed_all:
        mine = [r for r in rows if r["typed"] == typed]
        summary[typed] = {"passes": len(mine), "opened": sum(r["tui opened"] for r in mine),
                          "left something": sum(bool(r["left after %d s" % AFTER_SECONDS]) for r in mine),
                          "relay resumed a stopped vibe-rs": sum(r["relay rows 'tui stopped'"] for r in mine)}
    print(json.dumps({"uninstall.sh rc": p.returncode, "summary": summary}), flush=True)
    with open(os.path.join(out, "terminal-close.json"), "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "summary": summary}, f, indent=1)
    fn = summary.get("vibe --trust")
    return 0 if fn and fn["opened"] == fn["passes"] and fn["left something"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

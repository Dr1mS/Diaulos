#!/usr/bin/env python3
"""Closing a PTY terminal of `vibe`, then reading what is left of vibe-rs (Linux; T22).

    python3 tests/vibe3/pty_close_repro.py [--passes 20] [--vibe vibe] [--ignore-ttou]

Each pass opens a terminal (a PTY running `bash --norc -i`), types `vibe --trust` (--trust skips
the trust prompt for the fresh working folder), waits until the TUI is up, then closes the
terminal, in turn:
  close   the PTY master is closed, as when a terminal window is closed (the kernel then
          sends SIGHUP to bash); nothing else is sent;
  hup     SIGHUP is sent to bash; the PTY master stays open and is still read.
5 s later it reads /proc for the vibe-rs process. If vibe-rs is still there, it looks again 60 s
after the close.

HOME and VIBE_HOME are fresh temporary folders. The D-Bus session is passed through, so a vibe
signed in through the system keyring opens its main screen; MISTRAL_API_KEY is passed through too
when it is set. Nothing is typed into vibe: no prompt, no model call.

--ignore-ttou types `trap '' TTOU; vibe --trust` instead: vibe-rs then starts with SIGTTOU ignored.
Each pass prints whether SIGTTOU is ignored in vibe-rs (SigIgn in /proc/<pid>/status).

Python 3.8+, standard library only. Leftover processes are killed at the end of each pass.
"""
import argparse
import os
import pty
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time

SIGTTOU_BIT = 1 << (signal.SIGTTOU - 1)


def stat(pid):
    """state, ppid, session, tty foreground group, start time of a process, or None."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            text = f.read()
    except OSError:
        return None
    fields = text[text.rindex(")") + 2:].split()
    return {"state": fields[0], "ppid": int(fields[1]), "sid": int(fields[3]),
            "tpgid": int(fields[5]), "start": int(fields[19])}


def read_proc(pid, name):
    try:
        with open("/proc/%d/%s" % (pid, name), "rb") as f:
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def ignores_sigttou(pid):
    for line in read_proc(pid, "status").splitlines():
        if line.startswith("SigIgn:"):
            return bool(int(line.split()[1], 16) & SIGTTOU_BIT)
    return None


def descendants(root):
    children = {}
    for entry in os.listdir("/proc"):
        if entry.isdigit():
            s = stat(int(entry))
            if s:
                children.setdefault(s["ppid"], []).append(int(entry))
    found, todo = [], [root]
    while todo:
        for child in children.get(todo.pop(), []):
            found.append(child)
            todo.append(child)
    return found


def pump(fd, seconds):
    """Read and drop the terminal output for some seconds (a full PTY would block vibe)."""
    end = time.time() + seconds
    while time.time() < end:
        ready, _, _ = select.select([fd], [], [], max(0.0, min(0.1, end - time.time())))
        if ready:
            try:
                os.read(fd, 65536)
            except OSError:
                return


def find_tui(bash, fd, timeout):
    end = time.time() + timeout
    while time.time() < end:
        for pid in descendants(bash):
            if read_proc(pid, "comm").strip() == "vibe-rs":
                return pid
        pump(fd, 0.2)
    return None


def wait_exit(pid, timeout, fd=None):
    """Wait for a child to exit; with fd, keep reading the terminal meanwhile."""
    end = time.time() + timeout
    while time.time() < end:
        if os.waitpid(pid, os.WNOHANG)[0]:
            return True
        if fd is None:
            time.sleep(0.1)
        else:
            pump(fd, 0.1)
    return False


def describe(pid, start):
    s = stat(pid)
    if not s or s["start"] != start or s["state"] == "Z":
        return None
    parent = stat(s["ppid"]) or {}
    return "state %s, wchan %s, parent %s (%s), parent in same session: %s, tty foreground group %d" % (
        s["state"], read_proc(pid, "wchan"), s["ppid"], read_proc(s["ppid"], "cmdline").replace("\0", " ").strip(),
        parent.get("sid") == s["sid"], s["tpgid"])


def remove_tree(path):
    def make_writable_and_retry(func, failed, _):  # vibe writes some files read-only
        os.chmod(os.path.dirname(failed), 0o700)
        if os.path.isdir(failed):
            os.chmod(failed, 0o700)
        func(failed)
    shutil.rmtree(path, onerror=make_writable_and_retry)


def one_pass(vibe, typed, end):
    home = tempfile.mkdtemp(prefix="vibe-repro-")
    env = {"HOME": home, "VIBE_HOME": os.path.join(home, ".vibe"), "PATH": os.environ["PATH"],
           "TERM": "xterm-256color", "LANG": "C.UTF-8"}
    for key in ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "MISTRAL_API_KEY"):
        if key in os.environ:
            env[key] = os.environ[key]
    bash, fd = pty.fork()
    if bash == 0:
        os.chdir(home)
        os.execve("/bin/bash", ["bash", "--norc", "-i"], env)
    pump(fd, 1)
    os.write(fd, (typed % vibe + "\r").encode())
    tui = find_tui(bash, fd, 30)
    pump(fd, 4)  # let the TUI draw its first screen
    tracked = [(p, (stat(p) or {}).get("start")) for p in descendants(bash)]
    tui_start = (stat(tui) or {}).get("start") if tui else None
    ttou_ignored = ignores_sigttou(tui) if tui else None
    if end == "close":
        os.close(fd)
        bash_exited = wait_exit(bash, 10)
        if not bash_exited:
            os.kill(bash, signal.SIGHUP)
            wait_exit(bash, 10)
        time.sleep(5)
    else:  # the terminal stays open and keeps reading, as a terminal emulator does
        os.kill(bash, signal.SIGHUP)
        bash_exited = wait_exit(bash, 10, fd)
        pump(fd, 5)
    after_5 = describe(tui, tui_start) if tui else None
    after_60 = None
    if after_5:
        time.sleep(55)
        after_60 = describe(tui, tui_start)
    for p, start in tracked:  # clean up, never a reused pid
        if start is not None and (stat(p) or {}).get("start") == start:
            for sig in (signal.SIGCONT, signal.SIGKILL):
                try:
                    os.kill(p, sig)
                except OSError:
                    pass
    if end == "hup":
        os.close(fd)
    time.sleep(1)
    remove_tree(home)
    return {"tui": bool(tui), "ttou_ignored": ttou_ignored, "bash_exited": bash_exited,
            "after_5": after_5, "after_60": after_60}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--passes", type=int, default=20)
    ap.add_argument("--vibe", default="vibe")
    ap.add_argument("--ignore-ttou", action="store_true")
    args = ap.parse_args()
    typed = "trap '' TTOU; %s --trust" if args.ignore_ttou else "%s --trust"
    version = subprocess.run([args.vibe, "--version"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
    print("%s, kernel %s, typed in bash: %r" % (version, os.uname().release, typed % "vibe"), flush=True)
    stopped = opened = 0
    for i in range(args.passes):
        end = ("close", "hup")[i % 2]
        r = one_pass(args.vibe, typed, end)
        opened += r["tui"]
        stopped += bool(r["after_60"] and r["after_60"].startswith("state T"))
        print("pass %2d  %-5s  TUI up: %-5s  SIGTTOU ignored: %-5s  bash exited: %-5s  vibe-rs 5 s later: %s" % (
            i + 1, end, r["tui"], r["ttou_ignored"], r["bash_exited"], r["after_5"] or "gone"), flush=True)
        if r["after_5"]:
            print("         vibe-rs 60 s later: %s" % (r["after_60"] or "gone"), flush=True)
    print("%d/%d left stopped after 60 s (TUI up in %d/%d passes)" % (stopped, args.passes, opened, args.passes))
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main())

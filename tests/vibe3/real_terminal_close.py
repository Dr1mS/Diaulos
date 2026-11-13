#!/usr/bin/env python3
"""Ends real GNOME Terminal windows running `vibe` and reads what is left of vibe-rs (Linux, GNOME).

    python3 -B real_terminal_close.py [--passes 20] [--end close|hup] [--settle S] [--ignore-ttou]
                                      [--diaulos REPO]

Each pass:
1. starts a separate gnome-terminal-server with its own application id and throwaway settings
   (GSETTINGS_BACKEND=keyfile, confirm-close=false), so no "close anyway?" dialog shows up and
   the settings of this machine are not touched;
2. opens a window running `bash --rcfile <rc> -i`, where rc turns job control on (set -m) and runs
   `vibe --trust` (or `trap '' TTOU; vibe --trust` with --ignore-ttou); vibe-rs is then its own
   foreground job, as when the command is typed;
3. waits for vibe-rs and 4 s more (with --settle S: for vibe-app-server, then S s more);
4. ends the terminal:
     close  the window's own "close" action on D-Bus (org.gtk.Actions.Activate), the action of
            the window's close button;
     hup    SIGHUP to bash only; the window stays open (GNOME Terminal keeps reading it);
   and measures how long bash takes to exit (the proof that the gesture was done);
5. reads vibe-rs in /proc 5 s later; if it is still there, watches it until it leaves, and reads
   it again 60 s after the end if it has not.
Throwaway processes are then killed (each checked by its start time first).

Without --diaulos, HOME and VIBE_HOME are fresh for each pass. With --diaulos REPO, the set-up of
REPO/tests/vibe3/terminal_close.py is used once (t14_env, then install.sh --shell in a throwaway
HOME, AGENT_BRIDGE_HOME and VIBE_HOME), rc sources ~/.bashrc, and `vibe` is the shell function
of Diaulos; the rows "tui stopped" of the relay log are counted for each pass.

Windows open and close on the screen. No model call: nothing is typed into vibe.
Not run by the CI or tests/run-proofs.sh: it needs a GNOME session, like the real gates (T22, T24).
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pty_close_repro as repro  # noqa: E402

APP_ID = "org.diaulos.CloseTest"
MARK = "DIAULOS_CLOSE_TEST"


def marked(value):
    found = []
    for entry in os.listdir("/proc"):
        if entry.isdigit() and ("%s=%s" % (MARK, value)) in repro.read_proc(int(entry), "environ").split("\0"):
            found.append(int(entry))
    return found


def find(value, comm, timeout):
    end = time.time() + timeout
    while time.time() < end:
        for pid in marked(value):
            if repro.read_proc(pid, "comm").strip().startswith(comm):
                return pid
        time.sleep(0.2)
    return None


def pgid(pid):
    try:
        return os.getpgid(pid)
    except OSError:
        return None


def gone_after(pid, start, timeout):
    """Seconds until the process is gone (or a zombie), or None if still there after timeout."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = repro.stat(pid)
        if not s or s["start"] != start or s["state"] == "Z":
            return time.time() - t0
        time.sleep(0.02)
    return None


def relay_stopped_rows(ab):
    try:
        with open(os.path.join(ab, "state", "vibe-relay.log"), encoding="utf-8") as f:
            return sum(1 for line in f if json.loads(line).get("dir") == "tui stopped")
    except OSError:
        return 0


def close_window():
    return subprocess.run(["gdbus", "call", "--session", "--dest", APP_ID, "--object-path",
                           "/%s/window/1" % APP_ID.replace(".", "/"), "--method", "org.gtk.Actions.Activate",
                           "close", "[<'window'>]", "{}"], stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL).returncode == 0


def name_owner_pid():
    """pid of the process owning APP_ID on the session bus, or None."""
    p = subprocess.run(["gdbus", "call", "--session", "--dest", "org.freedesktop.DBus", "--object-path",
                        "/org/freedesktop/DBus", "--method", "org.freedesktop.DBus.GetConnectionUnixProcessID", APP_ID],
                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
    last = p.stdout.strip().strip("(),").split()[-1:]  # "(uint32 1234,)"
    return int(last[0]) if p.returncode == 0 and last and last[0].isdigit() else None


def start_server(work):
    """A gnome-terminal-server of its own that owns APP_ID, or None after 3 tries."""
    server_env = dict(os.environ, GSETTINGS_BACKEND="keyfile", XDG_CONFIG_HOME=os.path.join(work, "config"))
    for _ in range(3):
        end = time.time() + 10
        while name_owner_pid() and time.time() < end:  # the server of the last pass is still leaving
            time.sleep(0.2)
        with open(os.path.join(work, "server.log"), "a") as log:
            server = subprocess.Popen(["/usr/libexec/gnome-terminal-server", "--app-id", APP_ID], env=server_env,
                                      stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        end = time.time() + 10
        while time.time() < end and server.poll() is None:
            if name_owner_pid() == server.pid:
                return server
            time.sleep(0.2)
        if server.poll() is None:
            server.terminate()
        server.wait(10)
    return None


def one_pass(n, args, root, diaulos_env):
    work = tempfile.mkdtemp(prefix="pass-", dir=root)
    settings = os.path.join(work, "config", "glib-2.0", "settings")
    os.makedirs(settings)
    with open(os.path.join(settings, "keyfile"), "w") as f:
        f.write("[org/gnome/terminal/legacy]\nconfirm-close=false\n")
    typed = "trap '' TTOU; vibe --trust" if args.ignore_ttou else "vibe --trust"
    rc = os.path.join(work, "rc")
    with open(rc, "w") as f:
        f.write("%scd ~\nset -m\n%s\n" % ("source ~/.bashrc\n" if diaulos_env else "", typed))
    value = "%d-%d" % (os.getpid(), n)
    server = start_server(work)
    if server is None:
        with open(os.path.join(work, "server.log")) as f:
            sys.exit("gnome-terminal-server did not start:\n" + f.read())
    env = {k: os.environ[k] for k in ("PATH", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "DISPLAY",
                                      "WAYLAND_DISPLAY", "MISTRAL_API_KEY", "LANG", "USER", "LOGNAME")
           if k in os.environ}
    if diaulos_env:
        env.update(diaulos_env)
    else:
        home = os.path.join(work, "home")
        os.makedirs(home)
        env.update(HOME=home, VIBE_HOME=os.path.join(home, ".vibe"))
    env[MARK] = value
    stopped_before = relay_stopped_rows(diaulos_env["AGENT_BRIDGE_HOME"]) if diaulos_env else 0
    subprocess.run(["gnome-terminal", "--app-id", APP_ID, "--window", "--", "bash", "--rcfile", rc, "-i"],
                   env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    bash = find(value, "bash", 15)  # bash, vibe-rs, vibe-app-server: whole names (15 characters at most)
    tui = find(value, "vibe-rs", 30)
    server_up = find(value, "vibe-app-server", 60) if args.settle else True
    time.sleep(args.settle or 4)
    tracked = [(p, (repro.stat(p) or {}).get("start")) for p in marked(value)]
    bash_start = (repro.stat(bash) or {}).get("start") if bash else None
    tui_start = (repro.stat(tui) or {}).get("start") if tui else None
    row = {"tui": bool(tui and server_up), "own job": bool(tui and bash and pgid(tui) != pgid(bash)),
           "ttou_ignored": repro.ignores_sigttou(tui) if tui else None,
           "relay up": any("vibe_relay.py" in repro.read_proc(p, "cmdline") for p, _ in tracked)}
    if args.end == "close":
        row["done"] = close_window()
    else:
        try:
            os.kill(bash, signal.SIGHUP)
            row["done"] = True
        except (OSError, TypeError):
            row["done"] = False
    t_end = time.time()
    row["bash exit s"] = gone_after(bash, bash_start, 5) if bash else None
    time.sleep(max(0.0, t_end + 5 - time.time()))
    row["after_5"] = repro.describe(tui, tui_start) if tui else None
    row["after_60"] = row["tui exit s"] = None
    if row["after_5"]:  # watch it until it leaves, 60 s after the end at most
        left = gone_after(tui, tui_start, max(0.0, t_end + 60 - time.time()))
        row["tui exit s"] = None if left is None else time.time() - t_end
        row["after_60"] = repro.describe(tui, tui_start)
    elif tui:
        row["tui exit s"] = 0.0  # gone within 5 s
    row["relay tui stopped"] = (relay_stopped_rows(diaulos_env["AGENT_BRIDGE_HOME"]) - stopped_before
                                if diaulos_env else None)
    for p, start in tracked:
        if start is not None and (repro.stat(p) or {}).get("start") == start:
            for sig in (signal.SIGCONT, signal.SIGKILL):
                try:
                    os.kill(p, sig)
                except OSError:
                    pass
    if args.end == "hup":  # the window may stay open on "process exited": close it
        close_window()
    try:
        server.wait(10)
    except subprocess.TimeoutExpired:
        server.terminate()
        server.wait(10)
    repro.remove_tree(work)
    return row


def diaulos_setup(repo, out):
    sys.path.insert(0, os.path.join(repo, "tests", "vibe3"))
    import t14_env
    t14_env.setup(out)
    env = {"HOME": os.path.join(out, "home"), "AGENT_BRIDGE_HOME": os.path.join(out, "ab"),
           "VIBE_HOME": os.path.join(out, "vh")}
    full = dict(os.environ, **env)
    p = subprocess.run(["bash", os.path.join(repo, "install.sh"), "--shell"], env=full, cwd=repo,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    head = subprocess.run(["git", "-C", repo, "rev-parse", "--short", "HEAD"], stdout=subprocess.PIPE,
                          universal_newlines=True).stdout.strip()
    print("Diaulos %s, install.sh --shell rc %d" % (head, p.returncode), flush=True)
    return env, full


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--passes", type=int, default=20)
    ap.add_argument("--end", choices=("close", "hup"), default="close")
    ap.add_argument("--settle", type=float, default=0,
                    help="wait for vibe-app-server, then this many seconds, before the end (default: 4 s after vibe-rs)")
    ap.add_argument("--ignore-ttou", action="store_true")
    ap.add_argument("--diaulos", metavar="REPO")
    args = ap.parse_args()
    version = subprocess.run(["vibe", "--version"], stdout=subprocess.PIPE, universal_newlines=True).stdout.strip()
    gt = subprocess.run(["gnome-terminal", "--version"], stdout=subprocess.PIPE, universal_newlines=True).stdout
    print("%s, %s, kernel %s" % (version, gt.strip().lstrip("# "), os.uname().release), flush=True)
    print("end: %s, %s, vibe: %s%s" % (
        args.end, "%g s after vibe-app-server is up" % args.settle if args.settle else "4 s after vibe-rs is up",
        "shell function of Diaulos" if args.diaulos else "vibe command",
        ", SIGTTOU ignored" if args.ignore_ttou else ""), flush=True)
    root = tempfile.mkdtemp(prefix="real-close-")
    diaulos_env = full = None
    stopped = opened = slow = resumed = 0
    try:
        if args.diaulos:
            diaulos_env, full = diaulos_setup(os.path.abspath(args.diaulos), os.path.join(root, "diaulos"))
        for i in range(args.passes):
            r = one_pass(i + 1, args, root, diaulos_env)
            opened += r["tui"]
            stopped += bool(r["after_60"] and r["after_60"].startswith("state T"))
            slow += r["bash exit s"] is None or r["bash exit s"] >= 1
            resumed += r["relay tui stopped"] or 0
            print("pass %2d  TUI up: %-5s  own job: %-5s  SIGTTOU ignored: %-5s  %s done: %-5s  bash exit: %s"
                  "%s  vibe-rs 5 s later: %s" % (
                      i + 1, r["tui"], r["own job"], r["ttou_ignored"], args.end, r["done"],
                      "%.2f s" % r["bash exit s"] if r["bash exit s"] is not None else "not in 5 s",
                      "  relay up: %s, relay 'tui stopped': %d" % (r["relay up"], r["relay tui stopped"])
                      if args.diaulos else "",
                      r["after_5"] or "gone"), flush=True)
            if r["after_5"]:
                print("         vibe-rs 60 s later: %s" % (
                    r["after_60"] or "gone (left %.1f s after the end)" % r["tui exit s"]), flush=True)
    finally:
        if full:
            subprocess.run(["bash", os.path.join(os.path.abspath(args.diaulos), "uninstall.sh")], env=full,
                           cwd=os.path.abspath(args.diaulos), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        repro.remove_tree(root)
    print("%d/%d left stopped after 60 s, TUI up in %d/%d, bash exit 1 s or more: %d%s" % (
        stopped, args.passes, opened, args.passes, slow,
        ", relay 'tui stopped' rows: %d" % resumed if args.diaulos else ""))
    return 1 if stopped or slow else 0


if __name__ == "__main__":
    sys.exit(main())

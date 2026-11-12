#!/usr/bin/env python3
"""Real vibe TUI with the Diaulos plugin, in a pseudo-terminal, no human (task T3c, Linux).

Paid model calls: run it only on purpose. Everything is throwaway: a bridge home with a
copy of bridge/, a VIBE_HOME with a copy of ~/.vibe/config.toml (read only; save_dir
moved there; `sleep` and `python3` added to the bash allowlist of the copy, so no
approval is typed; update checks off (their prompt blocks a
second start), active_model set to devstral-small, the cheapest model of the
config), the shim install/vibe-tool.py in its tools/. The real HOME is kept
for vibe's API key.

Price: --price is the budget of the whole run (default 0.10 $, at most 0.50 $).
AGENT_BRIDGE_VIBE_MAX_PRICE puts vibe's own cap on each vibe process, and vibe counts
the cost of the session in that process, not of a turn; `vibe --resume` (step 3) starts
a new process whose count starts again at zero. So each process gets as cap what is
left of the budget, the script adds up the cost of every process (meta.json
stats.session_cost; the last value of the killed process is kept and added to what the
folders say afterwards, so the total may count high, never low), and stops with FAIL as
soon as the total is above the budget.

Usage: python3 tests/real_vibe_tui.py [--price 0.1] [--keep]

Steps, each printed with its measures:
  1. idle delivery, 3 passes: time from `bridge.py send` to the call of
     _handle_user_message (in-flight record marked started); the median must be < 3 s;
  2. a message sent during a turn running `sleep 20` (typed by the person), 3 passes:
     delivered after the end of that turn, within 1 s of the last write of the session;
  3. vibe killed (SIGKILL) during a delivered turn, then `vibe --resume`: the message
     is delivered again by the new process;
  4. bridge_send from vibe reaches the shared `claude` inbox with from = vibe:<suffix>;
     `bridge.py send` run by vibe's bash tool gives the same `from`;
  5. every key typed into the terminal is logged (keys.log): only the person's prompts
     of step 2 and their Enter, never an approval.
The cost of each session (meta.json stats.session_cost) is printed at the end.
"""
import argparse
import json
import os
import pty
import re
import shutil
import signal
import statistics
import struct
import subprocess
import sys
import tempfile
import termios
import fcntl
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VIBE = shutil.which("vibe") or os.path.expanduser("~/.local/bin/vibe")


def wait_for(predicate, timeout, step=0.02):
    deadline = time.time() + timeout
    while True:
        value = predicate()
        if value or time.time() > deadline:
            return value
        time.sleep(step)


class Tui:
    """vibe in a pty; output drained to tty.raw; every key typed logged to keys.log."""

    def __init__(self, folder, env, args):
        self.folder = folder
        self.raw = open(os.path.join(folder, "tty.raw"), "ab")
        self.keys = open(os.path.join(folder, "keys.log"), "a", encoding="utf-8")
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.environ.clear()
            os.environ.update(env, TERM="xterm-256color")
            os.execv(VIBE, [VIBE] + args)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 160, 0, 0))
        self.alive = True
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        while self.alive:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                break
            if not data:
                break
            self.raw.write(data)
            self.raw.flush()

    def type(self, text, why):
        self.keys.write(json.dumps({"t": round(time.time(), 3), "why": why, "keys": text}) + "\n")
        self.keys.flush()
        for ch in text.encode():
            os.write(self.fd, bytes([ch]))
            time.sleep(0.02)

    def kill(self, sig=signal.SIGKILL):
        with_suppress(lambda: os.kill(self.pid, sig))
        with_suppress(lambda: os.waitpid(self.pid, 0))
        self.alive = False
        with_suppress(lambda: os.close(self.fd))

    def stop(self):
        with_suppress(lambda: os.kill(self.pid, signal.SIGHUP))
        if not wait_for(lambda: os.waitpid(self.pid, os.WNOHANG)[0], 5, 0.1):
            self.kill()
        self.alive = False
        with_suppress(lambda: os.close(self.fd))


def with_suppress(fn):
    try:
        return fn()
    except (OSError, ChildProcessError):
        return None


class Run:
    def __init__(self, price, keep):
        self.tmp = tempfile.mkdtemp(prefix="diaulos-real-tui-")
        self.keep = keep
        self.bridge_home = os.path.join(self.tmp, "bridge")
        self.vibe_home = os.path.join(self.tmp, "vibe_home")
        self.work = os.path.join(self.tmp, "work")
        self.save_dir = os.path.join(self.vibe_home, "logs", "session")
        for d in (self.bridge_home, os.path.join(self.vibe_home, "tools"), self.work):
            os.makedirs(d)
        for name in os.listdir(os.path.join(ROOT, "bridge")):
            if name.endswith(".py"):
                shutil.copy(os.path.join(ROOT, "bridge", name), self.bridge_home)
        with open(os.path.join(ROOT, "install", "vibe-tool.py"), encoding="utf-8") as f:
            shim = f.read().replace("@AGENT_BRIDGE_HOME@", self.bridge_home)
        with open(os.path.join(self.vibe_home, "tools", "agent_bridge.py"), "w", encoding="utf-8") as f:
            f.write(shim)
        with open(os.path.expanduser("~/.vibe/config.toml"), encoding="utf-8") as f:
            config = f.read()
        config, n = re.subn(r'(?m)^save_dir = ".*"$', 'save_dir = "%s"' % self.save_dir, config)
        if n != 1:
            config += '\n[session_logging]\nsave_dir = "%s"\n' % self.save_dir
        config = re.sub(r'(?m)^enable_update_checks = .*$', 'enable_update_checks = false', config)
        config = re.sub(r'(?m)^enable_auto_update = .*$', 'enable_auto_update = false', config)
        config, n = re.subn(r'(?m)^active_model = ".*"$', 'active_model = "devstral-small"', config)
        if n != 1:
            sys.exit("no active_model line in the config copy")
        config, n = re.subn(r'(?m)^(\[tools\.bash\]\npermission = "ask"\nallowlist = \[)',
                            r'\1\n    "sleep",\n    "python3",', config)
        if n != 1:
            sys.exit("could not add sleep and python3 to the bash allowlist of the config copy")
        with open(os.path.join(self.vibe_home, "config.toml"), "w", encoding="utf-8") as f:
            f.write(config)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_BRIDGE_")}
        self.env.update(VIBE_HOME=self.vibe_home, AGENT_BRIDGE_HOME=self.bridge_home,
                        AGENT_BRIDGE_VIBE_MAX_PRICE=str(price))
        self.budget = price
        self.banked = 0.0  # cost of the vibe processes that are gone (killed in step 3)
        self.tui = None
        self.suffix = None
        self.results = {}

    # -- the bridge

    def bridge(self, *args):
        proc = subprocess.run([sys.executable, os.path.join(self.bridge_home, "bridge.py"), *args],
                              capture_output=True, text=True, env=self.env, timeout=60)
        if proc.returncode:
            raise RuntimeError(f"bridge.py {args[0]}: {proc.stderr or proc.stdout}")
        return proc.stdout

    def send(self, text):
        out = self.bridge("send", f"vibe:{self.suffix}", text, "--from", "claude")
        return re.search(r"\b([0-9a-f]{12})\b", out).group(1)

    def peer(self):
        try:
            with open(os.path.join(self.bridge_home, "peers", f"vibe-{self.suffix}.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def record(self):
        try:
            with open(os.path.join(self.bridge_home, "state", "vibe-inflight", f"{self.suffix}.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def started_record(self, pid=None):
        r = self.record()
        return r if r and r.get("started") and (pid is None or r.get("pid") == pid) else None

    def log(self):
        try:
            with open(os.path.join(self.bridge_home, "state", "vibe-plugin.log"), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def inbox(self, address):
        return [json.loads(line) for line in self.bridge("recv", address, "--json").splitlines()]

    # -- vibe

    def folders(self):
        try:
            return sorted(os.listdir(self.save_dir))
        except OSError:
            return []

    def messages_file(self):
        meta = None
        for d in self.folders():
            try:
                with open(os.path.join(self.save_dir, d, "meta.json")) as f:
                    m = json.load(f)
            except (OSError, ValueError):
                continue
            if self.suffix and m.get("session_id", "").endswith(self.suffix):
                if meta is None or m.get("end_time", "") >= meta[1].get("end_time", ""):
                    meta = (d, m)
        return os.path.join(self.save_dir, meta[0], "messages.jsonl") if meta else None

    def mtime(self):
        path = self.messages_file()
        try:
            return os.path.getmtime(path) if path else 0.0
        except OSError:
            return 0.0

    def spent(self):
        return self.banked + sum(c or 0 for _, c in self.costs())

    def check_budget(self, where):
        spent = self.spent()
        if spent > self.budget:
            raise RuntimeError(f"run total ${spent:.4f} is above the budget ${self.budget} ({where}): stopped")
        return spent

    def start_tui(self, *extra):
        left = self.budget - self.check_budget("before starting vibe")
        self.env["AGENT_BRIDGE_VIBE_MAX_PRICE"] = f"{max(left, 0.001):.4f}"
        self.tui = Tui(self.tmp, self.env, ["--trust", "--workdir", self.work, *extra])
        pid = self.tui.pid
        peer_file = lambda: [n for n in os.listdir(os.path.join(self.bridge_home, "peers"))
                             if n.startswith("vibe-")] if os.path.isdir(os.path.join(self.bridge_home, "peers")) else []
        ok = wait_for(lambda: any(self._tui_pid(n) == pid for n in peer_file()), 90, 0.2)
        if not ok:
            raise RuntimeError("the plugin wrote no heartbeat:\n" + self.log())
        name = [n for n in peer_file() if self._tui_pid(n) == pid][0]
        self.suffix = name[5:-5]
        time.sleep(3)  # let the TUI draw its input box
        return pid

    def _tui_pid(self, name):
        try:
            with open(os.path.join(self.bridge_home, "peers", name)) as f:
                return json.load(f).get("tui_pid")
        except (OSError, ValueError):
            return None

    def turn_done(self, timeout=180):
        """The delivered turn has ended: the plugin cleared its in-flight record."""
        return wait_for(lambda: self.record() is None, timeout, 0.1)

    # -- steps

    def step_idle(self):
        times = []
        for i in range(3):
            t0 = time.time()
            self.send(f"Answer with the single word IDLE{i}. Do not call any tool.")
            if not wait_for(self.started_record, 30):
                raise RuntimeError("idle message not delivered:\n" + self.log())
            times.append(time.time() - t0)
            if not self.turn_done():
                raise RuntimeError("idle turn did not end")
        self.results["idle_seconds"] = [round(t, 2) for t in times]
        self.results["idle_median"] = round(statistics.median(times), 2)
        print(f"1. idle delivery: {self.results['idle_seconds']} s, median {self.results['idle_median']} s",
              flush=True)
        assert self.results["idle_median"] < 3.0

    def step_busy(self):
        gaps = []
        for i in range(3):
            self.tui.type(f"Run the shell command `sleep 20` with your bash tool, then answer with the single "
                          f"word SLEPT{i}.", f"person prompt {i}")
            time.sleep(0.5)
            self.tui.type("\r", f"Enter {i}")
            time.sleep(8)  # the model answers, then its bash tool runs `sleep 20`
            msg = self.send(f"Answer with the single word AFTER{i}. Do not call any tool.")
            time.sleep(3)
            if self.inbox_count() != 1 or self.started_record():
                raise RuntimeError("the message was taken while the turn ran:\n" + self.log())
            last = self.mtime()
            deadline = time.time() + 120
            while time.time() < deadline:
                if self.started_record():
                    seen = time.time()
                    break
                last = max(last, self.mtime())
                time.sleep(0.02)
            else:
                raise RuntimeError("message sent during the turn was not delivered:\n" + self.log())
            gaps.append(seen - last)
            if not self.turn_done():
                raise RuntimeError("AFTER turn did not end")
            text = open(self.messages_file(), encoding="utf-8").read()
            assert f"SLEPT{i}" in text and msg in text, "turn order not as expected"
            assert text.index(f"SLEPT{i}") < text.index(msg), "the bridge message went in before the turn ended"
        self.results["after_turn_gap_seconds"] = [round(g, 2) for g in gaps]
        print(f"2. during `sleep 20`: delivered {self.results['after_turn_gap_seconds']} s after the last save "
              f"of the running turn", flush=True)
        # About 1 s: the plugin polls every second (VIBE-DESIGN §4.4, P5: measured 1.06 to 1.21 s).
        assert max(gaps) < 1.5, f"{max(gaps):.2f} s after the end of the turn; target about 1 s"

    def inbox_count(self):
        inbox = os.path.join(self.bridge_home, "inbox", f"vibe-{self.suffix}")
        try:
            return len([n for n in os.listdir(inbox) if n.endswith(".json")])
        except OSError:
            return 0

    def step_kill(self):
        msg = self.send("Run the shell command `sleep 15` with your bash tool, then answer with the single "
                        "word SURVIVED.")
        old_pid = self.tui.pid
        if not wait_for(lambda: self.started_record(old_pid), 30):
            raise RuntimeError("kill message not delivered:\n" + self.log())
        time.sleep(4)
        session = self.peer().get("session")
        self.tui.kill()
        time.sleep(1)
        self.banked += sum(c or 0 for _, c in self.costs())  # a new process counts from zero again
        print(f"3. killed vibe pid {old_pid} during the turn of {msg}", flush=True)
        new_pid = self.start_tui("--resume", session)
        if not wait_for(lambda: self.started_record(new_pid), 60):
            raise RuntimeError("message not delivered again after restart:\n" + self.log())
        self.turn_done()
        log = self.log()
        self.results["kill"] = {"message": msg, "put_back": f"put back" in log or "puts back" in log,
                                "delivered_again": log.count(msg)}
        print(f"   after `vibe --resume`: delivered again by pid {new_pid}; plugin log mentions {msg} "
              f"{log.count(msg)} times", flush=True)
        assert log.count(msg) >= 2

    def delivered_turn(self, text, what):
        """Send, wait until the plugin delivers it, then until its turn ends."""
        msg = self.send(text)
        if not wait_for(self.started_record, 30):
            raise RuntimeError(f"{what} not delivered:\n" + self.log())
        if not self.turn_done():
            raise RuntimeError(f"{what}: turn did not end")
        return msg

    def step_send(self):
        self.delivered_turn("Use your bridge_send tool to send the text PONG-TOOL to the address claude. "
                            "Then answer with the single word SENT1.", "bridge_send request")
        bridge = os.path.join(self.bridge_home, "bridge.py")
        self.delivered_turn(f"Run this exact shell command with your bash tool: python3 {bridge} send claude "
                            f"PONG-BASH -- then answer with the single word SENT2.", "bash request")
        got = []
        wanted = {(f"vibe:{self.suffix}", "PONG-TOOL"), (f"vibe:{self.suffix}", "PONG-BASH")}
        wait_for(lambda: got.extend((m["from"], m["body"]) for m in self.inbox("claude")) or wanted <= set(got),
                 30, 0.5)
        self.results["claude_inbox"] = got
        print(f"4. claude inbox: {got}", flush=True)
        assert wanted <= set(got), got

    def step_keys(self):
        with open(os.path.join(self.tmp, "keys.log"), encoding="utf-8") as f:
            keys = [json.loads(line) for line in f]
        whys = [k["why"] for k in keys]
        self.results["keys"] = whys
        print(f"5. keys typed: {len(keys)} entries: {whys}", flush=True)
        assert all(w.startswith(("person prompt", "Enter")) for w in whys)

    def costs(self):
        found = []
        for d in self.folders():
            try:
                with open(os.path.join(self.save_dir, d, "meta.json")) as f:
                    m = json.load(f)
            except (OSError, ValueError):
                continue  # not written yet
            found.append((d, (m.get("stats") or {}).get("session_cost")))
        return found

    def run(self):
        try:
            print(f"vibe {subprocess.run([VIBE, '--version'], capture_output=True, text=True).stdout.strip()}, "
                  f"budget of the run {self.budget} $", flush=True)
            self.start_tui()
            print(f"TUI up: vibe:{self.suffix}, heartbeat {json.dumps({k: self.peer().get(k) for k in ('wake', 'version', 'paused')})}",
                  flush=True)
            for step in (self.step_idle, self.step_busy, self.step_kill, self.step_send, self.step_keys):
                step()
                self.check_budget(f"after {step.__name__}")
            print("PASS", flush=True)
            return 0
        except (AssertionError, RuntimeError) as e:
            print(f"FAIL: {e!r}", flush=True)
            return 1
        finally:
            if self.tui:
                self.tui.stop()
            costs = self.costs()
            print(f"cost per session folder: {costs}; killed processes ${self.banked:.4f}; "
                  f"run total ${self.spent():.4f} (budget ${self.budget})", flush=True)
            cap_lines = [line.split(" ", 4)[-1] for line in self.log().splitlines() if "price cap" in line]
            print(f"plugin price-cap lines: {cap_lines}", flush=True)
            if self.keep:
                print(f"kept: {self.tmp}")
            else:
                shutil.rmtree(self.tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--price", type=float, default=0.1, help="budget of the whole run, in $")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    if a.price > 0.5:
        sys.exit("the budget of a run is at most 0.50 $")
    sys.exit(Run(a.price, a.keep).run())


if __name__ == "__main__":
    main()

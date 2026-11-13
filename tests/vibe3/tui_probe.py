#!/usr/bin/env python3
"""Vibe 3 probe, interactive part (task T10b): the real vibe TUI in a pseudo-terminal.

Linux only (pty, fcntl, termios).

Uses a throwaway VIBE_HOME set up beforehand (config copy on devstral-small, updates
off, a [[mcp_servers]] entry for tests/vibe3/probe_mcp.py, hooks.toml running
tests/vibe3/probe_hook.py for session_start, pre_agent, post_agent, session_end). It answers:
  - post_agent deny: is the retry run in the TUI, and is its reason shown on screen?
  - while a post_agent hook runs (it sleeps), does the TUI echo keys typed by the user?
  - while the TUI holds the session: `vibe -p --resume <id>` and vibe-acp session/load
  - once the TUI is closed: vibe-acp session/load again
Paid calls: two TUI model calls (the turn and its post_agent retry) and one -p call, on
devstral-small. Only the -p call is capped (--max-price 0.02): vibe has no price cap for
the TUI (--max-price applies to programmatic mode only). Each call is about 9 300 input
tokens, under 0.001 $ at the config prices; count them from the session journal.

    python3 tests/vibe3/tui_probe.py --vibe-home DIR --work DIR --probe-dir DIR --hooks-log FILE
"""
import argparse
import fcntl
import json
import os
import pty
import re
import shutil
import signal
import struct
import subprocess
import termios
import threading
import time

ANSI = re.compile(rb"\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>78DEHMNOZc]")


CSI = re.compile(rb"\x1b\[([0-9;?<>=]*)([ -/]*)([@-~])")


class Screen:
    """A minimal terminal screen: rows of characters, the cursor, CSI H, J and K."""

    def __init__(self, rows=50, cols=200):
        self.rows, self.cols = rows, cols
        self.grid = [[" "] * cols for _ in range(rows)]
        self.r = self.c = 0
        self.pending = b""

    def feed(self, data):
        data = self.pending + data
        self.pending = b""
        i = 0
        text = data
        while i < len(text):
            b = text[i]
            if b == 0x1b:
                m = CSI.match(text, i)
                if m:
                    self._csi(m.group(1).decode(), m.group(3).decode())
                    i = m.end()
                    continue
                m = ANSI.match(text, i)
                if m:
                    i = m.end()
                    continue
                self.pending = text[i:]  # an escape cut by the read
                return
            if b == 0x0d:
                self.c = 0
            elif b == 0x0a:
                self.r = min(self.r + 1, self.rows - 1)
            elif b >= 0x20:
                n = 1 if b < 0x80 else (2 if b >= 0xf0 else 1)
                j = i + 1
                while j < len(text) and 0x80 <= text[j] < 0xc0:
                    j += 1
                ch = text[i:j].decode("utf-8", "replace")
                if self.c < self.cols:
                    self.grid[self.r][self.c] = ch
                self.c += 1
                i = j
                continue
            i += 1

    def _csi(self, params, final):
        nums = [int(x) if x.isdigit() else 0 for x in params.lstrip("?").split(";")] if params else []
        if final in "Hf":
            r = (nums[0] if nums and nums[0] else 1) - 1
            c = (nums[1] if len(nums) > 1 and nums[1] else 1) - 1
            self.r, self.c = min(r, self.rows - 1), min(c, self.cols - 1)
        elif final == "J" and nums[:1] == [2]:
            self.grid = [[" "] * self.cols for _ in range(self.rows)]
        elif final == "K":
            for c in range(self.c, self.cols):
                self.grid[self.r][c] = " "
        elif final == "G":
            self.c = (nums[0] if nums and nums[0] else 1) - 1
        elif final == "C":
            self.c += nums[0] if nums and nums[0] else 1

    def has(self, needle):
        return any(needle in "".join(row) for row in self.grid)


class Tui:
    def __init__(self, cwd, env, args):
        self.chunks = []  # (time, bytes)
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(cwd)
            os.environ.clear()
            os.environ.update(env, TERM="xterm-256color")
            vibe = shutil.which("vibe", path=env["PATH"])
            os.execv(vibe, [vibe] + args)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self):
        while True:
            try:
                data = os.read(self.fd, 65536)
            except OSError:
                return
            if not data:
                return
            self.chunks.append((time.time(), data))

    def text(self, since=0.0):
        raw = b"".join(d for t, d in list(self.chunks) if t >= since)
        return ANSI.sub(b"", raw).decode("utf-8", "replace")

    def first_time(self, needle, since=0.0):
        """Time of the first output chunk after which a screen row holds `needle`.

        The TUI draws each key alone and redraws other regions (spinner, footer) between
        keys, so the text is read from a screen kept up to date: cursor moves (CSI H) and
        printed characters only, enough for these checks."""
        screen = Screen()
        for t, d in list(self.chunks):
            screen.feed(d)
            if t >= since and screen.has(needle):
                return t
        return None

    def on_screen(self, needle):
        screen = Screen()
        for _, d in list(self.chunks):
            screen.feed(d)
        return screen.has(needle)

    def type(self, text):
        for ch in text.encode():
            os.write(self.fd, bytes([ch]))
            time.sleep(0.03)

    def stop(self):
        try:
            os.kill(self.pid, signal.SIGHUP)
        except OSError:
            pass
        deadline = time.time() + 10
        while time.time() < deadline:
            if os.waitpid(self.pid, os.WNOHANG)[0]:
                return
            time.sleep(0.1)
        os.kill(self.pid, signal.SIGKILL)
        os.waitpid(self.pid, 0)


def wait_for(pred, timeout, step=0.1):
    deadline = time.time() + timeout
    while True:
        v = pred()
        if v or time.time() > deadline:
            return v
        time.sleep(step)


def hooks(path, event):
    try:
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    except OSError:
        return []
    return [r for r in rows if r["payload"].get("hook_event_name") == event]


def acp_load(env, cwd, sid):
    proc = subprocess.Popen(["vibe-acp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env, cwd=cwd)
    out = {}

    def call(mid, method, params):
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params}) + "\n")
        proc.stdin.flush()
        deadline = time.time() + 60
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                return {"error": "eof"}
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == mid:
                return msg
        return {"error": "timeout"}

    try:
        out["initialize"] = "result" in call(1, "initialize", {"protocolVersion": 1, "clientCapabilities": {}})
        load = call(2, "session/load", {"sessionId": sid, "cwd": cwd, "mcpServers": []})
        out["session/load"] = "ok" if "result" in load else load.get("error")
    finally:
        proc.kill()
        proc.wait()
    return out


def main():
    ap = argparse.ArgumentParser()
    for a in ("--vibe-home", "--work", "--probe-dir", "--hooks-log"):
        ap.add_argument(a, required=True)
    o = ap.parse_args()
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_BRIDGE_")}
    env["VIBE_HOME"] = o.vibe_home
    open(o.hooks_log, "w").close()
    report = {}

    tui = Tui(o.work, env, ["--trust"])
    try:
        if not wait_for(lambda: len(tui.text()) > 200, 30):
            raise SystemExit("the TUI drew nothing in 30 s")
        time.sleep(3)
        with open(os.path.join(o.probe_dir, "deny"), "w") as f:
            f.write("[bridge] Message from claude:test: reply exactly PONGHOOK and nothing else.")
        with open(os.path.join(o.probe_dir, "sleep"), "w") as f:
            f.write("10")
        t_send = time.time()
        tui.type("Answer exactly ONEWORD and nothing else.")
        tui.type("\r")
        if not wait_for(lambda: hooks(o.hooks_log, "post_agent"), 90):
            raise SystemExit("no post_agent hook in 90 s:\n" + tui.text()[-2000:])
        t_hook = hooks(o.hooks_log, "post_agent")[0]["at"]
        time.sleep(2)
        t_typed = time.time()
        tui.type("TYPEDXYZ")
        echoed = wait_for(lambda: tui.first_time("TYPEDXYZ", t_typed), 6)
        report["keys typed during the post_agent hook (it sleeps 10 s)"] = {
            "typed_after_hook_start_s": round(t_typed - t_hook, 2),
            "echoed_after_typing_s": round(echoed - t_typed, 2) if echoed else None,
            "echoed_before_hook_end": bool(echoed and echoed < t_hook + 10)}
        for _ in "TYPEDXYZ":
            tui.type("\x7f")
        got = wait_for(lambda: tui.first_time("PONGHOOK", t_send), 90)
        report["retry answer PONGHOOK on screen"] = bool(got)
        report["deny reason text on screen"] = bool(tui.first_time("Message from claude:test", t_send))
        report["hook notice on screen"] = [w for w in ("Requested turn revision", "probe-post-agent")
                                           if tui.first_time(w, t_send)]
        sid = hooks(o.hooks_log, "post_agent")[0]["payload"]["session_id"]
        report["session"] = sid
        wait_for(lambda: len(hooks(o.hooks_log, "post_agent")) >= 2, 30)
        time.sleep(2)
        proc = subprocess.run(["vibe", "-p", "Answer exactly LEASEOK and nothing else.", "--resume", sid,
                               "--max-price", "0.02", "--max-turns", "1", "--trust"],
                              capture_output=True, text=True, env=env, cwd=o.work, timeout=120)
        report["vibe -p --resume while the TUI holds it"] = {
            "rc": proc.returncode, "stdout": proc.stdout.strip()[-300:],
            "stderr": [l for l in proc.stderr.splitlines() if "keyring" not in l and "teleport" not in l
                       and "Custom Python" not in l][-5:]}
        report["vibe-acp session/load while the TUI holds it"] = acp_load(env, o.work, sid)
        time.sleep(3)
        report["LEASEOK on the TUI screen after"] = bool(tui.first_time("LEASEOK", t_send))
    finally:
        tui.stop()
    time.sleep(2)
    report["vibe-acp session/load once the TUI is closed"] = acp_load(env, o.work, sid)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()

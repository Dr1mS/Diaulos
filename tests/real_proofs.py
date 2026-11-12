#!/usr/bin/env python3
"""Real vibe-acp checks of T3b and T5b, with paid model calls (docs/VIBE-DESIGN.md §4.4, §4.11, §6).

Run by tests/run-proofs.sh when DIAULOS_REAL_VIBE=1; run it alone only on purpose.
Everything is throwaway: a bridge home with a copy of bridge/, a VIBE_HOME with a copy of
~/.vibe/config.toml (read only; save_dir moved there, update checks off, active_model set
to devstral-small), and the shim installed in it by install/vibe_install.py (task T4).
The real HOME is kept for vibe's API key.

Checks, each printed with its measures:
  1. tools: vibe's own loader lists bridge_send and bridge_list_agents (no model call);
  2. vibe:new@realproof starts a conversation (turn limit 5);
  3. 3 passes of `bridge.py send vibe:realproof`: time from send to the start of the turn
     (in-flight record written by vibe-run, just before session/prompt) and to the reply;
     session/load in a new vibe-acp with the shim and the plugin loaded; same session id
     and folder; the peer file says price_cap true;
  4. max_turns 1 stops at the turn limit, and the sender gets a [bridge] note (P3);
  5. a price cap of $0.001 in vibe-run stops a prompt of 8 chained bash calls on the way; the
     plugin log says the cap is set, the peer file says price_cap true, vibe-run logs
     `stopped at price cap` and the sender gets a [bridge] note (P3);
  6. the next message to that conversation starts with the cut note, once (P4); check 5's
     message has it too, after the turn-limit cut of check 4.
Price: --budget is the budget of the whole run (default 0.10 $, at most 0.50 $). Each
vibe-acp gets as cap what is left of it, the cost of every session (meta.json
stats.session_cost) is added up after each check, and the run stops with FAIL above it.
The last line is `total <cost>`, read by run-proofs.sh.

Usage: python3 tests/real_proofs.py [--budget 0.1] [--keep]
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOAD = ("import sys; from pathlib import Path; from vibe.core.tools.manager import ToolManager; "
        "t = ToolManager._load_tools_from_file(Path(sys.argv[1])); print(sorted(x.get_name() for x in t))")


def wait_for(check, timeout, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        value = check()
        if value:
            return value
        time.sleep(step)
    return check()


def vibe_python():
    """The Python that runs vibe: the interpreter of the `vibe` script (uv tool or pipx)."""
    vibe = shutil.which("vibe")
    if not vibe:
        return None
    with open(os.path.realpath(vibe), "rb") as f:
        first = f.readline().decode("utf-8", "replace").strip()
    return first[2:].strip() if first.startswith("#!") else None


class Proofs:
    def __init__(self, budget, keep):
        self.budget = budget
        self.keep = keep
        self.tmp = tempfile.mkdtemp(prefix="diaulos-proofs-")
        self.bridge_home = os.path.join(self.tmp, "bridge")
        self.vibe_home = os.path.join(self.tmp, "vibe_home")
        self.work = os.path.join(self.tmp, "work")
        self.save_dir = os.path.join(self.vibe_home, "logs", "session")
        for d in (self.bridge_home, self.vibe_home, self.work, os.path.join(self.bridge_home, "state")):
            os.makedirs(d, exist_ok=True)
        for name in os.listdir(os.path.join(ROOT, "bridge")):
            if name.endswith(".py"):
                shutil.copy(os.path.join(ROOT, "bridge", name), self.bridge_home)
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
        with open(os.path.join(self.vibe_home, "config.toml"), "w", encoding="utf-8") as f:
            f.write(config)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_BRIDGE_")}
        self.env.update(VIBE_HOME=self.vibe_home, AGENT_BRIDGE_HOME=self.bridge_home)
        out = subprocess.run([sys.executable, os.path.join(ROOT, "install", "vibe_install.py"), "install", ROOT,
                              self.bridge_home, self.vibe_home, "1"], capture_output=True, text=True, check=True)
        print(out.stdout.strip(), flush=True)
        self.shim = os.path.join(self.vibe_home, "tools", "agent_bridge.py")
        self.inbox = []
        self.peaks, self.banked = {}, 0.0  # see spent()

    # -- helpers

    def bridge(self, *args):
        proc = subprocess.run([sys.executable, os.path.join(self.bridge_home, "bridge.py"), *args],
                              capture_output=True, text=True, env=self.env, timeout=60)
        if proc.returncode:
            raise RuntimeError(f"bridge.py {args[0]}: {proc.stderr or proc.stdout}")
        return proc.stdout

    def limits(self, max_turns, max_price=None):
        left = self.budget - self.check_budget("before a vibe-acp")
        price = min(left, 0.10) if max_price is None else max_price
        with open(os.path.join(self.bridge_home, "state", "vibe-limits.json"), "w") as f:
            json.dump({"max_turns": max_turns, "max_price": round(max(price, 0.001), 4)}, f)

    def log(self, name):
        try:
            with open(os.path.join(self.bridge_home, "state", name), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def sessions(self):
        found = []
        for d in sorted(os.listdir(self.save_dir)) if os.path.isdir(self.save_dir) else []:
            try:
                with open(os.path.join(self.save_dir, d, "meta.json"), encoding="utf-8") as f:
                    meta = json.load(f)
            except (OSError, ValueError):
                continue
            found.append({"folder": d, "id": meta.get("session_id"),
                          "cost": (meta.get("stats") or {}).get("session_cost") or 0})
        return found

    def messages(self, folder):
        try:
            with open(os.path.join(self.save_dir, folder, "messages.jsonl"), encoding="utf-8") as f:
                return f.read()
        except OSError:
            return ""

    def peer(self, suffix):
        try:
            with open(os.path.join(self.bridge_home, "peers", f"vibe-{suffix}.json")) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def spent(self):
        """Cost of the run. A vibe-acp that loads a session counts from zero again and rewrites
        meta.json, so the highest value of each folder is kept and banked when it goes down."""
        for s in self.sessions():
            peak = self.peaks.get(s["folder"], 0)
            if s["cost"] < peak:
                self.banked += peak
            self.peaks[s["folder"]] = s["cost"] if s["cost"] < peak else max(peak, s["cost"])
        return self.banked + sum(self.peaks.values())

    def check_budget(self, where):
        spent = self.spent()
        if spent > self.budget:
            raise RuntimeError(f"run total ${spent:.4f} is above the budget ${self.budget} ({where}): stopped")
        return spent

    def new_conversation(self, text, name=None, sender="ext:proof"):
        """vibe:new (named or not); returns the new session, once vibe-new has started it."""
        before = {s["id"] for s in self.sessions()}
        count = self.log("vibe-new.log").count(" started ")
        self.bridge("send", f"vibe:new@{name}" if name else "vibe:new", text, "--from", sender,
                    "--workspace", self.work)
        if not wait_for(lambda: self.log("vibe-new.log").count(" started ") > count
                        or "failed" in self.log("vibe-new.log"), 240, 0.2):
            raise RuntimeError("vibe:new did not start:\n" + self.log("vibe-new.log"))
        wait_for(lambda: [s for s in self.sessions() if s["id"] not in before], 10)
        new = [s for s in self.sessions() if s["id"] not in before]
        if len(new) != 1:
            raise RuntimeError(f"vibe:new made {len(new)} sessions:\n" + self.log("vibe-new.log"))
        return new[0]

    # -- checks

    def check_tools(self):
        python = vibe_python()
        if not python:
            raise RuntimeError("vibe is not on PATH")
        proc = subprocess.run([python, "-c", LOAD, self.shim], capture_output=True, text=True,
                              env=dict(self.env, VIBE_HOME=os.path.join(self.tmp, "vibe-own-files")), timeout=120)
        tools = proc.stdout.strip()
        print(f"1. tools seen by vibe's loader: {tools}", flush=True)
        assert tools == "['bridge_list_agents', 'bridge_send']", proc.stderr

    def check_runner(self):
        self.limits(5)
        first = self.new_conversation("Answer with the single word OK0. Do not call any tool.", "realproof")
        suffix = first["id"][-12:]
        print(f"2. vibe:new@realproof started vibe:{suffix}, cost ${first['cost']}", flush=True)
        record = os.path.join(self.bridge_home, "state", "vibe-inflight", f"{suffix}.json")
        log = f"vibe-{suffix}.log"
        starts, replies = [], []
        for i in range(1, 4):
            self.limits(5)
            delivered = self.log(log).count(" delivered ")
            sent = time.time()
            self.bridge("send", "vibe:realproof", f"Answer with the single word OK{i}. Do not call any tool.",
                        "--from", "ext:proof")
            at = wait_for(lambda: _read_at(record), 60, 0.01)
            if not wait_for(lambda: self.log(log).count(" delivered ") > delivered, 240, 0.05):
                raise RuntimeError(f"pass {i} not delivered:\n" + self.log(log))
            replies.append(time.time() - sent)
            starts.append((at - sent) if at else None)
            wait_for(lambda: not os.path.exists(record), 10)
        after = self.sessions()
        peer = self.peer(suffix)
        print(f"3. send to start of turn: {_fmt(starts)} (median {_fmt([_median(starts)])}); "
              f"send to reply: {_fmt(replies)}", flush=True)
        print(f"   sessions {[(s['folder'], s['id']) for s in after]}; peer price_cap {peer.get('price_cap')}",
              flush=True)
        print("   " + self.log(log).strip().replace("\n", "\n   "), flush=True)
        assert [(s["folder"], s["id"]) for s in after] == [(first["folder"], first["id"])], "id or folder changed"
        assert peer.get("price_cap") is True, peer
        assert None not in starts and _median(starts) < 3.0, starts
        assert "OK3" in self.messages(first["folder"])

    def notes(self):
        """[bridge, not an agent] notes in the shared `claude` inbox (the sender of checks 4 to 6)."""
        out = self.bridge("recv", "claude", "--json")
        self.inbox += [json.loads(line) for line in out.splitlines()]
        return [m["body"] for m in self.inbox if m["body"].startswith("[bridge, not an agent]")]

    def check_turn_limit(self):
        self.limits(1)
        s = self.new_conversation("Run the bash command `echo one`, then the bash command `echo two`, then the bash "
                                  "command `echo three`, one call each, then answer DONE.", sender="claude")
        self.limit_conv = s
        line = [ln for ln in self.log("vibe-new.log").splitlines() if s["id"] in ln][-1]
        note = [n for n in self.notes() if "turn limit" in n]
        print(f"4. max_turns 1: {line.split(' ', 1)[1]}\n   note: {note}", flush=True)
        assert "(turn limit reached)" in line, line
        assert note, "no [bridge] note for the turn limit"

    def check_price_cap(self):
        """vibe checks its price limit before each model call of a prompt (_loop.py:1305-1314), on the
        cost counted in this vibe-acp process. One message asks for 8 bash calls that must run one
        after the other (each one uses the output of the one before), so each is its own model call;
        at about $0.0006 a call with devstral-small, the cap of $0.001 stops the prompt on the way.
        (12 independent calls went out in parallel in 2 model calls, under the cap: tried, 12 of 12 ran.)"""
        self.limits(20, max_price=0.001)
        s = self.limit_conv
        suffix = s["id"][-12:]
        log = f"vibe-{suffix}.log"
        before = self.messages(s["folder"])
        steps = 8
        self.cut = s
        self.bridge("send", f"vibe:{suffix}", f"Step 1: run the bash command `echo s1-$RANDOM`. Then, for steps 2 to "
                    f"{steps}, run `echo s<step>-<the number printed by the previous step>` and wait for its output "
                    "before the next step: one bash call per step, never two at once. Then answer DONE.",
                    "--from", "claude")
        if not wait_for(lambda: "stopped at price cap" in self.log(log) or " delivered " in self.log(log)
                        or "dropped" in self.log(log), 300, 0.2):
            raise RuntimeError("price-cap message not delivered:\n" + self.log(log))
        new = self.messages(s["folder"])[len(before):]
        ran = sorted(set(re.findall(r"echo s([1-9])-", new)))
        cost = [c["cost"] for c in self.sessions() if c["id"] == s["id"]][0]
        peer = self.peer(suffix)
        cap_set = "price cap $0.001 set" in self.log("vibe-plugin.log")
        stop = re.search(r"Price limit exceeded[^\"<\\]*", new)
        print(f"5. price cap $0.001 in vibe-run: steps {ran} of {steps} ran; session_cost ${cost} "
              f"(the folder's, all processes); plugin log 'price cap $0.001 set': {cap_set}; "
              f"{stop.group(0) if stop else 'stop text not saved in messages.jsonl'}; peer price_cap {peer.get('price_cap')}",
              flush=True)
        print("   " + self.log(log).strip().replace("\n", "\n   "), flush=True)
        note = [n for n in self.notes() if "price cap" in n]
        print(f"   note: {note}", flush=True)
        assert peer.get("price_cap") is True, peer
        assert cap_set
        assert len(ran) < steps, f"all {steps} steps ran: the cap did not stop the prompt"
        assert "stopped at price cap" in self.log(log), "vibe-run logged the cut turn as delivered"
        assert note, "no [bridge] note for the price cap"
        assert os.path.exists(os.path.join(self.bridge_home, "state", "vibe-cut", suffix))

    def check_cut_note(self):
        """The next text delivered after a cut turn starts with the cut note, once (§4.4, P4)."""
        s = self.cut
        suffix = s["id"][-12:]
        # The vibe-run of check 5 keeps its $0.001 cap until it exits: wait, so a new one reads the new limits.
        if not wait_for(lambda: not _alive(self.peer(suffix).get("runner_pid")), 60, 0.2):
            raise RuntimeError("the vibe-run of check 5 did not exit")
        self.limits(5)
        log = f"vibe-{suffix}.log"
        count = self.log(log).count(" delivered ")
        self.bridge("send", f"vibe:{suffix}", "Answer with the single word NEXT. Do not call any tool.",
                    "--from", "ext:proof")
        if not wait_for(lambda: self.log(log).count(" delivered ") > count, 240, 0.2):
            raise RuntimeError("message after the cut not delivered:\n" + self.log(log))
        note = "Note: the previous bridge turn here stopped at a limit"
        users = [str(m.get("content")) for m in map(json.loads, self.messages(s["folder"]).splitlines())
                 if m.get("role") == "user"]
        with_note = [u for u in users if u.startswith(note)]
        print(f"6. after the cut: {len(with_note)} user message(s) start with the note, the NEXT one: "
              f"{bool(with_note) and 'single word NEXT' in with_note[-1]}; cut file left: "
              f"{os.path.exists(os.path.join(self.bridge_home, 'state', 'vibe-cut', suffix))}", flush=True)
        # Check 4 cut this conversation at its turn limit, so check 5's message has the note too:
        # one note per cut, each in the first text after it, and the NEXT text has exactly one.
        assert users[0].startswith("Message(s) from another agent"), users[0][:120]
        assert len(with_note) == 2 and "single word NEXT" in with_note[-1], [u[:120] for u in users]
        assert with_note[-1].count(note) == 1 and users[-1] is with_note[-1]
        assert not os.path.exists(os.path.join(self.bridge_home, "state", "vibe-cut", suffix))

    def run(self):
        try:
            for check in (self.check_tools, self.check_runner, self.check_turn_limit, self.check_price_cap,
                          self.check_cut_note):
                check()
                self.check_budget(f"after {check.__name__}")
            print("PASS", flush=True)
            return 0
        except (AssertionError, RuntimeError, subprocess.SubprocessError) as e:
            print(f"FAIL: {e!r}", flush=True)
            return 1
        finally:
            wait_for(lambda: not self.workers(), 60, 0.5)  # vibe-run and vibe-new exit on their own
            total = self.spent()
            print(f"cost per session (highest value seen): {sorted(self.peaks.items())}; "
                  f"earlier vibe-acp processes ${self.banked:.4f}", flush=True)
            if self.keep:
                print(f"kept: {self.tmp}", flush=True)
            else:
                shutil.rmtree(self.tmp, ignore_errors=True)
            print(f"total {total:.4f}", flush=True)

    def workers(self):
        """Live vibe-run / vibe-new processes of this bridge home."""
        alive = []
        folder = os.path.join(self.bridge_home, "peers")
        for name in os.listdir(folder) if os.path.isdir(folder) else []:
            try:
                with open(os.path.join(folder, name)) as f:
                    peer = json.load(f)
            except (OSError, ValueError):
                continue
            for key in ("runner_pid", "pid", "acp_pid"):
                pid = peer.get(key)
                if isinstance(pid, int) and _alive(pid):
                    alive.append(pid)
        return alive


def _alive(pid):
    if not isinstance(pid, int) or pid <= 0:  # os.kill(0, 0) would signal our own group
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_at(path):
    try:
        with open(path) as f:
            return json.load(f).get("at")
    except (OSError, ValueError):
        return None


def _median(values):
    values = sorted(v for v in values if v is not None)
    return values[len(values) // 2] if values else None


def _fmt(values):
    return ", ".join("?" if v is None else f"{v:.2f}s" for v in values)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--budget", type=float, default=0.1, help="budget of the whole run, in $")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    if not 0 < a.budget <= 0.5:
        sys.exit("the budget of a run is above 0 and at most 0.50 $")
    sys.exit(Proofs(a.budget, a.keep).run())


if __name__ == "__main__":
    main()

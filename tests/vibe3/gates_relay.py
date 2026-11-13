#!/usr/bin/env python3
"""Gates R1 to R9 of the relay (docs/VIBE-DESIGN.md §8.9.9), in a throwaway environment. Linux only.

    python3 tests/vibe3/gates_relay.py OUT GATE [--passes N]

OUT is made by `t14_env.py OUT`; GATE is r1 to r9. The TUI opens with vibe-diaulos
(OUT/ab/vibe_diaulos.py) in OUT/work, with VIBE_HOME=OUT/vh, AGENT_BRIDGE_HOME=OUT/ab and
HOME=OUT/home. Messages are sent with `bridge.py send ... --from ext:gate-r`: an `ext:` sender with
no reader, so the model is told not to answer it with `send`. Each message asks for one short
word: one model call per delivered turn (devstral-small). Turn counts and tokens are in the report.

The report (JSON on stdout, OUT written <jetable>) holds, per pass, what the gate's criterion
reads: relay log rows (whitelisted keys only), hook log rows (booleans and Diaulos ids only),
the session's entry order (projection-state.json: history_positions), the relay state file, and
for the display, whether the screen shows the sender and the text.
"""
import glob
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)

from gates_tui import screen_text  # noqa: E402
from t14_env import env_of  # noqa: E402
from tui_probe import Tui, wait_for  # noqa: E402

SENDER = "ext:gate-r"
LONG = "count from 1 to 40, one number per line, then write the word %s"


class Run:
    def __init__(self, out):
        self.out = out
        self.ab = os.path.join(out, "ab")
        self.save = os.path.join(out, "vh", "logs", "session")
        self.env = env_of(out, home=True)
        self.env["AGENT_BRIDGE_VIBE_RELAY_CHECK"] = "1"
        self.turns = 0
        self.sessions = set()

    # ---------------------------------------------------------- the TUI and its relay

    def open(self, args=()):
        before = set(glob.glob(os.path.join(self.ab, "state", "vibe-relay", "*.json")))
        tui = Tui(os.path.join(self.out, "work"), self.env, ["--trust"] + list(args),
                  command=[sys.executable, os.path.join(self.ab, "vibe_diaulos.py")])
        tui.started = time.time()
        tui.drew = wait_for(lambda: len(tui.text()) > 200, 30) and time.time() - tui.started
        tui.relay = wait_for(lambda: [p for p in glob.glob(os.path.join(self.ab, "state", "vibe-relay", "*.json"))
                                      if p not in before and (self.read(p) or {}).get("session")], 30)
        tui.relay = tui.relay[0] if tui.relay else None
        return tui

    @staticmethod
    def read(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def state(self, tui):
        return self.read(tui.relay) if tui.relay else None

    def session(self, tui):
        sid = (self.state(tui) or {}).get("session")
        if sid:
            self.sessions.add(sid)
        return sid

    def current(self, tui):
        """The session of the TUI, from the session_start hook's file of its vibe-app-server."""
        real = (self.state(tui) or {}).get("real_pid")
        return (self.read(os.path.join(self.ab, "state", "vibe-current", "%s.json" % real)) or {}).get("session")

    # ---------------------------------------------------------- messages and logs

    def send(self, suffix, text):
        """(Diaulos id, time just before the send command started)."""
        at = time.time()
        proc = subprocess.run([sys.executable, os.path.join(self.ab, "bridge.py"), "send", "vibe:" + suffix, text,
                               "--from", SENDER], env=self.env, capture_output=True, text=True)
        m = re.search(r"sent ([0-9a-f]{12}) ", proc.stdout)
        return (m.group(1) if m else None), at

    def relay_rows(self, since=0.0):
        try:
            with open(os.path.join(self.ab, "state", "vibe-relay.log"), encoding="utf-8") as f:
                return [r for r in (json.loads(l) for l in f if l.strip()) if r.get("at", 0) >= since]
        except OSError:
            return []

    def hook_rows(self, since=0.0):
        rows = []
        try:
            with open(os.path.join(self.ab, "state", "vibe-hook.log"), encoding="utf-8") as f:
                for line in f:
                    parts = line.rstrip("\n").split(" ", 4)
                    try:
                        at = float(parts[1])
                    except (IndexError, ValueError):
                        continue  # a line of an older hook, without its epoch
                    if len(parts) == 5 and at >= since:
                        rows.append({"at": at, "text": parts[4]})
        except OSError:
            pass
        return rows

    def pre_agent_after(self, since):
        rows = [r for r in self.hook_rows(since) if r["text"].startswith("pre_agent") and "delivery" in r["text"]]
        return rows[0] if rows else None

    def projection(self, sid):
        paths = sorted(glob.glob(os.path.join(self.save, "unified", sid, "generations", "*", "projection-state.json")))
        return self.read(paths[-1]) if paths else None

    def entries(self, sid):
        """Kinds of the session's entries in order: delivery, person, assistant, hook."""
        api = (self.projection(sid) or {}).get("session_api") or {}
        order = sorted((api.get("history_positions") or {}).items(), key=lambda kv: kv[1])
        kinds = []
        for entry, _ in order:
            kinds.append("delivery" if entry.startswith("diaulos-") else "assistant" if entry.startswith("assistant-")
                         else "hook" if entry.startswith("hook-") else "person")
        return [k for k in kinds if k != "hook"]

    def turn_count(self, sid):
        api = (self.projection(sid) or {}).get("session_api") or {}
        return len(api.get("turn_outcomes") or {})

    def idle(self, sid, turns):
        """True when the session has `turns` finished turns."""
        return self.turn_count(sid) >= turns and ((self.projection(sid) or {}).get("snapshot") or {}).get(
            "latestTurn", {}).get("status") in ("completed", "interrupted", "failed")

    def discarded(self, sid):
        """Answers vibe marked discarded (a turn revision, "cause": "retry") in the session's journal."""
        count = 0
        for path in glob.glob(os.path.join(self.save, "unified", sid, "journal", "*.jsonl")):
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            count += len(re.findall(r'"(?:status|state|outcome)":\s*"discarded"|"cause":\s*"retry"', text))
        return count

    def tokens(self):
        total = [0, 0]
        for sid in self.sessions:
            usage = (((self.projection(sid) or {}).get("snapshot") or {}).get("session") or {}).get("tokenUsage") or {}
            total[0] += usage.get("inputTokens") or 0
            total[1] += usage.get("outputTokens") or 0
        return total

    def turns_total(self):
        return sum(self.turn_count(sid) for sid in self.sessions)

    def scrub(self, value):
        return json.loads(json.dumps(value).replace(self.out, "<jetable>"))


# -------------------------------------------------------------- one delivery

def deliver(run, tui, word, turns_before, text=None):
    sid = run.session(tui)
    msg, at = run.send(sid[-12:], text or "Answer with the single word %s and nothing else. Do not call any tool." % word)
    hook = wait_for(lambda: run.pre_agent_after(at), 10)
    done = wait_for(lambda: run.idle(sid, turns_before + 1), 90)
    rows = [r for r in run.relay_rows(at) if r.get("msgs") == [msg]]
    screen = screen_text(tui)
    shown = screen.split("<bridge-message id=\"%s\"" % msg)[-1] if msg else ""
    return {
        "msg": msg,
        "relay rows": [{k: r[k] for k in ("dir", "reason") if k in r} for r in rows],
        "enqueue answered after s": round(next((r["at"] for r in rows if r["dir"] == "delivered"), at) - at, 3),
        "pre_agent after s": round(hook["at"] - at, 3) if hook else None,
        "pre_agent row": hook and hook["text"].split(": ", 1)[-1],
        "turn finished": bool(done),
        "screen shows the sender": SENDER in screen,
        "screen shows the text and the answer": bool(msg) and word in shown,
    }


# -------------------------------------------------------------- gates

def r1(run, passes):
    """Routing to a stub, TUI draw time, added delay per line (fake server), -p and --version for real."""
    stub_log = os.path.join(run.out, "stub-args.jsonl")
    stub = os.path.join(run.out, "stub-vibe")
    with open(stub, "w", encoding="utf-8") as f:
        f.write("#!%s\nimport json, sys\nwith open(%r, 'a') as f:\n    f.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                % (sys.executable, stub_log))
    os.chmod(stub, 0o755)
    uses = [["-p", "hi"], ["--prompt-file", "x.txt"], ["mcp", "list"], ["config"], ["help"], ["update"],
            ["-v"], ["--version"], ["-h"], ["--help"], ["--setup"], ["--check-upgrade"]]
    relays_before = len(run.relay_rows())
    routed = []
    for use in uses:
        env = dict(run.env, AGENT_BRIDGE_VIBE_REAL=stub)
        subprocess.run([sys.executable, os.path.join(run.ab, "vibe_diaulos.py")] + use, env=env, timeout=30,
                       capture_output=True)
        with open(stub_log, encoding="utf-8") as f:
            last = json.loads(f.read().splitlines()[-1])
        routed.append({"use": use, "stub got the same arguments": last == use})
    starts = [r for r in run.relay_rows() if r.get("dir") == "start"][relays_before:]
    draws = []
    for _ in range(passes):
        tui = run.open()
        draws.append(round(tui.drew or -1, 2))
        tui.stop()
    delays = [line_delay(run) for _ in range(3)]
    starts_before_p = len([r for r in run.relay_rows() if r.get("dir") == "start"])
    real_version = subprocess.run([sys.executable, os.path.join(run.ab, "vibe_diaulos.py"), "--version"],
                                  env=run.env, capture_output=True, text=True, timeout=60)
    p_dir = os.path.join(run.out, "r1-p")
    os.makedirs(p_dir, exist_ok=True)
    real_p = subprocess.run([sys.executable, os.path.join(run.ab, "vibe_diaulos.py"), "-p",
                             "Answer with the single word PONGP and nothing else.", "--max-price", "0.50",
                             "--max-turns", "2", "--output-dir", p_dir], cwd=os.path.join(run.out, "work"),
                            env=run.env, capture_output=True, text=True, timeout=180)
    export = run.read(os.path.join(p_dir, "export.json")) or {}
    return {
        "non-TUI uses routed to the stub": routed,
        "relay starts during routing": len(starts),
        "TUI drew after s": draws,
        "added delay per line, ms (median of 100 lines, 3 runs)": delays,
        "real --version": real_version.stdout.strip(),
        "real -p rc, stdout": [real_p.returncode, real_p.stdout.strip()[:40]],
        "relay starts during real --version and -p": len([r for r in run.relay_rows() if r.get("dir") == "start"])
        - starts_before_p,
        "real -p cost_usd": export.get("cost_usd"),
    }


def line_delay(run):
    """Median time, in ms, from a line written to the relay to its read by the fake server, minus the same
    without the relay, over 100 lines."""
    fake = os.path.join(REPO, "tests", "fakes", "stamp_server.py")

    def measure(argv):
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=dict(run.env))
        sent = []
        for i in range(100):
            sent.append(time.time())
            proc.stdin.write((json.dumps({"jsonrpc": "2.0", "id": i, "method": "ping"}) + "\n").encode())
            proc.stdin.flush()
            time.sleep(0.005)
        proc.stdin.close()
        got = [float(l) for l in proc.stdout.read().decode().split()]
        proc.wait(10)
        return statistics.median(g - s for g, s in zip(got, sent)) * 1000

    direct = measure([sys.executable, fake])
    relayed = measure([sys.executable, os.path.join(run.ab, "vibe_relay.py"), "--home", run.ab, "--real",
                       sys.executable, fake])
    return round(relayed - direct, 3)


def r2(run, passes):
    """The relay's session after start, resume, /branch and /clear, 1 s after the hook's file changed."""
    found = []
    for i in range(passes):
        tui = run.open()
        row = {"start": [run.session(tui) == run.current(tui), bool(run.current(tui))]}
        sid = run.session(tui)
        deliver(run, tui, "R2START%d" % i, 0)
        # /branch copies the session and leaves the TUI on it: the relay must stay on sid.
        tui.type("/branch")
        time.sleep(0.5)
        tui.type("\r")
        time.sleep(3)
        row["/branch (copy, the TUI stays)"] = [run.session(tui) == run.current(tui) == sid,
                                                "vibe --resume" in screen_text(tui)]
        tui.type("\x1b")  # close the dialog of /branch
        time.sleep(1)
        before = run.current(tui)
        tui.type("/clear")
        time.sleep(0.5)
        tui.type("\r")
        wait_for(lambda: run.current(tui) and run.current(tui) != before, 15)
        time.sleep(1)
        row["/clear"] = [run.session(tui) == run.current(tui), run.current(tui) != before]
        tui.stop()
        time.sleep(1)
        tui = run.open(["--resume", sid])
        # the TUI answers --resume with session/start (a new empty session), then session/resume:
        # the action ends when the resumed session is the TUI's (its session_start hook ran)
        wait_for(lambda: run.current(tui) == sid, 15)
        time.sleep(1)
        row["resume"] = [run.session(tui) == run.current(tui) == sid, run.current(tui) == sid]
        tui.stop()
        found.append(row)
    return {"[relay session equals the TUI's, the TUI's session changed]": found}


def r3_r5_r6(run, passes):
    """R5: a delivery before the person typed anything (fresh TUI); R3: three more in the same TUI,
    idle; R6: display and header for every delivery."""
    found = {"R5": [], "R3": []}
    tui = None
    for i in range(passes):
        tui = run.open()
        found["R5"].append(deliver(run, tui, "PONGF%d" % i, 0))
        if i < passes - 1:
            tui.stop()
    for i in range(passes):
        found["R3"].append(deliver(run, tui, "PONGI%d" % i, i + 1))
    found["entries of the last TUI"] = run.entries(run.session(tui))
    tui.stop()
    return found


def r4(run, passes):
    """A second message (B) while the first one's turn (A) runs: queued behind it (two turns) or
    joined to it (one turn); either way each is once in the session, and B's word is answered."""
    found = []
    tui = run.open()
    sid = run.session(tui)
    for i in range(passes):
        turns, entries, discarded = run.turn_count(sid), len(run.entries(sid)), run.discarded(sid)
        a, at = run.send(sid[-12:], "Do not call any tool: " + LONG % ("DONEA%d" % i))
        wait_for(lambda: run.pre_agent_after(at), 10)
        b, bt = run.send(sid[-12:], "Answer with the single word PONGB%d and nothing else. Do not call any tool." % i)
        wait_for(lambda: run.entries(sid)[entries:].count("delivery") >= 2 and run.idle(sid, turns + 1), 120)
        time.sleep(4)  # a queued B starts its own turn now
        wait_for(lambda: run.idle(sid, run.turn_count(sid)), 60)
        rows = [r for r in run.relay_rows(at) if r.get("msgs") in ([a], [b])]
        added = run.entries(sid)[entries:]
        found.append({"relay rows": [{k: r[k] for k in ("dir", "reason", "msgs") if k in r} for r in rows],
                      "B sent while A's turn ran": any(r.get("msgs") == [b] and r["dir"] == "send"
                                                       and r.get("reason") == "busy" for r in rows),
                      "turns used": run.turn_count(sid) - turns,
                      "entries added": added,
                      "deliveries added (2 = each once)": added.count("delivery"),
                      "answers marked discarded": run.discarded(sid) - discarded,
                      "B answered on screen": ("PONGB%d" % i) in screen_text(tui).split("PONGB%d and nothing" % i)[-1]})
    tui.stop()
    return found


def r7(run, passes):
    """The person's Enter during a delivered turn; Esc during one; Enter and a delivery at once."""
    found = {"enter": [], "esc": [], "same instant": []}
    tui = run.open()
    sid = run.session(tui)
    for i in range(passes):
        turns = run.turn_count(sid)
        a, at = run.send(sid[-12:], "Do not call any tool: " + LONG % ("DONEE%d" % i))
        wait_for(lambda: run.pre_agent_after(at), 10)
        time.sleep(0.3)
        tui.type("Answer with the single word PERSON%d and nothing else.\r" % i)
        done = wait_for(lambda: run.idle(sid, turns + 2), 120)
        found["enter"].append({"turns finished": bool(done), "entries of the 2 turns": run.entries(sid)[-4:],
                               "PERSON answered on screen": ("PERSON%d" % i) in screen_text(tui).split(
                                   "PERSON%d and nothing" % i)[-1]})
    for i in range(passes):
        turns = run.turn_count(sid)
        a, at = run.send(sid[-12:], "Do not call any tool: count from 1 to 300, one number per line.")
        wait_for(lambda: run.pre_agent_after(at), 10)
        time.sleep(1.0)
        tui.type("\x1b")
        done = wait_for(lambda: run.idle(sid, turns + 1), 60)
        time.sleep(2)
        inbox = glob.glob(os.path.join(run.ab, "inbox", "vibe-" + sid[-12:], "*.json"))
        found["esc"].append({"turn ended": bool(done),
                             "status": ((run.projection(sid) or {}).get("snapshot") or {}).get("latestTurn", {}).get("status"),
                             "put back rows": len([r for r in run.relay_rows(at) if r.get("dir") == "put back"]),
                             "inbox": len(inbox)})
    for i in range(passes):
        turns, entries = run.turn_count(sid), len(run.entries(sid))
        tui.type("Answer with the single word SAME%d and nothing else." % i)
        time.sleep(0.5)
        tui.type("\r")
        run.send(sid[-12:], "Answer with the single word SAMED%d and nothing else. Do not call any tool." % i)
        wait_for(lambda: len(run.entries(sid)[entries:]) >= 4 and run.idle(sid, turns + 1), 120)
        time.sleep(4)
        wait_for(lambda: run.idle(sid, run.turn_count(sid)), 60)
        added = run.entries(sid)[entries:]
        found["same instant"].append({"turns used": run.turn_count(sid) - turns, "entries added": added,
                                      "person once, delivery once": [added.count("person"), added.count("delivery")]})
    found["invalid lines to the server"] = len([r for r in run.relay_rows() if r.get("dir") == "invalid line to server"])
    tui.stop()
    return found


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def r8(run, passes):
    """Ctrl+C; closing the terminal; killing the relay."""
    found = {"ctrl+c": [], "close": [], "kill relay": []}
    for _ in range(passes):
        tui = run.open()
        st = run.state(tui)
        tui.type("\x03")
        time.sleep(1.5)
        found["ctrl+c"].append({"TUI alive": alive(tui.pid) and not os.waitpid(tui.pid, os.WNOHANG)[0],
                                "relay alive": alive(st["relay_pid"])})
        tui.stop()
    for _ in range(passes):
        tui = run.open()
        st = run.state(tui)
        os.kill(tui.pid, signal.SIGHUP)
        os.waitpid(tui.pid, 0)
        time.sleep(5)
        found["close"].append({"relay alive 5 s after": alive(st["relay_pid"]),
                               "vibe-app-server alive 5 s after": alive(st["real_pid"])})
    for _ in range(passes):
        tui = run.open()
        st = run.state(tui)
        os.kill(st["relay_pid"], signal.SIGKILL)
        time.sleep(3)
        screen = screen_text(tui)
        tui.stop()
        ended = time.time()
        def line():
            text = subprocess.run([sys.executable, os.path.join(run.ab, "bridge.py"), "list", "--all"], env=run.env,
                                  capture_output=True, text=True).stdout
            found_line = [l for l in text.splitlines() if l.startswith("vibe:" + st["session"][-12:])]
            return found_line[0].split("  ")[-1] if found_line else "not listed (no saved message)"
        listed = wait_for(lambda: "TUI" not in line() and line(), 5, 0.2)
        found["kill relay"].append({"known screen": "App server closed unexpectedly" in screen,
                                    "listed closed after s": round(time.time() - ended, 2) if listed else None,
                                    "list says": listed,
                                    "vibe-app-server alive 5 s after": (time.sleep(2) or alive(st["real_pid"]))})
    return found


def r9(run, passes):
    """Every key of the relay log is whitelisted; greps by category (results only, never the terms)."""
    path = os.path.join(run.ab, "state", "vibe-relay.log")
    kept = {"dir", "at", "method", "id", "bytes", "pid", "argc", "rc", "reason", "session", "msgs", "wake"}
    rows = run.relay_rows()
    with open(path, encoding="utf-8") as f:
        text = f.read()
    terms = os.environ.get("GATE_TERMS_FILE")
    account = None
    if terms and os.path.exists(terms):
        account = subprocess.run(["grep", "-ciF", "-f", terms, path], capture_output=True, text=True).stdout.strip()
    return {
        "rows": len(rows),
        "rows with another key": len([r for r in rows if set(r) - kept]),
        "e-mail pattern": len(re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text)),
        "personal path (/home/, /Users/, C:\\Users\\)": len(re.findall(r"/home/|/Users/|[A-Za-z]:\\\\Users", text)),
        "throwaway folder path": text.count(run.out),
        "message text (words asked of the model)": len(re.findall(r"PONG|PERSON|SAME|DONE|count from", text)),
        "account values (grep -ciF -f, mode-600 file)": account,
    }


def verdict(gate, r):
    """Each "fails if" of §8.9.9 for this gate, as {criterion: passed}."""
    if gate == "r1":
        delays = r["added delay per line, ms (median of 100 lines, 3 runs)"]
        return {"10 non-TUI uses reach the stub unchanged": all(u["stub got the same arguments"]
                                                                for u in r["non-TUI uses routed to the stub"]),
                "no relay starts for them": r["relay starts during routing"] == 0
                and r["relay starts during real --version and -p"] == 0,
                "TUI draws within 5 s": all(0 < d <= 5 for d in r["TUI drew after s"]),
                "median added delay <= 5 ms per line": statistics.median(delays) <= 5,
                "real --version and -p work": r["real --version"].startswith("vibe 3")
                and r["real -p rc, stdout"][0] == 0 and "PONGP" in r["real -p rc, stdout"][1]}
    if gate == "r2":
        rows = r["[relay session equals the TUI's, the TUI's session changed]"]
        return {"relay session = TUI session 1 s after %s" % k: all(row[k][0] for row in rows) for k in rows[0]}
    if gate == "r3":
        def ok(d, check):
            return all(check(x) for x in d)
        delivered = lambda d: any(x.get("dir") == "delivered" for x in d["relay rows"]) and not any(
            x.get("dir") in ("error", "put back") for x in d["relay rows"])
        within2 = lambda d: d["pre_agent after s"] is not None and d["pre_agent after s"] <= 2
        return {"R3: answer not an error, pre_agent within 2 s": ok(r["R3"], lambda d: delivered(d) and within2(d)),
                "R5: answer not an error, turn starts within 2 s": ok(r["R5"], lambda d: delivered(d) and within2(d)),
                "R6 display: sender and text on screen": ok(r["R3"] + r["R5"], lambda d: d["screen shows the sender"]
                                                            and d["screen shows the text and the answer"]),
                "R6 header (replacement test): pre_agent sees a relay delivery": ok(
                    r["R3"] + r["R5"], lambda d: (d["pre_agent row"] or "").startswith("delivery True"))}
    if gate == "r4":
        return {"no discarded answer": all(p["answers marked discarded"] == 0 for p in r),
                "each message once in the session": all(p["deliveries added (2 = each once)"] == 2 for p in r),
                "B answered": all(p["B answered on screen"] for p in r)}
    if gate == "r7":
        return {"Enter: person's message once, after the delivered turn": all(
            p["turns finished"] and p["entries of the 2 turns"] == ["delivery", "assistant", "person", "assistant"]
            and p["PERSON answered on screen"] for p in r["enter"]),
                "Esc: not put back": all(p["status"] == "interrupted" and p["put back rows"] == 0 and p["inbox"] == 0
                                         for p in r["esc"]),
                "same instant: each once": all(p["person once, delivery once"] == [1, 1] for p in r["same instant"]),
                "every line to the server is JSON": r["invalid lines to the server"] == 0}
    if gate == "r8":
        return {"Ctrl+C ends neither TUI nor relay": all(p["TUI alive"] and p["relay alive"] for p in r["ctrl+c"]),
                "nothing alive 5 s after the terminal closed": all(
                    not p["relay alive 5 s after"] and not p["vibe-app-server alive 5 s after"] for p in r["close"]),
                "killed relay: known screen, server gone": all(
                    p["known screen"] and not p["vibe-app-server alive 5 s after"] for p in r["kill relay"]),
                "list shows it closed within 2 s": all(
                    p["listed closed after s"] is not None and p["listed closed after s"] <= 2 for p in r["kill relay"])}
    if gate == "r9":
        return {"only whitelisted keys": r["rows"] > 0 and r["rows with another key"] == 0,
                "every grep by category gives 0": all(r[k] == 0 for k in (
                    "e-mail pattern", "personal path (/home/, /Users/, C:\\Users\\)", "throwaway folder path",
                    "message text (words asked of the model)")) and r["account values (grep -ciF -f, mode-600 file)"] == "0"}
    return {}


GATES = {"r1": r1, "r2": r2, "r3": r3_r5_r6, "r4": r4, "r7": r7, "r8": r8, "r9": r9}


def main(argv):
    out, gate = os.path.abspath(argv[1]), argv[2]
    passes = int(argv[argv.index("--passes") + 1]) if "--passes" in argv else 3
    run = Run(out)
    t0 = time.time()
    result = GATES[gate](run, passes)
    checks = verdict(gate, result)
    report = {"gate": gate, "passes": passes, "seconds": round(time.time() - t0, 1), "result": result,
              "TUI model turns": run.turns_total(), "tokens (input, output)": run.tokens(),
              "verdict": checks, "pass": bool(checks) and all(checks.values())}
    text = json.dumps(run.scrub(report), indent=1, ensure_ascii=False)
    with open(os.path.join(out, "gate-%s.json" % gate), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""T12 probe: can a stdio relay in front of vibe-app-server start a turn in an idle vibe 3 TUI?

Probe only (Linux). Needs `gates.py setup OUT` first (throwaway VIBE_HOME OUT/vh, devstral-small,
the gateprobe plugin whose hooks log to OUT/hooks.log). The real `vibe` launcher is not used:
it overwrites VIBE_APP_SERVER_BIN with the installed vibe-app-server (vibe/cli/launcher.py,
_app_server_env). OUT/bin/vibe is a link to the bundled vibe-rs, run with the relay
(tests/vibe3/relay_app_server.py) as its app-server.

    python3 tests/vibe3/relay_probe.py OUT REPORT_DIR

1. form: does vibe-rs start the relay from VIBE_APP_SERVER_BIN (a path), and from
   VIBE_APP_SERVER_CMD (a shell line)? No prompt typed: no model call.
2. inject: the person types one prompt (turn ONE); once idle, the relay sends the same request
   with another text. Is the turn run, shown, saved? Two short model calls on devstral-small,
   no price cap in the TUI: counted from the session's projection-state.json.
3. relay death: the relay is killed (SIGKILL) while idle. What do the TUI and the session do?
Writes REPORT_DIR/relay-report.json, relay-screens.txt, relay-hooks.log and relay-log-summary.jsonl
(the relay log: direction, time, method, id and size of each line, nothing else),
with paths of OUT replaced by <jetable>.
"""
import glob
import json
import os
import re
import shutil
import signal
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from gates import env_of  # noqa: E402
from gates_tui import screen_text  # noqa: E402
from tui_probe import Tui, wait_for  # noqa: E402
from wake_process import usage  # noqa: E402

SITE = os.path.expanduser("~/.local/share/uv/tools/mistral-vibe")
VIBE_RS = os.path.join(SITE, "lib", "python3.12", "site-packages", "vibe", "_bin", "vibe-rs")
REAL = os.path.join(SITE, "bin", "vibe-app-server")
RELAY = os.path.join(HERE, "relay_app_server.py")
TYPED = "Answer exactly ONE and nothing else."
INJECTED = "Answer exactly RELAYED and nothing else."


def summary(r):
    """The relay log is already a whitelist (relay_app_server.KEPT); the summary keeps the same keys."""
    row = {k: r[k] for k in ("dir", "at", "method", "id", "bytes", "pid", "argc", "rc", "reason") if k in r}
    if "at" in row:
        row["at"] = round(row["at"], 3)
    return row


def main(out, report_dir):
    os.makedirs(os.path.join(out, "bin"), exist_ok=True)
    link = os.path.join(out, "bin", "vibe")
    if not os.path.exists(link):
        os.symlink(VIBE_RS, link)
    gd, hooks, rlog, rdir = (os.path.join(out, n) for n in ("gd", "hooks.log", "relay.log", "rd"))
    os.makedirs(rdir, exist_ok=True)
    for name in os.listdir(gd):
        os.remove(os.path.join(gd, name))
    base = env_of(out)
    for k in ("VIBE_APP_SERVER_BIN", "VIBE_APP_SERVER_CMD", "VIBE_APP_SERVER_CWD"):
        base.pop(k, None)
    base.update(PATH=os.path.join(out, "bin") + os.pathsep + base["PATH"], RELAY_LOG=rlog,
                RELAY_DIR=rdir, RELAY_REAL=REAL, RELAY_TEMPLATE_TEXT=TYPED)
    report, screens = {}, []

    def rows(path):
        try:
            with open(path, encoding="utf-8") as f:
                return [json.loads(line) for line in f if line.strip()]
        except OSError:
            return []

    def hook_starts(event):
        return [r for r in rows(hooks) if r["kind"] == "start" and r.get("event") == event]

    def hook_ends(event):
        return [r for r in rows(hooks) if r["kind"] == "end" and r.get("event") == event]

    def shot(tui, label):
        screens.append("==== %s (t=%.3f)\n%s\n" % (label, time.time(), screen_text(tui)))

    # 1. form
    for name, extra in (("BIN (path)", {"VIBE_APP_SERVER_BIN": RELAY}),
                        ("CMD (shell line)", {"VIBE_APP_SERVER_CMD": "%s %s" % (sys.executable, RELAY)})):
        open(rlog, "w").close()
        tui = Tui(os.path.join(out, "work"), dict(base, **extra), ["--trust"])
        try:
            drew = wait_for(lambda: len(tui.text()) > 200, 30)
            time.sleep(4)
            shot(tui, "form " + name)
            starts = [r for r in rows(rlog) if r["dir"] == "start"]
            report["1 form " + name] = {
                "TUI drew": bool(drew),
                "relay started": bool(starts),
                "relay arguments (count)": starts[0].get("argc") if starts else None,
                "lines tui>srv": sum(1 for r in rows(rlog) if r["dir"] == "tui>srv"),
                "lines srv>tui": sum(1 for r in rows(rlog) if r["dir"] == "srv>tui"),
                "greeting on screen": tui.on_screen("how can I help"),
            }
        finally:
            tui.stop()
        time.sleep(1)

    # 2. inject
    open(rlog, "w").close()
    open(hooks, "w").close()
    tui = Tui(os.path.join(out, "work"), dict(base, VIBE_APP_SERVER_BIN=RELAY), ["--trust"])
    try:
        if not wait_for(lambda: len(tui.text()) > 200, 30):
            raise SystemExit("the TUI drew nothing in 30 s")
        time.sleep(3)
        tui.type(TYPED)
        tui.type("\r")
        if not wait_for(lambda: hook_ends("post_agent"), 120):
            shot(tui, "turn ONE did not end")
            raise SystemExit("turn ONE did not end in 120 s")
        time.sleep(3)
        shot(tui, "idle after turn ONE")
        t_inject = time.time()
        with open(os.path.join(rdir, "inject"), "w", encoding="utf-8") as f:
            f.write(INJECTED)
        ended = wait_for(lambda: len(hook_ends("post_agent")) >= 2, 120)
        time.sleep(3)
        shot(tui, "after the injected turn")
        after = screens[-1]
        relay_rows = rows(rlog)
        template = [r for r in relay_rows if r["dir"] == "template"]
        sent = [r for r in relay_rows if r["dir"] == "relay>srv"]
        kept = [r for r in relay_rows if r["dir"] == "srv>relay"]
        ids = set()
        for r in relay_rows:
            if r["dir"] == "tui>srv" and "id" in r:
                ids.add(json.dumps(r["id"]))
        pre = hook_starts("pre_agent")
        report["2 inject"] = {
            "template method": template[-1]["method"] if template else None,
            "relay request sent": bool(sent),
            "answer kept by the relay (id, bytes)": [kept[0].get("id"), kept[0].get("bytes")] if kept else None,
            "TUI request ids (sample)": sorted(ids)[:8],
            "relay id collides with a TUI id": any(json.dumps("diaulos-relay-1") == i for i in ids),
            "pre_agent hooks": len(pre),
            "injected turn ran (second post_agent)": bool(ended),
            "injected turn starts after inject (s)": round(pre[1]["at"] - t_inject, 2) if len(pre) > 1 else None,
            "RELAYED prompt in the capture after the injected turn": "Answer exactly RELAYED" in after,
            "RELAYED answer in the capture after the injected turn": "RELAYED" in after.replace("Answer exactly RELAYED", ""),
        }
        sid = pre[0]["payload"].get("session_id") if pre else None
        report["session"] = sid

        # 3. relay death
        rpid = [r for r in relay_rows if r["dir"] == "start"][0]["pid"]
        t_kill = time.time()
        os.kill(rpid, signal.SIGKILL)
        time.sleep(5)
        shot(tui, "5 s after SIGKILL of the relay")
        alive = os.waitpid(tui.pid, os.WNOHANG)[0] == 0
        if alive:
            tui.type("Answer exactly THREE and nothing else.")
            tui.type("\r")
            time.sleep(8)
            shot(tui, "8 s after typing THREE")
            alive_after = os.waitpid(tui.pid, os.WNOHANG)[0] == 0
        else:
            alive_after = False
        report["3 relay killed"] = {
            "TUI process alive 5 s after": alive,
            "TUI process alive after typing THREE": alive_after,
            "pre_agent hooks after kill": len([r for r in hook_starts("pre_agent") if r["at"] > t_kill]),
            "screen text after kill (last rows)": screen_text(tui).splitlines()[-8:],
        }
    finally:
        tui.stop()
    save_dir = os.path.join(out, "vh", "logs", "session")
    if report.get("session"):
        folder = os.path.join(save_dir, "unified", report["session"])
        files = [p for p in glob.glob(os.path.join(folder, "**", "*"), recursive=True) if os.path.isfile(p)]
        stored = " ".join(open(p, encoding="utf-8", errors="replace").read() for p in files)
        tokens = usage(save_dir, report["session"])
        meta = {}
        try:
            with open(os.path.join(folder, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError) as exc:
            meta = {"error": repr(exc)}
        report["4 session on disk"] = {
            "injected text saved": "Answer exactly RELAYED" in stored,
            "THREE saved": "Answer exactly THREE" in stored,
            "meta.json readable, end_time": meta.get("end_time", meta.get("error")),
            "tokens (input, output), cumulative": tokens,
            "cost at config prices ($)": round(tokens[0] * 0.1e-6 + tokens[1] * 0.3e-6, 5) if tokens else None,
        }
    os.makedirs(report_dir, exist_ok=True)

    def anon(text):
        text = text.replace(out, "<jetable>").replace(os.path.expanduser("~"), "/home/<compte>")
        return re.sub(r"Hello [^,\s]+, how can I help", "Hello <prénom>, how can I help", text)

    outputs = {"relay-report.json": json.dumps(report, indent=1), "relay-screens.txt": "\n".join(screens)}
    with open(hooks, encoding="utf-8") as f:
        outputs["relay-hooks.log"] = f.read()
    # The relay log holds no params, result or text (whitelist in relay_app_server.py).
    outputs["relay-log-summary.jsonl"] = "".join(json.dumps(summary(r)) + "\n" for r in rows(rlog))
    for name, text in outputs.items():
        with open(os.path.join(report_dir, name), "w", encoding="utf-8") as f:
            f.write(anon(text))
    print(anon(outputs["relay-report.json"]))


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1]), os.path.abspath(sys.argv[2]))

#!/usr/bin/env python3
"""T12 probe (1 of n): does the end of a background process wake an idle vibe 3 TUI?

Linux only. vibe 3.1.2 offers the model background processes (tools.process in run_typescript,
`enable_background_processes`, default true). When one reaches a terminal state, the Runtime
"injects a metadata-only notification" (harness prompt; mistralai_vibe_local_harness
vibe/_runtime.py, _submit_terminal_process). This probe checks whether that notification starts
a turn when the session is idle, with nobody typing.

    python3 tests/vibe3/wake_process.py OUT [SECONDS]

OUT is a throwaway folder made by `gates.py setup OUT` (VIBE_HOME OUT/vh, devstral-small, the
gateprobe plugin whose hooks log every pre_agent and post_agent to OUT/hooks.log). The probe
sets [tools.bash] permission = "always" in OUT/vh/config.toml (the background start is gated like
bash), opens the TUI, types one prompt asking the model to start `sleep SECONDS; echo WAKE7` in the
background and end its turn, then waits SECONDS + 40 s without typing.

A turn that starts on its own shows as a second pre_agent hook in hooks.log. The TUI has no price
cap (--max-price is for -p only): one prompt, a few model calls, counted afterwards from the
session's projection-state.json (cumulative inputTokens and outputTokens).
Writes OUT/wake-report.json, OUT/wake-hooks.log and OUT/wake-screens.txt.
"""
import glob
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from gates import env_of  # noqa: E402
from gates_tui import screen_text  # noqa: E402
from tui_probe import Tui, wait_for  # noqa: E402

PROMPT = ("Call run_typescript once with this program: "
          "await tools.process.start({command: \"sleep %d; echo WAKE7\"}). "
          "Do not wait for the process and do not read its output. "
          "Then answer exactly STARTED and end your turn.")


def usage(save_dir, sid):
    """Cumulative (inputTokens, outputTokens) of the session, from its latest generation."""
    states = sorted(glob.glob(os.path.join(save_dir, "unified", sid, "generations", "*", "projection-state.json")))
    if not states:
        return None
    text = open(states[-1], encoding="utf-8").read()
    best = (0, 0)
    for chunk in text.split('"inputTokens":')[1:]:
        inp = int("".join(c for c in chunk[:12] if c.isdigit()) or 0)
        out = 0
        if '"outputTokens":' in chunk[:400]:
            tail = chunk.split('"outputTokens":', 1)[1]
            out = int("".join(c for c in tail[:12] if c.isdigit()) or 0)
        best = max(best, (inp, out))
    return best


def allow_shell(out):
    """The background start is gated like bash (vibe/app_server/_unified_permissions.py,
    _SHELL_TOOL_SOURCES): [tools.bash] permission = "always", in the throwaway config only."""
    cfg = os.path.join(out, "vh", "config.toml")
    with open(cfg, encoding="utf-8") as f:
        text = f.read()
    head, sep, rest = text.partition("[tools.bash]\n")
    if not sep:
        text += '\n[tools.bash]\npermission = "always"\n'
    elif not rest.startswith('permission = "always"'):
        rest = rest.replace('permission = "ask"', 'permission = "always"', 1)
        text = head + sep + rest
    with open(cfg, "w", encoding="utf-8") as f:
        f.write(text)


def main(out, seconds):
    allow_shell(out)
    gd, log = os.path.join(out, "gd"), os.path.join(out, "hooks.log")
    for name in os.listdir(gd):
        os.remove(os.path.join(gd, name))
    open(log, "w").close()

    def rows():
        with open(log, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def starts(event):
        return [r for r in rows() if r["kind"] == "start" and r.get("event") == event]

    screens, report = [], {"sleep seconds": seconds}

    def shot(label):
        screens.append("==== %s (t=%.3f)\n%s\n" % (label, time.time(), screen_text(tui)))

    tui = Tui(os.path.join(out, "work"), env_of(out), ["--trust"])
    try:
        if not wait_for(lambda: len(tui.text()) > 200, 30):
            raise SystemExit("the TUI drew nothing in 30 s")
        time.sleep(3)
        shot("started")
        t_prompt = time.time()
        tui.type(PROMPT % seconds)
        tui.type("\r")
        if not wait_for(lambda: [r for r in rows() if r["kind"] == "end" and r.get("event") == "post_agent"], 120):
            shot("no post_agent in 120 s")
            raise SystemExit("turn 1 did not end in 120 s")
        t_idle = time.time()
        report["turn 1 ended after prompt (s)"] = round(t_idle - t_prompt, 2)
        time.sleep(1)
        shot("turn 1 ended, idle")
        # Nobody types from here on.
        second = wait_for(lambda: len(starts("pre_agent")) >= 2, seconds + 40)
        shot("after the wait")
        pre = starts("pre_agent")
        report["pre_agent hooks"] = len(pre)
        report["post_agent hooks"] = len(starts("post_agent"))
        report["turn started without typing"] = bool(second)
        if second:
            report["second turn starts after idle (s)"] = round(pre[1]["at"] - t_idle, 2)
            wait_for(lambda: len(starts("post_agent")) >= 2, 90)
            time.sleep(2)
            shot("after the second turn")
        for needle in ("STARTED", "WAKE7", "Background process", "completed"):
            report["on screen after idle: " + needle] = bool(tui.first_time(needle, t_idle))
        sid = pre[0]["payload"].get("session_id") if pre else None
        report["session"] = sid
    finally:
        tui.stop()
        with open(os.path.join(out, "wake-screens.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(screens))
        shutil.copy(log, os.path.join(out, "wake-hooks.log"))
    save_dir = os.path.join(out, "vh", "logs", "session")
    if report.get("session"):
        tokens = usage(save_dir, report["session"])
        report["tokens (input, output), cumulative"] = tokens
        if tokens:
            report["cost at config prices ($)"] = round(tokens[0] * 0.1e-6 + tokens[1] * 0.3e-6, 5)
        files = [p for p in glob.glob(os.path.join(save_dir, "unified", report["session"], "**", "*"), recursive=True)
                 if os.path.isfile(p)]
        stored = " ".join(open(p, encoding="utf-8", errors="replace").read() for p in files)
        report["session files mention a background-process notification"] = "background-process:" in stored
        report["session files mention tools.process"] = "tools.process" in stored
    with open(os.path.join(out, "wake-report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main(os.path.abspath(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 20)

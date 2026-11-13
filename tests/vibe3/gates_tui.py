"""Gates G1 and G3 in the real vibe TUI (task T11, docs/VIBE-DESIGN.md §8.4). Linux only.

Run by `python3 tests/vibe3/gates.py tui OUT` after `gates.py setup OUT`. It drives the TUI in a
pseudo-terminal (tests/vibe3/tui_probe.py) and steers tests/vibe3/gate_hook.py through OUT/gd:

1. G3 start/pre: session_start and pre_agent answer a system_message. Is it on screen?
2. G1 Enter: turn ONE ends, its post_agent hook listens 25 s. A message typed and sent with
   Enter while it listens: when does its pre_agent hook start, and is the listening hook stopped?
3. G1 Esc: the next post_agent hook listens; Esc is pressed. Is the hook stopped, and when?
4. G3 post: a turn whose listening post_agent hook gets a message and answers deny with a
   system_message. Is the system_message on screen, and is the retry run?

The TUI has no price cap (--max-price is for -p only): four short model calls on devstral-small
(ONE, TWO, FOUR, and FOUR's retry), counted afterwards from the session journal.
Writes OUT/tui-report.json (the report), OUT/tui-hooks.log (every hook start, end and signal)
and OUT/tui-screens.txt (the screen at each step).
"""
import json
import os
import shutil
import time

from tui_probe import Screen, Tui, wait_for


def screen_text(tui):
    screen = Screen()
    for _, data in list(tui.chunks):
        screen.feed(data)
    return "\n".join("".join(row).rstrip() for row in screen.grid if "".join(row).strip())


def main(out, env):
    gd, log = os.path.join(out, "gd"), os.path.join(out, "hooks.log")
    for name in os.listdir(gd):
        os.remove(os.path.join(gd, name))
    open(log, "w").close()

    def put(name, text):
        with open(os.path.join(gd, name), "w", encoding="utf-8") as f:
            f.write(text)

    def drop(name):
        try:
            os.remove(os.path.join(gd, name))
        except OSError:
            pass

    def rows():
        with open(log, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def first(kind, event, after=0.0):
        for r in rows():
            if r["kind"] == kind and r.get("event") == event and r["at"] > after:
                return r
        return None

    screens, report = [], {}

    def shot(label):
        screens.append("==== %s (t=%.3f)\n%s\n" % (label, time.time(), screen_text(tui)))

    put("session_start_sysmsg", "G3START session_start note")
    put("pre_agent_sysmsg", "G3PRE pre_agent note")
    put("listen", "25")
    tui = Tui(os.path.join(out, "work"), env, ["--trust"])
    try:
        if not wait_for(lambda: len(tui.text()) > 200, 30):
            raise SystemExit("the TUI drew nothing in 30 s")
        time.sleep(3)
        shot("started")

        # 1 and 2: turn ONE, then a message sent with Enter while its post_agent hook listens.
        t_one = time.time()
        tui.type("Answer exactly ONE and nothing else.")
        tui.type("\r")
        hook1 = wait_for(lambda: first("start", "post_agent", t_one), 90)
        if not hook1:
            raise SystemExit("no post_agent hook in 90 s")
        time.sleep(2)
        shot("post_agent of ONE listening")
        t_enter = time.time()
        tui.type("Answer exactly TWO and nothing else.")
        tui.type("\r")
        time.sleep(2)
        shot("2 s after Enter on TWO")
        pre2 = wait_for(lambda: first("start", "pre_agent", t_enter), 45)
        end1 = first("end", "post_agent", hook1["at"]) or first("signal", "post_agent", hook1["at"])
        sig1 = first("signal", "post_agent", hook1["at"])
        report["G1 Enter while post_agent listens (25 s)"] = {
            "post_agent start": hook1["at"], "Enter sent": t_enter,
            "pre_agent of TWO starts after Enter (s)": round(pre2["at"] - t_enter, 2) if pre2 else None,
            "listening hook ends after Enter (s)": round(end1["at"] - t_enter, 2) if end1 else None,
            "listening hook ended by": end1["kind"] if end1 else None,
            "signal": sig1 and sig1["signal"],
            "TWO handled before the hook ended": bool(pre2 and end1 and pre2["at"] < end1["at"])}
        shot("after pre_agent of TWO")

        # 3: Esc while the post_agent hook of TWO listens.
        hook2 = wait_for(lambda: first("start", "post_agent", (pre2 or end1 or hook1)["at"]), 90)
        if hook2:
            time.sleep(2)
            shot("post_agent of TWO listening")
            t_esc = time.time()
            tui.type("\x1b")
            time.sleep(1)
            shot("1 s after Esc")
            end2 = wait_for(lambda: first("end", "post_agent", hook2["at"]) or first("signal", "post_agent", hook2["at"]), 30)
            report["G1 Esc while post_agent listens (25 s)"] = {
                "post_agent start": hook2["at"], "Esc sent": t_esc,
                "hook ends after Esc (s)": round(end2["at"] - t_esc, 2) if end2 else None,
                "hook ended by": end2["kind"] if end2 else None,
                "stopped by Esc (ended before its 25 s)": bool(end2 and end2["at"] < hook2["at"] + 24)}
            shot("after the end of post_agent of TWO")
        else:
            report["G1 Esc while post_agent listens (25 s)"] = "no post_agent hook for TWO in 90 s"

        # 4: a listening post_agent hook gets a message and answers deny with a system_message.
        put("post_sysmsg", "G3POST message from claude:test")
        time.sleep(1)
        t_four = time.time()
        tui.type("Answer exactly FOUR and nothing else.")
        tui.type("\r")
        hook4 = wait_for(lambda: first("start", "post_agent", t_four), 90)
        if hook4:
            drop("listen")
            time.sleep(2)
            put("inbox", "[bridge] Message from claude:test: reply exactly FIVE and nothing else.")
            denied = wait_for(lambda: first("end", "post_agent", hook4["at"]), 30)
            retry = wait_for(lambda: first("start", "post_agent", denied["at"]) if denied else None, 90)
            time.sleep(2)
            shot("after the retry of FOUR")
            report["G3 post_agent deny with system_message"] = {
                "deny answered": bool(denied and denied.get("answer")),
                "retry ran (second post_agent)": bool(retry),
                "G3POST on screen": bool(tui.first_time("G3POST", t_four)),
                "reason text on screen": bool(tui.first_time("reply exactly FIVE", t_four)),
                "FIVE on screen": bool(tui.first_time("FIVE", denied["at"] if denied else t_four))}
        else:
            report["G3 post_agent deny with system_message"] = "no post_agent hook for FOUR in 90 s"
        report["G3 session_start system_message on screen"] = bool(tui.first_time("G3START"))
        report["G3 pre_agent system_message on screen"] = bool(tui.first_time("G3PRE"))
        sid = first("start", "session_start")
        report["session"] = sid and sid["payload"].get("session_id")
        shot("end")
    finally:
        tui.stop()
        with open(os.path.join(out, "tui-screens.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(screens))
        shutil.copy(log, os.path.join(out, "tui-hooks.log"))
        with open(os.path.join(out, "tui-report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
    print(json.dumps(report, indent=1))

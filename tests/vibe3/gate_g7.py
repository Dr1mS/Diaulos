#!/usr/bin/env python3
"""Gate G7 (docs/VIBE-DESIGN.md §8.8): does the plugin's launcher ./vibe-mcp start from vibe's copy
of the plugin, with its executable bit, and does the server answer tools/list? Linux only.

    python3 tests/vibe3/gate_g7.py OUT [--home]

OUT is made by `t14_env.py OUT`. The TUI opens with the real `vibe` command (read, never changed)
in OUT/work, VIBE_HOME=OUT/vh, AGENT_BRIDGE_HOME=OUT/ab, and with --home HOME=OUT/home. Nothing is
typed: no model call. After 15 s it reads:
  - the plugin copies in <save_dir>/plugins/packages/ and the mode of their vibe-mcp;
  - OUT/ab/state/vibe-mcp.log: initialize and tools/list, from the plugin copy;
  - OUT/ab/state/vibe-current/: the file written by the session_start hook;
  - the screen: the hook's line "Diaulos: this conversation is vibe:…".
Prints a JSON report, with OUT written <jetable>, and its "pass"; exits 1 if it fails.
"""
import glob
import json
import os
import stat
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from gates_tui import screen_text  # noqa: E402
from t14_env import env_of  # noqa: E402
from tui_probe import Tui, wait_for  # noqa: E402


def main(out, home):
    ab = os.path.join(out, "ab")
    mcp_log = os.path.join(ab, "state", "vibe-mcp.log")
    if os.path.exists(mcp_log):
        os.remove(mcp_log)
    tui = Tui(os.path.join(out, "work"), env_of(out, home=home), ["--trust"])
    try:
        drew = wait_for(lambda: len(tui.text()) > 200, 30)
        time.sleep(15)
        screen = screen_text(tui)
    finally:
        tui.stop()
    packages = glob.glob(os.path.join(out, "vh", "logs", "session", "plugins", "packages", "*", "*", "vibe-mcp"))
    log = open(mcp_log, encoding="utf-8").read().splitlines() if os.path.exists(mcp_log) else []
    currents = []
    for path in glob.glob(os.path.join(ab, "state", "vibe-current", "*.json")):
        with open(path, encoding="utf-8") as f:
            currents.append(json.load(f))
    report = {
        "HOME redirected": home,
        "TUI drew": bool(drew),
        "plugin copies with vibe-mcp": len(packages),
        "vibe-mcp mode in the copies": sorted({stat.filemode(os.stat(p).st_mode) for p in packages}),
        "vibe-mcp.log lines": [line.split(" ", 2)[-1] for line in log],
        "initialize from the plugin copy": any("initialize" in l and "plugin copy" in l for l in log),
        "tools/list from the plugin copy": any("tools/list" in l and "plugin copy" in l for l in log),
        "vibe-current files": [{k: c.get(k) for k in ("address", "source", "run")} for c in currents],
        "session_start line on screen": [l.strip() for l in screen.splitlines() if "Diaulos:" in l],
    }
    report["pass"] = bool(report["TUI drew"] and packages and all(
        os.stat(p).st_mode & stat.S_IXUSR for p in packages) and report["initialize from the plugin copy"]
        and report["tools/list from the plugin copy"] and currents and report["session_start line on screen"])
    text = json.dumps(report, indent=1, ensure_ascii=False).replace(out, "<jetable>")
    print(text)
    return report


if __name__ == "__main__":
    sys.exit(0 if main(os.path.abspath(sys.argv[1]), "--home" in sys.argv[2:])["pass"] else 1)

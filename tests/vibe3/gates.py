#!/usr/bin/env python3
"""Gate probes G1 to G4 before the vibe 3 code (task T11, docs/VIBE-DESIGN.md §8.4).

Linux only (the TUI part uses tests/vibe3/tui_probe.py: pty, fcntl, termios).
Everything runs in a throwaway folder OUT: OUT/vh is a VIBE_HOME made from the person's
~/.vibe/config.toml (read only, never written) with devstral-small, no update checks, no
`mcp_servers = []` line and its save_dir under OUT; OUT/vh/plugins/gateprobe is a native
plugin with an MCP server (tests/vibe3/probe_mcp.py) and hooks (tests/vibe3/gate_hook.py).

    python3 tests/vibe3/gates.py setup OUT
    python3 tests/vibe3/gates.py p OUT TAG PROMPT [--resume ID] [--key KEY]   # one capped -p run
    python3 tests/vibe3/gates.py tui OUT                                       # G1 and G3

Each -p run is capped with --max-price 0.02 --max-turns 3 and writes OUT/TAG/ (stdout,
stderr, export.json, the hook and MCP logs of the run). A TUI turn has no price cap in vibe
(--max-price is for -p only): the TUI part makes as few model calls as it can, and its report
counts them from the session journal.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
NAME = "gateprobe"


def setup(out):
    vh = os.path.join(out, "vh")
    os.makedirs(os.path.join(vh, "plugins", NAME, "ai.mistral.vibe"), exist_ok=True)
    os.makedirs(os.path.join(out, "work"), exist_ok=True)
    os.makedirs(os.path.join(out, "gd"), exist_ok=True)
    with open(os.path.expanduser("~/.vibe/config.toml"), encoding="utf-8") as f:
        config = f.read()
    config = re.sub(r'(?m)^active_model = ".*"$', 'active_model = "devstral-small"', config)
    config = re.sub(r"(?m)^enable_update_checks = .*$", "enable_update_checks = false", config)
    config = re.sub(r"(?m)^mcp_servers = \[\]\n", "", config)
    config = re.sub(r'(?m)^save_dir = ".*"$', 'save_dir = "%s"' % os.path.join(vh, "logs", "session"), config)
    with open(os.path.join(vh, "config.toml"), "w", encoding="utf-8") as f:
        f.write(config)
    root = os.path.join(vh, "plugins", NAME)
    manifest = {"$schema": "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json", "name": NAME, "version": "0.0.1", "description": "Diaulos gate probes",
                "extensions": {"ai.mistral.vibe": {"schemaVersion": 1, "toolNamespace": "diaulos"}}}
    with open(os.path.join(root, "plugin.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
    env = {"PROBE_LOG": os.path.join(out, "mcp.log")}
    mcp = {"$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
           "mcpServers": {"bridge": {"type": "stdio", "command": "python3",
                                     "args": [os.path.join(HERE, "probe_mcp.py")], "env": env}}}
    with open(os.path.join(root, "mcp.json"), "w", encoding="utf-8") as f:
        json.dump(mcp, f, indent=1)
    command = "GATE_LOG=%s GATE_DIR=%s %s %s" % (os.path.join(out, "hooks.log"), os.path.join(out, "gd"),
                                                 sys.executable, os.path.join(HERE, "gate_hook.py"))
    with open(os.path.join(root, "ai.mistral.vibe", "hooks.toml"), "w", encoding="utf-8") as f:
        for kind in ("session_start", "pre_agent", "post_agent"):
            f.write('[[hooks]]\nname = "gate-%s"\ntype = "%s"\ncommand = "%s"\ntimeout = 60.0\n\n'
                    % (kind.replace("_", "-"), kind, command))
    print("setup", out)


def env_of(out):
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENT_BRIDGE_")}
    env["VIBE_HOME"] = os.path.join(out, "vh")
    return env


def run_p(out, tag, prompt, resume=None, key=None):
    """One capped -p run. `key`: comma-separated names, one [tools."<name>"] permission = "always" block each, for this run only."""
    cfg = os.path.join(out, "vh", "config.toml")
    with open(cfg, encoding="utf-8") as f:
        base = f.read()
    if key:
        with open(cfg, "a", encoding="utf-8") as f:
            for k in key.split(","):
                f.write('\n[tools."%s"]\npermission = "always"\n' % k)
    d = os.path.join(out, tag)
    os.makedirs(d, exist_ok=True)
    for log in ("hooks.log", "mcp.log"):
        open(os.path.join(out, log), "w").close()
    args = ["vibe", "-p", prompt, "--max-price", "0.02", "--max-turns", "3", "--trust",
            "--output", "text", "--output-dir", os.path.join(d, "export")]
    if resume:
        args += ["--resume", resume]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, env=env_of(out),
                              cwd=os.path.join(out, "work"), timeout=180)
    finally:
        with open(cfg, "w", encoding="utf-8") as f:
            f.write(base)
    for name, text in (("stdout", proc.stdout), ("stderr", proc.stderr)):
        with open(os.path.join(d, name), "w", encoding="utf-8") as f:
            f.write(text)
    for log in ("hooks.log", "mcp.log"):
        shutil.copy(os.path.join(out, log), os.path.join(d, log))
    export = {}
    try:
        with open(os.path.join(d, "export", "export.json"), encoding="utf-8") as f:
            export = json.load(f)
    except OSError:
        pass
    hooks = [json.loads(l) for l in open(os.path.join(d, "hooks.log"), encoding="utf-8") if l.strip()]
    print(json.dumps({"tag": tag, "rc": proc.returncode, "stdout": proc.stdout.strip()[-300:],
                      "stderr": [l for l in proc.stderr.splitlines() if "keyring" not in l][-6:],
                      "session_id": export.get("session_id"), "outcome": export.get("outcome"),
                      "usage": export.get("usage"), "cost_usd": export.get("cost_usd"),
                      "hooks": [(h["kind"], h["event"], (h.get("payload") or {}).get("source"),
                                 (h.get("payload") or {}).get("session_id"), h.get("answer"))
                                for h in hooks]}, indent=1))


def main():
    cmd, out = sys.argv[1], os.path.abspath(sys.argv[2])
    if cmd == "setup":
        setup(out)
    elif cmd == "p":
        rest = sys.argv[3:]
        opts = {}
        for flag in ("--resume", "--key"):
            if flag in rest:
                i = rest.index(flag)
                opts[flag[2:]] = rest[i + 1]
                del rest[i:i + 2]
        run_p(out, rest[0], rest[1], **opts)
    elif cmd == "tui":
        import gates_tui
        gates_tui.main(out, env_of(out))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()

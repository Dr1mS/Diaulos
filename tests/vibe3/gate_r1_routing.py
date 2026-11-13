"""The free part of gate R1 (docs/VIBE-DESIGN.md §8.9.9): the non-TUI uses listed in r1() of
tests/vibe3/gates_relay.py, routed by bridge/vibe_diaulos.py to a stub vibe. No vibe, no model call.

    python3 tests/vibe3/gate_r1_routing.py <repo> <empty throwaway folder>
"""
import json, os, re, subprocess, sys
repo, out = sys.argv[1], sys.argv[2]
src = open(os.path.join(repo, "tests/vibe3/gates_relay.py"), encoding="utf-8").read()
uses = eval(re.search(r"uses = (\[\[.*?\]\])\n", src, re.S).group(1))
stub_log = os.path.join(out, "stub-args.jsonl"); stub = os.path.join(out, "stub-vibe")
open(stub, "w").write("#!%s\nimport json, sys\nwith open(%r, 'a') as f:\n    f.write(json.dumps(sys.argv[1:]) + '\\n')\n" % (sys.executable, stub_log))
os.chmod(stub, 0o755)
env = {k: v for k, v in os.environ.items() if not k.startswith(("AGENT_BRIDGE_", "VIBE_"))}
env.update(HOME=os.path.join(out, "home"), AGENT_BRIDGE_HOME=os.path.join(out, "ab"), AGENT_BRIDGE_VIBE_REAL=stub)
ok = 0
for use in uses:
    p = subprocess.run([sys.executable, os.path.join(repo, "bridge/vibe_diaulos.py")] + use, env=env, timeout=30, capture_output=True, text=True)
    last = json.loads(open(stub_log).read().splitlines()[-1])
    same = last == use; ok += same
    print("%-22s rc %d  stub got same arguments: %s" % (" ".join(use), p.returncode, same))
log = os.path.join(out, "ab/state/vibe-relay.log")
starts = sum(1 for l in open(log) if json.loads(l).get("dir") == "start") if os.path.exists(log) else 0
print("uses %d, same %d, relay starts %d" % (len(uses), ok, starts))
print("PASS" if ok == len(uses) == 13 and starts == 0 else "FAIL")

#!/usr/bin/env bash
# Proofs of the Mistral Vibe kind (docs/VIBE-DESIGN.md §8, task T14). Linux and macOS.
#
#   tests/run-proofs.sh                         every unit test, no model call
#   DIAULOS_REAL_VIBE=1 tests/run-proofs.sh     the same, then the real gates (paid calls)
#
# The real gates run in a throwaway folder made by tests/vibe3/t14_env.py: its own VIBE_HOME
# (config.toml copied from ~/.vibe/config.toml, read only, with devstral-small), its own bridge
# home and HOME. Nothing outside that folder is written: not ~/.vibe, not ~/.agent-bridge.
#   tests/vibe3/gate_g7.py      the plugin's MCP launcher and hooks, no model call
#   tests/vibe3/gates_relay.py  R1 to R9 of §8.9.9, 3 passes each; one model call per delivered
#                               turn (about 50 turns, a few cents); vibe -p runs with --max-price 0.50,
#                               TUI turns have no price cap
# R9 greps the relay log for the person's account values when GATE_TERMS_FILE names a mode-600
# file of them (one per line); without it R9 fails. Rerun after every vibe upgrade: the relay
# rests on vibe's private protocol.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
LOG="$(mktemp -t diaulos-proofs.XXXXXX)"
trap 'rm -f "$LOG"' EXIT
status=0

echo "== unit tests ($("$PY" --version 2>&1))"
# DIAULOS_REAL_VIBE is left out here: no unit test makes a model call.
env -u DIAULOS_REAL_VIBE "$PY" -m unittest discover -s tests -p "test_*.py" >"$LOG" 2>&1
unit=$?
if [ $unit -eq 0 ]; then
  tail -n 3 "$LOG" | grep -E "^(Ran|OK)"
else
  cat "$LOG"  # the whole output, for a failure seen in CI
  status=1
fi

if [ "${DIAULOS_REAL_VIBE:-}" != "1" ]; then
  echo "== real gates skipped (set DIAULOS_REAL_VIBE=1; paid model calls)"
  exit $status
fi
if ! command -v vibe >/dev/null; then
  echo "== real gates: vibe must be on PATH"
  exit 1
fi

OUT="$(mktemp -d -t diaulos-gates.XXXXXX)"
echo "== real gates in $OUT (kept for reading)"
"$PY" tests/vibe3/t14_env.py "$OUT" >/dev/null || { echo "t14_env.py failed"; exit 1; }
for gate in g7 r1 r2 r3 r4 r7 r8 r9; do
  if [ "$gate" = g7 ]; then
    "$PY" tests/vibe3/gate_g7.py "$OUT" --home >"$OUT/gate-g7.json"
  else
    "$PY" tests/vibe3/gates_relay.py "$OUT" "$gate" --passes 3 >/dev/null
  fi
  if [ $? -eq 0 ]; then echo "$gate PASS"; else echo "$gate FAIL (see $OUT/gate-$gate.json)"; status=1; fi
done
"$PY" - "$OUT" <<'PY'
import glob, json, sys
turns = tokens_in = tokens_out = 0
for path in glob.glob(sys.argv[1] + "/gate-r*.json"):
    report = json.load(open(path, encoding="utf-8"))
    turns += report.get("TUI model turns", 0)
    tokens_in += report.get("tokens (input, output)", [0, 0])[0]
    tokens_out += report.get("tokens (input, output)", [0, 0])[1]
print("== TUI model turns %d, tokens in %d, out %d" % (turns, tokens_in, tokens_out))
PY
[ $status -eq 0 ] && echo "== PASS" || echo "== FAIL"
exit $status

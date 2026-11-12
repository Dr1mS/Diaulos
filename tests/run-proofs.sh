#!/usr/bin/env bash
# Proofs of the Mistral Vibe kind (docs/VIBE-DESIGN.md §6, task T5b). Linux and macOS.
#
#   tests/run-proofs.sh                         every unit test, no model call
#   DIAULOS_REAL_VIBE=1 tests/run-proofs.sh     the same, then the real checks (paid calls)
#
# The real checks use a throwaway bridge home and VIBE_HOME, devstral-small, and the shim
# installed by install/vibe_install.py; the real HOME is only read, for vibe's API key:
#   tests/real_proofs.py    vibe-acp: tools listed, vibe-new, 3 passes of vibe-run with
#                           their delays, session/load, max_turns 1, price cap $0.001
#   tests/real_vibe_tui.py  vibe TUI in a pseudo-terminal: 3 idle deliveries, 3 during a
#                           turn, kill and --resume, bridge_send, keys typed
# DIAULOS_PROOF_BUDGET is the budget of all real checks together, in $ (default 0.10,
# at most 0.50). Each script stops above what it is given; the second one gets what the
# first one left. Rerun after every vibe upgrade: delivery and the price cap rest on
# vibe's private names.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3}"
LOG="$(mktemp -t diaulos-proofs.XXXXXX)"
trap 'rm -f "$LOG"' EXIT
status=0

echo "== unit tests ($("$PY" --version 2>&1))"
# DIAULOS_REAL_VIBE is left out here: its RealVibeAcpTest uses the config's own model.
env -u DIAULOS_REAL_VIBE "$PY" -m unittest discover -s tests -p "test_*.py" >"$LOG" 2>&1
unit=$?
if [ $unit -eq 0 ]; then
  tail -n 3 "$LOG" | grep -E "^(Ran|OK)"
else
  cat "$LOG"  # the whole output, for a failure seen in CI
  status=1
fi

if [ "${DIAULOS_REAL_VIBE:-}" != "1" ]; then
  echo "== real checks skipped (set DIAULOS_REAL_VIBE=1; paid model calls)"
  exit $status
fi
if ! command -v vibe >/dev/null || ! command -v vibe-acp >/dev/null; then
  echo "== real checks: vibe and vibe-acp must be on PATH"
  exit 1
fi

BUDGET="${DIAULOS_PROOF_BUDGET:-0.10}"
if ! "$PY" -c "import sys; b = float(sys.argv[1]); sys.exit(not 0 < b <= 0.5)" "$BUDGET" 2>/dev/null; then
  echo "DIAULOS_PROOF_BUDGET=$BUDGET: give a budget above 0 and at most 0.50"
  exit 1
fi

echo "== real vibe-acp checks (budget \$$BUDGET)"
"$PY" tests/real_proofs.py --budget "$BUDGET" | tee "$LOG"
[ "${PIPESTATUS[0]}" -eq 0 ] || status=1
spent="$(sed -n 's/^total \([0-9.]*\)$/\1/p' "$LOG" | tail -n 1)"
left="$("$PY" -c "import sys; print(f'{float(sys.argv[1]) - float(sys.argv[2] or 0):.4f}')" "$BUDGET" "${spent:-}")"

echo "== real vibe TUI checks (budget left \$$left)"
if "$PY" -c "import sys; sys.exit(not float(sys.argv[1]) >= 0.02)" "$left"; then
  "$PY" tests/real_vibe_tui.py --price "$left" | tee "$LOG"
  [ "${PIPESTATUS[0]}" -eq 0 ] || status=1
  tui="$(sed -n 's/.*run total \$\([0-9.]*\).*/\1/p' "$LOG" | tail -n 1)"
else
  echo "skipped: less than \$0.02 left"
  tui=0
  status=1
fi

"$PY" -c "import sys; a, b = (float(x or 0) for x in sys.argv[1:]); print(f'== cost of the real checks: \${a:.4f} + \${b:.4f} = \${a + b:.4f} (budget \$$BUDGET)')" "${spent:-0}" "${tui:-0}"
[ $status -eq 0 ] && echo "== PASS" || echo "== FAIL"
exit $status

#!/usr/bin/env bash
# S6: does `vibe -p` run an MCP tool without a permission setting?
# A: no setting.  B: `[tools.probe_ping]` / `permission = "always"` appended
# to the throwaway config.toml. These are the exact commands of run p4-perm
# (output: out/p4-perm/s6.txt). Two programmatic calls, --max-price 0.10 each.
# Usage: ./s6_permission.sh [run-name]   (default p4-perm; it rebuilds out/<run>)
set -eu
cd "$(dirname "$0")"
source ./setup_home.sh "${1:-p4-perm}" >/dev/null
export PROBE_LISTEN=0
cd "$WORK"
PROMPT="Call the tool probe_ping exactly once, then answer with the single word DONE."
echo "--- A: no permission setting" | tee "$PROBE_DIR/s6.txt"
vibe -p "$PROMPT" --max-price 0.10 --trust --output text 2>&1 | tail -5 | tee -a "$PROBE_DIR/s6.txt"
echo "probe tools/call count: $(grep -c tools/call "$PROBE_LOG")" | tee -a "$PROBE_DIR/s6.txt"
printf '\n[tools.probe_ping]\npermission = "always"\n' >> "$VIBE_HOME/config.toml"
echo "--- B: [tools.probe_ping] permission = always" | tee -a "$PROBE_DIR/s6.txt"
vibe -p "$PROMPT" --max-price 0.10 --trust --output text 2>&1 | tail -5 | tee -a "$PROBE_DIR/s6.txt"
echo "probe tools/call count: $(grep -c tools/call "$PROBE_LOG")" | tee -a "$PROBE_DIR/s6.txt"

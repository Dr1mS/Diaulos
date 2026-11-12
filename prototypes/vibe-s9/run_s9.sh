#!/usr/bin/env bash
# S9 (B2 b and c): BridgeEcho in `vibe -p` without --auto-approve, then in
# vibe-acp (session/new + one prompt). Exact commands of runs p-mode and
# acp-mode. Two real model calls; -p is capped with --max-price 0.10.
# Usage: ./run_s9.sh
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"; source ./setup_home.sh p-mode >/dev/null; cd "$WORK"
vibe -p "Call the tool bridge_echo once with text S9P, then reply with its session_id only." \
  --max-price 0.10 --trust --output text > "$S9_DIR/run.txt" 2>&1
cd "$HERE"; source ./setup_home.sh acp-mode >/dev/null; unset PROBE_EXTRA_SERVER
python3 ../vibe-acp/acp_probe.py "$S9_DIR" new \
  "Call the tool bridge_echo once with text S9A, then reply with its session_id only." > "$S9_DIR/run.txt" 2>&1
cd "$HERE"; python3 ../vibe/sanitize_out.py out

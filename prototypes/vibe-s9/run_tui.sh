#!/usr/bin/env bash
# S9 (B2 a and B3): BridgeEcho in the interactive TUI, no --auto-approve, then
# /clear, a second call, /compact, a third call. Same steps as run tui-mode.
# No price cap exists in the TUI: three short turns and one compaction.
# Needs pyte for screen.py: set PY to a python that has it.
# Usage: PY=<venv>/bin/python ./run_tui.sh
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="${PY:?python with pyte}"
cd "$HERE"; source ./setup_home.sh tui-mode >/dev/null
D="$S9_DIR"; DRIVE="$HERE/../vibe/pty_drive.py"; SCREEN="$HERE/../vibe/screen.py"
calls() { grep -c '"run"' "$S9_LOG" 2>/dev/null || echo 0; }
wait_calls() { for _ in $(seq 120); do [ "$(calls)" -ge "$1" ] && break; sleep 1; done; sleep 8; }
type_cmd() { for k in $(echo "$1" | grep -o .); do printf '%s' "$k" > "$D/keys"; sleep 0.5; done; sleep 1; printf '\\r' > "$D/keys"; }
cd "$WORK"
python3 "$DRIVE" "$D" 900 -- vibe --trust --workdir "$WORK" \
  "Call the tool bridge_echo once with text S9T1, then reply with its session_id only." &
wait_calls 1; "$PY" "$SCREEN" "$D" > "$D/screen-1-call.txt"
type_cmd /clear; sleep 4; "$PY" "$SCREEN" "$D" > "$D/screen-2-clear.txt"
printf 'Call bridge_echo once with text S9T2, then reply with its session_id only.\\r' > "$D/keys"
wait_calls 2; "$PY" "$SCREEN" "$D" > "$D/screen-3-after-clear.txt"
type_cmd /compact; sleep 25; "$PY" "$SCREEN" "$D" > "$D/screen-4-compact.txt"
printf 'Call bridge_echo once with text S9T3, then reply with its session_id only.\\r' > "$D/keys"
wait_calls 3; "$PY" "$SCREEN" "$D" > "$D/screen-5-after-compact.txt"
touch "$D/stop"; wait
cd "$HERE"; python3 ../vibe/sanitize_out.py out

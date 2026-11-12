#!/usr/bin/env bash
# S9: throwaway VIBE_HOME with only the BridgeEcho tool (no MCP server, no hook,
# no [tools.*] permission). Usage: source setup_home.sh <run-name>
# Exports S9_DIR, VIBE_HOME, S9_LOG, WORK.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="${1:?run name}"
export S9_DIR="$HERE/out/$RUN"
rm -rf "$S9_DIR"
mkdir -p "$S9_DIR/vibe_home/tools" "$S9_DIR/work"
export VIBE_HOME="$S9_DIR/vibe_home"
export S9_LOG="$S9_DIR/tool.jsonl"
export WORK="$S9_DIR/work"
python3 - "$HOME/.vibe/config.toml" "$VIBE_HOME/config.toml" <<'PY'
import os, re, sys
src, dst = sys.argv[1:]
text = open(src, encoding="utf-8").read()
home = os.path.dirname(dst)
text, n = re.subn(r'(?m)^save_dir = ".*"$', f'save_dir = "{home}/logs/session"', text)
assert n == 1
assert "[tools.bridge_echo]" not in text
open(dst, "w", encoding="utf-8").write(text)
PY
cp "$HERE/tools/bridge_probe_tool.py" "$VIBE_HOME/tools/"
echo "S9_DIR=$S9_DIR"

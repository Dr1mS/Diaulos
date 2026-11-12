#!/usr/bin/env bash
# Build a throwaway VIBE_HOME for one probe run, so ~/.vibe stays untouched.
# Usage: source setup_home.sh <run-name> [push]
#   Exports PROBE_DIR, VIBE_HOME, PROBE_LOG, WORK and VIBE_ENABLE_EXPERIMENTAL_HOOKS.
#   The config is a copy of ~/.vibe/config.toml (the API key stays in the OS
#   keyring, which vibe reads by variable name), with the probe MCP server
#   declared in mcp_servers, and a hooks.toml with the probe hook on all three
#   hook types.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN="${1:?run name}"
PUSH="${2:-}"
export PROBE_DIR="$HERE/out/$RUN"
rm -rf "$PROBE_DIR"
mkdir -p "$PROBE_DIR/vibe_home" "$PROBE_DIR/work"
export VIBE_HOME="$PROBE_DIR/vibe_home"
export PROBE_LOG="$PROBE_DIR/probe.jsonl"
export WORK="$PROBE_DIR/work"
export VIBE_ENABLE_EXPERIMENTAL_HOOKS=true
push_env=""
[ "$PUSH" = push ] && push_env=', PROBE_PUSH = "1"'
python3 - "$HOME/.vibe/config.toml" "$VIBE_HOME/config.toml" "$HERE/probe_mcp.py" "$PROBE_LOG" "$push_env" <<'EOF'
import sys
src, dst, server, log, push = sys.argv[1:]
text = open(src, encoding="utf-8").read()
entry = ('mcp_servers = [{ name = "probe", transport = "stdio", command = "python3", '
         f'args = ["{server}"], env = {{ PROBE_LOG = "{log}"{push} }} }}]')
assert "mcp_servers = []" in text
text = text.replace("mcp_servers = []", entry, 1)
# The copied config has an absolute save_dir under ~/.vibe: point it at the
# throwaway home, or the probe sessions land in the real session folder.
import os, re
home = os.path.dirname(dst)
text, n = re.subn(r'(?m)^save_dir = ".*"$', f'save_dir = "{home}/logs/session"', text)
assert n == 1
open(dst, "w", encoding="utf-8").write(text)
EOF
mkdir -p "$VIBE_HOME/tools"
cp "$HERE/tools/agent_bridge_probe.py" "$VIBE_HOME/tools/"
cat > "$VIBE_HOME/hooks.toml" <<EOF
[[hooks]]
name = "probe-post"
type = "post_agent_turn"
command = "python3 $HERE/probe_hook.py"
timeout = 3600

[[hooks]]
name = "probe-before"
type = "before_tool"
command = "python3 $HERE/probe_hook.py"

[[hooks]]
name = "probe-after"
type = "after_tool"
command = "python3 $HERE/probe_hook.py"
EOF
echo "PROBE_DIR=$PROBE_DIR"

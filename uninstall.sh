#!/usr/bin/env bash
# Remove what install.sh added. Mailbox data in ~/.agent-bridge is kept unless --purge is given.
set -euo pipefail

BRIDGE_HOME="${AGENT_BRIDGE_HOME:-$HOME/.agent-bridge}"

# Stop running relays.
for f in "$BRIDGE_HOME"/peers/agy-*.json; do
  [ -e "$f" ] || continue
  pid=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("relay_pid") or "")' "$f")
  [ -n "$pid" ] && kill "$pid" 2>/dev/null && echo "stopped relay $pid"
done

python3 - <<'EOF'
import json, os
home = os.path.expanduser("~")

hooks_path = os.path.join(home, ".gemini/config/hooks.json")
if os.path.exists(hooks_path):
    with open(hooks_path) as f:
        hooks = json.load(f)
    if hooks.pop("agent-bridge", None) is not None:
        with open(hooks_path, "w") as f:
            json.dump(hooks, f, indent=2)
            f.write("\n")
        print("removed agy hooks")

settings_path = os.path.join(home, ".claude/settings.json")
if os.path.exists(settings_path):
    with open(settings_path) as f:
        settings = json.load(f)
    allow = settings.get("permissions", {}).get("allow", [])
    keep = [r for r in allow if "~/.agent-bridge/bridge.py" not in r and not r.startswith("mcp__agent-bridge__")]
    if keep != allow:
        settings["permissions"]["allow"] = keep
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        print("removed claude allow rules")
EOF

if command -v claude >/dev/null; then
  claude mcp remove -s user agent-bridge >/dev/null 2>&1 && echo "removed claude mcp server"
fi
if grep -qF "# agent-bridge: claude starts with the MCP channel" "$HOME/.bashrc" 2>/dev/null; then
  python3 - "$HOME/.bashrc" <<'EOF'
import sys
path = sys.argv[1]
lines = open(path).read().split("\n")
out, skip = [], 0
for line in lines:
    if line == "# agent-bridge: claude starts with the MCP channel":
        skip = 2  # the marker and the source line after it
        if out and out[-1] == "":
            out.pop()
    if skip:
        skip -= 1
        continue
    out.append(line)
open(path, "w").write("\n".join(out))
EOF
  echo "removed claude wrapper from ~/.bashrc"
fi
rm -f "$BRIDGE_HOME/claude-channel.sh"
rm -rf "$HOME/.claude/skills/agy-bridge" && echo "removed claude skill"
rm -f "$BRIDGE_HOME/bridge.py" "$BRIDGE_HOME/mcp_channel.py"
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$BRIDGE_HOME" && echo "purged $BRIDGE_HOME"
fi

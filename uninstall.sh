#!/usr/bin/env bash
# Remove what install.sh added. Mailbox data in ~/.agent-bridge is kept unless --purge is given.
set -euo pipefail

BRIDGE_HOME="${AGENT_BRIDGE_HOME:-$HOME/.agent-bridge}"

# Stop running relays (verify process name and command line before killing).
for f in "$BRIDGE_HOME"/peers/agy-*.json; do
  [ -e "$f" ] || continue
  pid=$(python3 -c 'import json,sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        print(json.load(f).get("relay_pid") or "")
except Exception:
    pass' "$f" 2>/dev/null || true)
  if [ -n "$pid" ] && [ -d "/proc/$pid" ]; then
    if grep -q "python" "/proc/$pid/cmdline" 2>/dev/null && grep -qF "agy-relay" "/proc/$pid/cmdline" 2>/dev/null; then
      kill "$pid" 2>/dev/null && echo "stopped relay $pid"
    fi
  fi
done

python3 - "$BRIDGE_HOME" <<'EOF'
import json, os, sys
bridge_home = sys.argv[1] if len(sys.argv) > 1 else ""
home = os.path.expanduser("~")

def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
        return json.loads(text) if text else {}
    except (OSError, json.JSONDecodeError):
        return {}

def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

hooks_path = os.path.join(home, ".gemini/config/hooks.json")
if os.path.exists(hooks_path):
    hooks = load(hooks_path)
    if isinstance(hooks, dict) and hooks.pop("agent-bridge", None) is not None:
        save(hooks_path, hooks)
        print("removed agy hooks")

settings_path = os.path.join(home, ".claude/settings.json")
if os.path.exists(settings_path):
    settings = load(settings_path)
    if isinstance(settings, dict):
        allow = settings.get("permissions", {}).get("allow", [])
        if isinstance(allow, list):
            keep = [r for r in allow if not (isinstance(r, str) and (
                ".agent-bridge" in r or
                (bridge_home and bridge_home in r) or
                r.startswith("mcp__agent-bridge__")
            ))]
            if keep != allow:
                settings["permissions"]["allow"] = keep
                save(settings_path, settings)
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
rm -f "$BRIDGE_HOME/bridge.py" "$BRIDGE_HOME/mcp_channel.py" "$BRIDGE_HOME/state/agy.auto-relay"
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$BRIDGE_HOME" && echo "purged $BRIDGE_HOME"
fi

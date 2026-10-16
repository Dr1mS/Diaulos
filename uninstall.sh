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
    keep = [r for r in allow if "~/.agent-bridge/bridge.py" not in r]
    if keep != allow:
        settings["permissions"]["allow"] = keep
        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)
            f.write("\n")
        print("removed claude allow rules")
EOF

rm -rf "$HOME/.claude/skills/agy-bridge" && echo "removed claude skill"
rm -f "$BRIDGE_HOME/bridge.py"
if [ "${1:-}" = "--purge" ]; then
  rm -rf "$BRIDGE_HOME" && echo "purged $BRIDGE_HOME"
fi

#!/usr/bin/env bash
# Install the Claude Code <-> agy agent bridge for the current user.
#   ~/.agent-bridge/bridge.py            the bridge (mailbox data lives next to it)
#   ~/.gemini/config/hooks.json          global agy hooks, under the key "agent-bridge"
#   ~/.claude/skills/agy-bridge/SKILL.md Claude Code skill
#   ~/.claude/settings.json              allow rule for bridge commands
# Re-run it to update. Each config file it edits is backed up first as <file>.bak-agent-bridge.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_HOME="${AGENT_BRIDGE_HOME:-$HOME/.agent-bridge}"

mkdir -p "$BRIDGE_HOME"
install -m 755 "$REPO/bridge/bridge.py" "$BRIDGE_HOME/bridge.py"
install -m 755 "$REPO/bridge/mcp_channel.py" "$BRIDGE_HOME/mcp_channel.py"
echo "bridge      -> $BRIDGE_HOME/{bridge,mcp_channel}.py"

# MCP channel server for every Claude Code session. It only reads its inbox in
# sessions started with: claude --dangerously-load-development-channels server:agent-bridge
if command -v claude >/dev/null; then
  claude mcp remove -s user agent-bridge >/dev/null 2>&1 || true
  claude mcp add -s user agent-bridge -- python3 "$BRIDGE_HOME/mcp_channel.py" >/dev/null
  echo "claude mcp  -> agent-bridge (user scope)"
else
  echo "claude CLI not found: skipped MCP server registration"
fi

mkdir -p "$HOME/.claude/skills/agy-bridge"
install -m 644 "$REPO/install/claude-skill/SKILL.md" "$HOME/.claude/skills/agy-bridge/SKILL.md"
echo "claude skill -> ~/.claude/skills/agy-bridge/SKILL.md"

python3 - "$REPO/install/agy-hooks.json" <<'EOF'
import json, os, shutil, sys

def load(path):
    try:
        with open(path) as f:
            text = f.read().strip()
        return json.loads(text) if text else {}
    except FileNotFoundError:
        return {}

def save(path, data):
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak-agent-bridge")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.rename(tmp, path)

home = os.path.expanduser("~")

hooks_path = os.path.join(home, ".gemini/config/hooks.json")
hooks = load(hooks_path)
with open(sys.argv[1]) as f:
    hooks["agent-bridge"] = json.load(f)
save(hooks_path, hooks)
print("agy hooks   -> ~/.gemini/config/hooks.json (key agent-bridge)")

settings_path = os.path.join(home, ".claude/settings.json")
settings = load(settings_path)
allow = settings.setdefault("permissions", {}).setdefault("allow", [])
rules = ["Bash(python3 ~/.agent-bridge/bridge.py *)",
         "Bash(python3 -u ~/.agent-bridge/bridge.py watch *)",
         "mcp__agent-bridge__send",
         "mcp__agent-bridge__list_agents"]
for r in rules:
    if r not in allow:
        allow.append(r)
save(settings_path, settings)
print("claude allow -> ~/.claude/settings.json")
EOF

echo
echo "Done. Restart agy sessions so they load the new hooks."
echo "Start Claude Code with: claude --dangerously-load-development-channels server:agent-bridge"

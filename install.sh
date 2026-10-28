#!/usr/bin/env bash
# Install the Claude Code <-> agy agent bridge for the current user.
#   ~/.agent-bridge/bridge.py            the bridge (mailbox data lives next to it)
#   ~/.gemini/config/hooks.json          global agy hooks, under the key "agent-bridge"
#   ~/.claude/skills/agy-bridge/SKILL.md Claude Code skill
#   ~/.claude/settings.json              allow rule for bridge commands
# Re-run it to update. Each config file it edits is backed up first as <file>.bak-agent-bridge.
# Options:
#   --shell       a plain `claude` starts with the MCP channel (~/.bashrc)
#   --auto-relay  experimental: agy hooks start the relay of Antigravity Desktop/IDE
#                 themselves. Re-run without it to turn it off.
set -euo pipefail

SHELL_WRAPPER=0
AUTO_RELAY=0
for arg in "$@"; do
  case "$arg" in
    --shell) SHELL_WRAPPER=1 ;;
    --auto-relay) AUTO_RELAY=1 ;;
    *) echo "unknown option: $arg (expected --shell, --auto-relay)" >&2; exit 2 ;;
  esac
done

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_HOME="${AGENT_BRIDGE_HOME:-$HOME/.agent-bridge}"

mkdir -p "$BRIDGE_HOME"
install -m 755 "$REPO/bridge/bridge.py" "$BRIDGE_HOME/bridge.py"
install -m 755 "$REPO/bridge/mcp_channel.py" "$BRIDGE_HOME/mcp_channel.py"
echo "bridge      -> $BRIDGE_HOME/{bridge,mcp_channel}.py"

# Experimental: the hooks read the language server's token from its command line
# and start the relay themselves, so agy never has to run ensure-relay.
mkdir -p "$BRIDGE_HOME/state"
if [ "$AUTO_RELAY" = 1 ]; then
  touch "$BRIDGE_HOME/state/agy.auto-relay"
  echo "auto-relay  -> on (experimental, Antigravity Desktop/IDE on Linux)"
else
  rm -f "$BRIDGE_HOME/state/agy.auto-relay"
fi

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

python3 - "$REPO/install/agy-hooks.json" "$BRIDGE_HOME/bridge.py" <<'EOF'
import json, os, shutil, sys

template, target_script = sys.argv[1:3]

def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read().strip()
        return json.loads(text) if text else {}
    except (OSError, json.JSONDecodeError):
        return {}

def save(path, data):
    if os.path.exists(path):
        shutil.copy2(path, path + ".bak-agent-bridge")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.rename(tmp, path)

home = os.path.expanduser("~")

hooks_path = os.path.join(home, ".gemini/config/hooks.json")
hooks = load(hooks_path)
with open(template, encoding="utf-8") as f:
    spec = json.load(f)

default_script = os.path.join(home, ".agent-bridge/bridge.py")
if target_script != default_script:
    script_arg = f'"{target_script}"' if " " in target_script else target_script
    for handlers in spec.values():
        for h in handlers:
            h["command"] = h["command"].replace("~/.agent-bridge/bridge.py", script_arg)

hooks["agent-bridge"] = spec
save(hooks_path, hooks)
print("agy hooks   -> ~/.gemini/config/hooks.json (key agent-bridge)")

settings_path = os.path.join(home, ".claude/settings.json")
settings = load(settings_path)
allow = settings.setdefault("permissions", {}).setdefault("allow", [])
rules = ["Bash(python3 ~/.agent-bridge/bridge.py *)",
         "Bash(python3 -u ~/.agent-bridge/bridge.py watch *)",
         "mcp__agent-bridge__send",
         "mcp__agent-bridge__list_agents"]
if target_script != default_script:
    rules.extend([f"Bash(python3 {target_script} *)",
                  f"Bash(python3 -u {target_script} watch *)"])
for r in rules:
    if r not in allow:
        allow.append(r)
save(settings_path, settings)
print("claude allow -> ~/.claude/settings.json")
EOF

install -m 644 "$REPO/install/claude-channel.sh" "$BRIDGE_HOME/claude-channel.sh"
MARK="# agent-bridge: claude starts with the MCP channel"
if [ "$SHELL_WRAPPER" = 1 ]; then
  if ! grep -qF "$MARK" "$HOME/.bashrc" 2>/dev/null; then
    printf '\n%s\n[ -f "%s" ] && . "%s"\n' "$MARK" "$BRIDGE_HOME/claude-channel.sh" "$BRIDGE_HOME/claude-channel.sh" >> "$HOME/.bashrc"
  fi
  echo "shell       -> ~/.bashrc sources claude-channel.sh (open a new terminal)"
fi

echo
echo "Done. Restart agy sessions so they load the new hooks."
if [ "$SHELL_WRAPPER" = 1 ]; then
  echo "In a new terminal, plain \`claude\` now starts with the agent-bridge channel."
else
  echo "Start Claude Code with: claude --dangerously-load-development-channels server:agent-bridge"
  echo "(or re-run with --shell so a plain \`claude\` does it)"
fi

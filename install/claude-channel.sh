# Sourced from ~/.bashrc by `install.sh --shell`.
# Makes a plain `claude` start interactive sessions with the agent-bridge MCP
# channel, so other agents can wake the session. Subcommands, print mode and
# --help/--version run unchanged. Bypass with `command claude ...`, or set
# AGENT_BRIDGE_CHANNEL=0 to turn the wrapper off for a shell.
claude() {
  if [ "${AGENT_BRIDGE_CHANNEL:-1}" = "0" ]; then
    command claude "$@"
    return
  fi
  case "${1:-}" in
    agents|attach|auth|auto-mode|doctor|gateway|import|install|logs|mcp|plugin|plugins|purge|respawn|rm|setup-token|stop|ultrareview|update)
      command claude "$@"
      return ;;
  esac
  local arg
  for arg in "$@"; do
    case "$arg" in
      -p|--print|-v|--version|-h|--help|--channels*|--dangerously-load-development-channels*)
        command claude "$@"
        return ;;
    esac
  done
  command claude --dangerously-load-development-channels server:agent-bridge "$@"
}

# Makes a plain `vibe` (Mistral Vibe 3) open its TUI behind the Diaulos relay, so messages
# from other agents wake the conversation (docs/VIBE-DESIGN.md §8.9). vibe-diaulos sends
# -p, --prompt-file, mcp, config, help, update, --version, --help, --setup and
# --check-upgrade to the real vibe unchanged. Bypass with `command vibe ...`, or set
# AGENT_BRIDGE_VIBE_RELAY=0 to turn the wrapper off for a shell.
_agent_bridge_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd)"
if [ -n "$_agent_bridge_dir" ] && [ -f "$_agent_bridge_dir/vibe_diaulos.py" ]; then
  _agent_bridge_q="$(printf '%q' "$_agent_bridge_dir/vibe_diaulos.py")"
  eval "vibe() {
    if [ \"\${AGENT_BRIDGE_VIBE_RELAY:-1}\" = \"0\" ]; then
      command vibe \"\$@\"
      return
    fi
    python3 $_agent_bridge_q \"\$@\"
  }"
fi
unset _agent_bridge_dir _agent_bridge_q

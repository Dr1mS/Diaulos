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

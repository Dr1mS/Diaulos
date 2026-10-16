# Dot-sourced from the PowerShell profile by `install.ps1 -Shell` (Windows
# counterpart of claude-channel.sh). Makes a plain `claude` start interactive
# sessions with the agent-bridge MCP channel, so other agents can wake the
# session. Subcommands, print mode and --help/--version run unchanged. Bypass
# with `claude.cmd ...`, or set $env:AGENT_BRIDGE_CHANNEL = '0' to turn the
# wrapper off for a shell.
function claude {
    # The real claude: the npm script or the executable, never this function.
    $real = Get-Command claude -CommandType ExternalScript, Application -ErrorAction Stop | Select-Object -First 1
    if ($env:AGENT_BRIDGE_CHANNEL -eq '0') { & $real @args; return }
    $subcommands = 'agents', 'attach', 'auth', 'auto-mode', 'doctor', 'gateway', 'import', 'install', 'logs',
        'mcp', 'plugin', 'plugins', 'purge', 'respawn', 'rm', 'setup-token', 'stop', 'ultrareview', 'update'
    if ($args.Count -gt 0 -and $subcommands -contains $args[0]) { & $real @args; return }
    foreach ($arg in $args) {
        if ($arg -in '-p', '--print', '-v', '--version', '-h', '--help' -or
            $arg -like '--channels*' -or $arg -like '--dangerously-load-development-channels*') {
            & $real @args
            return
        }
    }
    & $real --dangerously-load-development-channels server:agent-bridge @args
}

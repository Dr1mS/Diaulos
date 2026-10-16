@echo off
rem Target of the cmd.exe macro `claude` that `install.ps1 -Shell` defines with
rem doskey in cmd's AutoRun (counterpart of claude-channel.sh). Starts interactive
rem sessions with the agent-bridge MCP channel; subcommands, print mode and
rem --help/--version run unchanged. Macros only apply to typed commands, so the
rem `claude` below is the real one. Bypass with `claude.cmd ...`, or
rem `set AGENT_BRIDGE_CHANNEL=0` to turn the wrapper off for a shell.
if "%AGENT_BRIDGE_CHANNEL%"=="0" goto plain
for %%s in (agents attach auth auto-mode doctor gateway import install logs mcp plugin plugins purge respawn rm setup-token stop ultrareview update) do if /i "%~1"=="%%s" goto plain
rem `for` also splits on "=", so --channels=x yields --channels.
for %%a in (%*) do (
  for %%f in (-p --print -v --version -h --help --channels --dangerously-load-development-channels) do (
    if /i "%%~a"=="%%f" goto plain
  )
)
claude --dangerously-load-development-channels server:agent-bridge %*
exit /b
:plain
claude %*

# Install the Claude Code <-> agy agent bridge for the current Windows user.
# Windows counterpart of install.sh; same targets:
#   ~/.agent-bridge/bridge.py            the bridge (mailbox data lives next to it)
#   ~/.gemini/config/hooks.json          global agy hooks, under the key "agent-bridge"
#   ~/.claude/skills/agy-bridge/SKILL.md Claude Code skill
#   ~/.claude/settings.json              allow rule for bridge commands
# Re-run it to update. Each config file it edits is backed up first as <file>.bak-agent-bridge.
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 [-Shell]
param([switch]$Shell)
$ErrorActionPreference = 'Stop'

$Repo = $PSScriptRoot
$BridgeHome = if ($env:AGENT_BRIDGE_HOME) { $env:AGENT_BRIDGE_HOME } else { Join-Path $HOME '.agent-bridge' }

# Absolute interpreter path: hooks and the MCP server then never depend on PATH or app aliases.
$Python = $null
foreach ($name in 'python3', 'python', 'py') {
    if (Get-Command $name -ErrorAction SilentlyContinue) {
        # No double quotes in -c: Windows PowerShell does not escape them for native programs.
        $exe = & $name -c 'import sys; assert sys.version_info >= (3, 8); print(sys.executable)' 2>$null
        if ($LASTEXITCODE -eq 0 -and $exe) { $Python = $exe.Trim(); break }
    }
}
if (-not $Python) { throw 'Python 3.8+ not found (python3, python or py)' }
Write-Host "python      -> $Python"

New-Item -ItemType Directory -Force $BridgeHome | Out-Null
Copy-Item (Join-Path $Repo 'bridge\bridge.py') $BridgeHome -Force
Copy-Item (Join-Path $Repo 'bridge\mcp_channel.py') $BridgeHome -Force
Write-Host "bridge      -> $BridgeHome\{bridge,mcp_channel}.py"

# MCP channel server for every Claude Code session. It only reads its inbox in
# sessions started with: claude --dangerously-load-development-channels server:agent-bridge
if (Get-Command claude -ErrorAction SilentlyContinue) {
    $ErrorActionPreference = 'Continue'
    claude mcp remove -s user agent-bridge 2>$null | Out-Null
    claude mcp add -s user agent-bridge -- $Python (Join-Path $BridgeHome 'mcp_channel.py') | Out-Null
    $ok = $LASTEXITCODE -eq 0
    $ErrorActionPreference = 'Stop'
    if (-not $ok) { throw 'claude mcp add failed' }
    Write-Host 'claude mcp  -> agent-bridge (user scope)'
} else {
    Write-Host 'claude CLI not found: skipped MCP server registration'
}

$SkillDir = Join-Path $HOME '.claude\skills\agy-bridge'
New-Item -ItemType Directory -Force $SkillDir | Out-Null
Copy-Item (Join-Path $Repo 'install\claude-skill\SKILL.md') $SkillDir -Force
Write-Host 'claude skill -> ~/.claude/skills/agy-bridge/SKILL.md'

# Kept ASCII-only: Windows PowerShell pipes text to native programs in ASCII.
$Configure = @'
import json, os, shutil, sys

template, python, script, hook_cmd = sys.argv[1:5]

def load(path):
    try:
        with open(path, encoding="utf-8-sig") as f:
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
    os.replace(tmp, path)

# agy runs hooks with `cmd /c <command>`. Windows cmd /c strips outer quotes
# if multiple quoted tokens exist. Using a dedicated batch launcher avoids
# quoting issues even when python or bridge.py paths contain spaces.
# cmd.exe reads batch files in the active OEM code page: write agy-hook.cmd
# using the "oem" codec so accented characters in user paths work.
try:
    with open(hook_cmd, "w", encoding="oem") as f:
        f.write(f'@"{python}" "{script}" agy-hook %1\n')
except UnicodeEncodeError as e:
    sys.exit(f"path {hook_cmd!r} contains characters not representable in Windows OEM code page: {e}")

launcher = '"%s"' % hook_cmd if " " in hook_cmd else hook_cmd

home = os.path.expanduser("~")

hooks_path = os.path.join(home, ".gemini", "config", "hooks.json")
hooks = load(hooks_path)
with open(template, encoding="utf-8") as f:
    spec = json.load(f)
for event, handlers in spec.items():
    for h in handlers:
        h["command"] = f"{launcher} {event}"
hooks["agent-bridge"] = spec
save(hooks_path, hooks)
print("agy hooks   -> ~/.gemini/config/hooks.json (key agent-bridge)")

settings_path = os.path.join(home, ".claude", "settings.json")
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
'@
$Configure | & $Python - (Join-Path $Repo 'install\agy-hooks.json') $Python (Join-Path $BridgeHome 'bridge.py') (Join-Path $BridgeHome 'agy-hook.cmd')
if ($LASTEXITCODE -ne 0) { throw 'configuration step failed' }

$Wrapper = Join-Path $BridgeHome 'claude-channel.ps1'
Copy-Item (Join-Path $Repo 'install\claude-channel.ps1') $Wrapper -Force
$CmdWrapper = Join-Path $BridgeHome 'claude-channel.cmd'
Copy-Item (Join-Path $Repo 'install\claude-channel.cmd') $CmdWrapper -Force
$Mark = '# agent-bridge: claude starts with the MCP channel'
if ($Shell) {
    # PowerShell: a function in the profile.
    $ProfilePath = $PROFILE.CurrentUserAllHosts
    $current = if (Test-Path $ProfilePath) { Get-Content $ProfilePath -Raw } else { '' }
    if ($current -notlike "*$Mark*") {
        New-Item -ItemType Directory -Force (Split-Path $ProfilePath) | Out-Null
        Add-Content -Path $ProfilePath -Encoding UTF8 -Value "`r`n$Mark`r`nif (Test-Path '$Wrapper') { . '$Wrapper' }"
    }
    Write-Host "shell       -> $ProfilePath dot-sources claude-channel.ps1 (open a new terminal)"

    # cmd.exe: a doskey macro set by AutoRun, added after any existing AutoRun command.
    $Key = 'HKCU:\Software\Microsoft\Command Processor'
    if (-not (Test-Path $Key)) { New-Item $Key -Force | Out-Null }
    $autorun = (Get-ItemProperty $Key -ErrorAction SilentlyContinue).AutoRun
    if ($autorun -notlike '*claude-channel.cmd*') {
        $macro = "doskey claude=`"$CmdWrapper`" `$*"
        $autorun = if ($autorun) { "$autorun & $macro" } else { $macro }
        Set-ItemProperty $Key -Name AutoRun -Value $autorun
    }
    Write-Host 'shell       -> cmd.exe AutoRun defines the claude macro (open a new terminal)'
}

Write-Host ''
Write-Host 'Done. Restart agy sessions so they load the new hooks.'
if ($Shell) {
    Write-Host 'In a new terminal (PowerShell or cmd), plain `claude` now starts with the agent-bridge channel.'
} else {
    Write-Host 'Start Claude Code with: claude --dangerously-load-development-channels server:agent-bridge'
    Write-Host '(or re-run with -Shell so a plain `claude` does it)'
}

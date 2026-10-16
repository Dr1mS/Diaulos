# Remove what install.ps1 added. Mailbox data in ~/.agent-bridge is kept unless -Purge is given.
#   powershell -ExecutionPolicy Bypass -File .\uninstall.ps1 [-Purge]
param([switch]$Purge)
$ErrorActionPreference = 'Stop'

$BridgeHome = if ($env:AGENT_BRIDGE_HOME) { $env:AGENT_BRIDGE_HOME } else { Join-Path $HOME '.agent-bridge' }

# Stop running relays.
Get-ChildItem (Join-Path $BridgeHome 'peers') -Filter 'agy-*.json' -ErrorAction SilentlyContinue | ForEach-Object {
    $relayPid = (Get-Content $_.FullName -Raw | ConvertFrom-Json).relay_pid
    if ($relayPid) {
        try { Stop-Process -Id $relayPid -Force -ErrorAction Stop; Write-Host "stopped relay $relayPid" } catch {}
    }
}

$Python = $null
foreach ($name in 'python3', 'python', 'py') {
    if (Get-Command $name -ErrorAction SilentlyContinue) { $Python = $name; break }
}

# Kept ASCII-only: Windows PowerShell pipes text to native programs in ASCII.
$Unconfigure = @'
import json, os
home = os.path.expanduser("~")

def load(path):
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)

def save(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")

hooks_path = os.path.join(home, ".gemini", "config", "hooks.json")
if os.path.exists(hooks_path):
    hooks = load(hooks_path)
    if hooks.pop("agent-bridge", None) is not None:
        save(hooks_path, hooks)
        print("removed agy hooks")

settings_path = os.path.join(home, ".claude", "settings.json")
if os.path.exists(settings_path):
    settings = load(settings_path)
    allow = settings.get("permissions", {}).get("allow", [])
    keep = [r for r in allow if "~/.agent-bridge/bridge.py" not in r and not r.startswith("mcp__agent-bridge__")]
    if keep != allow:
        settings["permissions"]["allow"] = keep
        save(settings_path, settings)
        print("removed claude allow rules")
'@
if ($Python) { $Unconfigure | & $Python - } else { Write-Host 'Python not found: hooks.json and settings.json left as is' }

if (Get-Command claude -ErrorAction SilentlyContinue) {
    $ErrorActionPreference = 'Continue'
    claude mcp remove -s user agent-bridge 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { Write-Host 'removed claude mcp server' }
    $ErrorActionPreference = 'Stop'
}

$Mark = '# agent-bridge: claude starts with the MCP channel'
$ProfilePath = $PROFILE.CurrentUserAllHosts
if ((Test-Path $ProfilePath) -and (Select-String -Path $ProfilePath -SimpleMatch $Mark -Quiet)) {
    $lines = Get-Content $ProfilePath
    $out = New-Object System.Collections.Generic.List[string]
    $skip = 0
    foreach ($line in $lines) {
        if ($line -eq $Mark) {
            $skip = 2  # the marker and the dot-source line after it
            if ($out.Count -gt 0 -and $out[$out.Count - 1] -eq '') { $out.RemoveAt($out.Count - 1) }
        }
        if ($skip) { $skip--; continue }
        $out.Add($line)
    }
    Set-Content -Path $ProfilePath -Value $out -Encoding UTF8
    Write-Host "removed claude wrapper from $ProfilePath"
}

$Key = 'HKCU:\Software\Microsoft\Command Processor'
$autorun = (Get-ItemProperty $Key -ErrorAction SilentlyContinue).AutoRun
if ($autorun -like '*claude-channel.cmd*') {
    # Drop only our doskey macro; keep any other AutoRun command.
    $rest = ($autorun -split ' & ' | Where-Object { $_ -notlike '*claude-channel.cmd*' }) -join ' & '
    if ($rest) { Set-ItemProperty $Key -Name AutoRun -Value $rest } else { Remove-ItemProperty $Key -Name AutoRun }
    Write-Host 'removed claude macro from cmd.exe AutoRun'
}

Remove-Item (Join-Path $BridgeHome 'claude-channel.ps1'), (Join-Path $BridgeHome 'claude-channel.cmd') -Force -ErrorAction SilentlyContinue
$SkillDir = Join-Path $HOME '.claude\skills\agy-bridge'
if (Test-Path $SkillDir) { Remove-Item $SkillDir -Recurse -Force; Write-Host 'removed claude skill' }
Remove-Item (Join-Path $BridgeHome 'bridge.py'), (Join-Path $BridgeHome 'mcp_channel.py') -Force -ErrorAction SilentlyContinue
if ($Purge -and (Test-Path $BridgeHome)) { Remove-Item $BridgeHome -Recurse -Force; Write-Host "purged $BridgeHome" }

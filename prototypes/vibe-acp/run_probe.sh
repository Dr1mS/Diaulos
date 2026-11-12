#!/usr/bin/env bash
# vibe-acp probe (A1-A5). Throwaway VIBE_HOME in out/run/vibe_home: a copy of
# ~/.vibe/config.toml with save_dir moved into the run and the probe MCP
# server (../vibe/probe_mcp.py, as "probe") in mcp_servers. No tool permission
# set. The API key stays in the OS keyring.
# Step 1: session/new + "Reply with the single word OK1." (no tool).
# Step 2: title written into meta.json (title_source manual), like /rename.
# Step 3: new vibe-acp process, session/load + a prompt that calls probe_ping.
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
SERVER="$HERE/../vibe/probe_mcp.py"
RUN="$HERE/out/run"
rm -rf "$RUN"; mkdir -p "$RUN/vibe_home" "$RUN/work"
export VIBE_HOME="$RUN/vibe_home" PROBE_EXTRA_SERVER="$SERVER"
python3 - "$HOME/.vibe/config.toml" "$VIBE_HOME/config.toml" "$SERVER" "$RUN/config-server.jsonl" <<'EOF'
import re, sys, os
src, dst, server, log = sys.argv[1:]
t = open(src, encoding="utf-8").read()
assert "mcp_servers = []" in t
t = t.replace("mcp_servers = []", 'mcp_servers = [{ name = "probe", transport = "stdio", '
              f'command = "python3", args = ["{server}"], env = {{ PROBE_LOG = "{log}" }} }}]', 1)
t, n = re.subn(r'(?m)^save_dir = ".*"$', f'save_dir = "{os.path.dirname(dst)}/logs/session"', t)
assert n == 1
open(dst, "w", encoding="utf-8").write(t)
EOF
show() {
  for m in "$VIBE_HOME"/logs/session/*/meta.json; do
    python3 - "$m" <<'EOF'
import json, os, sys
p = sys.argv[1]; m = json.load(open(p)); d = os.path.dirname(p)
n = sum(1 for _ in open(os.path.join(d, "messages.jsonl")))
s = m.get("stats", {})
print(f"  {os.path.basename(d)} id={m['session_id']} title={m.get('title')!r} source={m.get('title_source')}"
      f" messages={n} cost=${s.get('session_cost')} tokens={s.get('session_total_llm_tokens')}")
EOF
  done
}
echo "== step 1: session/new"
python3 "$HERE/acp_probe.py" "$RUN" new "Reply with the single word OK1."
show
META=$(ls "$VIBE_HOME"/logs/session/*/meta.json | head -1)
SID=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['session_id'])" "$META")
echo "== step 2: title written into meta.json"
python3 - "$META" <<'EOF'
import json, sys
p = sys.argv[1]; m = json.load(open(p))
m["title"], m["title_source"] = "acp-probe-title", "manual"
json.dump(m, open(p, "w"), indent=2)
EOF
show
echo "== step 3: new process, session/load $SID"
python3 "$HERE/acp_probe.py" "$RUN" load "$SID" "Call the tool probe_ping exactly once, then answer with the single word OK2."
show
echo "== MCP servers started (config: probe; mcpServers param: extra)"
for f in config-server extra; do
  echo "  $f: $( [ -f "$RUN/$f.jsonl" ] && grep -c '"kind": "start"' "$RUN/$f.jsonl" || echo 0) starts, $( [ -f "$RUN/$f.jsonl" ] && grep -c 'tools/call' "$RUN/$f.jsonl" || echo 0) tools/call"
done

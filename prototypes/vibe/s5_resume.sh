#!/usr/bin/env bash
# S5: run one programmatic turn on a closed session with --resume, and show
# whether vibe appends to the same session folder or writes a new one.
# Usage: s5_resume.sh <probe-dir>   (a probe dir whose vibe has exited)
set -u
D="$(cd "${1:?probe dir}" && pwd)"
export VIBE_HOME="$D/vibe_home" PROBE_DIR="$D" PROBE_LOG="$D/probe.jsonl"
export VIBE_ENABLE_EXPERIMENTAL_HOOKS=true PROBE_LISTEN=0
show() {
  for m in "$VIBE_HOME"/logs/session/*/meta.json; do
    python3 - "$m" <<'EOF'
import json, os, sys
p = sys.argv[1]
m = json.load(open(p))
d = os.path.dirname(p)
n = sum(1 for _ in open(os.path.join(d, "messages.jsonl")))
print(f"  {os.path.basename(d)} id={m['session_id']} parent={m.get('parent_session_id')}"
      f" title={m.get('title')!r} source={m.get('title_source')} messages={n}"
      f" cwd={m['environment']['working_directory']}")
EOF
  done
}
ID=$(python3 -c "import json,glob,sys; print(json.load(open(sorted(glob.glob(sys.argv[1]+'/logs/session/*/meta.json'))[0]))['session_id'])" "$VIBE_HOME")
echo "before:"; show
echo "run: vibe -p ... --resume $ID --max-price 0.10 --trust --workdir $D/work --output text"
vibe -p "Reply with the single word RESUMED1." --resume "$ID" --max-price 0.10 \
  --trust --workdir "$D/work" --output text
echo "exit=$?"
echo "after:"; show

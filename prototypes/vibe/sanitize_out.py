#!/usr/bin/env python3
"""Clean probe outputs before committing them. Idempotent.

Usage: sanitize_out.py [dir-or-file ...]   (default: out/ next to this script)

On each file given, and every regular file under each dir given (text files only, binary ones skipped):
- the home directory of the account that ran the probe becomes
  /home/<compte>;
- the "username" field of vibe's meta.json becomes "<compte>";
- any client_key value (vibe's experiments key, specific to the machine)
  becomes "<masqué>".
On JSON-lines probe logs (probe.jsonl, and any *.jsonl with "env"/"parents"):
- each "env" object becomes {"keys": [...names...], "values": {...}}, values
  kept only for VIBE_*, PROBE_*, TERM, LC_CTYPE, SHELL;
- each "parents" chain is cut above the vibe process: the launcher's own
  command lines (the probing agent's shell and session) become "<launcher>".
"""

import json
import os
import re
import sys

KEEP = ("VIBE_", "PROBE_")
KEEP_EXACT = {"TERM", "LC_CTYPE", "SHELL"}
HOME = os.path.expanduser("~").rstrip("/")
ACCOUNT_HOME = "/home/<compte>"
USERNAME_RE = re.compile(r'("username"\s*:\s*)"[^"]*"')
CLIENT_KEY_RE = re.compile(r'("client_key"\s*[:=]\s*)"[^"]*"')


def shrink(env):
    if "keys" in env and "values" in env:
        return env
    return {
        "keys": sorted(env),
        "values": {k: v for k, v in env.items() if k.startswith(KEEP) or k in KEEP_EXACT},
    }


def cut_parents(chain):
    out, seen_vibe = [], False
    for p in chain:
        if seen_vibe:
            out.append({"pid": p["pid"], "cmd": "<launcher>"})
            continue
        out.append(p)
        if "/bin/vibe" in p["cmd"]:
            seen_vibe = True
    return out


def clean_jsonl_record(line):
    try:
        rec = json.loads(line)
    except ValueError:
        return line
    if not isinstance(rec, dict):
        return line
    changed = False
    if isinstance(rec.get("env"), dict):
        rec["env"] = shrink(rec["env"])
        changed = True
    if isinstance(rec.get("parents"), list):
        rec["parents"] = cut_parents(rec["parents"])
        changed = True
    return json.dumps(rec, ensure_ascii=False) if changed else line


def clean_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except (UnicodeDecodeError, OSError):
        return False
    new = text
    if path.endswith(".jsonl"):
        new = "\n".join(clean_jsonl_record(l) if l.strip() else l for l in new.split("\n"))
    new = new.replace(HOME + "/", ACCOUNT_HOME + "/").replace(HOME + '"', ACCOUNT_HOME + '"')
    new = USERNAME_RE.sub(r'\1"<compte>"', new)
    new = CLIENT_KEY_RE.sub(r'\1"<masqué>"', new)
    if new != text:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new)
        return True
    return False


def main():
    dirs = sys.argv[1:] or [os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")]
    changed = 0
    for d in dirs:
        if os.path.isfile(d):
            changed += clean_file(d)
            continue
        for root, _, files in os.walk(d):
            for name in files:
                if clean_file(os.path.join(root, name)):
                    changed += 1
    print(f"files changed: {changed}")


if __name__ == "__main__":
    main()

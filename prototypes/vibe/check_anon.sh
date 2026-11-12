#!/usr/bin/env bash
# Anonymity check of the probe files that git would commit under the given
# directories (tracked or untracked, .gitignore applied), this script excepted
# (it holds the patterns). Prints a count per
# category, never the matched values. Run from the repository.
# Usage: check_anon.sh <dir> [dir ...]
set -u
FILES=$( { git ls-files -- "$@"; git ls-files --others --exclude-standard -- "$@"; } | sort -u | grep -v 'check_anon\.sh$')
count() { # $1 label, $2 grep -E pattern (case-insensitive)
  local n
  n=$(printf '%s\n' "$FILES" | xargs -d '\n' grep -oiE -- "$2" 2>/dev/null | wc -l)
  printf '%-58s %s\n' "$1" "$n"
}
echo "files checked: $(printf '%s\n' "$FILES" | grep -c .)"
count "home directory of this account, as a path" "$(printf '%s' "$HOME" | sed 's/[.[\*^$/]/\\&/g')(/|\")"
count "username field with a value other than <compte>" '"username": "[^<"][^"]*"'
count "client_key with a value other than <masqué>" '"?client_key"?[[:space:]]*[:=][[:space:]]*"[^<"][^"]*"'
count "Mistral API key value (MISTRAL_API_KEY=... or \"...\")" 'MISTRAL_API_KEY"?[[:space:]]*[:=][[:space:]]*"?[A-Za-z0-9]{16,}'
count "Claude session id of the probing agent" "${CLAUDE_CODE_SESSION_ID:-no-session-id-set}"
count "launcher command lines (claude flags, run-role)" 'dangerously|run-role\.py'
count "machine name ($(hostname | wc -c) chars)" "$(hostname)"
count "e-mail addresses, all" '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,}'
count "  of which vibe@mistral.ai (public, in vibe's system prompt)" 'vibe@mistral\.ai'

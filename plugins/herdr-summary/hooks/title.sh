#!/bin/sh
# UserPromptSubmit: hands Claude Code the title register.ts wrote for this
# session, as the hook's JSON reply. Claude Code writes the session title to the
# terminal title, which herdr-core reports and herdr's agents list shows.
input=$(cat)
[ -n "${HC_TERMINAL_ID:-}" ] || exit 0
session=$(printf '%s' "$input" | sed -n 's/.*"session_id"[[:space:]]*:[[:space:]]*"\([A-Za-z0-9-]*\)".*/\1/p' | head -n 1)
[ -n "$session" ] || exit 0
file="${XDG_RUNTIME_DIR:-${TMPDIR:-/tmp}}/claude-herdr-summary/$session.json"
[ -f "$file" ] && cat "$file"
exit 0

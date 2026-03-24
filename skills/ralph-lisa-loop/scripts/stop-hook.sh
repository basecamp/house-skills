#!/usr/bin/env bash
#
# ralph-lisa-loop stop hook
#
# Phase-aware Stop hook for the ralph-lisa loop. Reads session state from
# tmp/ralph-lisa-loop-session.md and decides whether to block (continue
# the loop) or allow (let Claude stop).
#
# Install in ~/.claude/settings.json → hooks.Stop:
#   {
#     "matcher": "",
#     "hooks": [{
#       "type": "command",
#       "command": "<absolute-path>/scripts/stop-hook.sh",
#       "timeout": 10000
#     }]
#   }
#
# Exit codes:
#   0 = allow stop (no session, awaiting_human, or complete)
#   2 = block stop + re-inject continuation prompt
#
# Note: Claude sends hook JSON on stdin (e.g., transcript_path), but this
# script relies solely on the session file.

set -euo pipefail

# ── Locate session file ────────────────────────────────────────────────

# Try tmp/ralph-lisa-loop-session.md relative to git root, then cwd
find_session() {
  local git_root
  git_root="$(git rev-parse --show-toplevel 2>/dev/null || true)"

  if [[ -n "$git_root" && -f "$git_root/tmp/ralph-lisa-loop-session.md" ]]; then
    echo "$git_root/tmp/ralph-lisa-loop-session.md"
    return 0
  fi

  if [[ -f "tmp/ralph-lisa-loop-session.md" ]]; then
    echo "tmp/ralph-lisa-loop-session.md"
    return 0
  fi

  return 1
}

SESSION_FILE="$(find_session)" || exit 0  # No session file → allow stop

# ── Parse YAML frontmatter ─────────────────────────────────────────────

# Extract a YAML field value from frontmatter (between --- delimiters)
yaml_field() {
  local field="$1"
  sed -n '/^---$/,/^---$/p' "$SESSION_FILE" \
    | grep "^${field}:" \
    | head -1 \
    | sed "s/^${field}:[[:space:]]*//" || true
}

STATUS="$(yaml_field status)"
MODE="$(yaml_field mode)"
ROUND="$(yaml_field current_round)"
OPEN_FINDINGS="$(yaml_field open_findings_count)"
OPEN_DISPUTES="$(yaml_field open_disputes_count)"
MAX_ROUNDS="$(yaml_field max_rounds)"

# ── Decision logic ──────────────────────────────────────────────────────

# Allow stop if awaiting human input
if [[ "$STATUS" == "awaiting_human" ]]; then
  exit 0
fi

# Allow stop if session is complete
if [[ "$STATUS" == "complete" ]]; then
  exit 0
fi

# Active session → block stop and re-inject continuation

# ── Extract continuation block ──────────────────────────────────────────

# Validate both markers exist and are ordered before extracting.
# If markers are missing or malformed, fall through to the fallback.
CONTINUATION=""

START_LINE="$(grep -n '<!-- CONTINUATION BLOCK' "$SESSION_FILE" | head -1 | cut -d: -f1 || true)"
END_LINE="$(grep -n '<!-- END CONTINUATION BLOCK' "$SESSION_FILE" | head -1 | cut -d: -f1 || true)"

if [[ -n "$START_LINE" && -n "$END_LINE" && "$START_LINE" -lt "$END_LINE" ]]; then
  CONTINUATION="$(sed -n "${START_LINE},${END_LINE}p" "$SESSION_FILE" \
    | grep -v '^<!--' \
    | tr '\n' ' ' \
    | sed 's/  */ /g; s/^ *//; s/ *$//')" || true
fi

# Fallback if markers are missing or empty
if [[ -z "$CONTINUATION" ]]; then
  CONTINUATION="You are running the ralph-lisa loop. Read tmp/ralph-lisa-loop-session.md for state and follow the ralph-lisa-loop skill guide. Take the next action for your current mode and round. Mode: ${MODE}. Round: ${ROUND}. Open findings: ${OPEN_FINDINGS}. Open disputes: ${OPEN_DISPUTES}."
fi

# ── Build stop hook response ────────────────────────────────────────────

# Output JSON for the hook system:
# - decision: "block" prevents Claude from stopping
# - reason: shown in Claude's context as the continuation prompt
# JSON-escape the continuation text to handle quotes/backslashes/tabs safely.
# Try jq first; fall back to manual escaping if jq is missing or broken.
json_escape() {
  local raw="$1"
  local result
  if result=$(printf '%s' "$raw" | jq -Rsa . 2>/dev/null) && [[ -n "$result" ]]; then
    echo "$result"
  else
    local escaped="${raw//\\/\\\\}"
    escaped="${escaped//\"/\\\"}"
    escaped="${escaped//$'\t'/\\t}"
    escaped="${escaped//$'\n'/\\n}"
    escaped="${escaped//$'\r'/\\r}"
    printf '"%s"' "$escaped"
  fi
}

# Build progress prefix for UI visibility
# yaml_field returns literal "null" for unset YAML values — normalize to empty
[[ "$ROUND" == "null" ]] && ROUND=""
[[ "$MAX_ROUNDS" == "null" ]] && MAX_ROUNDS=""
[[ "$OPEN_FINDINGS" == "null" ]] && OPEN_FINDINGS=""
[[ "$OPEN_DISPUTES" == "null" ]] && OPEN_DISPUTES=""
[[ "$MODE" == "null" ]] && MODE=""
PROGRESS="[Round ${ROUND:-?}/${MAX_ROUNDS:-?} | ${OPEN_FINDINGS:-0} findings | ${OPEN_DISPUTES:-0} disputes | ${MODE:-unknown}]"

REASON=$(json_escape "[ralph-lisa-loop] ${PROGRESS} ${CONTINUATION}")
printf '{"decision":"block","reason":%s}\n' "$REASON"

exit 2

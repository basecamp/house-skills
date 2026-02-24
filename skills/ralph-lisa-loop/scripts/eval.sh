#!/usr/bin/env bash
#
# ralph-lisa-loop eval checks
#
# Validates the structural integrity of a ralph-lisa-loop session file.
# Run after session completes to verify protocol compliance.
#
# Usage: eval.sh [session-path]
#   Default: .claude/ralph-lisa-loop-session.md

# Do NOT use set -e — all checks must run even if earlier ones fail.

SESSION="${1:-.claude/ralph-lisa-loop-session.md}"

fail_count=0
warn_count=0

# ── Helpers ─────────────────────────────────────────────────────────────

report() {
  local num="$1" label="$2" status="$3" detail="${4:-}"
  if [[ "$status" == "FAIL" ]]; then
    ((fail_count++)) || true
  elif [[ "$status" == "WARN" ]]; then
    ((warn_count++)) || true
  fi
  if [[ -n "$detail" ]]; then
    printf '%2d) %-40s %s  %s\n' "$num" "$label" "$status" "$detail"
  else
    printf '%2d) %-40s %s\n' "$num" "$label" "$status"
  fi
}

yaml_field() {
  local file="$1" field="$2"
  sed -n '/^---$/,/^---$/p' "$file" \
    | grep "^${field}:" \
    | head -1 \
    | sed "s/^${field}:[[:space:]]*//"
}

# ── Check 1: Session file exists ────────────────────────────────────────

echo "=== Ralph-Lisa Loop Eval Checks ==="
echo "Session: $SESSION"
echo ""

if [[ ! -f "$SESSION" ]]; then
  report 1 "Session file exists" "FAIL" "not found: $SESSION"
  echo ""
  echo "=== Results: 1 FAIL, 0 WARN ==="
  exit 1
fi
report 1 "Session file exists" "PASS"

# ── Check 2: Round count ────────────────────────────────────────────────

rounds=$(grep -c "^## Round" "$SESSION" 2>/dev/null) || true
report 2 "Round count" "PASS" "$rounds rounds"

# ── Checks 3-8: Per-round section counts ────────────────────────────────

check_section() {
  local num="$1" label="$2" pattern="$3"
  local count
  count=$(grep -c "^### $pattern" "$SESSION" 2>/dev/null) || true
  if [[ "$rounds" -eq "$count" ]]; then
    report "$num" "$label per round" "PASS" "$count"
  else
    report "$num" "$label per round" "WARN" "$rounds rounds, $count sections"
  fi
}

check_section 3 "Self-Review" "Self-Review"
check_section 4 "External Review" "External Review"
check_section 5 "Reconciliation" "Reconciliation"
check_section 6 "Synthesis" "Synthesis"
check_section 7 "Finding Ledger" "Finding Ledger"
check_section 8 "Gate Check" "Gate Check"

# ── Check 9: Finding IDs present ────────────────────────────────────────

# Anchor to finding-ledger rows (| F-{n} |) to avoid matching dispute IDs (D-F-{n})
if grep -qE "^\|[[:space:]]*F-[0-9]+" "$SESSION" 2>/dev/null; then
  report 9 "Finding IDs present" "PASS"
else
  report 9 "Finding IDs present" "WARN" "no F-{n} IDs in finding ledger rows"
fi

# ── Check 10: Final status ──────────────────────────────────────────────

status=$(yaml_field "$SESSION" "status")
if [[ "$status" == "complete" ]]; then
  report 10 "Final status = complete" "PASS"
else
  report 10 "Final status = complete" "WARN" "status=$status"
fi

# ── Check 11: Continuation block well-formed ────────────────────────────

has_start=$(grep -c "<!-- CONTINUATION BLOCK" "$SESSION" 2>/dev/null) || true
has_end=$(grep -c "<!-- END CONTINUATION BLOCK" "$SESSION" 2>/dev/null) || true
if [[ "$has_start" -ge 1 && "$has_end" -ge 1 ]]; then
  content=$(sed -n '/<!-- CONTINUATION BLOCK/,/<!-- END CONTINUATION BLOCK/p' "$SESSION" \
    | grep -v '^<!--' \
    | tr -d '[:space:]')
  if [[ -n "$content" ]]; then
    report 11 "Continuation block well-formed" "PASS"
  else
    report 11 "Continuation block well-formed" "FAIL" "markers present but content empty"
  fi
else
  report 11 "Continuation block well-formed" "FAIL" "missing markers (start=$has_start end=$has_end)"
fi

# ── Check 12: Cache consistency ─────────────────────────────────────────

last_gate=$(grep "^Derived open findings:" "$SESSION" 2>/dev/null | tail -1)
if [[ -z "$last_gate" ]]; then
  report 12 "Cache consistency" "FAIL" "no gate check line found"
elif echo "$last_gate" | grep -q "Cache match: yes"; then
  report 12 "Cache consistency" "PASS"
else
  report 12 "Cache consistency" "FAIL" "$last_gate"
fi

# ── Check 13: No open findings ──────────────────────────────────────────

# Parse finding ledger rows (id starts with F-) and derive latest state per ID.
# A finding may appear in multiple rounds; the last occurrence wins.
open_findings=$(awk -F'|' '
  {
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2)  # id column
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", $6)  # state column
    if ($2 ~ /^F-[0-9]+$/) {
      state[$2] = $6
    }
  }
  END {
    count = 0
    for (id in state) {
      if (state[id] == "open" || state[id] == "disputed") count++
    }
    print count+0
  }
' "$SESSION" 2>/dev/null)
if [[ "${open_findings:-0}" -eq 0 ]]; then
  report 13 "No open/disputed findings" "PASS"
else
  report 13 "No open/disputed findings" "FAIL" "$open_findings found"
fi

# ── Check 14: No open disputes ──────────────────────────────────────────

# Parse dispute ledger rows (id starts with D-F-) and derive latest state per ID.
# A dispute may appear in multiple rounds; the last occurrence wins.
open_disputes=$(awk -F'|' '
  {
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2)  # id column
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", $7)  # state column
    if ($2 ~ /^D-F-[0-9]+$/) {
      state[$2] = $7
    }
  }
  END {
    count = 0
    for (id in state) {
      if (state[id] == "open") count++
    }
    print count+0
  }
' "$SESSION" 2>/dev/null)
if [[ "${open_disputes:-0}" -eq 0 ]]; then
  report 14 "No open disputes" "PASS"
else
  report 14 "No open disputes" "FAIL" "$open_disputes found"
fi

# ── Check 15: Rejection integrity ───────────────────────────────────────

# Parse finding ledger rows with state=rejected_with_reason and validate metadata.
# Track latest snapshot per finding ID; later rows overwrite earlier ones.
rejection_results=$(awk -F'|' '
  {
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", $2)   # id
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", $6)   # state
    if ($2 ~ /^F-[0-9]+$/) {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", $11)  # rejection_rationale
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", $12)  # rejection_approved_by
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", $13)  # rejection_approved_round
      st[$2] = $6
      rat[$2] = $11
      apby[$2] = $12
      aprd[$2] = $13
    }
  }
  END {
    total = 0; fail = 0
    for (id in st) {
      if (st[id] == "rejected_with_reason") {
        total++
        if (rat[id] == "" || apby[id] != "mediator" || aprd[id] == "") fail++
      }
    }
    printf "%d %d", total+0, fail+0
  }
' "$SESSION" 2>/dev/null)
rejected_total=$(echo "$rejection_results" | awk '{print $1}')
rejection_fail=$(echo "$rejection_results" | awk '{print $2}')

if [[ "${rejection_fail:-0}" -gt 0 ]]; then
  report 15 "Rejection integrity" "FAIL" "$rejection_fail of $rejected_total rejected findings with invalid metadata"
elif [[ "${rejected_total:-0}" -eq 0 ]]; then
  report 15 "Rejection integrity" "PASS" "no rejections"
else
  report 15 "Rejection integrity" "PASS" "$rejected_total rejections verified"
fi

# ── Check 16: Session archived ──────────────────────────────────────────

sid=$(yaml_field "$SESSION" "session_id")
session_dir=$(dirname "$SESSION")
archive_path="${session_dir}/ralph-lisa-loop-history/session-${sid}.md"
if [[ -f "$archive_path" ]]; then
  report 16 "Session archived" "PASS"
else
  report 16 "Session archived" "WARN" "not found: $archive_path"
fi

# ── Check 17: Round summaries have gate data ────────────────────────────

gate_data_count=$(grep -c "^Derived open findings:" "$SESSION" 2>/dev/null) || true
gate_section_count=$(grep -c "^### Gate Check" "$SESSION" 2>/dev/null) || true
if [[ "$gate_section_count" -gt 0 && "$gate_data_count" -ge "$gate_section_count" ]]; then
  report 17 "Round summaries have gate data" "PASS" "$gate_data_count entries"
elif [[ "$gate_section_count" -eq 0 ]]; then
  report 17 "Round summaries have gate data" "WARN" "no Gate Check sections"
else
  report 17 "Round summaries have gate data" "WARN" "$gate_section_count sections, $gate_data_count with data"
fi

# ── Summary ─────────────────────────────────────────────────────────────

echo ""
echo "=== Results: $fail_count FAIL, $warn_count WARN ==="

if [[ "$fail_count" -gt 0 ]]; then
  exit 1
fi
exit 0

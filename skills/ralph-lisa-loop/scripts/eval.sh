#!/usr/bin/env bash
#
# ralph-lisa-loop eval checks
#
# Validates the structural integrity of a ralph-lisa-loop session file.
# Run after session completes to verify protocol compliance.
#
# Usage: eval.sh [session-path] [--mid-session]
#   Default: tmp/ralph-lisa-loop-session.md
#   --mid-session: run structural checks only (1, 2, 12, 13), skip completion checks

# Do NOT use set -e — all checks must run even if earlier ones fail.

# Resolve default session path relative to git repo root (like stop-hook.sh),
# falling back to cwd if git is unavailable.
GIT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
SESSION="$GIT_ROOT/tmp/ralph-lisa-loop-session.md"
MID_SESSION=false
for arg in "$@"; do
  if [[ "$arg" == "--mid-session" ]]; then
    MID_SESSION=true
  elif [[ "$arg" != -* ]]; then
    SESSION="$arg"
  fi
done

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

# ── Checks 3-9: Per-round section counts (skip in mid-session) ───────────

if [[ "$MID_SESSION" == false ]]; then

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

check_section 3 "Implement" "Implement"
check_section 4 "Self-Review" "Self-Review"
check_section 5 "External Review" "External Review"
check_section 6 "Reconciliation" "Reconciliation"
check_section 7 "Synthesis" "Synthesis"
check_section 8 "Finding Ledger" "Finding Ledger"
check_section 9 "Gate Check" "Gate Check"

# ── Check 10: Finding IDs present ───────────────────────────────────────

# Anchor to finding-ledger rows (| F-{n} |) to avoid matching dispute IDs (D-F-{n})
if grep -qE "^\|[[:space:]]*F-[0-9]+" "$SESSION" 2>/dev/null; then
  report 10 "Finding IDs present" "PASS"
else
  report 10 "Finding IDs present" "WARN" "no F-{n} IDs in finding ledger rows"
fi

fi  # end skip in mid-session (checks 3-10)

# ── Check 11: Final status (skip in mid-session) ─────────────────────────

status=$(yaml_field "$SESSION" "status")
if [[ "$MID_SESSION" == false ]]; then
  if [[ "$status" == "complete" ]]; then
    report 11 "Final status = complete" "PASS"
  else
    report 11 "Final status = complete" "WARN" "status=$status"
  fi
fi

# ── Check 12: Continuation block well-formed ────────────────────────────

has_start=$(grep -c "<!-- CONTINUATION BLOCK" "$SESSION" 2>/dev/null) || true
has_end=$(grep -c "<!-- END CONTINUATION BLOCK" "$SESSION" 2>/dev/null) || true
if [[ "$has_start" -ge 1 && "$has_end" -ge 1 ]]; then
  content=$(sed -n '/<!-- CONTINUATION BLOCK/,/<!-- END CONTINUATION BLOCK/p' "$SESSION" \
    | grep -v '^<!--' \
    | tr -d '[:space:]')
  if [[ -n "$content" ]]; then
    report 12 "Continuation block well-formed" "PASS"
  else
    report 12 "Continuation block well-formed" "FAIL" "markers present but content empty"
  fi
else
  report 12 "Continuation block well-formed" "FAIL" "missing markers (start=$has_start end=$has_end)"
fi

# ── Check 13: Cache consistency ─────────────────────────────────────────

last_gate=$(grep "^Derived open findings:" "$SESSION" 2>/dev/null | tail -1)
if [[ -z "$last_gate" ]]; then
  report 13 "Cache consistency" "FAIL" "no gate check line found"
elif echo "$last_gate" | grep -q "Cache match: yes"; then
  report 13 "Cache consistency" "PASS"
else
  report 13 "Cache consistency" "FAIL" "$last_gate"
fi

# ── Checks 14-18: Completion-only checks (skip in mid-session) ──────────

if [[ "$MID_SESSION" == false ]]; then

# ── Check 14: No open findings ──────────────────────────────────────────

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
  report 14 "No open/disputed findings" "PASS"
else
  report 14 "No open/disputed findings" "FAIL" "$open_findings found"
fi

# ── Check 15: No open disputes ──────────────────────────────────────────

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
  report 15 "No open disputes" "PASS"
else
  report 15 "No open disputes" "FAIL" "$open_disputes found"
fi

# ── Check 16: Rejection integrity ───────────────────────────────────────

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
  report 16 "Rejection integrity" "FAIL" "$rejection_fail of $rejected_total rejected findings with invalid metadata"
elif [[ "${rejected_total:-0}" -eq 0 ]]; then
  report 16 "Rejection integrity" "PASS" "no rejections"
else
  report 16 "Rejection integrity" "PASS" "$rejected_total rejections verified"
fi

# ── Check 17: Session archived ──────────────────────────────────────────

sid=$(yaml_field "$SESSION" "session_id")
session_dir=$(dirname "$SESSION")
archive_path="${session_dir}/ralph-lisa-loop-history/session-${sid}.md"
if [[ -f "$archive_path" ]]; then
  report 17 "Session archived" "PASS"
else
  report 17 "Session archived" "WARN" "not found: $archive_path"
fi

# ── Check 18: Round summaries have gate data ────────────────────────────

gate_data_count=$(grep -c "^Derived open findings:" "$SESSION" 2>/dev/null) || true
gate_section_count=$(grep -c "^### Gate Check" "$SESSION" 2>/dev/null) || true
if [[ "$gate_section_count" -gt 0 && "$gate_data_count" -ge "$gate_section_count" ]]; then
  report 18 "Round summaries have gate data" "PASS" "$gate_data_count entries"
elif [[ "$gate_section_count" -eq 0 ]]; then
  report 18 "Round summaries have gate data" "WARN" "no Gate Check sections"
else
  report 18 "Round summaries have gate data" "WARN" "$gate_section_count sections, $gate_data_count with data"
fi

# ── Check 19: Reviewer backend set ──────────────────────────────────────

backend=$(yaml_field "$SESSION" "reviewer_backend")
if [[ -n "$backend" && "$backend" != "null" ]]; then
  report 19 "Reviewer backend set" "PASS" "backend=$backend"
else
  report 19 "Reviewer backend set" "FAIL" "reviewer_backend is missing or null"
fi

# ── Shared: Extract audit lines from Gate Check sections only ────────────

# Gate Check sections start with "### Gate Check" and end at any markdown heading
# (## or ###). The /^##/ pattern matches both. Extract "Review channel:" lines only
# from within these sections.
gate_check_audit_lines=$(awk '
  /^### Gate Check/ { in_gate = 1; next }
  /^##/ { in_gate = 0 }
  in_gate && /^Review channel:/ { print NR ":" $0 }
' "$SESSION" 2>/dev/null || true)

# ── Check 20: Review audit presence ────────────────────────────────────

# Validate each Gate Check section has a complete audit line with all three tokens.
audit_full_count=0
audit_partial_count=0
while IFS= read -r line; do
  [[ -z "$line" ]] && continue
  content="${line#*:}"  # strip line number prefix
  has_channel=false; has_effort=false; has_policy=false
  echo "$content" | grep -q "Review channel:" && has_channel=true
  echo "$content" | grep -q "Reasoning effort:" && has_effort=true
  echo "$content" | grep -q "Policy compliant:" && has_policy=true
  if [[ "$has_channel" == true && "$has_effort" == true && "$has_policy" == true ]]; then
    ((audit_full_count++)) || true
  elif [[ "$has_channel" == true || "$has_effort" == true || "$has_policy" == true ]]; then
    ((audit_partial_count++)) || true
  fi
done <<< "$gate_check_audit_lines"

# Note: this is aggregate validation (total audit lines >= total gate sections).
# A section with 2 audit lines and another with 0 would still pass. Acceptable
# since the protocol writes exactly one audit line per Gate Check section.
if [[ "$gate_section_count" -gt 0 && "$audit_full_count" -ge "$gate_section_count" ]]; then
  report 20 "Review audit presence" "PASS" "$audit_full_count complete entries"
elif [[ "$gate_section_count" -eq 0 ]]; then
  report 20 "Review audit presence" "WARN" "no Gate Check sections"
elif [[ "$audit_partial_count" -gt 0 ]]; then
  report 20 "Review audit presence" "WARN" "$audit_partial_count partial entries (missing channel/effort/policy tokens)"
else
  report 20 "Review audit presence" "WARN" "$gate_section_count rounds, $audit_full_count with full audit trail"
fi

# ── Check 21: Reasoning policy compliance ────────────────────────────────

# All rounds should use xhigh reasoning effort.
# Per-line channel awareness: in mcp_degraded sessions, skip MCP rounds (effort
# uncontrollable) but still check exec rounds (effort set via -c flag).
# Self-review-only rounds have no external effort — always skip.
# Uses the section-scoped gate_check_audit_lines from above.
channel_status_for_21=$(yaml_field "$SESSION" "review_channel_status")
policy_violations=0
skipped_rounds=0

while IFS= read -r match; do
  [[ -z "$match" ]] && continue
  content="${match#*:}"  # strip line number prefix
  channel=$(echo "$content" | sed 's/.*Review channel:[[:space:]]*//' | sed 's/\..*//')
  effort=$(echo "$content" | sed 's/.*Reasoning effort:[[:space:]]*//' | sed 's/\..*//')

  # Skip rounds where effort is not controllable
  if [[ "$channel" == "self-review-only" ]]; then
    ((skipped_rounds++)) || true
    continue
  fi
  if [[ "$channel_status_for_21" == "mcp_degraded" && "$channel" == "mcp" ]]; then
    ((skipped_rounds++)) || true
    continue
  fi

  if [[ "$effort" != "xhigh" ]]; then
    ((policy_violations++)) || true
  fi
done <<< "$gate_check_audit_lines"

if [[ "$policy_violations" -eq 0 && "$skipped_rounds" -gt 0 ]]; then
  report 21 "Reasoning policy compliance" "PASS" "$skipped_rounds rounds skipped (effort not controllable)"
elif [[ "$policy_violations" -eq 0 ]]; then
  report 21 "Reasoning policy compliance" "PASS"
else
  report 21 "Reasoning policy compliance" "WARN" "$policy_violations rounds not using xhigh ($skipped_rounds skipped)"
fi

# ── Check 22: Review channel status valid ────────────────────────────────

channel_status=$(yaml_field "$SESSION" "review_channel_status")
case "$channel_status" in
  mcp_ready|mcp_degraded|exec_opt_in)
    report 22 "Review channel status valid" "PASS" "status=$channel_status"
    ;;
  blocked)
    report 22 "Review channel status valid" "FAIL" "status=blocked (session should not complete in blocked state)"
    ;;
  null|"")
    report 22 "Review channel status valid" "FAIL" "review_channel_status is missing or null (preflight should set this)"
    ;;
  *)
    report 22 "Review channel status valid" "FAIL" "unknown status: $channel_status"
    ;;
esac

# ── Check 23: Compaction integrity ───────────────────────────────────────

# If compaction occurred, verify cumulative ledgers exist and contain valid IDs.
# State comparison not feasible (compacted round rows are gone).
compacted_through=$(yaml_field "$SESSION" "compacted_through_round")
if [[ "$compacted_through" =~ ^[0-9]+$ && "$compacted_through" -gt 0 ]]; then
  # Structural: verify compacted section exists with cumulative ledgers
  has_compacted_section=$(grep -c "^## Rounds 1-" "$SESSION" 2>/dev/null) || true
  has_cumulative_findings=$(grep -c "^### Cumulative Finding Ledger" "$SESSION" 2>/dev/null) || true
  has_cumulative_disputes=$(grep -c "^### Cumulative Dispute Ledger" "$SESSION" 2>/dev/null) || true

  if [[ "$has_compacted_section" -lt 1 || "$has_cumulative_findings" -lt 1 || "$has_cumulative_disputes" -lt 1 ]]; then
    report 23 "Compaction integrity" "WARN" "compacted_through_round=$compacted_through but missing cumulative ledger sections"
  else
    # Semantic: verify finding and dispute IDs from compacted rounds appear in
    # the cumulative ledgers. State comparison is not feasible after compaction
    # (compacted round rows are gone, recent rounds may have updated state).
    # Single awk — safe against malformed input.
    compaction_result=$(awk -F'|' '
      function trim(s) { gsub(/^[[:space:]]+|[[:space:]]+$/, "", s); return s }

      /^### Cumulative Finding Ledger/ { section = "cf"; next }
      /^### Cumulative Dispute Ledger/ { section = "cd"; next }
      /^### Finding Ledger/ { section = "fl"; next }
      /^### Dispute Ledger/ { section = "dl"; next }
      /^##/ { section = "" }

      section == "cf" { id = trim($2); if (id ~ /^F-[0-9]+$/) cumul_f[id] = 1 }
      section == "cd" { id = trim($2); fid = trim($3); if (id ~ /^D-F-[0-9]+$/) { cumul_d[id] = 1; cumul_d_ref[id] = fid } }
      section == "fl" { id = trim($2); if (id ~ /^F-[0-9]+$/) seen_f[id] = 1 }
      section == "dl" { id = trim($2); if (id ~ /^D-F-[0-9]+$/) seen_d[id] = 1 }

      END {
        drift = ""
        # Dispute referential integrity: every cumulative dispute finding_id
        # column should reference a finding in cumulative or recent rounds.
        for (id in cumul_d) {
          fid = cumul_d_ref[id]
          if (fid == "" || (!(fid in cumul_f) && !(fid in seen_f))) drift = drift " " id "(orphan-ref)"
        }
        # Finding ID cross-reference is not feasible: compacted round rows
        # are replaced by the cumulative, so cumulative IS the authoritative
        # record. Nothing to cross-reference against.
        if (drift == "") print "ok"
        else print "drift:" drift
      }
    ' "$SESSION" 2>/dev/null)

    if [[ "$compaction_result" == "ok" ]]; then
      report 23 "Compaction integrity" "PASS" "compacted through round $compacted_through, cumulative ledger IDs valid"
    else
      report 23 "Compaction integrity" "WARN" "$compaction_result"
    fi
  fi
else
  report 23 "Compaction integrity" "PASS" "no compaction performed"
fi

fi  # end completion-only checks (14-23)

# ── Summary ─────────────────────────────────────────────────────────────

if [[ "$MID_SESSION" == true ]]; then
  echo ""
  echo "=== Mid-Session Results: $fail_count FAIL, $warn_count WARN (structural checks only) ==="
else
  echo ""
  echo "=== Results: $fail_count FAIL, $warn_count WARN ==="
fi

if [[ "$fail_count" -gt 0 ]]; then
  exit 1
fi
exit 0

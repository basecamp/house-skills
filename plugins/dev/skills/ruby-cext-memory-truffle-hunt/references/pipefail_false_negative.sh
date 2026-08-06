#!/usr/bin/env bash
# BURNED FALSE NEGATIVE, round 4 -- a detector that silently inverts its own verdict.
#
# Mechanism: `set -o pipefail` + `grep -q`.
#   grep -q exits 0 on the FIRST match and closes its stdin.
#   The upstream producer (printf/cat/echo of a large buffer) then gets SIGPIPE -> 141.
#   pipefail promotes the RIGHTMOST NON-ZERO status, so the pipeline reports 141.
#   `if <pipeline>; then FOUND; else CLEAN; fi` therefore takes the CLEAN branch
#   *because* the pattern matched.
#
# Fires only when the searched text is larger than one pipe buffer (64 KiB) OR the
# match is early enough that grep exits before the producer finishes -- i.e. exactly
# on a verbose sanitizer report with the ERROR line at the top. A small fixture
# passes, so this survives unit-testing the harness.
#
# This is not hypothetical: it reported a reproduced ASan stack-buffer-overflow in a
# real audit target as VERDICT=CLEAN. (Target withheld -- the finding overlaps an
# advisory that was still unpublished at the time of writing. The mechanism below is
# the reusable part; nothing about it depends on which gem it was.)
#
# Run:  bash pipefail_false_negative.sh
set -u

# A sanitizer-report-shaped buffer: match near the top, lots of memory map after it.
big=$'==1==ERROR: AddressSanitizer: stack-buffer-overflow\n'
for _ in $(seq 1 2000); do big+="ffff8d400000-ffff8d573000 r-xp 00000000 00:107 183737483 /usr/lib/libasan.so"$'\n'; done
printf 'buffer bytes = %s\n' "${#big}"

echo
echo "--- BROKEN: set -o pipefail + grep -q ---"
( set -o pipefail
  if printf '%s\n' "$big" | grep -q "ERROR: AddressSanitizer"; then
    echo "  verdict = ASAN-ERROR   (correct)"
  else
    echo "  verdict = CLEAN        (WRONG -- the pattern IS present)"
  fi
  printf '%s\n' "$big" | grep -q "ERROR: AddressSanitizer"
  echo "  pipeline status = $?   (141 = SIGPIPE on the producer)" )

echo
echo "--- FIX 1: bash pattern match, no pipeline ---"
case "$big" in
  *"ERROR: AddressSanitizer"*) echo "  verdict = ASAN-ERROR   (correct)" ;;
  *)                           echo "  verdict = CLEAN        (WRONG)" ;;
esac

echo
echo "--- FIX 2: keep grep, drop -q so the producer is never cut off ---"
( set -o pipefail
  n=$(printf '%s\n' "$big" | grep -c "ERROR: AddressSanitizer")
  [ "$n" -gt 0 ] && echo "  verdict = ASAN-ERROR   (correct, matches=$n)" )

echo
echo "Note: grep -c consumes all input, so no SIGPIPE. 'grep -q' and 'head -1' are"
echo "the two common early-exit consumers that trip pipefail this way."

#!/usr/bin/env bash
#
# Git activity fetcher — local git log to day-cached JSON.
#
# Usage:
#   ./git-activity.sh --repos name:path[,name:path,...] --since DATE --until DATE
#                     [--author PATTERN] [--all-branches] [--reuse]
#
# Output:
#   ~/.cache/recap/git/{repo}/[{scope}/]{YYYY-MM-DD}/log.json  per-day per-repo
#   Prints cache paths to stdout when done.
#
set -euo pipefail

REPOS=""
SINCE_DATE=""
UNTIL_DATE=""
REUSE=false
AUTHOR=""
ALL_BRANCHES=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --repos) REPOS="$2"; shift 2 ;;
    --since) SINCE_DATE="$2"; shift 2 ;;
    --until) UNTIL_DATE="$2"; shift 2 ;;
    --author) AUTHOR="$2"; shift 2 ;;
    --all-branches) ALL_BRANCHES=true; shift ;;
    --reuse) REUSE=true; shift ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$REPOS" ]]; then
  echo "Error: --repos name:path[,name:path,...] is required" >&2
  exit 1
fi

if [[ -z "$SINCE_DATE" ]]; then
  echo "Error: --since DATE is required" >&2
  exit 1
fi

[[ -z "$UNTIL_DATE" ]] && UNTIL_DATE=$(date -u +%Y-%m-%d)

# Normalize to YYYY-MM-DD
SINCE_DAY="${SINCE_DATE:0:10}"
UNTIL_DAY="${UNTIL_DATE:0:10}"

CACHE_BASE="$HOME/.cache/recap/git"

LOG_SCOPE=()
SCOPE=""
if [[ "$ALL_BRANCHES" == "true" ]]; then
  LOG_SCOPE+=(--branches --remotes)
  SCOPE="all-branches"
fi
if [[ -n "$AUTHOR" ]]; then
  LOG_SCOPE+=(--author="$AUTHOR")
  AUTHOR_SLUG=$(printf '%s' "$AUTHOR" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9' '-' | sed 's/^-//; s/-$//')
  AUTHOR_DIGEST=$(printf '%s' "$AUTHOR" | git hash-object --stdin | cut -c1-8)
  SCOPE="${SCOPE:+$SCOPE-}author-$AUTHOR_SLUG-$AUTHOR_DIGEST"
fi

# Generate list of days in range
days_in_range() {
  local current="$1" end="$2"
  while [[ "$current" < "$end" || "$current" == "$end" ]]; do
    echo "$current"
    current=$(date -d "$current + 1 day" +%Y-%m-%d 2>/dev/null || date -j -v+1d -f "%Y-%m-%d" "$current" +%Y-%m-%d)
  done
}

# Parse repos: "name:path,name:path" → iterate
IFS=',' read -ra REPO_PAIRS <<< "$REPOS"

for pair in "${REPO_PAIRS[@]}"; do
  REPO_NAME="${pair%%:*}"
  REPO_PATH="${pair#*:}"

  # Expand ~ in path
  REPO_PATH="${REPO_PATH/#\~/$HOME}"

  if [[ ! -d "$REPO_PATH/.git" && ! -f "$REPO_PATH/.git" ]]; then
    echo "WARN: $REPO_PATH is not a git repo, skipping $REPO_NAME" >&2
    continue
  fi

  echo "Fetching git log for $REPO_NAME ($REPO_PATH)..." >&2

  for day in $(days_in_range "$SINCE_DAY" "$UNTIL_DAY"); do
    NEXT_DAY=$(date -d "$day + 1 day" +%Y-%m-%d 2>/dev/null || date -j -v+1d -f "%Y-%m-%d" "$day" +%Y-%m-%d)
    CACHE_DIR="$CACHE_BASE/$REPO_NAME${SCOPE:+/$SCOPE}/$day"
    CACHE_FILE="$CACHE_DIR/log.json"

    # Skip if reuse and cache exists with complete flag
    if [[ "$REUSE" == "true" && -f "$CACHE_FILE" ]]; then
      if jq -e '.metadata.complete == true' "$CACHE_FILE" >/dev/null 2>&1; then
        echo "  $day: reusing cache" >&2
        continue
      fi
    fi

    mkdir -p "$CACHE_DIR"

    # Fetch git log for this day — write to file to avoid null byte issues
    COMMITS_FILE=$(mktemp)
    GIT_STDERR=$(mktemp)
    GIT_EXIT=0
    git -C "$REPO_PATH" log ${LOG_SCOPE[@]+"${LOG_SCOPE[@]}"} \
      --format='%H%x1f%h%x1f%s%x1f%an%x1f%aI%x1f%b%x1e' \
      --since="${day}T00:00:00" --until="${NEXT_DAY}T00:00:00" \
      --no-merges > "$COMMITS_FILE" 2>"$GIT_STDERR" || GIT_EXIT=$?

    if [[ "$GIT_EXIT" -ne 0 ]]; then
      echo "  ERROR: git log failed for $REPO_NAME/$day (exit $GIT_EXIT): $(cat "$GIT_STDERR")" >&2
      rm -f "$COMMITS_FILE" "$GIT_STDERR"
      exit 1
    fi
    rm -f "$GIT_STDERR"

    if [[ ! -s "$COMMITS_FILE" ]]; then
      # No commits (exit 0, empty output) — write empty but complete
      rm -f "$COMMITS_FILE"
      jq -n --arg repo "$REPO_NAME" --arg day "$day" '{
        repo: $repo,
        date: $day,
        commits: [],
        metadata: { complete: true, count: 0 }
      }' > "$CACHE_FILE"
      continue
    fi

    # Parse commits into JSON — let jq handle all string escaping to avoid
    # control character / newline issues that break awk-based JSON serialization
    jq -R -s --arg repo "$REPO_NAME" --arg day "$day" '
      split("\u001e") | map(select(length > 0)) |
      map(split("\u001f")) |
      map(select(length >= 5) | {
        hash: .[0],
        short_hash: .[1],
        subject: .[2],
        author: .[3],
        date: .[4],
        body: (.[5] // "" | gsub("^\\s+|\\s+$"; ""))
      }) |
      {
        repo: $repo,
        date: $day,
        commits: .,
        metadata: { complete: true, count: length }
      }
    ' "$COMMITS_FILE" > "$CACHE_FILE"

    rm -f "$COMMITS_FILE"
    COUNT=$(jq '.metadata.count' "$CACHE_FILE")
    echo "  $day: $COUNT commits" >&2
  done

  echo "$CACHE_BASE/$REPO_NAME${SCOPE:+/$SCOPE}" >&2
done

# Print summary to stdout as JSON
echo '{"status":"complete","cache_base":"'"$CACHE_BASE"'"}'

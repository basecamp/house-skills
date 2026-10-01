---
name: git-activity
description: |
  Fetch git log from local repos into day-cached JSON atoms.
  Simplest activity fetcher — no API calls, no pagination.
  Part of the recap plugin's activity fetcher pipeline.
triggers:
  - /recap:git-activity
  - git activity
  - git log fetch
---

# Git Activity Fetcher

Fetch git commit history from local repositories into per-day cached JSON atoms
at `~/.cache/recap/git/{repo}/{YYYY-MM-DD}/log.json`. These atoms feed the
`/recap` orchestrator for progressive timescale synthesis.

## Invocation

```
/recap:git-activity --repos coworker:~/Work/basecamp/coworker --since 2026-03-23
/recap:git-activity --repos coworker:~/Work/basecamp/coworker,house-skills:~/Work/basecamp/house-skills --since 2026-03-23 --until 2026-03-30
/recap:git-activity --repos coworker:~/Work/basecamp/coworker --since 2026-03-23 --reuse
/recap:git-activity --repos coworker:~/Work/basecamp/coworker --since 2026-03-23 --author "Jeremy Daer" --all-branches
```

## Contract

- **Idempotent:** same input produces same output, safe to re-run
- **Day-cached:** one JSON file per repo per day at `~/.cache/recap/git/{repo}/{YYYY-MM-DD}/log.json`,
  or under a scope directory when `--author` or `--all-branches` narrows or widens the log
- **`--reuse`:** skip fetch if cache exists and is marked complete
- **No API calls:** reads local git repos only

## Quick Run

```bash
# 1. Determine date range
SINCE=$(date -d "7 days ago" +%Y-%m-%d)
UNTIL=$(date +%Y-%m-%d)

# 2. Run the fetcher
"$SKILL_DIR/scripts/git-activity.sh" \
  --repos "coworker:$HOME/Work/basecamp/coworker" \
  --since "$SINCE" --until "$UNTIL"

# 3. Verify cache
ls ~/.cache/recap/git/coworker/
cat ~/.cache/recap/git/coworker/$SINCE/log.json | jq '.metadata'
```

Where `$SKILL_DIR` = directory containing this SKILL.md.

## Output Format

Each `log.json` contains:

```json
{
  "repo": "coworker",
  "date": "2026-03-24",
  "commits": [
    {
      "hash": "abc123...",
      "short_hash": "abc123",
      "subject": "Add weekly digest skill",
      "author": "Jeremy",
      "date": "2026-03-24T10:30:00-05:00",
      "body": "Extended commit message..."
    }
  ],
  "metadata": { "complete": true, "count": 3 }
}
```

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--repos` | Yes | Comma-separated `name:path` pairs |
| `--since` | Yes | Start date (YYYY-MM-DD) |
| `--until` | No | End date (default: today) |
| `--author` | No | Keep only commits whose author matches this pattern (`git log --author`) |
| `--all-branches` | No | Log every local and remote-tracking branch, not just the checked-out one |
| `--reuse` | No | Skip fetch if cache exists and is complete |

By default the fetcher logs the checked-out branch and every author: the repo's
activity. For one person's work, pass `--author` and `--all-branches`, so
coworkers' commits drop out and commits on other branches, pushed or not, come in.

## Cache Structure

```
~/.cache/recap/git/
  coworker/
    2026-03-24/log.json
    2026-03-25/log.json
    all-branches-author-jeremy-daer-189a21ec/
      2026-03-24/log.json
  house-skills/
    2026-03-24/log.json
```

A filtered fetch caches under its own scope directory, named from the filters, so
`--reuse` never hands a filtered run the unfiltered day or the other way round.
The name ends in a digest of the exact `--author` pattern: `Jeff` and `^Jeff$`
match different commits, so they must not share a directory.

Day-sized atoms enable progressive aggregation: a weekly digest reads 7 daily
atoms. A monthly digest reads ~30. Different timescales, same data.

## Failure Modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| "not a git repo" | Path doesn't point to repo | Check `--repos` paths |
| Empty commits for a day | No commits on that date | Expected — empty days are cached as complete |
| awk/jq parse error | Unusual characters in commit messages | Falls back to simpler format automatically |

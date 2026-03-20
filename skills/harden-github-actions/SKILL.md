---
name: zizmor-resolution
description: >
  Use when resolving zizmor warnings in GitHub Actions workflows, hardening CI
  pipelines, or pinning actions to SHA hashes. Covers artipacked, template-injection,
  excessive-permissions, secrets-outside-env, dependabot-execution, and when to
  suppress vs fix.
---

# Resolving Zizmor Warnings in GitHub Actions

## Overview

zizmor identifies security vulnerabilities in GitHub Actions workflows. This skill documents
the decision guidelines for resolving each warning type: when to fix, how to fix, and when
to suppress with an inline comment explaining why.

**Core principle:** Fix the vulnerability whenever possible. Suppress only when the fix would
break required functionality, and always include a reason in the suppression comment.

## Prerequisites

This work should be done on a branch in a git worktree. Before starting any work, verify
you are in the worktree directory and on the correct branch:

```bash
pwd          # should be the worktree path
git branch   # should show the feature branch, not main
```

## Workflow Order

Always work in this order. Each step is a separate commit.

1. **Pin actions** with `pinact run`
2. **Address zizmor warnings** by severity (high → medium → low → informational).
3. **Run actionlint** and fix any findings. Commit.
4. **Add zizmor CI job** using the standard template
5. **Configure dependabot** to batch github-actions updates weekly

## Running pinact

Run `pinact run --min-age 10` from the repository root. This pins all actions in `.github/workflows/` to SHA hashes, skipping any versions published less than 10 days ago.

## Running zizmor

**Always run zizmor with a GitHub token** so that online audits (like `ref-version-mismatch`
and `impostor-commit`) can resolve SHAs against the GitHub API. Without a token, these audits
are silently skipped and findings will only surface in CI.

```bash
GITHUB_TOKEN=$(gh auth token) zizmor .
```

Filter severity by passing the flag `--min-severity=<level>` where level can be `high`, `medium`, or `low`. Informational warnings may be emitted by omitting this flag entirely.

### Auto-fix workflow

For each severity level (high, then medium, then low, then informational):

1. Run `zizmor --fix=all --min-severity=<level> .` to auto-correct fixable findings (`--fix` alone uses safe mode which silently holds back some fixes; use `--fix=all` and rely on diff review as the safety net)
2. **STOP and review the diff.** Check each auto-fix against the Decision Guide below.
   - `cache-poisoning` fixes will disable caching — almost always revert these and suppress instead
   - `artipacked` fixes add `persist-credentials: false` — revert if the workflow needs `git push`
   - `superfluous-actions` fixes replace actions with inline code — always revert these and suppress instead
   - `bot-conditions` auto-fix replaces `github.actor` with `user.login` — revert and apply the dual check instead (see rule file)
   - `template-injection` fixes are generally correct
3. Revert any incorrect fixes
4. For reverted fixes, apply the correct resolution manually (e.g., suppress with a reason)
5. Manually fix anything `--fix` didn't handle. **For `excessive-permissions`: you MUST research each action's permissions. Do not guess. See the permission research process below.**
6. Run `zizmor --min-severity=<level> .` to verify a clean check at this severity level
7. Commit

After completing all default severity levels, run a pedantic pass:

1. Run `zizmor --persona=pedantic --min-severity=high .`
2. Address findings the same way as above — the most common pedantic finding is
   `excessive-permissions` on single-job workflows where zizmor's default persona doesn't
   flag it. Apply the same fix: `permissions: {}` at workflow level, scoped per job.
3. Run `zizmor --persona=pedantic --min-severity=high .` to verify clean
4. Commit

## Decision Guide by Rule

When you encounter a zizmor finding, read the corresponding rule file in `references/` for
full decision guidance, suppression checklists, and examples. Only read the rules you need.

| Rule | File | Action |
|------|------|--------|
| `artipacked` | `references/rule-artipacked.md` | Fix (add `persist-credentials: false`); suppress only if job does `git push` |
| `template-injection` | `references/rule-template-injection.md` | Always fix (move expressions to `env:` vars) |
| `excessive-permissions` | `references/rule-excessive-permissions.md` | Always fix (set `permissions: {}` at workflow level, scope per job) |
| `dangerous-triggers` | `references/rule-dangerous-triggers.md` | Fix or suppress with 5-point checklist |
| `secrets-outside-env` | `references/rule-secrets-outside-env.md` | Fix (add `environment:`) or suppress with 3-point checklist |
| `bot-conditions` | `references/rule-bot-conditions.md` | Always fix (dual check: `actor` + `user.login`); revert auto-fix |
| `superfluous-actions` | `references/rule-superfluous-actions.md` | Always suppress (never replace with inline code) |
| `cache-poisoning` | `references/rule-cache-poisoning.md` | Suppress (default); revert auto-fixes; only escalate if custom cache keys |
| `unpinned-images` | `references/rule-unpinned-images.md` | Suppress (default); digest pinning is nontrivial |
| `dependabot-execution` | `references/rule-dependabot-execution.md` | Fix or suppress with 3-point checklist |
| `dependabot-cooldown` | `references/rule-dependabot-cooldown.md` | Always fix (add `cooldown: default-days: 10` to all ecosystems) |

Permission mappings for `excessive-permissions` are in `references/permission-mappings.md`.

For findings not covered in this skill, consult https://docs.zizmor.sh/audits/ for detailed explanations and resolution guidance.


## Suppression Format

Always use inline comments with the rule name and a reason:

```
# zizmor: ignore[rule-name] -- reason why suppression is necessary
```

The `--` separator before the reason is a convention for readability. Never suppress without
a reason. If you can't articulate why the fix would break something, apply the fix instead.

## Standard Zizmor CI Job

Add this job to the repository's main CI workflow file (often `ci.yml` or `ci-checks.yml`),
placed near existing lint jobs (e.g., rubocop, eslint) since it's a linting concern:

```yaml
lint-actions:
  name: GitHub Actions audit
  runs-on: ubuntu-latest

  steps:
    - uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
      with:
        persist-credentials: false

    - name: Run actionlint
      uses: rhysd/actionlint@393031adb9afb225ee52ae2ccd7a5af5525e03e8 # v1.7.11

    - name: Run zizmor
      uses: zizmorcore/zizmor-action@71321a20a9ded102f6e9ce5718a2fcec2c4f70d8 # v0.5.2
      with:
        advanced-security: false
```

Note: pin the checkout and zizmor-action SHAs.

**Before adding this job, check if the workflow already has a standalone `actionlint` job.**
If it does, remove it — `lint-actions` replaces it. Do not create duplicate actionlint runs.

## Dependabot Configuration

### GitHub Actions entry

Ensure `.github/dependabot.yml` includes a github-actions entry with batching.
The schedule **must** be `weekly` — not daily.

```yaml
- package-ecosystem: github-actions
  directory: "/"
  groups:
    github-actions:
      patterns:
        - "*"
  schedule:
    interval: weekly
  cooldown:
    default-days: 10
```

The `groups` block batches all action updates into a single PR instead of one PR per action.

### Cooldown on all ecosystems

Add `cooldown: default-days: 10` to **every** ecosystem entry in `dependabot.yml`, not just
github-actions. This gives a 10-day waiting period after any version is published before
dependabot proposes it, avoiding churn from rapid post-release patches. If an ecosystem
entry is missing the cooldown block, add it.

## Common Mistakes

| Mistake | Correction |
|---------|------------|
| Guessing what permissions an action needs | **Read the action's README.** If it's not in the permission mappings table, research it before proceeding. |
| Accepting `cache-poisoning` auto-fixes without review | `--fix=all` disables caching; almost always revert and suppress instead |
| Suppressing without a reason | Always explain WHY the fix can't be applied |
| Suppressing `template-injection` | This should always be fixed, never suppressed |
| Adding `persist-credentials: false` to a workflow that does `git push` | Suppress `artipacked` with a comment instead |
| Fixing permissions by removing the block entirely | Move to job-level, don't remove — implicit permissions may be too broad |
| Using `--fix` instead of `--fix=all` | Safe mode silently holds back fixes; use `--fix=all` and review the diff |
| Committing without verifying clean zizmor output | Always re-run `zizmor --min-severity=<level> .` before committing |
| Analyzing all findings up front before starting work | Follow the workflow order step by step — pin, then fix by severity, then CI job, then dependabot |
| Adding the zizmor CI job at the end of the workflow file | Place it near existing lint jobs — it's a linting concern, not a test |
| Replacing an action with inline code for `superfluous-actions` | Always suppress — actions are more maintainable and receive upstream fixes |
| Not specifying permissions on reusable workflow caller jobs | Caller jobs must declare permissions; reusable workflows inherit from the caller |
| Running commands in the main repo instead of the worktree | Verify `pwd` and `git branch` before starting |

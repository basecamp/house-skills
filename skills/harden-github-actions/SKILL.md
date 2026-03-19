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
3. **Add zizmor CI job** using the standard template
4. **Configure dependabot** to batch github-actions updates weekly

## Running pinact

Run `pinact run --min-age 10` from the repository root. This pins all actions in `.github/workflows/` to SHA hashes, skipping any versions published less than 10 days ago.

## Running zizmor

Run zizmor against the entire project directory by running `zizmor <directory>`, e.g. `zizmor ~/Work/basecamp/fizzy`

Filter severity by passing the flag `--min-severity=<level>` where level can be `high`, `medium`, or `low`. Informational warnings may be emitted by omitting this flag entirely.

### Auto-fix workflow

For each severity level (high, then medium, then low, then informational):

1. Run `zizmor --fix=all --min-severity=<level> .` to auto-correct fixable findings (`--fix` alone uses safe mode which silently holds back some fixes; use `--fix=all` and rely on diff review as the safety net)
2. **STOP and review the diff.** Check each auto-fix against the Decision Guide below.
   - `cache-poisoning` fixes will disable caching — almost always revert these and suppress instead
   - `artipacked` fixes add `persist-credentials: false` — revert if the workflow needs `git push`
   - `superfluous-actions` fixes replace actions with inline code — always revert these and suppress instead
   - `bot-conditions` and `template-injection` fixes are generally correct
3. Revert any incorrect fixes
4. For reverted fixes, apply the correct resolution manually (e.g., suppress with a reason)
5. Manually fix anything `--fix` didn't handle. **For `excessive-permissions`: you MUST research each action's permissions. Do not guess. See the permission research process below.**
6. Run `zizmor --min-severity=<level> .` to verify a clean check at this severity level
7. Commit

### Reference

For findings not covered in this skill, consult https://docs.zizmor.sh/audits/ for detailed explanations and resolution guidance.

## Decision Guide by Rule

### `artipacked` — Credential Persistence After Checkout

`actions/checkout` persists git credentials by default, which later steps can exfiltrate.

**Fix (default):** Add `persist-credentials: false` to every `actions/checkout` step.

```yaml
- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
  with:
    persist-credentials: false
```

**Suppress only if ALL of these are true:**
1. A later step in the SAME job runs `git push`, `git tag`, or similar write operation
2. The push target is the same repository (not a fork or external repo requiring a PAT)
3. No other authentication mechanism (e.g., deploy key, PAT) is used for the push

If you cannot confirm all three, apply the fix. If fixing would break the workflow and you
cannot confirm all three, **stop and report the finding — do not suppress.**

```yaml
- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2 # zizmor: ignore[artipacked] -- credentials needed for git push
```

### `template-injection` — Expression Injection in `run:` Blocks

Using `${{ }}` directly in `run:` scripts allows attackers to inject shell commands via
crafted PR titles, branch names, etc.

**Fix (always):** Pass expressions through `env:` vars. Never use `${{ }}` directly in `run:`.

```yaml
# BEFORE (vulnerable)
- run: |
    tags="${{ steps.meta.outputs.tags }}"
    echo "$tags"

# AFTER (safe)
- env:
    TAGS: ${{ steps.meta.outputs.tags }}
  run: |
    tags="$TAGS"
    echo "$tags"
```

This applies to ALL expressions in `run:` blocks, including `github.event.pull_request.title`,
`steps.*.outputs.*`, `inputs.*`, etc. There is no valid reason to suppress this rule.

### `excessive-permissions` — Overly Broad Permissions

Top-level `permissions:` grants those permissions to ALL jobs, violating least privilege.

**Fix (always):** Replace the workflow-level `permissions:` block with `permissions: {}` (deny
all) and move permissions down to each individual job. The `permissions: {}` ensures every job
starts with zero permissions, so any job that forgets to declare its own permissions will fail
safe rather than inheriting broad defaults.

```yaml
# BEFORE (too broad)
permissions:
  contents: read
  packages: write
  id-token: write
  attestations: write

jobs:
  build:
    ...
  merge:
    ...

# AFTER (deny-all at workflow level, scoped per job)
permissions: {}

jobs:
  build:
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
    ...
  merge:
    permissions:
      packages: write
      id-token: write
    ...
```

**How to determine which permissions each job needs — DO NOT GUESS:**

1. Start from the existing top-level `permissions:` block — that's the universe of permissions to distribute
2. For each job, identify which actions it uses
3. **For each action, check the permission mappings table below. If the action is not in the table, you MUST fetch its README on GitHub and read the documented permission requirements before proceeding.**
4. Assign only the permissions each job actually needs
5. Don't invent permissions that weren't in the original block

Permission mappings are in `references/permission-mappings.md`. **Check that file first.** If the action
is not listed there, you MUST fetch its README on GitHub and read the documented permission
requirements before proceeding, then add it to the reference file.

**Reusable workflows (`uses: ./.github/workflows/...`):** Caller jobs that invoke reusable
workflows must specify permissions explicitly. Reusable workflows inherit the caller job's
permissions, and permissions can only be maintained or reduced through the chain — never
elevated. When you set `permissions: {}` at the workflow level, every job (including reusable
workflow calls) starts with zero permissions and must declare what it needs.

### `dependabot-execution` — Insecure External Code Execution

Dependabot's `insecure-external-code-execution: allow` lets package managers run arbitrary
code during dependency resolution.

**Fix (if possible):** Remove `insecure-external-code-execution: allow`.

**Suppress only if ALL of these are true:**
1. The package ecosystem requires code execution to resolve dependencies (e.g., Bundler with
   private gem sources that use custom `source` blocks in the Gemfile)
2. You have confirmed that removing `allow` causes dependency resolution to fail
3. The private registry is trusted (e.g., an internal GitHub Packages registry)

If you cannot confirm all three, apply the fix. If fixing would break dependency resolution
and you cannot confirm all three, **stop and report the finding — do not suppress.**

```yaml
insecure-external-code-execution: allow # zizmor: ignore[dependabot-execution] -- required for Bundler to resolve gems from the private github-basecamp registry
```

### `bot-conditions` — Spoofable Bot Actor Check

Using `github.actor == 'dependabot[bot]'` in job conditions is spoofable.

**Fix (always):** Use `github.event.pull_request.user.login` instead, which is verified by
GitHub and cannot be spoofed. The `--fix=all` auto-fix handles this correctly.

```yaml
# BEFORE (spoofable)
if: github.actor == 'dependabot[bot]'

# AFTER (verified)
if: github.event.pull_request.user.login == 'dependabot[bot]'
```

### `dangerous-triggers` — Dangerous Workflow Triggers

zizmor flags `pull_request_target` and `workflow_run` triggers. Both execute in the target
repository's context (with write permissions and access to secrets) while remaining
triggerable by external forks. Even workflows that don't explicitly check out PR code can
be vulnerable to indirect execution vectors (argument injection, environment injection via
`LD_PRELOAD`, `GITHUB_ENV` manipulation, etc.).

**Fix (preferred):**
- Replace `workflow_run` with `workflow_call` (convert to a reusable workflow)
- Replace `pull_request_target` with `pull_request` unless write permissions are required
- If `pull_request_target` is necessary, never check out or run PR-controlled code

**Suppress only if ALL of these are true:**
1. The workflow requires write access to the PR (e.g., labeling, commenting, gating) or
   needs to run after another workflow completes
2. The workflow does NOT check out the PR's head ref (`actions/checkout` with `ref:
   ${{ github.event.pull_request.head.sha }}` or similar)
3. The workflow does NOT run any code from the PR (no `run:` steps that execute checked-out
   files, no build/test steps)
4. The workflow does NOT pass attacker-controllable values into `GITHUB_ENV`, `GITHUB_PATH`,
   or `GITHUB_OUTPUT`
5. For `workflow_run`: you have confirmed it cannot be replaced with `workflow_call`

If you cannot confirm all applicable criteria, do NOT suppress. If fixing would break the
workflow and you cannot confirm all criteria, **stop and report the finding — do not
suppress or fix.**

```yaml
on:
  pull_request_target: # zizmor: ignore[dangerous-triggers] -- required for write access to PRs from forks; workflow only runs trusted actions, no PR code is checked out or executed
```

### `secrets-outside-env` — Secrets Used Without GitHub Environment

zizmor flags jobs that use `secrets.*` (other than `secrets.GITHUB_TOKEN`) without an
`environment:` declaration. Org/repo-level secrets are available to any workflow run,
including from forks. Environment-scoped secrets are only available to jobs targeting that
environment, providing an additional access control layer.

**Fix (preferred):** Add an `environment:` declaration to the job and move the secrets into
that environment's secret store (removing them from repo/org-wide secrets).

```yaml
# BEFORE (flagged)
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - run: ./deploy.sh
        env:
          API_KEY: ${{ secrets.API_KEY }}

# AFTER (fixed)
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - run: ./deploy.sh
        env:
          API_KEY: ${{ secrets.API_KEY }}
```

**Suppress only if ALL of these are true:**
1. The job genuinely needs a non-GITHUB_TOKEN secret (not just a misconfiguration)
2. Adding an `environment:` declaration would break the workflow (e.g., environment
   protection rules would block PR-triggered runs that need the secret)
3. The secret is not exposed to untrusted code (e.g., not passed to a step that runs
   PR-submitted code)

If you cannot confirm all three, apply the fix. If the situation is ambiguous, **stop and
report the finding — do not suppress or fix.**

```yaml
jobs:
  test:
    runs-on: ubuntu-latest # zizmor: ignore[secrets-outside-env] -- API key needed in PR test runs; environment protection would block CI
```

### `superfluous-actions` — Action Can Be Replaced With Inline Code

zizmor suggests replacing certain actions with equivalent inline shell commands.

**Suppress (always):** Never replace an existing action with inline code. Actions are more
readable, maintainable, and receive upstream security fixes automatically. Suppress with a
note to consider removal in the future.

```yaml
- uses: softprops/action-gh-release@... # zizmor: ignore[superfluous-actions] -- consider removal
```

### `cache-poisoning` — Cache Poisoning in Release Workflows

zizmor warns when setup actions (e.g., `setup-go`, `setup-ruby`, `setup-node`) enable caching
in workflows triggered by tags or `workflow_dispatch`, since a poisoned cache from a PR could
theoretically affect the release build.

**WARNING:** `--fix=all` will disable caching for these findings. Almost always revert these
auto-fixes and suppress instead.

**Suppress only if ALL of these are true:**
1. The cached dependencies are used for testing or linting, NOT for building the published
   release artifact
2. The release artifact is built from a clean install or a separate non-cached step
3. The workflow's cache is not shared with workflows triggered by `pull_request` from forks
   (GitHub isolates caches by branch, but verify if the workflow uses custom cache keys)

If you cannot confirm all three, disable caching for that step. If the situation is
ambiguous, **stop and report the finding — do not suppress or fix.**

```yaml
- uses: ruby/setup-ruby@... # zizmor: ignore[cache-poisoning] -- cached deps are for testing, not release artifact generation
```

### `dependabot-cooldown` — Missing Cooldown on Dependabot Ecosystems

zizmor warns when dependabot ecosystem entries lack a cooldown period.

**Fix (always):** Add `cooldown: default-days: 10` to every ecosystem entry in
`.github/dependabot.yml`, not just `github-actions`. This gives a 10-day waiting period
after any version is published before dependabot proposes it, avoiding churn from rapid
post-release patches.

```yaml
cooldown:
  default-days: 10
```

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

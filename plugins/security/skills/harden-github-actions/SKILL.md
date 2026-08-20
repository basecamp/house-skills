---
name: harden-github-actions
description: >
  Use when resolving zizmor warnings in GitHub Actions workflows, hardening CI
  pipelines, or pinning actions to SHA hashes. Covers artipacked, template-injection,
  excessive-permissions, secrets-outside-env, dependabot-execution, and when to
  suppress vs fix. Also covers scheduling vulnerability scanners (govulncheck,
  bundler-audit, npm audit, Trivy) and maintaining pinned toolchain versions.
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

This ordering governs a **zizmor-hardening pass**. If you are here only to schedule a
vulnerability scanner or to sort out a pinned toolchain, go straight to
"Advisory scanning: put it on a clock, not on the diff" — those tasks stand on their own and
do not require a repository-wide hardening pass first.

Always work in this order. Each step is a separate commit.

1. **Add zizmor CI job** using the standard template
2. **Configure dependabot** to batch github-actions updates weekly
3. **Add local workflow linting** to `bin/setup` and `bin/ci` (see below). Skip if these
   scripts don't exist in the project.
4. **Pin actions** with `pinact run`
5. **Address zizmor warnings** by severity (high → medium → low → informational).
6. **Ensure all permissions are job-level.** Check every workflow file for top-level
   `permissions:` blocks. Replace with `permissions: {}` and add per-job permissions.
   This goes beyond what zizmor flags — zizmor misses single-job workflows. Commit.
7. **Run actionlint** and fix any findings. Commit.

## Running pinact

Run `pinact run --min-age 7` from the repository root. This pins all actions in `.github/workflows/` to SHA hashes, skipping any versions published less than 7 days ago.

**This value must match `default-days` in `dependabot.yml`** — otherwise dependabot will propose updates that pinact refuses to pin, or pinact will pin versions dependabot has not yet cleared.

**Do not re-run `pinact run` after adding inline suppressions.** pinact requires the
`# vX.Y.Z` version comment to sit immediately after the SHA. When a `zizmor: ignore` comment
sits between the SHA and the version comment (the placement dependabot requires, see
[Suppression Format](#suppression-format)), a re-run rewrites the line and silently deletes
the suppression. This is safe in practice because pinact runs only during this one-time
hardening pass: it is not added to CI, `bin/ci`, or `bin/setup`, and dependabot handles later
version bumps by updating the SHA and the trailing `# vX.Y.Z` while preserving the comment in
between. So pin first (this step), then add suppressions while addressing findings, and do not
pin again. If you must re-pin later, re-add the suppressions afterward and verify with
`zizmor`.

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
| `dependabot-cooldown` | `references/rule-dependabot-cooldown.md` | Always fix (add `cooldown: default-days: 7` to all ecosystems) |

Permission mappings for `excessive-permissions` are in `references/permission-mappings.md`.

For findings not covered in this skill, consult https://docs.zizmor.sh/audits/ for detailed explanations and resolution guidance.


## Suppression Format

Always use inline comments with the rule name and a reason:

```
# zizmor: ignore[rule-name] -- reason why suppression is necessary
```

The `--` separator before the reason is a convention for readability. Never suppress without
a reason. If you can't articulate why the fix would break something, apply the fix instead.

### Suppressing on a pinned action

When the suppression lands on a `uses:` line that pinact has pinned, the `zizmor: ignore`
comment goes **between the SHA and the version-pin comment**, leaving `# vX.Y.Z` last:

```yaml
uses: ruby/setup-ruby@<sha> # zizmor: ignore[cache-poisoning] -- reason # v1.302.0
```

Dependabot reads the trailing `# vX.Y.Z` comment to track the pinned version, so it must be
the last comment on the line. Do not put the version comment first (`# v1.302.0 # zizmor:
...`): pinact tolerates that order, but dependabot will no longer recognize the pin and will
stop proposing updates for that action. This ordering interacts with re-running pinact, so
read the warning under "Running pinact".

## Standard Zizmor CI Job

Add this job to the repository's main CI workflow file (often `ci.yml` or `ci-checks.yml`).

**Placement matters.** Before inserting, find the existing lint job (rubocop, eslint,
golangci-lint, etc.) in the workflow and place `lint-actions` immediately after it. If there
is no lint job, place it immediately before the first test job. **Never append it to the end
of the file** — it is a linting concern, not a test or deployment step:

```yaml
lint-actions:
  name: GitHub Actions audit
  runs-on: ubuntu-latest

  steps:
    - uses: actions/checkout@v6
      with:
        persist-credentials: false

    - name: Run actionlint
      uses: rhysd/actionlint@v1.7.11

    - name: Run zizmor
      uses: zizmorcore/zizmor-action@v0.6.2
      with:
        advanced-security: false
```

Use version tags, not SHA hashes — run `pinact run --min-age 7` immediately after adding
this job to pin them. This ensures the SHAs match what pinact produces for the rest of the
workflow.

**Before adding this job, check if the workflow already has a standalone `actionlint` job.**
If it does, remove it — `lint-actions` replaces it. Do not create duplicate actionlint runs.

## Local Workflow Linting

If the project has a `bin/ci` script (or equivalent like `config/ci.rb`), add workflow
linting so developers catch issues locally before pushing. If `bin/setup` also exists, add
tool installation there too. **Skip this section entirely if there is no local CI script.**

### bin/setup — tool installation

Check if `actionlint`, `shellcheck`, and `zizmor` are already installed. If not, install
them using the platform's package manager. Read the existing `bin/setup` script to understand
its conventions before adding to it.

**shellcheck is required** — actionlint uses it to lint shell scripts in `run:` blocks.
Without shellcheck, actionlint silently skips script checks and local results won't match CI.

Install all three tools using the same pattern:

```bash
for tool in actionlint shellcheck zizmor; do
  if ! command -v "$tool" &> /dev/null; then
    if command -v brew &> /dev/null; then
      brew install "$tool"
    elif command -v pacman &> /dev/null; then
      sudo pacman -S --noconfirm "$tool"
    else
      echo "Error: install $tool manually" >&2
      exit 1
    fi
  fi
done
```

Adapt this to match the script's existing style (e.g., if it uses functions, conditionals,
or a different error pattern, follow that convention).

### bin/ci — running the linters

Add actionlint and zizmor as separate steps. Read the existing `bin/ci` script to understand
its conventions before adding to it.

```bash
# Lint GitHub Actions workflows
actionlint
zizmor .
```

Each tool should be a separate command so failures are clearly attributable. Place these
near other linting steps if the script has them.

### Examples

- **bin/setup + config/ci.rb**: [lexxy#882](https://github.com/basecamp/lexxy/pull/882)
- **Makefile**: [basecamp-sdk@aa1f2d50](https://github.com/basecamp/basecamp-sdk/commit/aa1f2d50)

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
    default-days: 7
```

The `groups` block batches all action updates into a single PR instead of one PR per action.

### Cooldown on all ecosystems

Add cooldown to **every** ecosystem entry in `dependabot.yml`. Use semver-granular cooldowns
for real package ecosystems so low-risk patches flow faster while major bumps get more soak
time:

```yaml
# For package ecosystems (bundler, npm, gomod, gradle, pip, etc.)
cooldown:
  semver-major-days: 7
  semver-minor-days: 3
  semver-patch-days: 2
  default-days: 7

# For github-actions (semver-granular keys are NOT supported)
cooldown:
  default-days: 7
```

If an ecosystem entry is missing the cooldown block, add it. If a cooldown block is already
there with different values, **leave it alone.** The values above are a starting default for
a repo that has none — not a fleet standard to converge on.

The five main apps (bc3, haystack, launchpad, queenbee, fizzy) all deliberately run a longer
soak on majors:

```yaml
cooldown:
  default-days: 7
  semver-major-days: 14
```

Overriding that with the block above would *shorten* their major-bump soak from 14 days to 7,
which is backwards. A duration that merely differs from the block above is a deliberate choice,
not drift — leave it.

**One exception: fix a block that buys no cooldown at all.** The semver-granular keys are, per
GitHub's own option reference, *"supported only where indicated"* — ignored everywhere else.
These ecosystems honour **`default-days` only**:

> Bazel, Devcontainers, Docker, Docker Compose, GitHub Actions, Gitsubmodule, Helm, Nix flakes,
> OpenTofu, pre-commit, Terraform, vcpkg

So on one of those, a block with semver keys and **no `default-days`** has every key ignored and
no cooldown applied — add `default-days`. Semver keys sitting *alongside* a `default-days` there
are merely inert; leave them rather than churn the diff. On the semver-capable ecosystems
(bundler, npm, gomod, gradle, pip, cargo, maven, nuget, …) all four keys work, so any existing
combination stands as written.

## Advisory scanning: put it on a clock, not on the diff

Vulnerability scanners — `govulncheck`, `bundler-audit`, `npm audit`, Trivy — differ from
linters in a way that decides where they belong. A linter fires on what the diff changed. A
scanner fires on what someone *published*, against code that hasn't changed at all.

So the **full** scan belongs on a schedule, not on every PR. A whole-tree scanner wired as a
required check turns every newly published CVE into a red build on unrelated work: the author
of the blocked PR didn't cause it and usually can't fix it in that branch. `basecamp/cli`
demonstrated this exactly — its govulncheck job passed at 09:24 and failed an hour later on
the identical commit, blocking an unrelated one-character PR.

That is an argument about *which* findings gate, not against pre-merge scanning. A PR can
genuinely introduce a vulnerability — adding a dependency with an already-published advisory,
or making a vulnerable path newly reachable — and a schedule-only setup lets that merge and
ship until the next cron. So gate a PR on findings the PR *introduced*, by diffing against a
baseline from the target branch. Where the tool can't express that (govulncheck has no baseline
mode), run it on PRs as a non-blocking informational job and keep the scheduled run as the
enforcing one.

**Give a scheduled run somewhere to report.** A cron job that only goes red in the Actions tab
recreates the silence it was added to end; GitHub's failure mail for scheduled workflows goes
to whoever last edited the cron, which is not a team signal. Have the job report into a single
tracking issue.

Keep **one** issue, but write every run into it — comment when it's open, create when it isn't.
Do not skip reporting just because an issue exists: that issue's body is a snapshot of the run
that filed it, so an issue open for advisory A will silently swallow advisory B. Read-then-write
on an issue is not atomic either, so give the workflow a `concurrency` group or a manual
dispatch overlapping the cron can file two.

The reporting job needs `issues: write` on top of `contents: read`. Under the `permissions: {}`
default this section already requires, a job without it fails to file anything — the scanner
then breaks exactly as silently as having no scanner. Treat scanner output as untrusted text
when you report it: advisory summaries and package names come from outside the repo, so write
them to a file and pass `--body-file` rather than interpolating them into a shell command.

**Refresh the advisory database in the same job.** A scan is only as fresh as the data it
reads, and several tools keep that data locally: `bundle-audit check` reads a cached
`ruby-advisory-db` unless you pass `--update` (or run `bundle-audit update` first). Put the
scan on a clock without this and a cached or self-hosted runner keeps passing against last
month's advisories — the failure is invisible, because a stale database looks exactly like
good news. `govulncheck` queries `vuln.go.dev` at run time and needs nothing extra.

**Distinguish "found something" from "could not run."** Scanners use distinct exit codes —
`govulncheck` returns 3 for findings and other nonzero codes for failing to run at all. Collapse
them and a transient module-proxy error becomes an issue announcing vulnerabilities, which then
suppresses the next real finding. Report both, worded for what actually happened, and report
failures that happen *before* the scan too.

**Carry known-unfixable findings in an explicit list, not by disabling the job.** Where a
finding has no available fix, a bare scanner is red forever and gets ignored. Name the accepted
IDs, write down next to them why each is accepted and what would change that, and fail on
everything else. The list keeps the run quiet only for the findings someone actually looked at.

An advisory ID does not change when a fix ships, so an ID-only list keeps suppressing a finding
at the exact moment it becomes fixable. Accept an ID only for as long as it has no fix, say so
beside the entry, and re-check the list whenever you bump the dependency it belongs to.

### Pinned tool versions have no auto-bumper

If CI pins a toolchain or a downloaded binary, nothing in Dependabot will move it:

- Dependabot does **not** update Go's `go`/`toolchain` directive
  ([dependabot-core#13520](https://github.com/dependabot/dependabot-core/issues/13520), open
  since 2025-11-11), and GitHub raises no security alert for a vulnerable toolchain version in
  `go.mod` either. Neither the version-update nor the security-update path covers it.
- The same applies to a checksum-verified release binary (see `references/rule-unpinned-images.md`)
  — version and checksum are bumped by hand.

Which leaves a choice worth making deliberately:

| | reproducible | self-healing |
|---|---|---|
| exact patch (`go 1.26.7`, read via `go-version-file`) | yes | no — needs a manual bump |
| `stable`, or a range plus `check-latest: true` | no — floats | yes |
| bare minor (`1.26`), no `check-latest` | no | not reliably |

Three things that table hides, all worth knowing before picking a row:

- **The third row is a trap rather than a middle ground.** `setup-go` with `go-version: 1.26`
  and no `check-latest` satisfies the range from the runner image's tool cache. It isn't pinned
  forever — the patch moves when the image is rebuilt — but it moves on GitHub's schedule, not
  yours, so a published advisory can sit unfixed for as long as the image lags.
- **`check-latest` re-resolves the version *spec*.** Against an exact version it is a no-op, so
  it cannot rescue row one. It is also redundant with `stable`: `setup-go` resolves that alias
  from the release manifest into a concrete version *before* the `check-latest` branch runs, so
  `stable` already floats on its own.
- **Go's `go` directive is a minimum, not a pin.** `setup-go` reading it via `go-version-file`
  installs exactly that version, which is what makes row one exact *in CI*. Elsewhere — a
  `FROM golang:X` image build, a developer machine — `GOTOOLCHAIN=auto` will happily select a
  newer toolchain, and will download the required one if the local toolchain is older. So a
  `go.mod` bump does reach a Docker build that has network access, but don't rely on that
  implicitly: bump `FROM golang:` and any nested module's `go.mod` too, or say why you didn't.

Either of the first two rows is defensible. Pick one, and pair it with something that tells you
the pin has gone stale.

A scanner is part of that, but only part, and the gap is the same in both directions:

- For a **toolchain** pin, `govulncheck` reports the standard library of whatever toolchain
  built the code — so it catches a *reachable* stdlib advisory. It is not a release monitor: a
  newer patch with nothing reachable in it, or a fix in the compiler or `cmd/go` rather than in
  the analysed package graph, leaves the scan green while the pin ages. Watch Go's release and
  security announcements as well.
- For a pinned **tool binary** — the checksum-verified actionlint of
  `references/rule-unpinned-images.md` — no project dependency scanner looks at it at all: it
  inspects your dependencies, not your CI's own tooling.

So pair an exact pin with a scanner *and* a release watch. The scanner tells you the pin is
dangerous; only the release watch tells you it is merely stale.

Two limits of `schedule` worth designing around:

- Scheduled workflows run **only on the default branch**, so a release branch or a deployed SHA
  that differs from it is not scanned. If you ship from something other than the default branch,
  check that ref out explicitly.
- In a public repository, GitHub **disables scheduled workflows after 60 days with no repository
  activity** — precisely the dormant repo where a scanner was the only thing still looking.

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
| Analyzing all findings up front before starting work | Follow the workflow order step by step — CI job, dependabot, local linting, pin, then fix by severity |
| Adding the zizmor CI job at the end of the workflow file | Place it near existing lint jobs — it's a linting concern, not a test |
| Replacing an action with inline code for `superfluous-actions` | Always suppress — actions are more maintainable and receive upstream fixes |
| Not specifying permissions on reusable workflow caller jobs | Caller jobs must declare permissions; reusable workflows inherit from the caller |
| Adding tools to bin/setup when there's no bin/ci | Only add local linting if a local CI script exists to run the tools |
| Making a whole-tree vulnerability scanner a required PR check | Schedule the full scan. Gate a PR only on findings it introduced, or run it informational |
| Skipping the scanner report because an issue is already open | That issue is a snapshot of an older run; a new advisory gets swallowed. Comment every run into it |
| Treating any nonzero scanner exit as "vulnerabilities found" | `govulncheck` uses 3 for findings; other codes mean it could not run. A wrong issue suppresses the next real one |
| Assuming Dependabot maintains a pinned toolchain | It does not for Go's `go`/`toolchain` directive, and there is no security alert for it either |
| Running commands in the main repo instead of the worktree | Verify `pwd` and `git branch` before starting |

## Common PR Feedback (Incorrect or Misleading)

Automated reviewers (Copilot, cubic, etc.) frequently flag these. They are wrong or
misleading — dismiss them.

| Feedback | Why it's wrong |
|----------|---------------|
| `ruby/setup-ruby` with `bundler-cache: true` needs `actions: write` | No. The cache goes through `@actions/cache`, which uses runner-injected credentials (`ACTIONS_RUNTIME_TOKEN`), not GITHUB_TOKEN — `actions: write` governs the cache *management* REST API, which the action never calls. Do not add it. |
| `persist-credentials: false` will break `git fetch` / `git worktree` | Only true for private repos. All our target repos are public — unauthenticated HTTPS fetch works fine. |
| `cooldown` is not a valid Dependabot configuration key | It is valid. GitHub added `cooldown` to Dependabot v2 config in late 2025. Copilot's training data predates this feature. |
| Checkout version inconsistency (v3 in existing jobs vs v6 in lint-actions) | The skill pins existing versions as-is; upgrading is dependabot's job after merge. The lint-actions job template uses v6 independently. |

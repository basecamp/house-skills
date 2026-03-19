# `dangerous-triggers` — Dangerous Workflow Triggers

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

---
name: ralph-lisa-loop
description: |
  Explicitly launch the automated Ralph-Lisa plan-implement-review workflow. Use only
  when the current request directly asks to start or run Ralph-Lisa; a mention or
  discussion of the skill is not an invocation. Never infer it from ordinary planning,
  building, implementation, or review requests.
---

# ralph-lisa-loop

## Activation gate

This is a heavyweight, explicit-opt-in workflow. Apply this gate before preflight,
reference loading, hook inspection, session creation, or subagent dispatch.

Activate only when the current user request directly asks to start Ralph-Lisa, or uses
the skill's canonical host invocation: `$ralph-lisa-loop` in Codex,
`/dev:ralph-lisa-loop` from this Claude Code plugin, or `/ralph-lisa-loop` when
installed as a standalone Claude Code skill. An imperative request to run, start, or
use the Ralph-Lisa loop also qualifies.

Do **not** activate when:
- The user merely names, quotes, discusses, audits, or configures this skill.
- The user asks for ordinary planning, building, implementation, review, or Codex help,
  even if the task might benefit from a structured loop.
- The user has scoped the current agent as reviewer-only or explicitly excluded
  implementation, delegation, or automation.
- Another orchestration workflow is already active, unless the user explicitly asks to
  replace it with Ralph-Lisa or run both. Once active, Ralph-Lisa owns its external-review
  channel; do not separately activate `consult-outside-expert` for its Codex rounds.

An already-running loop is exempt. If a session file (`tmp/ralph-lisa-loop-session.md`)
exists with status `active` or `awaiting_human`, this workflow was already explicitly
started, so continue it — the explicit-opt-in requirement governs initial activation, not
continuation. A bare "continue" between rounds, a mediator decision resolving an
`awaiting_human` round, or a compacted or hook-restored context reloading this skill mid-run
all resume the existing session rather than fail the gate.

If the gate fails, answer normally and stop. Do not perform preflight, open the guide,
inspect or install hooks, create a session, or spawn subagents.

## Preflight

Do not enter the round loop until all preflight checks pass.

### Step 1: Stop hook check

Read `~/.claude/settings.json` and look for a `Stop` hook entry pointing to this
skill's `scripts/stop-hook.sh`.

If the hook is NOT installed, tell the user:

> The ralph-lisa loop works best with the stop hook installed — it keeps the loop
> running automatically so you don't have to type "continue" each round. The hook
> is dormant when no loop session is active (it checks for a session file and
> exits immediately if none exists).
>
> Want me to add it to your settings?

If the user agrees, add this entry to `~/.claude/settings.json` under `hooks.Stop`
(create the key path if it doesn't exist):

```json
{
  "matcher": "",
  "hooks": [{
    "type": "command",
    "command": "SKILL_SCRIPTS_DIR/stop-hook.sh",
    "timeout": 10000
  }]
}
```

Replace `SKILL_SCRIPTS_DIR` with the absolute path to this skill's `scripts/`
directory (resolve from the skill installation location).

If the user declines the hook, proceed in Manual tier (the user will type
"continue" between rounds). Note the tier in the session's first round summary.

If the hook IS already installed, proceed without mentioning it.

### Step 2: Codex reviewer channel check

Codex reviews through `codex exec`, which runs non-interactively. Check that the
CLI is on PATH and can authenticate: `command -v codex && { codex login status >/dev/null 2>&1 || [ -n "${CODEX_API_KEY:-}" ]; }` (a stored login, or `CODEX_API_KEY`, which `codex exec` reads and `login status` doesn't check).

- If both succeed → record `reviewer_backend: exec` and `review_channel_status: exec_ready` in session, proceed.
- If either fails → hard stop:
  > The ralph-lisa loop requires the Codex CLI as reviewer. Install it with
  > `npm i -g @openai/codex`, sign in with `codex login`, then re-invoke the skill.

### Step 3: Reasoning policy initialization

Confirm rope length and inform the user of the reasoning policy (no action
needed from them):

> Reasoning policy: xhigh for all rounds, with detailed reasoning summaries.

## Protocol

Open `@references/guide.md` and follow it. Do not proceed without it.

When the activation gate passes, run the automated plan-implement loop with subagent
workers and Codex as reviewer. The orchestrator dispatches subagents for
planning/implementation and self-review, and Codex for external review. It supports:
- Plans stress-tested through parallel ideation then iterative convergence
- Implementation reviewed each round with zero-finding close gate
- Adjustable autonomy via rope-length (0 = approve everything, 5 = full auto)
- Walk-away execution with all decisions tracked in a session file
- Context-efficient execution that completes in a single context window

The guide contains:
- Core protocol: orchestrator + three subagent types (planner/implementor worker, self-reviewer, Codex external reviewer)
- Round mechanics: implement, self-review, external review, reconciliation, synthesis, gate check
- Subagent dispatch patterns and prompt templates
- Plan context loading rules
- Rope-length semantics and salience scoring
- Finding and dispute tracking with stable IDs
- Close gate derivation and anti-gaming constraints
- Phase transition (plan -> implement) with decisions ledger
- Parallel ideation protocol (Round 1 independence via subagents)
- Session file format and continuation block structure
- Stop hook integration for loop enforcement
- Prompt pack reference (`@references/prompts.md`)
- Session template (`@references/session-template.md`)
- Eval checks and failure modes

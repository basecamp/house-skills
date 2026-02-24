# Ralph-Lisa Loop Guide

Automated plan-implement loop with expert review. Claude implements, Codex reviews, the human steers. The human is the quality gate and steering authority — able to see everything, intervene at will, but not required to keep the loop running.

---

## Core Principle

One loop, two modes (plan/implement). A single rope-length knob (0-5) controls how often the system interrupts for human input. Closure always requires zero open findings and zero unresolved disputes, regardless of rope setting.

The loop: **Produce → Self-Review → External Review → Reconcile → Synthesis → Gate Check → Loop or Close.**

---

## Roles

- **Implementor** (Claude): Does the work. Also does self-review. Proposes synthesis but does not approve design decisions unilaterally.
- **Reviewer** (Codex): Independent external reviewer. Finds problems, doesn't praise. Reviews via MCP thread or `codex exec` fallback.
- **Mediator** (Human): Steering authority. Sets scope, resolves disputes, approves rejections. Sees everything in Claude's conversation. Intervenes when they choose, gets elicited when salience warrants it.

The implementor must STOP and escalate to the mediator — not proceed autonomously — when design decisions arise. The mediator is sole authority for dispute resolution and finding rejection.

---

## Rope-Length Semantics (0-5)

Rope length controls **interruption salience** — when the system pauses for human input. It does NOT relax quality standards. The close gate is invariant across all levels.

### Salience Scoring

Each potential interruption receives a salience score (1-5):

| Score | Consequence level | Examples |
|-------|-------------------|----------|
| **1** | Cosmetic / easily reversible | Naming choices, formatting, minor style |
| **2** | Low consequence, reversible | Implementation detail between equivalent approaches |
| **3** | Moderate consequence | API shape decisions, dependency choices, data model tradeoffs |
| **4** | High consequence, hard to reverse | Architectural direction, security model, performance strategy |
| **5** | Irreversible / catastrophic risk | Scope redefinition, fundamental approach change, data loss risk |

### Rope-to-Threshold Mapping

| Rope | Threshold | Escalates when salience >= |
|------|-----------|---------------------------|
| **0** | 1 | Everything (mediator approves each round) |
| **1** | 2 | Low consequence and above |
| **2** | 3 | Moderate consequence and above |
| **3** | 4 | High consequence and above **(Default)** |
| **4** | 5 | Only irreversible/catastrophic |
| **5** | ∞ | Salience-triggered escalations disabled. Only mandatory escalations apply. |

### Mandatory Escalations (All Rope Levels)

These ALWAYS escalate regardless of salience score:
- **Stall**: same finding (by ID or `supersedes` chain) unresolved 3+ rounds
- **Round limit approaching**: `max_rounds - 2`
- **Disputed finding with no resolution path**

### Below-Threshold Findings

When salience < rope threshold, the system continues without interrupting — but the finding remains **OPEN**. It must still be fixed or rejected with mediator-approved rationale. The rope controls interruption, not accountability.

---

## Workflow

### Initialization

1. Create session file from `@session-template.md` → `.claude/ralph-lisa-loop-session.md`
   - Set `artifact_path`, `mode` (plan|implement), `rope_length`, `original_prompt`
   - Generate `session_id` (timestamp or UUID)
2. If mode=plan: run parallel ideation (see below), then enter round loop
3. If mode=implement: read converged plan and decisions ledger, begin implementation, enter round loop

### Parallel Ideation (Plan Mode, Round 1 Only)

**Independence protocol** — Codex must form its own view without being anchored by Claude's draft.

1. Claude develops initial plan from user's prompt (writes to artifact file)
2. Claude calls Codex with the independent ideation prompt from `@prompts.md` — containing **ONLY the original task prompt and reviewer persona**. Claude's draft is NOT included.
3. Claude reads Codex's independent plan
4. Claude synthesizes both into a unified plan artifact, documenting which ideas came from each source and where they diverge
5. Update session file: `current_round=2`, record `codex_plan_thread_id`

The independence guarantee: the Round 1 Codex prompt contains the task description and reviewer persona, but zero content from Claude's draft.

### Round Loop (Both Modes)

```
1. Self-Review
   - Claude examines the artifact critically
   - Produce findings with globally stable IDs (F-{seq}) and H/M/L labels
   - Be genuinely critical — don't softball

2. External Review
   - Claude sends artifact to Codex via mcp__codex__codex-reply (or codex exec fallback)
   - Use delta-only continuation prompt for Round 2+ (see prompts.md)
   - Codex reviews, returns findings with H/M/L labels
   - Claude assigns finding IDs to Codex's findings (F-{next_seq})
   - Claude checks for supersedes/duplicate relationships with existing findings

3. Reconciliation
   - Map agreements and disagreements between self-review and external review
   - For disagreements: open disputes (D-{finding_id}, e.g. D-F-3)
   - Score each potential interruption for salience (1-5)
   - If salience >= rope threshold: set status=awaiting_human, present to mediator
   - If salience < rope threshold: log salience score and rationale, continue
     (finding remains OPEN — still requires fix or mediator-approved rejection)

4. Synthesis
   - Address agreed findings (mark resolved with fix evidence)
   - Update artifact
   - Recompute gate counts from finding/dispute records
   - Update session file: round summary with finding IDs, states, derived counts
   - Update continuation block with current state
   - Verify cache matches derived counts (fail closed on mismatch)

5. Gate Check
   - Derive: open_findings, open_disputes, rejection_integrity (see Close Gate)
   - Cache match? (fail closed on mismatch, log warning)
   - Gate passes? → close phase (or transition if plan mode)
   - Gate fails? → next round with delta-only context
```

### Status Transitions

```
active → awaiting_human
  Trigger: salience >= rope threshold, or mandatory escalation fires
  Action: set status=awaiting_human, present decision via AskUserQuestion
  Effect: stop hook yields, human can respond

awaiting_human → active
  Trigger: mediator provides response (next conversation turn)
  Action: set status=active, append mediator decision to round log,
          update affected findings/disputes per decision, continue loop
  Constraint: exactly one transition per mediator response

active → complete
  Trigger: implement-mode close gate passes
  Action: (1) final synthesis,
          (2) mediator attestation (required unless attestation-exempt),
          (3) archive session to .claude/ralph-lisa-loop-history/session-{id}.md,
          (4) THEN set status=complete
  Effect: stop hook yields permanently after status change
```

Claude MUST check for `status: awaiting_human` at the start of each turn. If `awaiting_human` and a mediator response is present, transition to `active` before any other action.

### Phase Transition (Plan → Implement)

On plan-mode close gate passing:

1. Verify cumulative counters are current (they are updated event-driven — see below)
2. Update session: `mode=implement`, `current_round=1`
3. **Compile decisions ledger**: extract all resolved disputes and rejected-with-reason findings into `## Implementation Decisions` section. Read-only context, not gating state.
4. Clear finding and dispute ledgers for the implementation phase (fresh start)
5. Start new Codex MCP thread for implementation reviews (record `codex_impl_thread_id`)
6. Notify human: "Plan converged after N rounds. Transitioning to implementation."
7. Claude reads converged plan + decisions ledger, begins implementation

### Completion

On implement-mode close gate passing:

1. Final synthesis (`status` remains `active` — stop hook keeps enforcing)
2. Mediator attestation — **required** unless attestation-exempt (see below)
3. Archive session file to `.claude/ralph-lisa-loop-history/session-{id}.md`
4. Set `status: complete` (only AFTER synthesis + archive succeed)
5. Stop hook now allows exit

**Attestation-exempt criteria** (ALL must be true, checked against cumulative counters):
- `rope_length == 5`
- `total_disputes_opened_all_phases == 0`
- `total_rejections_all_phases == 0`
- `total_rounds_all_phases <= 5`

If any criterion is false, attestation is required.

### Cumulative Counter Update Rules

Counters are **event-driven** — incremented at the moment the event occurs, not batch-computed at phase boundaries. They are monotonically increasing and never cleared.

| Counter | Increment event |
|---------|----------------|
| `total_rounds_all_phases` | +1 when a round's synthesis step completes (both phases) |
| `total_disputes_opened_all_phases` | +1 when a new dispute record is created (both phases) |
| `total_rejections_all_phases` | +1 when a finding transitions to `rejected_with_reason` (both phases) |

These counters reflect all-time totals across both plan and implement phases. Because they update on each event, they are always current — no batch recomputation needed at phase transition or completion.

---

## Close Gate (Invariant)

The close gate is **derived from finding and dispute records**, not from mutable counters.

```
open_findings = count of findings where state IN ("open", "disputed")
open_disputes = count of disputes where state == "open"
rejection_integrity = all findings where state == "rejected_with_reason"
                      MUST have non-empty rejection_rationale,
                      rejection_approved_by == "mediator",
                      and rejection_approved_round set

close_gate_pass = (open_findings == 0)
                  AND (open_disputes == 0)
                  AND (rejection_integrity == true)
```

The YAML fields `open_findings_count` and `open_disputes_count` are **caches**. If a mismatch between cache and derived counts is detected, the system **fails closed** (does not pass gate) and logs a warning.

H/M/L labels are descriptive metadata for human triage — they do not control gating. ALL findings must reach a terminal state.

---

## Finding and Dispute Tracking

### Finding Record

| Field | Values |
|-------|--------|
| `id` | F-{seq} — globally unique, monotonically increasing |
| `source` | `implementor_self` or `reviewer` |
| `priority` | H, M, or L (descriptive, does not affect gate) |
| `claim` | What's wrong |
| `evidence` | Specific reference |
| `required_action` | What to do |
| `state` | `open`, `resolved`, `disputed`, `rejected_with_reason` |
| `introduced_round` | Round number when first raised |
| `resolved_round` | Round number when resolved (if applicable) |
| `supersedes` | ID of prior finding this replaces (for refined findings) |
| `duplicate_of` | ID of existing finding this duplicates |
| `rejection_rationale` | Required when `rejected_with_reason` |
| `rejection_approved_by` | Required when `rejected_with_reason`. Must be `mediator`. |
| `rejection_approved_round` | Required when `rejected_with_reason` |

**ID stability rules:**
- A finding keeps its ID across rounds until resolved
- If a reviewer rewords or refines a prior finding, the new version gets a new ID with `supersedes: F-{old}` — the old finding is marked resolved, the new one is open
- Stall detection follows `supersedes` chains: 3+ rounds unresolved triggers mandatory escalation
- `duplicate_of` marks a finding as a duplicate — the duplicate is immediately resolved, the original remains

### Dispute Record

| Field | Values |
|-------|--------|
| `id` | D-{finding_id} (e.g. D-F-7) |
| `finding_id` | References the contested finding |
| `implementor_position` | Why it shouldn't be fixed |
| `reviewer_position` | Why it should |
| `mediator_decision` | Resolution (when given). Mediator is sole authority. |
| `state` | `open` or `resolved` |

**Rejection authority**: `rejected_with_reason` requires mediator approval only. The reviewer cannot veto a mediator-approved rejection but CAN raise a new finding if they believe the rejection rationale is flawed (new finding, new ID, normal lifecycle).

### Round Summary (Required Each Round)

Every round synthesis MUST include:
- New findings (by ID)
- Resolved findings (by ID, with fix evidence or rejection rationale)
- Still-open findings (by ID)
- New disputes / resolved disputes (by ID)
- Derived gate counts (recomputed from records)
- Cache mismatch check (compare derived to YAML cache)

---

## Anti-Gaming Constraints

1. Reviewer findings cannot be dismissed without fix evidence or mediator-approved rejection rationale
2. Disputed findings remain blocking until explicitly resolved
3. "No findings" claims from the reviewer require Claude to verify: explicitly check that the response contains no substantive suggestions even if unlabeled
4. Repeated unresolved finding IDs (following `supersedes` chains) across 3+ rounds trigger mandatory mediator escalation
5. Implementor cannot self-approve `rejected_with_reason` — only mediator can
6. Round summaries must include new/resolved/still-open findings by ID — omission blocks the close gate
7. Cache-vs-derived count mismatch fails the gate closed and logs warning

---

## Codex Interaction

### MCP Thread Pattern (Primary)

**Plan-phase Round 1** (independent ideation):
```
mcp__codex__codex(
  prompt="[independent ideation prompt from prompts.md — task only, NO Claude draft]",
  cwd="[project dir]"
) → threadId → save as codex_plan_thread_id
```

**Plan-phase Round 2+** (review):
```
mcp__codex__codex-reply(
  threadId="[codex_plan_thread_id]",
  prompt="[delta-only continuation + plan review prompt from prompts.md]"
)
```

**Implement-phase Round 1** (new thread):
```
mcp__codex__codex(
  prompt="[reviewer persona + implementation review prompt from prompts.md]",
  cwd="[project dir]"
) → threadId → save as codex_impl_thread_id
```

**Implement-phase Round 2+** (review):
```
mcp__codex__codex-reply(
  threadId="[codex_impl_thread_id]",
  prompt="[delta-only continuation + implementation review prompt from prompts.md]"
)
```

### codex exec Fallback

When Codex MCP is not configured, fall back to `codex exec` with session continuation:

**Plan-phase Round 1** (new session):
```bash
codex exec "[independent ideation prompt — task only, NO Claude draft]" \
  -o /tmp/ralph-lisa-loop-review.txt \
  --json 2>/dev/null | grep session_id
# Save session_id → codex_plan_session_id in session file
```

**Round 2+** (resume prior session):
```bash
codex exec resume "$codex_plan_session_id" \
  "[delta-only continuation prompt]" \
  -o /tmp/ralph-lisa-loop-review.txt
```

**Implement-phase** — same pattern: new session for Round 1, `exec resume` for Round 2+. Save session ID as `codex_impl_session_id`.

Session continuation preserves Codex's context across rounds — the reviewer remembers prior findings, decisions, and artifact state. This is the direct analog of MCP thread persistence.

---

## Automation Tiers

| Tier | What works | What's manual |
|------|------------|---------------|
| **Manual** | Skill guide + prompts + session template. Claude follows protocol, human types "continue" between rounds. | Loop continuation |
| **Semi-auto** | Stop hook registered. Loop continues automatically. `awaiting_human` respected. | Hook registration (one-time) |
| **Full-auto** | Stop hook + Codex MCP configured. Zero human input during execution; attestation at close unless attestation-exempt. | MCP server setup (one-time) |

### Stop Hook Setup

Add to `~/.claude/settings.json` → `hooks.Stop`:

```json
{
  "matcher": "",
  "hooks": [{
    "type": "command",
    "command": "<absolute-path-to-installed-skill>/scripts/stop-hook.sh",
    "timeout": 10000
  }]
}
```

Restart Claude Code. The hook no-ops (exits 0) when no session file exists.

The hook reads `.claude/ralph-lisa-loop-session.md` and:
- **Allows stop** if: no session file, `status: awaiting_human`, or `status: complete`
- **Blocks stop** if: `status: active` — extracts the continuation block (fixed-size, ~200 bytes) and re-injects it as the continuation prompt

---

## Eval Checks

Run against `.claude/ralph-lisa-loop-session.md` after session completes:

```bash
scripts/eval.sh [session-path]
# Default: .claude/ralph-lisa-loop-session.md
# Exit 0 = all checks pass, exit 1 = any FAIL
```

| # | Check | FAIL/WARN | What it verifies |
|---|-------|-----------|------------------|
| 1 | Session file exists | FAIL | File at given path exists |
| 2 | Round count | info | Count of `## Round` headings |
| 3 | Self-Review per round | WARN | `### Self-Review` count matches rounds |
| 4 | External Review per round | WARN | `### External Review` count matches rounds |
| 5 | Reconciliation per round | WARN | `### Reconciliation` count matches rounds |
| 6 | Synthesis per round | WARN | `### Synthesis` count matches rounds |
| 7 | Finding Ledger per round | WARN | `### Finding Ledger` count matches rounds |
| 8 | Gate Check per round | WARN | `### Gate Check` count matches rounds |
| 9 | Finding IDs present | WARN | At least one `F-{n}` ID in session |
| 10 | Final status = complete | WARN | YAML `status:` field is `complete` |
| 11 | Continuation block well-formed | FAIL | Both markers present, content non-empty |
| 12 | Cache consistency | FAIL | Last gate check line has `Cache match: yes` |
| 13 | No open/disputed findings | FAIL | No finding with latest state `open` or `disputed` |
| 14 | No open disputes | FAIL | No dispute with latest state `open` |
| 15 | Rejection integrity | FAIL | Each `rejected_with_reason` has rationale, `mediator` approval, round |
| 16 | Session archived | WARN | Archive file exists at history path |
| 17 | Round summaries have gate data | WARN | Each Gate Check section has `Derived open findings:` line |

---

## Failure Modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| Reviewers keep repeating | No delta-only constraint | Use delta-only continuation prompt, enforce no repeats |
| Convergence stalls | Disagreements not mediated | Force explicit dispute resolution, check stall detection |
| Low signal feedback | Wrong reviewer context | Include richer context in review prompt |
| Too many findings, no progress | Everything open, nothing resolved | Prioritize H findings, batch L findings |
| Reviewer contradicts themselves | No evidence requirement | Require evidence field in findings |
| MCP thread lost | Thread ID not recorded | Record in session file, fall back to codex exec |
| Implementor steamrolls decisions | Not escalating tradeoffs | Check salience scoring, lower rope threshold |
| "No findings" when issues exist | Reviewer shallow or prompt too terse | Claude verifies: check for unlabeled suggestions in response |
| False convergence | Both agents aligned but wrong | Mediator validates key decisions at close (attestation) |
| Cache mismatch at gate | Bug in count tracking | Gate fails closed, log warning, recompute from records |
| Stop hook blocks during awaiting_human | Hook not checking status | Hook checks status first, yields on awaiting_human |
| Session lost on resume | Session file not persisted | Session file lives in .claude/, survives across turns |
| Stale continuation block | Not updated after round | Claude updates continuation block each synthesis step |
| Attestation skipped at high rope | Attestation-exempt criteria not checked | Verify all four criteria against immutable counters |
| Phase transition with open disputes | Gate check missed | Gate blocks transition if any dispute state=open |
| Rejected finding without metadata | Gate not checking rejection fields | Gate verifies rationale + approved_by + approved_round |

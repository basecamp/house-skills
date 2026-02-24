# Session Template

Copy this to `.claude/ralph-lisa-loop-session.md` at initialization. Replace bracketed values.

---

```markdown
---
session_id: [auto-generated UUID or timestamp]
artifact_path: [path to plan or code being reviewed]
mode: plan
rope_length: 3
status: active
current_round: 1
open_findings_count: 0
open_disputes_count: 0
codex_plan_thread_id: null
codex_impl_thread_id: null
codex_plan_session_id: null
codex_impl_session_id: null
max_rounds: 20
total_rounds_all_phases: 0
total_disputes_opened_all_phases: 0
total_rejections_all_phases: 0
original_prompt: |
  [the user's initial prompt, verbatim]
---

<!-- CONTINUATION BLOCK — injected by stop hook, kept compact -->
You are running the ralph-lisa loop. Read this file for state and follow the
ralph-lisa-loop skill guide. Take the next action for your current mode and round.
Mode: plan. Round: 1. Open findings: 0. Open disputes: 0.
<!-- END CONTINUATION BLOCK -->

<!-- ROUND LOG — grows each round, NOT injected by stop hook -->

## Round 1

### Self-Review
[Claude's critical self-review of the artifact. Produce findings with H/M/L labels.]

### External Review
[Codex's review response. Claude assigns finding IDs.]

### Reconciliation
[Map agreements and disagreements. Open disputes where implementor disagrees.]

### Synthesis
[Address agreed findings. Update artifact. Document what changed.]

### Finding Ledger

| id | source | priority | claim | state | introduced_round | resolved_round | supersedes | duplicate_of | rejection_rationale | rejection_approved_by | rejection_approved_round |
|----|--------|----------|-------|-------|------------------|----------------|------------|--------------|--------------------|-----------------------|--------------------------|
| F-1 | [implementor_self\|reviewer] | [H\|M\|L] | [what's wrong] | [open\|resolved\|disputed\|rejected_with_reason] | 1 | | | | | | |

### Dispute Ledger

| id | finding_id | implementor_position | reviewer_position | mediator_decision | state |
|----|------------|---------------------|-------------------|-------------------|-------|
| D-F-1 | F-1 | [why not fix] | [why fix] | [resolution] | [open\|resolved] |

### Gate Check
Derived open findings: 0. Derived open disputes: 0. Cache match: yes.

---

## Implementation Decisions
[Populated at plan->implement transition. Read-only context for implementation phase.
Contains resolved disputes and rejected-with-reason findings from plan phase.]

```

## Field Reference

### Session Frontmatter

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | string | Unique identifier for this session |
| `artifact_path` | string | Path to the artifact under review |
| `mode` | enum | `plan` or `implement` |
| `rope_length` | int 0-5 | Interruption threshold (see guide) |
| `status` | enum | `active`, `awaiting_human`, `complete` |
| `current_round` | int | Current round number within this phase |
| `open_findings_count` | int | **Cache** — must match record-derived count |
| `open_disputes_count` | int | **Cache** — must match record-derived count |
| `codex_plan_thread_id` | string/null | MCP thread ID for plan-phase reviews |
| `codex_impl_thread_id` | string/null | MCP thread ID for implement-phase reviews |
| `codex_plan_session_id` | string/null | `codex exec` session ID for plan-phase reviews (fallback) |
| `codex_impl_session_id` | string/null | `codex exec` session ID for implement-phase reviews (fallback) |
| `max_rounds` | int | Safety limit per phase |
| `total_rounds_all_phases` | int | **Immutable cumulative** — survives phase transition |
| `total_disputes_opened_all_phases` | int | **Immutable cumulative** — survives phase transition |
| `total_rejections_all_phases` | int | **Immutable cumulative** — survives phase transition |
| `original_prompt` | string | User's initial prompt, verbatim |

### Continuation Block

The text between `<!-- CONTINUATION BLOCK -->` and `<!-- END CONTINUATION BLOCK -->` is:
- Extracted by the stop hook and re-injected as the continuation prompt
- Fixed-size (~200 bytes) regardless of session length
- Updated by Claude each round with current mode, round, and derived counts

### Finding States

```
open ──────────────► resolved         (fix evidence provided)
  │
  ├──────────────► disputed          (implementor disagrees)
  │                   │
  │                   └──► resolved  (mediator decides)
  │
  └──────────────► rejected_with_reason  (mediator approves rejection)
```

### Dispute States

```
open ──────────────► resolved         (mediator decides)
```

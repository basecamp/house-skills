# Session Template

Copy this to `.claude/ralph-lisa-loop-session.md` at initialization. Replace bracketed values.

---

```markdown
---
session_id: [auto-generated UUID or timestamp]
artifact_path: [path to plan or code being reviewed]
mode: plan
plan_only: false
rope_length: 3
status: active
current_round: 1
open_findings_count: 0
open_disputes_count: 0
reviewer_backend: null
review_channel_status: null
reasoning_effort: xhigh
codex_plan_thread_id: null
codex_impl_thread_id: null
codex_plan_session_id: null
codex_impl_session_id: null
max_rounds: 20
total_rounds_all_phases: 0
total_disputes_opened_all_phases: 0
total_rejections_all_phases: 0
compacted_through_round: 0
compaction_count: 0
original_prompt: |
  [the user's initial prompt, verbatim]
---

<!-- CONTINUATION BLOCK — injected by stop hook, kept compact -->
ralph-lisa-loop | mode=plan | round=1 | findings=0 | disputes=0 | status=active
Read .claude/ralph-lisa-loop-session.md. Follow the ralph-lisa-loop skill guide.
Next: [specific next action, e.g., "Self-review for round 1"]
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
Review channel: mcp. Reasoning effort: xhigh. Policy compliant: yes.

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
| `plan_only` | bool | If true, skip implementation phase after plan converges |
| `rope_length` | int 0-5 | Interruption threshold (see guide) |
| `status` | enum | `active`, `awaiting_human`, `complete` |
| `current_round` | int | Current round number within this phase |
| `open_findings_count` | int | **Cache** — must match record-derived count |
| `open_disputes_count` | int | **Cache** — must match record-derived count |
| `reviewer_backend` | enum/null | `mcp` or `exec` — set at startup, null before preflight |
| `review_channel_status` | enum/null | `mcp_ready`, `mcp_degraded`, `exec_opt_in`, `blocked` |
| `reasoning_effort` | string | Reasoning effort for all Codex calls (default: `xhigh`) |
| `codex_plan_thread_id` | string/null | MCP thread ID for plan-phase reviews |
| `codex_impl_thread_id` | string/null | MCP thread ID for implement-phase reviews |
| `codex_plan_session_id` | string/null | `codex exec` session ID for plan-phase reviews (fallback) |
| `codex_impl_session_id` | string/null | `codex exec` session ID for implement-phase reviews (fallback) |
| `max_rounds` | int | Safety limit per phase |
| `total_rounds_all_phases` | int | **Immutable cumulative** — survives phase transition |
| `total_disputes_opened_all_phases` | int | **Immutable cumulative** — survives phase transition |
| `total_rejections_all_phases` | int | **Immutable cumulative** — survives phase transition |
| `compacted_through_round` | int | Last round included in compaction (0 = no compaction yet) |
| `compaction_count` | int | Number of times compaction has been performed |
| `original_prompt` | string | User's initial prompt, verbatim |

### Continuation Block

The text between `<!-- CONTINUATION BLOCK -->` and `<!-- END CONTINUATION BLOCK -->` is:
- Extracted by the stop hook and re-injected as the continuation prompt
- Fixed-size (~200 bytes) regardless of session length
- Updated by Claude each round with current mode, round, and derived counts
- Line 1 is machine-parseable state: `ralph-lisa-loop | mode=X | round=N | findings=N | disputes=N | status=X`
- Line 3 is the specific next action (updated each synthesis step)

### Compaction

When `current_round > 6`, old rounds (1 through current-3) are compacted into a cumulative summary. See the Context Management section in the guide. The `compacted_through_round` field tracks the last compacted round; `compaction_count` tracks how many times compaction has been performed. Compaction is lossless for gating — all finding/dispute states carry forward.

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

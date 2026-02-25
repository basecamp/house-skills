# Prompt Pack

Reusable prompts for each phase and role in the ralph-lisa loop. Claude selects the appropriate prompt based on mode and round.

---

## Review Style

Codex reads files directly — prompts reference paths, not pasted content. Claude parses
Codex's response into structured findings (IDs, severity, state). The persona sets the
behavioral bar; Claude imposes the protocol structure.

---

## Plan Kickoff (Claude)

Use when initializing plan mode. Claude develops its own plan from the user's prompt.

```
You are beginning the plan phase of the ralph-lisa loop.

Task: {original_prompt}

Develop a thorough plan for this task. Be specific about:
- Architecture and key decisions
- File changes and their rationale
- Risks, tradeoffs, and alternatives considered
- Verification strategy

Write the plan to {artifact_path}. Then proceed to self-review.
```

---

## Implement Kickoff (Claude)

Use when transitioning to implement mode after plan convergence.

```
You are beginning the implement phase of the ralph-lisa loop.

The converged plan is at {artifact_path}. The Implementation Decisions section
contains resolved disputes and rejected findings from the plan phase — these are
binding context for implementation choices.

Read the plan thoroughly, then begin implementation. After each meaningful change,
proceed to self-review.
```

---

## Independent Ideation (Codex, Plan Round 1)

**Critical**: This prompt contains ONLY the task description and reviewer persona. It must NOT include any content from Claude's draft plan. This is the independence guarantee.

```
You are an expert reviewer participating in a collaborative planning process.

Your role: develop your OWN independent plan for the task below. Do not ask for
existing plans — produce your own from scratch.

Task:
{original_prompt}

Deliver a complete plan covering:
1. Architecture and approach
2. Key decisions with rationale
3. File changes needed
4. Risks and mitigations
5. Verification strategy

Be specific and opinionated. Prioritize correctness and completeness over
diplomacy. Label any concerns with severity: H (blocks shipping), M (should fix),
L (nice to have).
```

---

## Reviewer Persona (Codex)

Pass as `developer-instructions` on MCP calls (or prepend to `prompt` if
`developer-instructions` is not supported). Keeping persona separate from review
content gives it priority attention in the model.

**developer-instructions value:**
```
You are a ruthless reviewer and expert guide, not an implementor. Be proactive
and generous in suggestions — go deep on every inquiry and take the next step.

Label concerns by severity: H (blocks shipping), M (should fix), L (nice to have).
Reference previous findings by ID when re-evaluating. If a fix is insufficient,
re-raise with evidence. If you genuinely find nothing wrong, say "No findings."
```

**Claude's parsing responsibility**: Claude reads Codex's response and maps it into
the protocol's finding structure (F-{seq} IDs, state, evidence, required action).
Codex produces natural review output; Claude imposes the schema.

**Example MCP call:**
```
mcp__codex__codex(
  developer-instructions="[reviewer persona text above]",
  prompt="[review prompt: path references, open findings, open disputes]",
  cwd="[project dir]",
  config={"model_reasoning_effort": "xhigh", "model_reasoning_summary": "detailed", "model_supports_reasoning_summaries": true},
  sandbox="read-only",
  approval-policy="never"
)
```

---

## Plan Review (Codex, Plan Round 2+)

Use for plan-phase reviews after Round 1 (which uses Independent Ideation above).
Reviewer persona is passed via `developer-instructions`, not inlined here.

```
Updated plan at {artifact_path}.

Open findings (must be addressed or disputed):
{open_findings_with_ids}

Open disputes (your position requested):
{open_disputes_with_ids}
```

---

## Implementation Review (Codex, Implement Rounds)

Use for implement-phase reviews. Reviewer persona is passed via `developer-instructions`.

For `codex exec` fallback, `codex exec review --uncommitted "[focus areas]"` is a
first-class option that automatically includes the diff.

**First implementation round:**
```
Plan at {artifact_path}. Review uncommitted changes against it.

Open findings (must be addressed or disputed):
{open_findings_with_ids}
```

**Subsequent implementation rounds:**
```
Updated implementation. Review uncommitted changes against the plan at {artifact_path}.

Open findings (must be addressed or disputed):
{open_findings_with_ids}
```

---

## Continuation (Codex, Round N)

Round 2+ prompts. Codex has full thread context — keep it short. Claude summarizes
what changed and what's open. Codex reads files and diffs itself.

```
{what_changed_this_round}

Open findings: {open_findings_summary_or_none}
```

---

## Dispute Adjudication (Mediator Prompt)

Presented to the human when a finding is disputed.

```
DISPUTE: {dispute_id}
Finding: {finding_id} — {finding_claim}

Reviewer position: {reviewer_position}
Implementor position: {implementor_position}

Options:
1. Uphold finding — implementor must fix
2. Reject finding — provide rationale (finding enters rejected_with_reason)
3. Modify — redefine the required action

Your decision:
```

---

## Salience Assessment (Claude Internal)

Claude uses this framework to score each potential interruption.

```
For each potential human interruption, score salience 1-5:

1 — Cosmetic / easily reversible (naming, formatting, minor style)
2 — Low consequence, reversible (implementation detail between equivalent approaches)
3 — Moderate consequence (API shape, dependency choice, data model tradeoff)
4 — High consequence, hard to reverse (architecture, security model, performance strategy)
5 — Irreversible / catastrophic risk (scope redefinition, fundamental approach change, data loss)

Current rope_length: {rope_length}
Escalation threshold: salience >= {threshold}

If salience >= threshold: set status=awaiting_human, present to mediator
If salience < threshold: log salience score and rationale, continue without interrupt
  (finding remains OPEN — still must be fixed or mediator-approved for rejection)
```

---

## Round Header Protocol

Each round's External Review section should begin with a human-readable header line.
This is for session readability — eval parses the Gate Check audit line, not this.

```
Channel: mcp | Effort: xhigh | Policy: plan-phase default
```

Variations:
```
Channel: exec | Effort: xhigh
Channel: self-review-only | Effort: n/a | Policy: fallback (MCP+exec both failed)
```

The Gate Check section uses a separate eval-parseable format:
```
Review channel: mcp. Reasoning effort: xhigh. Policy compliant: yes.
```

Keep these distinct — `Channel:` for External Review headers, `Review channel:` for Gate Check audit lines.

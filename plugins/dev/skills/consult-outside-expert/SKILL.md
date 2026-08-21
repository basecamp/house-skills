---
name: consult-outside-expert
description: |
  Explicitly launch an independent outside-expert consultation via Codex MCP. Use only
  when the user directly asks to run this workflow or asks another expert/agent for an
  independent second opinion. Do not infer it from ordinary review, feedback, validation,
  planning, or discussion about the skill itself.
---

# consult-outside-expert

## Activation gate

This is a heavyweight, explicit-opt-in workflow. Apply this gate before opening any
reference, creating a review log, or contacting another agent.

Activate only when the current user request directly asks to run this consultation, or
uses the skill's canonical host invocation: `$consult-outside-expert` in Codex,
`/dev:consult-outside-expert` from this Claude Code plugin, or
`/consult-outside-expert` when installed as a standalone Claude Code skill. An
imperative request to ask an independent outside expert or agent for a second opinion
also qualifies.

Do **not** activate when:
- The user merely names, quotes, discusses, audits, or configures this skill.
- The user asks the current agent for ordinary review, feedback, validation, planning,
  or an opinion without explicitly requesting another independent reviewer.
- A message is only a status update or follow-up from a completed consultation and does
  not explicitly request another review round.
- `ralph-lisa-loop` is active. That workflow owns its external-review channel; do not
  nest this skill unless the user explicitly asks for a separate consultation as well.

An already-running consultation is exempt. If `review-log.md` or `review-session.md`
exists for the current consultation, it was already explicitly started, so continue it
when the user supplies a requested mediator decision or asks to resume. A reply such as
"Option A" after a decision-point prompt applies that decision and continues the existing
session; the explicit-opt-in requirement governs initial activation, not continuation.

If the gate fails, answer normally and stop. Do not open the guide, spawn an expert, or
create consultation artifacts.

## Protocol

Open `@references/guide.md` and follow it. Do not proceed without it.

When the activation gate passes, consult an outside expert to collaboratively refine
work through iterative back-and-forth. It supports:
- Fresh perspective on your work
- Stress-testing from a different angle
- Steelmanning of ideas
- Progressive convergence on optimal outcomes

The guide contains:
- The consultation loop and mediator role
- Round templates and expert prompts
- Convergence gates and quality criteria
- Eval checks and failure modes
- Working log templates (manual + MCP modes)

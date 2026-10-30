# Skill Crafting Guide

Create effective agent skills through co-development. Skills improve through use, not through thought.

---

## Core Principle: The Co-Development Flywheel

You can't study exemplars that don't exist. Instead, skill and output improve together:

```
Skill v0 + Target 1
       ↓
   Co-develop (skill ↔ output)
       ↓
   Output 1 graduates → Exemplar 1
       ↓
Skill v1 + Target 2
       ↓
   Co-develop (skill ↔ output)
       ↓
   Output 2 graduates → Exemplar 2
       ↓
   ... continue until mature
```

Each flywheel turn:
- Skill handles more edge cases
- Eval loop catches more failures
- Failure modes table grows from real friction
- Structure emerges where needed

---

## Phase 1: Bootstrap

No exemplars yet. Take a stab. Be wrong.

### Define the problem
- What task does this skill perform?
- What does "good" look like (even if vague)?
- Who will use this skill?

### Draft v0
Create `skills/SKILL_NAME/SKILL.md`:

```yaml
---
name: skill-name
description: One line describing what the skill does
triggers:
  - phrase that should activate this skill
  - another trigger phrase
---

# skill-name

Open `@references/guide.md` and follow it. Do not proceed without it.

[Brief description of what the skill produces]
```

Create `skills/SKILL_NAME/references/guide.md`:
- Rough process outline
- Initial templates (if applicable)
- Placeholder for eval checks
- Empty failure modes table

**v0 will be wrong. That's the point.**

---

## Phase 2: Co-Develop

This is the engine. Skill and output improve in tandem.

### Pick Target 1
- Real project, not a toy
- Representative of the skill's intended use
- You care about the output quality

### Work the target
Execute the skill on Target 1. As you work:

1. **Notice friction** - Where does the skill fail to guide you?
2. **Notice gaps** - What decisions aren't covered?
3. **Notice waste** - What guidance didn't help?

### Update skill mid-flight
Don't wait until done. When you hit friction:
- Add guidance that would have helped
- Add a failure mode entry
- Add or refine an eval check

### Escalation triggers
STOP and involve the human when you encounter:

| Trigger | Example |
|---------|---------|
| **Design tradeoff** | Simple-but-limited vs complex-but-complete |
| **Scope change** | Fix would significantly change skill's purpose |
| **"By design" response** | Wanting to mark an issue as intentional |
| **Repeated issue** | Same problem surfaces 2+ rounds |
| **Accepting limitations** | Documenting a gap rather than fixing it |
| **Architectural choice** | Solution affects overall structure |

**When triggers fire** - STOP and present decision:

**Option 1: AskUserQuestion tool** (Claude Code)
```
AskUserQuestion({
  questions: [{
    question: "[Issue]? Stake: [what gets worse if wrong]",
    header: "Design",
    options: [
      { label: "Option A (Recommended)", description: "[approach] - Pro: X, Con: Y" },
      { label: "Option B", description: "[approach] - Pro: X, Con: Y" }
    ],
    multiSelect: false
  }]
})
```

**Option 2: Text template** (portable fallback)
```
DECISION POINT: [issue]
Stake: [what gets worse if we choose wrong]

Option A: [approach]
- Pro: ...
- Con: ...

Option B: [approach]
- Pro: ...
- Con: ...

Recommendation: [A/B] because [reasoning]

Your call?
```

Wait for explicit response before proceeding. Log: `Approved: [decision] by [user]`

**When no triggers fire** - state explicitly:
```
Decision points: none this round.
```

This creates an auditable trace. Silence cannot masquerade as compliance.

**Discretion expected.** Escalate design tradeoffs that shape the outcome, not every minor choice.

### Draft eval checks
Eval checks should be runnable where possible:
- `grep` for pattern violations
- `wc -l` for length constraints
- `jq` for schema validation
- Checklists for subjective criteria

### Complete Target 1
Keep iterating until output is good. "Good" means:
- You'd ship it
- It demonstrates skill's intent
- You learned from making it

### Graduate Exemplar 1
Output 1 is now Exemplar 1. Add to the guide's Exemplars section:
```markdown
## Exemplars

Study before starting:
- [Target 1 location/link]
```

---

## Phase 3: Spin the Flywheel

### Pick Target 2
Different enough to stress-test the skill:
- Different scale?
- Different constraints?
- Different edge cases?

### Co-develop again
- Skill v1 + Target 2
- Notice what v1 handles well (validate)
- Notice what v1 misses (extend)
- Update skill as you go

### Graduate Exemplar 2
Now you have two reference points. Patterns become clearer.

### Continue until mature
Keep spinning. Each turn:
- Skill changes shrink (converging)
- New targets reveal fewer gaps
- Outputs pass eval earlier
- Others can use the skill successfully

---

## Structure Emergence

Don't design structure upfront. Let it emerge from friction.

| Signal | Response |
|--------|----------|
| Keep hitting same failure | Add eval check |
| Output quality varies | Add template or constraint |
| Guidance feels like overhead | Remove or loosen it |
| Decisions feel arbitrary | Add rubric criteria |
| Others misuse the skill | Add "Things to get right" |

The right level of structure is discovered, not designed.

---

## Eval Loop Mechanics

Every skill should encode its own eval loop.

### Eval check anatomy
```markdown
| # | Check | Pass criteria | If fail |
|---|-------|---------------|---------|
| 1 | [Name] | [Runnable test or clear criterion] | [Specific fix] |
```

### Runnable checks (prefer these)
```bash
# Pattern violation
grep -n "bad pattern" file && echo "FAIL" || echo "PASS"

# Line count
wc -l file  # compare to target range

# Schema validation
jq '.required_field' file.json > /dev/null && echo "PASS"
```

### Subjective checks (when necessary)
- Clear rubric criteria
- Binary pass/fail, not scores
- Specific fix action if fail

### The loop
```
while not all_checks_pass:
    run_checks()
    for each failure:
        apply_fix()
    re-run_checks()
```

---

## Failure Modes Table

Catalog failures as you discover them. This is gold.

```markdown
## Failure Modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| [What you observe] | [Why it happened] | [How to fix] |
```

Grow this table with each flywheel turn. It becomes:
- Debugging guide for users
- Training data for the skill itself
- History of what you learned

---

## Maturity Signals

How to know when the skill is ready:

| Signal | Meaning |
|--------|---------|
| Skill changes shrink | Converging on stable form |
| New targets → no new failure modes | Coverage is good |
| Outputs pass eval on first/second try | Guidance is effective |
| Others succeed with the skill | Not author-dependent |
| Exemplars span the problem space | Skill generalizes |

Mature ≠ frozen. Skills can evolve. But mature means ready for others to use.

---

## Skill Anatomy Reference

```
skills/
  skill-name/
    SKILL.md              # Entry point with frontmatter
    references/
      guide.md            # Detailed process
      [other context]     # Templates, examples, etc.
```

### SKILL.md frontmatter
```yaml
---
name: skill-name           # Identifier
description: One line      # What it does
triggers:                  # Activation phrases
  - trigger phrase
---
```

### Guide structure (adapt as needed)
- Process overview
- Phases with gates
- Templates
- Eval checks table
- Failure modes table
- Exemplar references

---

## Exemplars

Study these before starting a new skill:

- **llms-txt** (`skills/llms-txt/`) - Demonstrates eval checks, failure modes, templates.
- **consult-outside-expert** (`skills/consult-outside-expert/`) - Co-developed using this skill. 10+ round Codex session with dual-review pattern.

---

## Recursive Validation

This skill should improve itself. If it can't, it doesn't work.

**Process:**
1. Reflected on what made llms-txt effective (informing exemplar)
2. Drafted v0 based on those learnings
3. Using consult-outside-expert with dual-review pattern (self-review + external Codex review + reconcile)
4. Presenting decision points to human mediator via AskUserQuestion
5. Iterating until maturity signals met

**Current status:** Mature (Target 1 complete)
- Target 1: consult-outside-expert skill ✓
- Review rounds: 10 (self-review + external Codex review, dual-review pattern)
- Convergence: 2 consecutive rounds with no H/M issues (rounds 9-10)
- Thread ID: `019bdc91-1ea3-71c3-ab8f-bda225806061`

**Maturity gate:** Zero open H/M issues AND no new H/M issues across 2 consecutive review rounds with proper dual-review process. ✓

---

## Self-Eval Checks

This skill must pass its own criteria.

**Note:** These are smoke tests verifying structure + content. Passing ≠ ready. Use consult-outside-expert for validation.

| # | Check | Command | Pass | If fail |
|---|-------|---------|------|---------|
| 1 | Has frontmatter with name | `head -1 SKILL.md \| grep -q "^---" && head -15 SKILL.md \| grep -q "^name:"` | Exit 0 | Add --- and name: |
| 2 | Has guide.md | `test -f references/guide.md` | Exit 0 | Create references/guide.md |
| 3 | Failure modes has table | `sed '/^```/,/^```/d' references/guide.md \| grep -A5 "^## Failure Modes" \| grep -q "^|"` | Exit 0 | Add table to Failure Modes |
| 4 | Eval checks has table | `sed '/^```/,/^```/d' references/guide.md \| grep -EA10 "^## (Self-Eval|Eval) Checks" \| grep -q "^|"` | Exit 0 | Add table to Eval Checks |
| 5 | Exemplars has real entries | `sed '/^```/,/^```/d' references/guide.md \| grep -A10 "^## Exemplars" \| grep -qE "^- .*(skills/\|http)"` | Exit 0 | Add exemplar with path/link |

```bash
# Run from skill directory
cd skills/skill-crafting

echo "1) Has frontmatter with name:"
head -1 SKILL.md | grep -q "^---" && head -15 SKILL.md | grep -q "^name:" && echo "PASS" || echo "FAIL"

echo "2) guide.md exists:"
test -f references/guide.md && echo "PASS" || echo "FAIL"

echo "3) Failure modes has table:"
sed '/^```/,/^```/d' references/guide.md | grep -A5 "^## Failure Modes" | grep -q "^|" && echo "PASS" || echo "FAIL"

echo "4) Eval checks has table:"
sed '/^```/,/^```/d' references/guide.md | grep -EA10 "^## (Self-Eval|Eval) Checks" | grep -q "^|" && echo "PASS" || echo "FAIL"

echo "5) Exemplars has real entries:"
sed '/^```/,/^```/d' references/guide.md | grep -A10 "^## Exemplars" | grep -qE "^- .*(skills/|http)" && echo "PASS" || echo "FAIL"
```

**Current status:**
| # | Status |
|---|--------|
| 1 | Pass |
| 2 | Pass |
| 3 | Pass |
| 4 | Pass |
| 5 | Pass (llms-txt referenced) |

## Failure Modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| Skill has no eval loop | Skipped eval checks in Phase 2 | Add checks before graduating |
| Output quality varies | No templates or constraints | Add structure where variance hurts |
| Others can't use the skill | Author-dependent tacit knowledge | Add exemplars and failure modes |
| Skill changes every target | Not converging | Check if problem is well-scoped |
| Over-specified structure | Designed upfront, not emerged | Remove what doesn't prevent failures |
| Design decisions made without human | Implementer steamrolled through tradeoffs | Add escalation triggers, re-review decisions |
| "By design" used to dismiss issues | Avoiding work, not genuine tradeoff | Escalate all "by design" responses to mediator |

---

## Quick Start

1. Create `skills/NAME/SKILL.md` with frontmatter
2. Create `skills/NAME/references/guide.md` with rough process
3. Pick a real target
4. Co-develop: update skill as you work the target
5. **STOP at design tradeoffs** - use escalation triggers, present decisions to human
6. Graduate output to exemplar
7. Spin flywheel with Target 2
8. Continue until mature

---
name: three-paragraph-recap
description: |
  Check prose against a voice contract before it is posted, or hand material to
  the three-paragraph-recap agent and get the prose back already checked. Three
  paragraphs: two of explanation, one naming the next step. About forty rules
  aimed at what generated prose does wrong — bookkeeping, process narration,
  proof of work, ornament, hedging, metaphor, counted-but-unnamed, em dashes.
  Built for agents posting to Basecamp on somebody's behalf, where the failure is
  prose that passes review and says nothing.
  Use before any agent-written comment, message or chat line goes out.
triggers:
  - /three-paragraph-recap
  - check this before posting
  - does this pass the voice contract
  - write the comment for this
---

# three-paragraph-recap

Two entry points to the same contract.

**The agent** (`three-paragraph-recap`) takes raw material, a reader and the
decision owed, and returns three paragraphs already proved against the gate. Use
it when something has to be written.

**The gate** (`scripts/preview.py`) checks prose anything wrote. Use it when
something has already been written.

```bash
SKILL_DIR="$(dirname "$0")"   # or the skill's own path
python3 "$SKILL_DIR/scripts/preview.py" draft.html            # a comment or message
python3 "$SKILL_DIR/scripts/preview.py" draft.html --chat     # a chat line or DM
echo $?                                                       # 0 passes, 1 does not
```

Write the draft to a file rather than a shell argument. Prose is full of
apostrophes, and quoting it through a shell is how the bytes you checked stop
being the bytes you post.

## The shape

Three prose paragraphs. Two of explanation, the last naming a next step and
containing the literal words "next step". Three is a ceiling, not a target.

Tables, images and figures are not prose — they do not count toward the three and
no cap applies to them. Bullet lists are refused: they are exempt from the caps,
so five bullets carry what three paragraphs are not allowed to say.

| | comment | `--chat` |
|---|---|---|
| Total words | 180 | 135 |
| Per paragraph | 90 | 45 |
| Sentences per paragraph | 5 | 5 |
| Words per sentence | 25 | 25 |
| Negations per paragraph | 2 | 2 |

URLs and markup are stripped before counting. They are proof, not prose.

The negation cap is the one worth understanding. A regex cannot tell whether two
sentences state the same fact. But restating an **absence** has a mechanical
signature, because each paraphrase needs its own negation — "does not report",
"no message reaches anyone", "nobody learns it stopped" is one finding wearing
three coats. Say it once, then say what follows.

## Why a gate rather than an instruction

"Write concisely, no filler" works for a while and then stops. The failure is not
that the model forgets; it is that generated prose has tells that survive any
amount of instruction — a sentence that announces the sentence after it, a count
with nothing named, a participle asserting a relation it never argues, a hedge in
front of the one fact the reader has to act on.

Those are mechanically detectable, so they are detected at the write rather than
asked for. Every rule in `scripts/contract.py` was added because something
reached a reader and should not have, and the comment above it says what that
was. When a rule fires and the draft looks fine to you, the rule is usually
describing a failure you have not seen yet.

## Relationship to `recap`

`recap` and this overlap and are not the same thing, and the difference is worth
knowing before you reach for one.

`recap` gathers activity from Basecamp, git and GitHub, builds a narrative, and
composes a 200–500 word digest for a team about a week. Its editor already asks
for much of what this enforces: audience-first, concrete over abstract, no
corporate-speak, earn every paragraph.

This writes three paragraphs to one person who has to decide something today, and
it refuses rather than asks. A recap digest would fail this contract on length
alone, correctly — it is not a decision surface.

The seam is real: `recap`'s editor phase is composition with no gate, and this
contract could sit under it. Neither has been wired to the other, deliberately,
so that joining them stays a decision somebody makes rather than one that happens
by accident.

## What this is not

**Not a house style.** One person's rules for one job, hardened by being wrong in
public a few dozen times. Adopt it, fork it, or read it and take the three rules
you agree with.

**Not the Basecamp voice.** `copy-guide` is that — product and marketing copy,
written to a customer. This is written to one colleague who has to act. Where the
two disagree about anything a customer reads, `copy-guide` wins.

## Extending it

Add rules the way these were added: when something reaches a reader and should
not have, write the pattern and put the failure in a comment above it. A rule
with no incident behind it is a preference, and preferences are what make a gate
something people route around.

`contract_problems(paragraphs, max_para_words=…, max_total_words=…)` is the whole
API; it returns a list of strings. Run `scripts/test_contract.py` before and
after any change — the patterns interact more than they look like they do.

## The one rule that reaches the network

`unresolvable_links` checks a linked GitHub pull request exists by shelling out
to `gh`. Every other rule reads the text in front of it. That one is off unless
asked for, because a gate that waits on a network, needs a login, and reports a
private repo as a missing one is a gate people turn off entirely.

```bash
THREE_PARAGRAPHS_VERIFY_LINKS=1 python3 scripts/preview.py draft.html
```

# Working method

## Auditing an existing file

Read it line by line and classify each line OBVIOUS / GOTCHA / TASTE / POINTER. Don't rewrite as you go — classify the whole file first, because the cut list is usually large enough to change how you'd structure what remains.

Then check, in this order:

1. **Every command.** Does the task or script exist? A script is a path, so its existence is provable in either mode. In trusted mode you may additionally run the safe ones — tests, setup, anything local and reversible — but establish deploys, database resets, and anything touching a shared service from source, `--help`, or a dry run instead. Never deploy to prove a line of documentation. Fabricated commands are the most common defect, and they survive for years because nobody checks the docs.
2. **Every path and link.** Does it resolve today?
3. **Every literal** — ports, versions, limits. Read it from the file that defines it. A version pin cited from a linter config is a favorite failure: the linter's target version and the actual runtime pin drift apart, and the file confidently states the wrong one.
4. **Duplication of anything already in context** — a shared or global instruction file, the README, a script's own header.
5. **Contradictions**, both internal and against other loaded files. Where two files disagree, state which wins, or delete one of them.
6. **Bare absolutes.** Keep-or-cut isn't the only move — rephrase is. Each never/always not guarding a genuine invariant becomes default + rationale + where it stops; when no failure can be named behind it, cut it.

## The usual cut list

Recurring across repos:

- A `## Commands` section restating `rails test`, `npm test`, `bin/dev` — framework convention, all of it OBVIOUS.
- Commands that don't exist, often copied from a sibling repo that did have them.
- A version claim citing a stale authority.
- A verbatim copy of a shared instruction file that's already loaded — paid for twice per session, and the copy is missing whatever the original has gained since.
- Restated `--help` output.
- The same environment variable documented in two places.
- Unfalsifiable filler: "follow standard conventions," "use proper error handling," "write clean code." These survive because they're unarguable, and they inform nothing.
- Setup steps the setup script already performs and prints.

## The usual add list

Shorter, and the inverse failure: most files over-mandate and under-empower.

- The file's own epistemic status — priors, not law; override with a flagged reason when the code in front of you disagrees.
- The working norms — how hard to push back, adversarial review of one's own diff before calling it done, that "works" isn't "finished." No codebase demonstrates these and no agent assumes them, so where they're missing, that's a high-value addition, not padding.

## Worked example

Before — 116 lines describing a framework convention with a Bad/Good pair:

> ### Concerns
> We like to split logic into concerns to keep the base model focused…
> ```ruby
> # Bad
> module Card::Closable
>   include ActiveSupport::Concern
>   included do
>     def self.close_all
> …
> ```

After:

> Concerns use `extend ActiveSupport::Concern` — callbacks in `included do`, class methods in `class_methods do`, private section indented. A concern with no private methods or internal state shouldn't be a concern. Canonical: `app/models/recording/incineratable.rb`.

The rule survives, the exemplar is real and checkable, and the reader who needs the full shape opens one file that is guaranteed current.

## Phrase gotchas failure-first

Lead with the symptom, because that's what the reader is holding when they arrive.

> **Bad:** `bin/setup` installs dependencies and configures Docker services.
>
> **Good:** A MySQL connection error (`Can't connect to server on '127.0.0.1'`) always means run `bin/setup` — it restarts the containers itself. Don't `docker compose up` by hand.

## Conflicts

Two loaded files disagreeing is worse than either being wrong alone, because the model can't tell which to follow and won't mention the ambiguity.

Resolve by deciding which file **owns** the rule, stating it once there, and deleting the other. If the other file is outside your control — a personal or global file — say so explicitly rather than silently contradicting it.

## Anti-patterns in the file you're writing

- **A quota that's gameable by what the same section forbids.** "At least two lines of test per line of production" pulls directly against "don't write tests that don't check behavior." The ratio is satisfiable with exactly the brittle assertions you're warning about. Keep the value rule; drop the quota.
- **Rules in importance order.** Put the traps first. Anything the codebase demonstrates can be learned for free; anything it teaches *wrongly* cannot, and burying that at the bottom is backwards.
- **A fixed section template.** A mandated `## Commands` heading is what makes an author invent commands to fill it. Let the repo's actual gotchas decide the structure.

## Verify what you wrote

Perform the static checks above. In trusted mode, execute the safe commands among those that survived the cut, and establish the consequential ones without executing them.

Then measure. Where the harness exposes a context breakdown, check the always-on total against the budget — word counts are a proxy, and should be reported as one rather than as a token measurement.

Finally, re-read the file cold, as if you'd never seen the repository. Anything you can't act on without opening another file is either a POINTER that should name that file, or a line that should be gone.

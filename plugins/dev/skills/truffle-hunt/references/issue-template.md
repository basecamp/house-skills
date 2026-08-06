# Issue Anatomy

Only needed at the end of a hunt, once a finding is **routed** to a public upstream issue.
If untrusted input can reach the defect, stop — use the project's private disclosure path
instead and don't attach a reproducer.

Order matters: a maintainer decides whether to keep reading in the first three lines.

## 1. Summary

The mechanism in three sentences, and who is affected. Lead with the impact if it is
surprising — "silent data corruption under ordinary GC" earns more attention than "dangling
pointer", and it is the honest framing when the bug does not crash.

State up front if the finding is **latent** rather than reachable. Filing a latent defect is
fine; letting a maintainer discover for themselves that you overstated it is not.

## 2. Reproduction

Minimal, standalone, copy-pasteable. Include the control and what it prints.

**Explain any non-obvious step**, or a maintainer will simplify it away and conclude it
doesn't reproduce. Real examples worth calling out in kind:

- why an object must be parked off-stack (a local is conservatively pinned, masking the bug)
- why a warm-up call is needed (registration is lazy and one-shot)
- why a filler must match a specific size (allocator pools by size)

Give the amplified and natural rates where the defect is probabilistic.

## 3. Cause

Quoted source with `file:line`, showing the actual defect — not a paraphrase. Point at the
specific line, and if the surrounding code has a comment asserting the thing is safe, quote
that too; it is usually where the wrong assumption is written down.

## 4. Affected versions

A matrix. Include the boundary if there is one, whether HEAD is affected, and any version that
is safe only *incidentally* — say so explicitly, since the defect is still present there and
an unrelated refactor can flip it back.

## 5. Suggested fix

Ideally the idiom already used elsewhere in that same codebase — it is the fix most likely to
be accepted, and it shows you read the project rather than just scanned it. Say whether you
ran it.

## 6. Environment

Exact runtime version and build string, OS/arch, the dependency version **and how it was
installed** (packaged binary vs source build), the version of anything linked or vendored
beneath it, and any non-default flag the reproducer needs.

Print these from the running process, not from memory. If a verdict depends on one of them —
a linked library version, a build configuration — say which. This is where "can't reproduce"
comes from.

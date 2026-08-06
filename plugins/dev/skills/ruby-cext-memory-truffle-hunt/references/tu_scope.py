"""The linkage rule all four pass-1 sweeps resolve names by, written down once.

    from tu_scope import Scope, TREE, declared_scope, bind

WHY THIS FILE EXISTS
--------------------
Every one of the four sweeps is a whole-tree resolver: it sees a name at a use site and
has to find the declaration that name denotes. Every one of them started life with a
single dict keyed by the BARE NAME, and every one of them has since been patched, once
per table, for the same defect -- **an internal-linkage name resolved tree-wide instead
of in the using file**. Six patches across three scripts before anyone named the rule:

    sweep_static_values.py   file-scope slots        (round 8)
    sweep_escaped_conversion.py  registered cfuncs   (round 8)
    sweep_unmarked.py        callback bodies, struct bodies, dtype descriptors (round 7)

It is one rule and it is C's own, so it is stated here and imported rather than
re-derived. Both failure directions are real and the over-clear is the expensive one:
resolving to another translation unit's `static` either reports a defect against a body
that never runs (a false positive, an hour of pass 2) or -- the case that costs findings
-- DISCHARGES a real row on the strength of a body that never runs.

THE RULE
--------
A declaration is visible in a region, and a use binds to the narrowest declaration whose
region contains it. Three tiers:

    BLOCK   Scope(path, (start, end))   a function-local `static VALUE cache;`. Nothing
                                        outside that block can even name it, so two
                                        functions in ONE file each declaring `cache` are
                                        two objects.
    FILE    Scope(path)                 internal linkage: `static` in a .c/.cc/.cpp/.cxx.
                                        Another translation unit cannot name it.
    TREE    Scope() == TREE             external linkage, or ANY declaration made in a
                                        header -- the including .c gets a copy.

THE HEADER CARVE-OUT IS LOAD-BEARING, NOT A HEDGE.
`static` in a header is still internal linkage, per-includer, but the sweeps parse the
header ONCE and can only attribute its stores and its calls to the .c files that include
it -- which they do not track. Scoping a header static to the header therefore hides
every use of it: measured in round 8 as 10 UNSOURCED noise rows (unicorn x2, yajl-ruby)
in predicate C. So the split applies to TU_EXT only, and a header declaration stays
tree-wide. The residual is stated where it bites rather than left implicit: two .c files
that separately include a header `static` share one row here.

The fall-back tier is load-bearing too. A non-static callee genuinely IS tree-wide, and
so is every struct declared in a header: mysql2 wraps `mysql2_result_wrapper` in result.c
and declares it in result.h, which is the whole reason these are tree-wide resolvers at
all. This module narrows resolution; it never file-scopes everything.
"""

TU_EXT = frozenset((".c", ".cc", ".cpp", ".cxx"))


class Scope:
    """The region of one gem tree in which a declaration is visible.

    Immutable, hashable, and cheap to use as part of a dedupe identity -- which is the
    second thing it is for. A row keyed `(scope, name)` survives two translation units
    declaring the same name; a row keyed by the name alone merges them, and the merge is
    what let one file's `rb_global_variable` discharge another file's unregistered slot.
    """

    __slots__ = ("path", "span")

    def __init__(self, path=None, span=None):
        self.path = path                      # None => the whole tree
        self.span = tuple(span) if span else None   # (start, end) byte offsets in `path`

    @property
    def tier(self):
        """0 block, 1 file, 2 tree. Lower is narrower, and narrower wins."""
        if self.span is not None:
            return 0
        return 1 if self.path is not None else 2

    def contains(self, path, off=None):
        """Can a use at (`path`, `off`) see a declaration with this scope?

        `off=None` asks the FILE-level question ("could this file see it at all"), which
        is what a caller with no offset to hand -- a call site located by name, a struct
        used by type -- can honestly ask. A block scope answers yes to it, and the caller
        that has an offset gets the sharper answer.
        """
        if self.path is None:
            return True
        if path != self.path:
            return False
        if self.span is None or off is None:
            return True
        return self.span[0] <= off < self.span[1]

    def __eq__(self, other):
        return isinstance(other, Scope) \
            and self.path == other.path and self.span == other.span

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash((self.path, self.span))

    def __repr__(self):
        if self.path is None:
            return "Scope(tree)"
        if self.span is None:
            return "Scope(%s)" % self.path.name
        return "Scope(%s@%d:%d)" % (self.path.name, self.span[0], self.span[1])


TREE = Scope()


def declared_scope(path, is_static=False, span=None):
    """The Scope a declaration gets, applying the header carve-out.

    `span` -- a function body's (start, end) -- makes it block scope regardless of the
    file's extension, because a function-local static is internal to its block in a
    header exactly as in a .c.
    """
    if span is not None:
        return Scope(path, span)
    if is_static and path is not None and path.suffix in TU_EXT:
        return Scope(path)
    return TREE


def _pair(d):
    return (d.scope, d.path)


def bind(defs, at, off=None, key=_pair):
    """The declarations of ONE name that a use at (`at`, `off`) binds to.

    `defs` is every in-tree declaration carrying that name; `key(d)` yields
    `(Scope, declaring path)`. Returns a list, because C++ overloads and #ifdef variants
    genuinely leave more than one candidate and picking among those is the CALLER's
    problem -- this function answers only "which ones are even candidates".

    Two steps, both of them C's own rule rather than a heuristic:

      1. drop every declaration whose Scope does not CONTAIN the use. A `static` in
         another translation unit is not a candidate at all -- not a lower-ranked one.
      2. of what survives, keep only the narrowest tier present, and inside the tree-wide
         tier prefer a declaration made in the using file. That last preference is what
         round 7 shipped as "prefer this file, fall back tree-wide"; it still matters
         after step 1 because a NON-static definition in the using file outranks a
         non-static namesake elsewhere, and only one of the two can be the real one.

    An empty result is a real answer: the name is declared in this tree but not visibly
    from here. Callers must treat it as "unresolved", never as "resolve it anyway".
    """
    cands = []
    for d in defs:
        scope, dpath = key(d)
        if scope.contains(at, off):
            cands.append(((scope.tier, 0 if dpath == at else 1), d))
    if not cands:
        return []
    best = min(rank for rank, _d in cands)
    return [d for rank, d in cands if rank == best]

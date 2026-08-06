"""The linkage rule all four pass-1 sweeps resolve names by, written down once.

    from tu_scope import Scope, TREE, declared_scope, bind
    from tu_scope import top_level_units, scope_zero_braces, storage_depth
    from tu_scope import match_brace, match_paren, skip_post_declarator

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

THE SECOND THING THAT KEPT BEING RE-DERIVED: WHICH BRACES ARE SCOPES
--------------------------------------------------------------------
`declared_scope` answers "how far does this declaration reach"; the walk below answers
the question one step earlier -- "is this declaration at static-storage scope at all".
It is the same rule (C's own) and it was learned the same way, once per script:

    sweep_static_values.py       top_level_units + class_scopes   (round 8, vernier)
    sweep_escaped_conversion.py  ported verbatim                  (round 8)
    sweep_interior_escape.py     ported verbatim                  (round 9)
    sweep_static_values.py       _index_funcs, the FOURTH port     (round 9 review)

A depth-0 `{` has three dispositions, not two. It belongs to the CURRENT statement when
it opens an aggregate (`static struct {`) or an initialiser (`... = {`); it ENDS the
statement when it opens a function body; and -- the disposition C++ adds -- it opens a
scope with NO STORAGE DURATION OF ITS OWN when it follows `namespace X` or `extern "C"`.

Missing the third disposition is always an over-clear and always looks like a clean
sheet, because it empties an INDEX rather than dropping a row: a definition inside
`namespace X { ... }` sits at nonzero brace depth, a walk that counts raw braces skips
it, and every later stage walks a shorter list. Predicate C's own function index still
counted raw braces after its slot walk was ported, so a C++ gem's file-scope slots were
found while every function-local `static VALUE` in the same file stayed invisible --
`slots 0/0, HITS 0` on the commonest C++ extension layout there is.

So the walk lives here, beside the linkage rule, and the sweeps import it. A namespace
is walked THROUGH rather than into: `base` carries the enclosing offset, so a
declaration three namespaces deep still reports its own file:line, and its key is the
bare name -- which is correct for resolution (a namespace-scope name is reachable from
the whole file) and is the stated residual for a registration spelled `&prof::cache`.

THE THIRD THING, AND IT IS THE SAME WALK ONE STEP LATER: WHERE A DECLARATOR ENDS
-------------------------------------------------------------------------------
`storage_depth` says a definition is top-level; `skip_post_declarator` says where its
BODY starts. Every function index in this directory is the same five lines -- find an
identifier followed by `(`, match the parameter list, require a `{` -- and every one of
them has been patched for the same reason: **tokens between the `)` and the `{` that the
walk did not know how to cross**. Four appearances, three scripts, one gap:

    sweep_interior_escape.py  `__attribute__((...))`, `noexcept`   (round 9, thread D:321)
    sweep_interior_escape.py  `auto f(VALUE) -> VALUE {`           (round 9 review)
    sweep_static_values.py    `static VALUE f(void) __attribute__((noinline)) {`
                                                                  (round 9 review, C:929)

The measured symptom is identical in every host and it is the one this directory keeps
having to name: not a dropped row but an EMPTIED INDEX. Predicate D printed
`0 fn(s) | derive 0/0 -> hit 0`; predicate C printed `slots 0/0, HITS 0` on a file whose
attributed function holds an allocating `static VALUE cache`. Both read as a clean gem.
That is the whole reason this walk lives here now instead of being ported a fifth time.

WHY THE WORDS ARE OPEN AND THE PARENTHESES ARE CLOSED
`__attribute__` and `noexcept` shipped first as a CLOSED word list, and the trailing
return type broke it again within one review -- a list that has to be extended once per
spelling is a list that reports a clean gem once per spelling. So a bare token run is
crossed freely and a parenthesised group is crossed only after a keyword known to
introduce one. The split is where the danger is: a bare token cannot turn a
non-definition into a definition, because every construct that would be mis-read
announces itself with a character this walk refuses. A `(` is different -- it is how a
SECOND declarator gets between the two, which is exactly how `MACRO(x)` followed by a
real definition would hand the walk that definition's body under the macro's name.

THE REJECTION TABLE. It travels with the walk, and it is asserted by both callers'
self-tests rather than described here only -- opening the words up is what made
predicate D invent four functions out of trilogy's `XX(...)` X-macro lists and ffi's
`__declspec(align(8))`, each followed by a `typedef enum {` whose aggregate body was
then indexed as a function body under the macro's name.

    f(str) VALUE str; {           K&R parameter declarations -- the `;`
    VALUE f(VALUE);               a prototype, then anything -- the `;`
    struct S s = f(x), t = {1};   a declarator list with an initialiser -- the `=`
    MACRO(a) static VALUE g(V x)  a macro, then a definition -- the `(` of `g(`
    XX(A, 1) typedef enum {       an X-macro list, then a type -- `typedef`
    __declspec(align(8)) struct   an attribute, then a type -- `struct`

A keyword that starts a new declaration cannot appear in a declarator suffix, so
POST_DECL_STOP stops the walk exactly as `;` does. Predicate C's neighbours are
different from predicate D's -- it is looking for `static VALUE` declarations, so a
`typedef struct { ... } static_thing;` sitting after a rejection boundary is the shape
that would cost it -- and `typedef`, `struct` and `static` are all in the stop set for
precisely that reason.

STILL UNHANDLED, named so the next one is not a surprise: a constructor
member-initialiser list (`Foo::Foo(int x) : a(x) {`) stops at the `(` of `a(`; a
trailing return type that is itself a function-pointer type (`-> VALUE (*)(int)`) stops
at its `(`; and one naming an elaborated type (`-> struct S`) stops at `struct`. All
three cost recall rather than clearing a row, which is the direction this function is
allowed to be wrong in.
"""

import bisect
import re

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


# ------------------------------------------------- which braces open a storage scope
#
# Matched against the text between the last `;`/`}` and a `{`, so both patterns are
# `$`-anchored. `extern "C"` reaches here AFTER each sweep's strip_noise, which blanks
# string BODIES and keeps the quotes -- the text is `extern " " {` -- so the linkage
# pattern has to allow a blanked literal.
NAMESPACE_HEAD = re.compile(r"\bnamespace(?:\s+\w+(?:\s*::\s*\w+)*)?\s*$")
LINKAGE_HEAD = re.compile(r"\bextern\s*\"[^\"]*\"\s*$")


def match_brace(src, open_idx):
    """Index of the `}` matching the `{` at open_idx, or -1."""
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def match_paren(src, open_idx):
    """Index of the `)` matching the `(` at open_idx, or -1."""
    depth = 0
    for i in range(open_idx, len(src)):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


# ----------------------------------------------- where a declarator ends and a body begins
#
# The rejection table and the reason the words are open while the parentheses stay closed
# are in this module's docstring; these three sets are that table's implementation.
POST_DECL_PAREN = frozenset(("__attribute__", "__asm__", "asm", "__declspec", "throw",
                             "noexcept", "alignas", "_Alignas"))
POST_DECL_STOP = frozenset(("typedef", "struct", "union", "enum", "class", "namespace",
                            "template", "using", "static", "extern", "inline", "register"))
# The punctuation a declarator suffix may legally carry: the trailing-return arrow, pointer
# and reference declarators, template arguments, and the `::` of a qualified return type.
# `;`, `=`, `,`, `(`, `[`, `{` are all absent on purpose -- see the rejection table.
POST_DECL_PUNCT = frozenset("*&<>:~->")

_POST_DECL_WORD = re.compile(r"[A-Za-z_]\w*")


def skip_post_declarator(src, k):
    """Advance past the attributes, specifiers and trailing return type between `)` and `{`.

    Returns the first offset the walk will not consume; a caller accepts the definition
    only if that offset holds the `{`. Stopping early is therefore always a REJECT, which
    is the recall-losing direction and the one this function is allowed to be wrong in.
    """
    n = len(src)
    while k < n:
        while k < n and src[k] in " \t\r\n":
            k += 1
        if k >= n:
            return k
        m = _POST_DECL_WORD.match(src, k)
        if m:
            word, j = m.group(), m.end()
            if word in POST_DECL_STOP:
                return k                # a new declaration begins: not this declarator
            t = j
            while t < n and src[t] in " \t\r\n":
                t += 1
            if t < n and src[t] == "(":
                # A parenthesised group belongs to this declarator only when a keyword
                # known to take one introduced it. Otherwise it is a second declarator and
                # the walk has left the definition it started from.
                if word not in POST_DECL_PAREN:
                    return k
                close = match_paren(src, t)
                if close < 0:
                    return k
                k = close + 1
                continue
            k = j                       # a bare word: `noexcept`, `const`, `VALUE`, ...
            continue
        if src[k] in POST_DECL_PUNCT:
            k += 1
            continue
        return k
    return k


def top_level_units(src, base=0, transparent=None):
    """[(offset, text)] -- one entry per declaration or definition at static-storage scope.

    The three brace dispositions are in this module's docstring; this is where they are
    applied. `transparent`, when a set is passed, collects the offsets of the `{` and `}`
    of every namespace and linkage block the walk descends through -- the walk that knows
    which braces are scopes is the walk that knows which braces are not, so a caller
    counting depth reads it from here instead of keeping a second opinion about C++.

    Class BODIES fall out as inert units. They are a different scope with a different key
    (`Class::member`), and only predicate C descends them, through its own class_scopes.
    """
    n, i, start = len(src), 0, 0
    while i < n:
        c = src[i]
        if c == "{":
            close = match_brace(src, i)
            if close < 0:
                return
            pre = src[start:i].rstrip()
            if NAMESPACE_HEAD.search(pre) or LINKAGE_HEAD.search(pre):
                if transparent is not None:
                    transparent.add(i)
                    transparent.add(close)
                for u in top_level_units(src[i + 1:close], base + i + 1, transparent):
                    yield u
                i = start = close + 1
                continue
            if re.search(r"\b(struct|union|enum)\b\s*\w*$", pre) or pre.endswith("="):
                i = close + 1          # an aggregate or an initialiser: same statement
                continue
            yield base + start, src[start:close + 1]
            i = start = close + 1
            continue
        if c == ";":
            yield base + start, src[start:i + 1]
            i = start = i + 1
            continue
        i += 1


def scope_zero_braces(src):
    """Offsets of the `{`/`}` that open and close a namespace or linkage block."""
    transparent = set()
    for _u in top_level_units(src, 0, transparent):
        pass
    return transparent


def storage_depth(src):
    """A `depth(off)` closure: how many STORAGE scopes enclose an offset in `src`.

    Zero means static-storage scope -- file scope, or a namespace or linkage block at
    file scope, which is the same storage duration. A definition is a TOP-LEVEL one
    exactly when its declarator sits at depth 0, and every function index in this
    directory decides that here.

    The depths are precomputed and read by bisect rather than re-counted per name: a
    per-name `src.count("{")` is quadratic in the file, and the sweeps run it once per
    identifier followed by `(`.
    """
    transparent = scope_zero_braces(src)
    bpos, bdepth, depth = [], [], 0
    for m in re.finditer(r"[{}]", src):
        if m.start() in transparent:
            continue
        depth += 1 if m.group() == "{" else -1
        bpos.append(m.start())
        bdepth.append(depth)

    def depth_at(off):
        k = bisect.bisect_left(bpos, off)
        return bdepth[k - 1] if k else 0

    return depth_at

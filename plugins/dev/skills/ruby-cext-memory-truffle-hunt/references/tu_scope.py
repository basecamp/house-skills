"""The rules all four pass-1 sweeps kept re-deriving, written down once.

    from tu_scope import Scope, TREE, declared_scope, bind
    from tu_scope import top_level_units, scope_zero_braces, storage_depth
    from tu_scope import anonymous_namespace_spans, internal_linkage
    from tu_scope import match_brace, match_paren, skip_post_declarator
    from tu_scope import declarator_conformance, unshared_declarator_crossings
    from tu_scope import statement_before, pointer_typed, local_copies
    from tu_scope import blocks, innermost_block, writes, source_reads
    from tu_scope import alias_map, alias_set, alias_reads

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
                                        two objects -- and so are two disjoint BLOCKS in
                                        one function, which is why the span is the
                                        declaration's innermost enclosing block and not
                                        the function's body. `innermost_block` is that
                                        one line, shared, because `source_reads`'
                                        shadowing rule needs the same answer.
    FILE    Scope(path)                 internal linkage: `static` in a .c/.cc/.cpp/.cxx.
                                        Another translation unit cannot name it.
    TREE    Scope() == TREE             external linkage, or ANY declaration made in a
                                        header -- the including .c gets a copy.

INTERNAL LINKAGE HAS A SECOND SPELLING AND IT CARRIES NO `static`.
`namespace { VALUE cache; }` gives every name in the block internal linkage, from the
NAMESPACE -- C++ [basic.link]. A scope decision that reads only the declaration text
therefore hands two translation units ONE tree-scoped slot, and predicate C then let one
file's `rb_global_variable(&cache)` discharge the other file's unregistered allocating
one: an over-clear reached through a syntax the `static` grep cannot see. So the linkage
question is `internal_linkage(text, off, anon)` rather than a regex at each call site, and
the anonymous-namespace spans come from the same walk that already knows which braces are
transparent. Both predicates that scope slots ask it -- C for its file-scope units, D for
`file_scope_objects` -- because the rule is the same one twice, not two rules.

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

THE FOURTH RULE, AND IT IS NOT A PARSING RULE: WHICH LOCALS CARRY THE SAME POINTER
---------------------------------------------------------------------------------
The three rules above are lexical -- where a declaration lives, which braces are
scopes, where a declarator ends. This one is intra-procedural dataflow, and it is here
for the same measured reason and not for tidiness: `q = p;` copies a pointer, and a
predicate that tracks only `p` stops looking at the copy.

    predicate D  alias_names/_local_copies                     (round 9, thread D:1186)
    predicate B  escapes_by_return's `aliases`                 (round 9, thread B:716)

Both hosts fail the same way and it is this directory's signature failure: the funnel
counts the derivation, the scan finds no later use, and the row clears. D printed
`derive 1/1 -> windowed 0/0 -> hit 0` on a pointer read after a compaction; B printed
one converted non-cfunc and zero hits on `p = RSTRING_PTR(str); q = p; return q;`. A
recall loss that reads as a clean gem, twice, in two files -- which is the bar this
module's other three rules were extracted at.

The two callers seed it differently and that difference is theirs to keep: D seeds from
ONE derivation offset, B from every in-place conversion of its by-value parameter plus
`RSTRING_GETMEM`'s output argument. So `alias_set` takes the seeds as `{name: offset}`
and answers only the question both share -- which OTHER names now hold the same value.

THREE CONSTRAINTS, EACH FORCED BY A ROW THAT WOULD OTHERWISE HAVE MOVED THE WRONG WAY:

  the left-hand side must be POINTER-TYPED. Without it `c = RSTRING_PTR(s)[i]` makes an
  `int` look like the buffer and every later mention of `c` reads as an escape --
  stringio's `strio_getbyte`, a false positive with a very ordinary spelling.

  arithmetic keeps the pointer, and the BASE MUST BE THE LEFT OPERAND. `q = p + 1`
  points into the same String bytes; `off = e - p` is a ptrdiff_t and cannot dangle.
  `n + p` is legal C and loses recall here, which is the direction to be wrong in.

  a copy extends the set only if it runs AFTER the name it copies became an alias, so
  `r = q; ...; q = p;` does not make `r` an alias of the buffer.

`exclude` is the caller's, because the two disagree about what an assignment to a
pointer PARAMETER means: for B, `*out = p` is the escape it is looking for, so `out`
must not be swallowed as "just another alias" first. D asks about `out` in a different
order and passes nothing.

THE FIFTH RULE, AND THE FOURTH IS ONE OF ITS CALLERS: WHEN DOES AN OCCURRENCE STILL READ
-----------------------------------------------------------------------------------------
`source_reads(body, name, since)` -- moved here from predicate D, where it was already the
unification of three separately-patched defects. Every one of them was the same mistake:

    A BARE TOKEN OCCURRENCE ACCEPTED AS EVIDENCE THAT A NAME STILL HOLDS THE OBJECT IT
    ONCE HELD.

    D:1224  `guard = str; guard = other; ... RB_GC_GUARD(guard)`   a rebound guard alias
    D:1288  `p = ...; consume(p); str = other;`                    a WRITE counted as a read
    D:1614  `consume(p); { VALUE str = other; use(str); }`         a read of a SHADOWING
                                                                   redeclaration

The fourth member arrived one review later and in this file, which is what moved the
predicate down here instead of adding a fourth special case beside it:

    tu_scope  `p = RSTRING_PTR(str); p = "safe"; return p;`        a rebound ALIAS

`alias_set` tracked copies and never kills, so a local stayed in `seen` after it had been
overwritten and predicate B reported RETURNS-INTERIOR on a string literal. It is not a new
rule. It is rule 5 asked of the alias set, so `alias_map` calls `source_reads` twice: once
to decide whether a copy's right-hand side still reads the pointer (`q = p` after
`p = "safe"` makes no alias) and once, in `alias_reads`, to decide whether a LATER
occurrence of an alias still carries it.

THE KILL SET IS SHARED; THE PATH SENSITIVITY IS NOT, AND THAT IS THE WHOLE PARAMETER.
Disqualifier 2 -- "a write completes between `since` and the occurrence" -- is
path-INSENSITIVE, and D's liveness stage can afford that because there a disqualified read
costs a DISCHARGE and the row is reported: the openssl `to_der_internal` shape (a write in
both arms of an if/else, read after the join) over-REPORTS, which is the direction the
predicate is built to fail in. Applied to the alias set the polarity INVERTS. A killed
alias removes an escape and shortens a window, so a write that need not run would DISCHARGE
a live row:

    p = RSTRING_PTR(str);  if (c) { p = "safe"; }  return p;   /* still an escape */

So the caller names the kill it can afford. ANY_WRITE is D's liveness stage, unchanged.
DOMINATING_WRITE keeps only a write whose innermost enclosing block also contains both
`since` and the occurrence -- a cheap dominance test over the same `blocks()` the shadowing
disqualifier already computes -- and is what the alias set asks for. Both settings
over-report; they differ in which caller's polarity that means.

WHICH CALLERS NEED A RULE, AND DO THEY ALL CALL IT
-------------------------------------------------
Four of this module's five rules landed after the same rule had been patched into one host
and not the others, so the module ships two ways of asking that question rather than a
sixth prose warning:

    declarator_conformance(index)      the BEHAVIOURAL check. A caller hands in its own
                                       function index as `src -> {names}`; the accept table
                                       and the rejection table are driven through it and the
                                       mismatches come back. Every function index in this
                                       directory is registered in its own self-test, so an
                                       index that stops crossing `__attribute__((...))` --
                                       or starts inventing a body out of an X-macro list --
                                       fails there rather than on a corpus row nobody reads.

    unshared_declarator_crossings(src) the SOURCE tripwire. The gap has never once announced
                                       itself as a wrong answer; it announces itself as a
                                       hand-rolled `while src[k] in " \\t\\r\\n"` two lines
                                       above a `== "{"`. This finds those, and each sweep
                                       asserts the set it still has against a named
                                       allow-list, so a new one is a decision rather than an
                                       oversight. It is a lint and it is stated as one:
                                       it cannot see an index that never crosses at all.
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


def anonymous_namespace_spans(src):
    """[(open_brace, close_brace)] for every `namespace {` body, nested ones included.

    An ANONYMOUS namespace gives every name declared in it internal linkage, with no
    `static` anywhere in the declaration. A NAMED one does not, so the two cannot share a
    test even though `top_level_units` walks through both identically.
    """
    out = []
    i, start, n = 0, 0, len(src)
    while i < n:
        c = src[i]
        if c == "{":
            close = match_brace(src, i)
            if close < 0:
                return out
            pre = src[start:i].rstrip()
            if NAMESPACE_HEAD.search(pre) or LINKAGE_HEAD.search(pre):
                if ANON_NAMESPACE_HEAD.search(pre):
                    out.append((i, close))
                out.extend((i + 1 + a, i + 1 + b)
                           for a, b in anonymous_namespace_spans(src[i + 1:close]))
            i = start = close + 1
            continue
        if c == ";":
            i = start = i + 1
            continue
        i += 1
    return out


def internal_linkage(decl_text, off=None, anon=()):
    """Does a declaration have internal linkage? C's rule, both spellings.

    `decl_text` is the declaration's specifiers (the caller cuts the initialiser off, or an
    initialiser mentioning `static` in a nested expression flips the answer). `off` and
    `anon` add the C++ half: a declaration inside an anonymous namespace is internal
    whatever its specifiers say. Passing neither asks the C-only question, which is what a
    caller holding no offset can honestly ask.
    """
    if re.search(r"\bstatic\b", decl_text or ""):
        return True
    return off is not None and any(o < off < c for o, c in anon)


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
# ...and the one spelling of it that changes LINKAGE rather than only nesting: an
# anonymous namespace, which is `namespace` with nothing between it and the `{`.
ANON_NAMESPACE_HEAD = re.compile(r"\bnamespace\s*$")


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
_QUAL_NAME = re.compile(r"[A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)*")


def _skip_template_args(src, k):
    """Past a balanced `<...>` template-argument list starting at `<`, or -1.

    `Derived() : Base<T>()` -- a member-initialiser names a TYPE, and a type may be a
    template-id, which `_QUAL_NAME` does not spell (found by Codex on the #30 review).
    Nested `>>` needs no special case because each `>` decrements the depth on its own.

    Bailing on `;`, `{` or `}` is what keeps a plain comparison from eating the rest of the
    file: `a < b` never closes, and this returns -1, which `_skip_member_init` treats as a
    reject exactly as it treats every other failure.
    """
    depth, n = 0, len(src)
    while k < n:
        c = src[k]
        if c == "<":
            depth += 1
        elif c == ">":
            depth -= 1
            if depth == 0:
                return k + 1
        elif c in ";{}":
            return -1
        k += 1
    return -1


def _skip_member_init(src, k):
    """Past a constructor's member-initialiser list starting at the `:` at `k`, or -1.

    `Foo::Foo(int x) : a(x), b(Frame{0, 0}) {`. Listed in this module's docstring as STILL
    UNHANDLED until predicate A needed it: A indexes C++ class-body methods, and vernier's
    BaseCollector -- the tree that forced the whole class-scope walk -- declares its
    constructor this way. It is opt-in (`ctor_init`) rather than free, because a
    constructor is a definition B, C and D have never indexed and turning it on for them
    moves their function counts for no predicate's benefit.

    Each item is a (possibly qualified) name followed by a MATCHED group, and the list ends
    at the first item not followed by a comma. Failing is a reject: the alternative the
    hand-rolled version used -- jump to the next `{` in the file -- reads `c ? f(a) : g(b)`
    as an initialiser list and hands the walk some later function's body.
    """
    n = len(src)
    k += 1
    while True:
        while k < n and src[k] in " \t\r\n":
            k += 1
        m = _QUAL_NAME.match(src, k)
        if not m:
            return -1
        k = m.end()
        while k < n and src[k] in " \t\r\n":
            k += 1
        if k < n and src[k] == "<":
            k = _skip_template_args(src, k)
            if k < 0:
                return -1
            while k < n and src[k] in " \t\r\n":
                k += 1
        if k < n and src[k] == "(":
            close = match_paren(src, k)
        elif k < n and src[k] == "{":
            close = match_brace(src, k)
        else:
            return -1
        if close < 0:
            return -1
        k = close + 1
        while k < n and src[k] in " \t\r\n":
            k += 1
        if k < n and src[k] == ",":
            k += 1
            continue
        return k


def skip_post_declarator(src, k, ctor_init=False):
    """Advance past the attributes, specifiers and trailing return type between `)` and `{`.

    Returns the first offset the walk will not consume; a caller accepts the definition
    only if that offset holds the `{`. Stopping early is therefore always a REJECT, which
    is the recall-losing direction and the one this function is allowed to be wrong in.

    `ctor_init` additionally crosses a constructor's member-initialiser list. Only the
    caller that indexes C++ class bodies asks for it; see _skip_member_init.
    """
    n = len(src)
    while k < n:
        while k < n and src[k] in " \t\r\n":
            k += 1
        if k >= n:
            return k
        if ctor_init and src[k] == ":" and not src.startswith("::", k):
            j = _skip_member_init(src, k)
            return j if j >= 0 else k
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


# ------------------------------------------------- do all the callers use the walk above
#
# The accept table is the four spellings that have each emptied an index once, plus the two
# that arrive together; the rejection table is skip_post_declarator's own, verbatim from the
# module docstring. Every case is one definition named `bad` with one decoy in front of it,
# so a caller's index answers with a set of names and nothing else has to be agreed on.
_ACCEPT = {
    "plain":     "static const char *bad(VALUE str)\n{\n    return 0;\n}\n",
    "attr":      "static const char *bad(VALUE str) __attribute__((noinline))\n"
                 "{\n    return 0;\n}\n",
    "attr-same-line": "static const char *bad(VALUE str) __attribute__((noinline)) "
                      "{\n    return 0;\n}\n",
    "noexcept":  "static const char *bad(VALUE str) noexcept\n{\n    return 0;\n}\n",
    "bare-macro": "static const char *bad(VALUE str) EV_NOEXCEPT\n{\n    return 0;\n}\n",
    "trailing":  "static auto bad(VALUE str) -> const char *\n{\n    return 0;\n}\n",
    "attr-noexcept": "static const char *bad(VALUE str) __attribute__((noinline)) noexcept\n"
                     "{\n    return 0;\n}\n",
}
_REJECT = {
    "macro":     ("MY_EXPORT(sym)\nstatic const char *bad(VALUE str)\n"
                  "{\n    return 0;\n}\n", ("bad",)),
    "knr":       ("static const char *bad(str) VALUE str;\n{\n    return 0;\n}\n", ()),
    "proto":     ("static const char *helper(VALUE);\nstatic const char *bad(VALUE str)\n"
                  "{\n    return 0;\n}\n", ("bad",)),
    "init":      ("struct S s = mk(1), t = {2};\nstatic const char *bad(VALUE str)\n"
                  "{\n    return 0;\n}\n", ("bad",)),
    "typedef-aggregate": ("__declspec(align(8)) typedef struct { int x; } thing_t;\n"
                          "static const char *bad(VALUE str)\n{\n    return 0;\n}\n",
                          ("bad",)),
    "x-macro":   ("XX(A, 1)\ntypedef enum { E_A } phase_t;\n"
                  "static const char *bad(VALUE str)\n{\n    return 0;\n}\n", ("bad",)),
}


def declarator_cases():
    """{tag: (source, frozenset of names a conforming index returns)}.

    K&R indexes NOTHING -- the stated recall limit, shared by all four predicates, and it is
    asserted rather than tolerated so that a walk which starts accepting it says so.
    """
    out = {t: (s, frozenset(("bad",))) for t, s in _ACCEPT.items()}
    out.update({t: (s, frozenset(w)) for t, (s, w) in _REJECT.items()})
    return out


def declarator_conformance(index, prologue="#include <ruby.h>\n\n"):
    """{tag: (got, want)} for every case `index` disagrees with. Empty means conforming.

    `index` is the caller's OWN function index, as `src -> iterable of indexed names`. This
    is the check that answers "which callers need this rule and do they all call it" for
    rule 3: the four sweeps between them carry six function indexes, and five of the six
    were patched separately for the same gap before the walk was extracted.
    """
    bad = {}
    for tag, (src, want) in sorted(declarator_cases().items()):
        got = frozenset(index(prologue + src))
        if got != want:
            bad[tag] = (sorted(got), sorted(want))
    return bad


# The gap has never once presented as a wrong answer -- it presents as these two lines.
_WS_SKIP = re.compile(r'in " \\t\\r\\n"')
_BRACE_TEST = re.compile(r'==\s*"\{"')


def unshared_declarator_crossings(source, window=500):
    """Line numbers in a caller's own source that hand-roll the `)`-to-`{` crossing.

    A lint, and stated as one: it recognises the shape all six historical appearances had --
    a whitespace-only skip loop whose next decision is `== "{"` -- and it cannot see an index
    that crosses some other way or does not cross at all. Callers assert the result against a
    named allow-list, so a walk that stays hand-rolled is a decision with a reason next to it
    rather than the thing nobody noticed.
    """
    out = []
    for m in _WS_SKIP.finditer(source):
        if _BRACE_TEST.search(source[m.end():m.end() + window]):
            out.append(source.count("\n", 0, m.start()) + 1)
    return out


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


# ------------------------------------------------- which locals carry the same pointer
#
# `p`, or `p + n` / `p - n` / `p++` -- a base name with pointer arithmetic on it. The base
# comes FIRST, because `n + p` is legal C and `e - p` is not a pointer at all; see the
# module docstring for why that asymmetry is the whole test.
COPY_RHS = re.compile(r"([A-Za-z_]\w*)\s*(?:[-+][^;]*)?$")
_ASSIGN = re.compile(r"(?<![=!<>+\-*/%&|^])=(?!=)")
_LHS_TAIL = re.compile(r"(?:^|[\s*(])([A-Za-z_]\w*)\s*$")
_CAST_HEAD = re.compile(r"^\(\s*[A-Za-z_][\w\s*]*\)\s*")


def statement_before(body, rel):
    """The text of the partial statement ending at `rel`. Used to find an assignment."""
    lhs = body[max(0, rel - 400):rel]
    for cut in (";", "{", "}", ","):
        lhs = lhs[lhs.rfind(cut) + 1:]
    return lhs


def pointer_typed(body, name, upto):
    """Was `name` declared a pointer at or before `upto`?

    Either the assignment being read IS the declaration (`char *p = ...`), or a `*name`
    declarator appeared earlier in the frame.
    """
    stmt = statement_before(body, upto)
    return "*" in stmt or bool(re.search(r"\*\s*%s\b" % re.escape(name), body[:upto]))


def local_copies(body, after=0):
    """[(off, lhs, rhs_base)] for every `lhs = rhs;` at or after `after` that copies one name.

    Casts and redundant parentheses are stripped from the right-hand side: `q = (const char
    *)p;` is the same copy as `q = p;` and matching only the bare spelling loses it.
    """
    out = []
    for m in _ASSIGN.finditer(body):
        if m.start() < after:
            continue
        stmt = statement_before(body, m.start())
        d = _LHS_TAIL.search(stmt)
        semi = body.find(";", m.end())
        if not d or semi < 0:
            continue
        rhs = body[m.end():semi].strip()
        while True:
            stripped = _CAST_HEAD.sub("", rhs).strip()
            if stripped == rhs:
                break
            rhs = stripped
        base = COPY_RHS.fullmatch(rhs)
        if base and pointer_typed(body, d.group(1), m.start()):
            out.append((m.start(), d.group(1), base.group(1)))
    return out


# --------------------------------------------- when does an occurrence still READ the name
#
# Rule 5. Moved here from predicate D, where it was already the unification of three
# separately-patched defects; the fourth was in this file's own alias set, which is why it
# lives beside rule 4 rather than above it. The module docstring carries the family.
ANY_WRITE = "any-write"
DOMINATING_WRITE = "dominating-write"

# A declaration statement leads with a type, and nothing else in C leads with a bare
# identifier sequence. The prefix between the last statement boundary and the name may
# therefore hold only identifiers, `*`, `&`, and the commas and brackets of a declarator
# list -- one `(`, one `=` or one operator and it is an expression, not a declaration.
DECL_PREFIX = re.compile(r"^[\s\w*&,\[\]]+$")
# ...unless the leading word is one of these, which are the statement keywords that can be
# followed by a bare name. `return str;` is the one that would otherwise read as a
# declaration of `str`, and reading it as one would suppress every later use.
NOT_DECL_LEAD = frozenset(("return", "case", "goto", "else", "do", "break", "continue",
                           "default", "sizeof", "typedef"))


def writes(body, name):
    """Offsets of every plain assignment `name = ...` -- the WRITES, not the reads.

    A COMPOUND assignment is a read: `x += y` loads `x` first, so `\\s*=` deliberately does
    not match `+=`. `&x` is a read too, and a load-bearing one -- taking the address forces
    a stack slot, which is the second pin this skill measured on `StringValueCStr` -- and it
    cannot be the left-hand side of an assignment, so it is never matched here.

    A MEMBER OF THE SAME NAME IS NOT THIS NAME. `parser->state.start = start;` was counted
    as a write to the local `start` -- `>` was already in the lookbehind, so the `->`
    spelling was excluded and the `.` spelling was not, which is one character between the
    two halves of the same rule. Under ANY_WRITE that suppressed a genuine later read and
    over-reported; under DOMINATING_WRITE it killed the alias and dropped two real
    ESCAPES-INTO-CONTAINER rows in json's cResumableParser_feed. Found on the corpus.

    One of the three disqualifiers in source_reads(), which is the only caller that matters.
    """
    return [m.start() for m in
            re.finditer(r"(?<![=!<>+\-*/%%&|^.])\b%s\s*=(?!=)" % re.escape(name), body)]


def blocks(body):
    """[(open, close)] byte offsets for every brace-delimited block in `body`."""
    stack, out = [], []
    for m in re.finditer(r"[{}]", body):
        if m.group() == "{":
            stack.append(m.start())
        elif stack:
            out.append((stack.pop(), m.start()))
    return out


def innermost_block(body, off, within=None):
    """The narrowest `(open, close)` in `body` containing `off`, or None.

    `within` pre-computes `blocks(body)` for a caller asking about many offsets. This is
    both the region a shadowing declaration owns and -- item 3 of the follow-up -- the
    STORAGE SCOPE of a block-local `static`: two disjoint blocks in one function each
    declaring `static VALUE cache` are two objects, and the enclosing function's span
    merges them into one.
    """
    encl = [(o, c) for o, c in (blocks(body) if within is None else within) if o <= off < c]
    return min(encl, key=lambda oc: oc[1] - oc[0]) if encl else None


_BARE_ARM = re.compile(r"\b(?:else|do)\s*$")
_ARM_HEAD = re.compile(r"\b(if|for|while|switch)\s*$")
# A conditional OPERATOR guarding a write that has no conditional STATEMENT around it:
# `len && (p = x);`, `len || (p = x);`, `len ? (p = x) : 0;`. See conditional_stmt.
_COND_OP = re.compile(r"&&|\|\||\?")


def conditional_stmt(body, off):
    """Is the statement containing `off` the BRACELESS arm of a conditional?

        if (!a1obj) a1obj = OBJ_txt2obj(RSTRING_PTR(obj), 1);

    Found on the corpus, and it is the hole a brace-counting dominance test has by
    construction: this write has no block of its own, so its innermost enclosing block is
    the whole function and it reads as unconditional. openssl's `obj_to_asn1obj` is the
    shape -- two RETURNS-INTERIOR rows in four trees discharged on a write that runs only
    when the first call failed. Anything not recognised here stays UNCONDITIONAL, which is
    the direction that kills; the recogniser is therefore kept to the spellings that cannot
    be anything else: `else`/`do`, a `)` closing an `if`/`for`/`while`/`switch`, and a
    conditional OPERATOR earlier in the same statement.

    THE OPERATOR CASE IS A STATEMENT WITH NO CONDITIONAL STATEMENT IN IT (found by Codex on
    the #30 review, the fourth hole in this same test). A write can be guarded by `&&`,
    `||` or `?:` without any `if` at all:

        len && (p = "safe");            /* runs only when len is non-zero */
        len ? (p = "safe") : 0;

    Neither has a block, so the block test passes it; neither contains a transfer token, so
    `straight_line` passes it; and its head ends in `(` rather than `)`, so the arm test
    above passed it too. All three agreed it dominates, and on the `len == 0` path `p` still
    carries the interior -- measured, both spellings silently drop rmagick's
    RETURNS-INTERIOR row.

    Reading it as "any `&&`/`||`/`?` earlier in the statement" over-reports: a comma
    expression like `foo(a && b), p = x;` is not guarded and is called conditional anyway.
    That is the safe direction here and deliberately so -- an unrecognised guard KILLS a
    live row, an over-recognised one merely keeps a row a human then reads.
    """
    pre = body[:off]
    head = pre[max(pre.rfind(";"), pre.rfind("{"), pre.rfind("}")) + 1:].rstrip()
    if _BARE_ARM.search(head):
        return True
    if _COND_OP.search(head):
        return True
    if not head.endswith(")"):
        return False
    depth = 0
    for i in range(len(head) - 1, -1, -1):
        if head[i] == ")":
            depth += 1
        elif head[i] == "(":
            depth -= 1
            if depth == 0:
                return bool(_ARM_HEAD.search(head[:i]))
    return False


_TRANSFER = re.compile(r"[{}]|\b(?:break|continue|goto|return|case|default)\b")


def straight_line(body, a, b):
    """Does control reach `b` from `a` with no statement-level transfer in between?

    The second hole a brace-counting dominance test has, and the corpus named it twice in
    one run. Two arms of a `switch` share ONE pair of braces:

        case 0:  ptr = StringValuePtr(str);  ...  break;
        case 2:  ptr = StringValuePtr(str);  ...  break;

    so case 2's write sits in the same innermost block as case 0's derivation and reads as
    unconditional -- openssl's `ossl_bn.c` initialize and yajl's `yajl_encode_part` are the
    two, and both lost a window that way. A `break`, `continue`, `goto`, `return` or a new
    `case`/`default` label at the traversed depth says control need not arrive.

    DEPTH IS RELATIVE AND MAY GO NEGATIVE, which is the whole subtlety: `a` is often nested
    deeper than `b` (bigdecimal derives inside two `if`s and rewrites the pointer after
    both), and LEAVING a block on the way out is not a transfer. A token counts only at
    depth <= 0 -- its own level or an enclosing one. A `break` inside a nested loop between
    the two is at depth >= 1 and is correctly ignored.
    """
    depth = 0
    for m in _TRANSFER.finditer(body, a, b):
        t = m.group()
        if t == "{":
            depth += 1
        elif t == "}":
            depth -= 1
        elif depth <= 0:
            return False
    return True


def self_derived(body, off):
    """Does the write at `off` re-derive the name from ITSELF? `p = p + 1`.

    The third hole in the alias kill, and the one that is not about control flow at all --
    the other two ask WHETHER the write runs, this asks what the write STORES. A pointer
    walk is the commonest thing C does to an interior pointer:

        p = RSTRING_PTR(str);
        p = p + 1;                /* or p++, p = strchr(p, '/'), p += n */
        return p;                 /* still the String's interior */

    `writes()` sees a plain `p =` and disqualifier 2 drops every later occurrence, so the
    alias dies at the walk and predicate B's RETURNS-INTERIOR on rmagick's `rm_str2cstr`
    goes from RED to a clean sheet. Measured against `54fc3f2`: the same tree reports the
    row before this rule and not after it, while `p = "safe"` -- item 1's whole purpose --
    stays correctly killed in both.

    THE TEST IS THE RIGHT-HAND SIDE, not the operator, because the shapes that matter spell
    the walk five different ways and only one of them is a compound assignment (`+=`, which
    `writes()` already excludes as a read). Reading the name anywhere between the `=` and
    the `;` means the stored value was computed FROM the pointer, so the name still carries
    an interior pointer of the same object and the alias survives.

    IT OVER-REPORTS, DELIBERATELY, and that is the side this module fails on: `p = f(p)`
    where `f` returns something unrelated keeps an alias that no longer carries. Under
    DOMINATING_WRITE a missed kill REPORTS a row a human then reads; a wrongful kill
    DISCHARGES one silently, which is how this defect shipped. Under ANY_WRITE the claim is
    the plain one -- the occurrence after a self-derived write really does still read an
    interior pointer of the source object -- so the rule is not mode-specific.

    THE RIGHT-HAND SIDE IS AN EXPRESSION, NOT A LINE, and bounding it by the next `;` is
    wrong in the one shape this corpus is full of. trilogy's connect option block spells
    every write as an assignment INSIDE a condition:

        if ((val = rb_hash_aref(opts, ID2SYM(id_username))) != Qnil) {
            connopt.username = StringValueCStr(val);        <- the first `;` is HERE

    so a `;`-bounded right-hand side swallows the block's first statement and finds `val`
    in it, calling every one of those writes self-derived. It cost nothing in rows -- the
    twelve trilogy rows stayed discharged either way -- but each one then named the WRONG
    `RB_GC_GUARD`, because a stale alias reached the guard scan first. A discharge that
    cites the wrong reason is the failure `-v` exists to catch, so the bound is the
    assignment expression: stop at the `;`, at a `,` outside parentheses, or at the `)`
    that closes a parenthesis this right-hand side never opened.

    String and character literals are already blanked by each sweep's `strip_noise` before
    a body reaches here, so `p = "p"` cannot match its own name inside the literal.
    """
    eq = body.find("=", off)
    if eq < 0:
        return False
    name = re.match(r"\s*([A-Za-z_]\w*)", body[off:])
    if not name:
        return False
    depth, end = 0, len(body)
    for i in range(eq + 1, len(body)):
        c = body[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                end = i
                break
            depth -= 1
        elif depth == 0 and c in ";,":
            end = i
            break
    return re.search(r"\b%s\b" % re.escape(name.group(1)), body[eq + 1:end]) is not None


def source_reads(body, name, since, kill=ANY_WRITE):
    """Offsets of the occurrences of `name` that READ the object it held at `since`.

    ONE ROOT CAUSE, FOUR SYMPTOMS, ONE PREDICATE. Every discharge defect predicate D's
    liveness stage shipped, and the one this module's own alias set shipped, was the same
    mistake: a rule took a BARE TOKEN OCCURRENCE of a name as evidence that the name still
    held the object the derivation came from, without asking what that occurrence actually
    did. Four reviews found four spellings of it:

      `guarded`         `guard = str; guard = other; ... RB_GC_GUARD(guard)`
                        the occurrence is a genuine read of a genuine variable -- of the
                        WRONG OBJECT, because a write rebound the name in between.
      `last-use-after`  `p = RSTRING_PTR(str); rb_funcall(...); consume(p); str = other;`
                        the occurrence is not a read at all. An assignment's left-hand side
                        neither loads the old VALUE nor leaves it anywhere conservative
                        scanning can find it, so the frame may drop it at `consume`.
      `last-use-after`  `... consume(p); { VALUE str = other; use(str); }`
                        the occurrence is a genuine read of the wrong VARIABLE -- an inner
                        declaration shadows the source, and the outer `str` is dead.
      `alias_set`       `p = RSTRING_PTR(str); p = "safe"; return p;`
                        the occurrence reads a name that WAS an alias of the interior and
                        is not one any more. Predicate B reported RETURNS-INTERIOR on a
                        string literal.

    The first three DISCHARGE, so they lose findings rather than add noise. The fourth is
    the mirror -- it REPORTS -- and that difference is the `kill` argument, not a second
    predicate; see the module docstring. Three disqualifiers, in the order the shapes name
    them:

      1. the occurrence IS the write (`name =`, never `+=`, never `==`)
      2. a write to `name` COMPLETES between `since` and the occurrence, in either
         direction -- whatever the occurrence reads, it is not what was live at `since`,
         UNLESS the write re-derives the name from itself (`p = p + 1`), which stores the
         pointer back and is `self_derived` above
      3. an inner block declares `name`, contains the occurrence and does NOT contain
         `since` -- two variables, one spelling

    "COMPLETES" is load-bearing in 2 and was learned on the corpus. A write takes effect at
    the `;`, not at its left-hand side: in iconv's `val = rb_str_subseq(val, 0, slash-ptr);`
    the `val` in the ARGUMENT list is textually after the write's LHS token and is a genuine
    read of the object still live at the derivation. Measuring the write by its LHS offset
    disqualified it and raised a row on a correct site -- over-reporting, so cheap, but
    wrong, and it would have been read as a finding.

    DISQUALIFIER 2 IS PATH-INSENSITIVE UNDER `ANY_WRITE`, the same blind spot the window
    scan states. openssl's `to_der_internal` writes `str` in BOTH arms of an if/else and
    reads it after the join, so the else-arm's write disqualifies the then-arm's read
    although the two cannot both run. That direction over-REPORTS for D's liveness stage,
    which is the side it is built to fail on. `DOMINATING_WRITE` is the same disqualifier
    for a caller whose polarity is inverted -- the alias set, where a kill DISCHARGES: a
    write counts only when its innermost enclosing block also contains `since` and the
    occurrence, so `if (c) { p = "safe"; } return p;` keeps the alias. Both settings
    over-report. They disagree about whose over-report it is.

    `since` is the offset at which `name` provably held the object: the derivation, for the
    source; the copying assignment, for a guard alias. Disqualifier 2 is stated in both
    directions because callers ask about occurrences on both sides of it.

    THE RULE THIS DOES NOT IMPLEMENT is the compiler's. Even a qualifying read is only
    evidence that the SOURCE TEXT mentions the VALUE again; an optimised build may reuse
    the slot before its last syntactic use, which is RB_GC_GUARD's own documented
    rationale and the reason liveness() calls itself recall-biased. This narrows an
    over-clear; it does not make `last-use-after` sound.
    """
    wr = writes(body, name)
    # Where each write takes EFFECT: the `;` that ends the assignment statement, so the
    # right-hand side of the write itself still reads the old object.
    done = [(w, body.find(";", w) if body.find(";", w) >= 0 else len(body)) for w in wr]
    bl = blocks(body)
    # The region each shadowing declaration owns: its INNERMOST enclosing block, kept only
    # when that block does not also contain `since`. Innermost and not merely "some
    # enclosing block", or an inner redeclaration would suppress reads of the outer
    # variable elsewhere in the same `if` -- over-reporting rather than over-clearing, but
    # wrong, and the two directions are not interchangeable just because one is cheaper.
    shadow = []
    for d in _declarations(body, name):
        b = innermost_block(body, d, bl)
        if b is None:
            continue                      # declared at the frame's own top level
        o, c = b
        if not o <= since < c:
            shadow.append((d, o, c))
    out = []
    for m in re.finditer(r"\b%s\b" % re.escape(name), body):
        at = m.start()
        if at in wr:
            continue
        lo, hi = (at, since) if at < since else (since, at)
        # Strictly after `since`, and complete by the occurrence. The strict bound is what
        # keeps a guard alias's OWN defining write (`guard = str;`, whose offset IS the
        # `since` it establishes) from disqualifying every later read of the alias.
        killed = False
        for w, end in done:
            if not (lo < w and end <= hi):
                continue
            if self_derived(body, w):
                continue      # a pointer walk stores the pointer back into the name
            if kill == DOMINATING_WRITE:
                b = innermost_block(body, w, bl)
                if b is not None and not (b[0] <= since < b[1] and b[0] <= at < b[1]):
                    continue      # a write that need not run cannot discharge a live row
                if conditional_stmt(body, w):
                    continue      # ...and a braceless arm has no block to be found in
                if not straight_line(body, lo, w):
                    continue      # ...and two switch arms share one pair of braces
            killed = True
            break
        if killed:
            continue
        if any(d < at and o <= at < c for d, o, c in shadow):
            continue
        out.append(at)
    return out


def _declarations(body, name):
    """Offsets where `name` is DECLARED, as opposed to used.

    Best-effort and deliberately under-inclusive: a shape this misses leaves the old
    behaviour in place, and the old behaviour over-clears, so the bias is stated rather
    than assumed. Known miss: a declaration split across a macro.
    """
    out = []
    for m in re.finditer(r"\b%s\b" % re.escape(name), body):
        pre = body[:m.start()]
        stmt = pre[max(pre.rfind(";"), pre.rfind("{"), pre.rfind("}")) + 1:]
        if not DECL_PREFIX.match(stmt):
            continue
        words = re.findall(r"[A-Za-z_]\w*", stmt)
        if not words or words[0] in NOT_DECL_LEAD:
            continue
        out.append(m.start())
    return out


def alias_map(body, seeds, exclude=()):
    """{name: offset} -- every local carrying the pointer, and where it started to.

    `seeds` is `{name: offset}`, the offset at which each name became an alias.

    A COPY ONLY PROPAGATES FROM A NAME THAT STILL HOLDS THE POINTER. The transitive closure
    used to ask only that the copy run after the name it copies became an alias, which is
    rule 4's ordering constraint and not the whole question: after `p = "safe";` a later
    `q = p;` copies the literal, not the interior. That is rule 5, asked of the right-hand
    side, with the kill mode the alias set's polarity requires.
    """
    if not seeds:
        return {}
    copies = local_copies(body, min(seeds.values()))
    exclude = set(exclude)
    seen = dict(seeds)
    frontier = sorted(seeds.items(), key=lambda kv: kv[1])
    while frontier:
        cur, since = frontier.pop()
        live = source_reads(body, cur, since, kill=DOMINATING_WRITE)
        for off, lhs, rhs in copies:
            if rhs != cur or off <= since or lhs in seen or lhs in exclude:
                continue
            semi = body.find(";", off)
            if semi < 0:
                semi = len(body)
            if not any(off < r < semi for r in live):
                continue        # the right-hand side no longer reads the pointer
            seen[lhs] = off
            frontier.append((lhs, off))
    return seen


def alias_set(body, seeds, exclude=()):
    """Every local in `body` that carries the same pointer as one of `seeds`.

    The names in offset order, seeds included; an empty `seeds` yields an empty list, which
    every caller must treat as "no alias", never as "any name". A caller that then looks for
    OCCURRENCES of these names wants `alias_reads` instead -- a name in this set has held
    the pointer, which is not the same claim as "every mention of it reads the pointer".
    """
    m = alias_map(body, seeds, exclude)
    return sorted(m, key=lambda n: m[n])


def alias_reads(body, seeds, exclude=()):
    """Offsets of every occurrence in `body` that still evaluates to the seeded pointer.

    This is the answer both callers actually want. `alias_set` names the carriers;
    `p = "safe"` leaves `p` a carrier that is no longer carrying, and a caller matching the
    NAME reports the string literal as an escaping interior pointer -- which is what
    predicate B did. The kill is rule 5, not a special case beside it.
    """
    m = alias_map(body, seeds, exclude)
    out = set()
    for nm, since in m.items():
        out.update(source_reads(body, nm, since, kill=DOMINATING_WRITE))
    return out


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

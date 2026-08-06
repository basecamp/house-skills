#!/usr/bin/env python3
"""Pass-1 sweep: a VALUE field of a GC-managed struct that the type's dmark forgets.

This is the checkable invariant behind round 3's worst bug -- mysql2's `fieldTypes`, a
VALUE field of an xmalloc'd wrapper struct that `rb_mysql_result_mark` never marked. The
struct is malloc'd, so the collector neither scans nor updates it: every VALUE in it has
to be marked by hand, and one that isn't is freed by ORDINARY GC, no compaction needed.

    python3 sweep_unmarked.py <gem-dir> [<gem-dir> ...]

One gem tree per argument. Resolution is whole-tree, so a struct declared in a .h and
marked in a .c resolves correctly -- the v1 script globbed only *.c/*.cpp and therefore
never saw `mysql2_result_wrapper` in result.h, returning identical output for the patched
and unpatched trees. That is the failure this rewrite exists to fix.

DESIGN: START FROM THE WRAPPER, NOT THE STRUCT
----------------------------------------------
v1 enumerated every struct in the file, which reported three stack-local argument structs
(`nogvl_send_query_args` and friends) -- the discriminator's textbook safe case, and pure
false positives. A struct that is never wrapped is not GC-managed and is out of scope by
construction, so this walks:

    TypedData_Make_Struct / _Wrap_Struct / _Get_Struct / Data_Make_Struct / ...
      -> the rb_data_type_t it names
        -> that type's .dmark / .dcompact
          -> resolved across the whole tree, one level of in-tree callees deep

and only then enumerates the wrapped struct's VALUE fields. The three false positives drop
out for the right reason: nothing wraps them.

Recall-biased (truffle-hunt pass 1): it over-reports, and pass 2 applies the discriminator
by hand. To keep the recall auditable rather than assumed it prints, on stderr, every field
it CLEARED and why -- an over-clear is the failure mode that makes a broken gem look safe,
so the clears are the part worth reading.

CATEGORIES
  UNMARKED   field named in no mark function        -- freed by ordinary GC (mysql2 shape)
  MENTIONED  field named in the dmark body but not inside any marking call -- the round-5
             (b) case; see below. Not a clear, and not a claim: pass 2 decides
  NO-COMPACT field marked movable, absent from dcompact -- stale after compaction only
             (openssl#1088 shape)
  NO-COMPACT-UNKNOWN  same, but the movable mark was reached through a helper, so which
             field the primitive applied to is not resolved here. Conservative by design
  VALUE*     a VALUE array/pointer field; needs a marking loop, check the bound by hand

ROUND 6, DEFECT B1: A MEMBER CALL RESOLVED BY FILE ORDER
--------------------------------------------------------
Callee bodies were indexed by BARE NAME, first-wins, so in a C++ tree `collector->mark()`
bound to whichever `mark()` body `rglob` happened to return first. vernier 1.10.1 declares
FIVE of them -- Thread (EMPTY), ThreadTable, BaseCollector, TimeCollector, HeapTracker --
and the script reported `stack_table_value` UNMARKED on two separate structs while
`BaseCollector::mark` and `HeapTracker::mark` each mark it with a plain `rb_gc_mark`. Two
false positives on the safest field in the gem.

A second half made it worse and hid the first: `find_calls` guarded on `if args:`, and a
zero-argument call has an EMPTY argument list, so `collector->mark()` was not merely
mis-resolved -- it was never walked at all. vernier's entire marking path is that shape.

Resolution is now by (class, method):

    class Foo : public Bar { ... }        base-clause parsed; a method not on Foo is
                                          looked up on Bar, breadth-first
    BaseCollector *c = ...; c->mark()     locals, parameters, `auto x = static_cast<T*>`
                                          and data members type the receiver
    this->mark() / mark()                 the enclosing class, then its bases

File-order first-wins survives ONLY where the receiver type is genuinely undeterminable
-- `thread->mark()` over a `std::unique_ptr` in a range-for is the corpus case -- and each
such pick is COUNTED into `tree.ambiguous` and printed, because an arbitrary pick that
nothing counts is round-5 (a) all over again: a verdict decided by iteration order,
invisibly. vernier falls back three times, on `clear`, `size` and `lock`; none of them
marks anything.

Resolution is by DECLARED type, not by dispatch: `collector->mark()` walks
`BaseCollector::mark` and not the `TimeCollector::mark` override that may actually run.
For vernier both mark `stack_table_value`, so the clear holds under either -- but a
derived override that DROPPED a mark its base performs would be an over-clear this pass
cannot see. Checked by hand on the one tree it applies to.

The acceptance fixture is a green and a red at once, on one tree: `stack_table_value` must
STOP being reported and `start_thread` must KEEP being reported. A fix that resolved the
receiver by crediting everything the call touches passes the first half and fails the
second.

ROUND 6, PREDICATE A: A SEVERITY COLUMN ON THE UNMARKED ROWS
------------------------------------------------------------
The categories above say which rows are suspects; they do not rank them, and the base sweep
reports stackprof's five undifferentiated. Predicate A appends a grade to UNMARKED and
MENTIONED rows and NOTHING ELSE: it ADDS A COLUMN, NEVER A ROW. `grade_suspects` asserts
its keys are a subset of the suspects, `--verify-column` re-sweeps each tree from a fresh
Tree and compares the (struct, field) sets, and the self-test runs that comparison over
every fixture tree. `--no-grade` turns the column off.

The instance is stackprof `_stackprof.interval` (tmm1/stackprof#244). `NUM2INT` falls
through to `rb_to_int`, which converts a TEMPORARY; the gem then stores the UNCONVERTED
ORIGINAL, so the unmarked field points at a heap object nobody marked whenever the caller
passed a Rational, a Complex, or any `#to_int` duck type. Measured on 0.2.28:

  UNMARKED/HEAP-IF-COERCED   interval          NUM2INT @:213, NUM2UINT @:238, store @:251
  UNMARKED/IMMEDIATE-ONLY    mode              only ever the four static symbols
  UNMARKED/REGISTERED        empty_string      rb_global_variable @:1007
  UNMARKED/REGISTERED        fake_frame_names  rb_global_variable @:1011
  VALUE*                     frames_buffer     not gradable; it IS marked

Four refinements the corpus forced, each with a generated red in --self-test, and each one
a downgrade a plausible first cut grants and should not:

  INT2NUM is NOT immediate. rb_int2num_inline returns RB_INT2FIX only when RB_FIXABLE and
  rb_int2big otherwise (ruby/internal/arithmetic/int.h:239); LONG2NUM the same (long.h:308).
  INT2FIX and LONG2FIX are immediate; DBL2NUM is excluded because flonums are conditional.

  A DYNAMIC symbol is a heap object, but the discriminator is the INTERNING FUNCTION, not
  rb_intern-vs-rb_intern_str. ruby/internal/symbol.h documents rb_intern, rb_intern2,
  rb_intern_str and rb_to_id as producing STATIC symbols -- "would never be garbage
  collected" -- and only rb_to_symbol (:226) as producing dynamic ones. So
  `ID2SYM(rb_intern_str(x))` IS immediate and `ID2SYM(rb_to_symbol(x))` is not.

  Check_Type(a, T_STRING) ASSERTS a type; it does not convert one, so it is neither a
  downgrade nor coercion evidence.

  REGISTERED is a DOWNGRADE, NOT A CLEAR -- registration is per-slot (round 4 measured
  stackprof's registered empty_string pinned while its unregistered sibling objtracer was
  not), and clearing would delete a row, which this pass is forbidden to do.

And two precedence rules that are the design, not a detail. An INT2FIX-only field read back
through NUM2LONG is IMMEDIATE-ONLY, not HEAP-IF-COERCED: coercing a field that cannot hold
a heap object is not a defect. And a store whose RHS is not a BARE IDENTIFIER is not the
shape at all -- `w->f = rb_to_int(arg)` keeps the conversion RESULT, which is the fix; a
source route that flags any coerced token in the RHS flags every correct call site.

ROUND 5: FOUR OVER-CLEARS THIS SCRIPT SHIPPED WITH
---------------------------------------------------
All four made a broken struct read as safe, which is the one failure mode the effort
exists to prevent. Each has a generated red fixture in --self-test.

(a) The de-dupe key was `(struct, field)` across ALL dtypes, so the first dtype to be
    walked decided the verdict for every other dtype wrapping the same struct. msgpack
    wraps `msgpack_buffer_t` with both `buffer_data_type` (marks) and
    `buffer_view_data_type` (`.dmark = NULL`); which one won was iteration order. The key
    is now `(dtype, struct, field)`, and the coalescing that the de-dupe was actually
    added for -- the legacy `<inline:>` compat wrapper -- is applied only to that case.

(b) The clearing test was "is the field NAME present anywhere in the dmark body", so
    `if (w->callback) rb_gc_mark(w->other);` cleared `callback`. A field must now appear
    inside a marking call's own ARGUMENT LIST. Three tiers, because a one-tier "must be a
    primitive" rule flags sqlite3 PR #723's `functions`/`collations`/`aggregators`, which
    are marked via `rb_sqlite3_pin_array_and_contents(c->functions)` -- acceptance item 4
    is the control that discriminates the correct fix from the plausible one.

(c) A dmark reaching the field through a callee (`mark_value(w->cb)` where the helper
    calls `rb_gc_mark_movable` on its own parameter) named the field nowhere near the
    movable call, so it cleared instead of reporting. Helper-tier marks now carry the
    helper's own primitive kind, and a movable one with no dcompact reports
    NO-COMPACT-UNKNOWN rather than clearing.

Plus a parsing defect found while fixing (a): `TypedData_Get_Struct(o, t, view ?
&buffer_view_data_type : &buffer_data_type, b)` -- msgpack buffer_class.c:151 -- yielded
the whole ternary as the dtype name, matching no rb_data_type_t, so the wrap site
vanished. Both branches are now registered.

(d) The function wrap forms -- `rb_data_typed_object_zalloc` and its three siblings --
    take no struct-type argument, so they were registered with `None` and the struct was
    dropped even though `rb_data_typed_object_zalloc(klass, sizeof(foo_t), &foo_type)`
    names it one argument over. `struct_type_for`'s fallback covered that up wherever a
    callback body carried a sizeof or a cast, which is why it survived the corpus: the
    case it does NOT cover is a dtype whose callbacks are all `0`/`RUBY_DEFAULT_FREE` --
    and a NULL dmark is what makes every VALUE field in the wrapper a suspect. The sweep
    was blind exactly where its predicate is loudest, and said `struct type unresolved`.
    `sizeof_arg` now reads the struct off the call site, and the fallback stays.

NAME RESOLUTION IS SHARED, AND LIVES IN tu_scope.py
---------------------------------------------------
Every lookup that turns a NAME at a use site into a DEFINITION goes through
`tu_scope.bind`, which states C's linkage rule once for all four predicates: a use binds
to a definition in its own file first, a `static` definition in another .c/.cc/.cpp/.cxx
is not a candidate at all, and everything else -- non-static definitions, and anything
declared in a HEADER -- stays tree-wide. That module is a sibling file and these scripts
will not run without it; references/ is the unit that ships.

ACCEPTANCE (--self-test): flags fieldTypes on mysql2 m2-red and not on m2-green; clears
all six VALUE fields of sqlite3 pr-723's struct and flags whichever one a mutated tree
stops marking; clears vernier's overloaded-`mark()` shape without clearing the unmarked
field beside it; and, for predicate A, grades a generated stackprof reduction on all four
grades plus its own reds for the four refinements above.

Run it before trusting any result from this script. The fixture trees are gem/git
checkouts and are deliberately not committed here; rebuild the directory like so:

    mkdir acceptance && cd acceptance
    gem unpack mysql2 -v 0.5.6 && mv mysql2-0.5.6 m2-red
    cp -r m2-red m2-green
    # in m2-green/ext/mysql2/result.c, add the two lines the upstream fix adds:
    #   rb_gc_mark_movable(w->fieldTypes);   in rb_mysql_result_mark
    #   rb_mysql2_gc_location(w->fieldTypes); in rb_mysql_result_compact
    git clone https://github.com/sparklemotion/sqlite3-ruby sqlite3-pr723
    cd sqlite3-pr723 && git fetch origin pull/723/head && git checkout FETCH_HEAD

Then: python3 sweep_unmarked.py --self-test acceptance   (expects 53/53 PASS)

Two of the fifty-three run against the REAL gem rather than a generated reduction, and look for
fixtures beside the acceptance dir: `../corpus/stackprof-0.2.28` for the target grades, and
`../fixtest/sp-pristine` + `../fixtest/sp-fixed` for the red/green pair (sp-fixed is the
tree with `VALUE interval` changed to `long`, which is the upstream fix's shape). When they
are absent those two print SKIP and are counted as skipped -- never silently as passes,
because absence of a failure signal is not a negative result.

Note on the sqlite3 fixture: it is PR #723, NOT `main`. `main` has exactly one VALUE
field in that struct (`busy_handler`) and it IS marked, so `main` measures 0 suspects and
is not a fixture for this predicate at all -- its known defects are raw VALUEs handed to
the C library, which is a different predicate. The de-marked variant the self-test
generates from the pr-723 tree is the real positive control: without it, "clears all six"
can be the unresolved-struct artifact wearing a green tick, which is exactly how this
script behaved before typedef resolution was fixed.
"""
import argparse
import pathlib
import re
import subprocess
import sys
import tempfile

# The linkage rule, shared with the other three predicates. Sibling module, so
# `python3 .../sweep_unmarked.py` finds it wherever it is run from; references/ is the unit
# that ships, and a script copied out of it on its own will not import.
import tu_scope

# C only, and `.rs` is deliberately absent -- see SKILL.md, "Rust extensions need a
# different sweep, not these three". A magnus extension has no rb_data_type_t initialiser
# in its source (the DataType is built by data_type_builder! inside a derive expansion), so
# this file's wrap-site regexes return 0 wrap sites on Rust BY CONSTRUCTION -- and a zero
# reads as a clean verdict. Parsing .rs here bought nothing and mis-stated the coverage.
# All three sweeps share this tuple verbatim; keep them in step.
C_EXT = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp")

# ---------------------------------------------------------------- lexing helpers
#
# Comments and string literals are stripped before any brace matching: a brace inside
# either one silently desynchronises the matcher, and a desynchronised matcher yields an
# empty mark body, which reads as "marks nothing" -- a false positive factory.


def blank(span):
    """Spaces of the same length, but NEWLINES KEPT so line numbers survive.

    Blanking a block comment's newlines shortened rmagick's rmimage.cpp from 16,432 lines
    to 12,633. Nothing in round 4 printed a line number, so it never bit; anything that
    reports a call SITE needs this. Length is unchanged either way, so every byte offset
    into the stripped text still matches the original.
    """
    return "".join("\n" if ch == "\n" else " " for ch in span)


def strip_noise(src):
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == "/*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(blank(src[i:j]))
            i = j
        elif two == "//":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(blank(src[i:j]))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            # Keep the quotes so `"name"` in an rb_data_type_t still parses as a token.
            out.append(c + blank(src[i + 1:j - 1]) + c if j - i >= 2 else blank(src[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip_directives(src):
    """Blank out preprocessor directive lines, keeping the code inside conditionals.

    Three things depend on this. `#ifdef HAVE_RB_GC_MARK_MOVABLE` sits *between* the
    positional entries of mysql2's `rb_data_type_t`, so leaving the directive in makes the
    4th element parse as `#ifdef ... rb_mysql_result_compact #endif` -- not an identifier,
    so `.dcompact` reads as absent and every movable field reports NO-COMPACT. And
    mysql2's `#define TypedData_Get_Struct(obj, type, ignore, sval)` compat shim otherwise
    registers a wrap site on an `rb_data_type_t` literally named `ignore`.

    Both arms of a conditional are kept. For mark functions that is the recall-safe
    direction only because the alternative arm is the *legacy* mark path, which marks the
    same fields; a real `#if 0` block would over-clear, so the cleared list is printed.
    """
    out, i, n = [], 0, len(src)
    while i < n:
        j = src.find("\n", i)
        j = n if j < 0 else j
        line = src[i:j]
        if line.lstrip().startswith("#"):
            # A directive continues while the line ends in a backslash.
            while line.rstrip().endswith("\\") and j < n:
                out.append(" " * (j - i) + "\n")
                i = j + 1
                j = src.find("\n", i)
                j = n if j < 0 else j
                line = src[i:j]
            out.append(" " * (j - i))
        else:
            out.append(line)
        out.append("\n" if j < n else "")
        i = j + 1
    return "".join(out)


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


def callback_name(text):
    """The function a dtype callback slot names, with casts and parentheses removed.

    ROUND 9: `.dmark = (RUBY_DATA_FUNC)mark_wrap` -- a cast on a callback field. The
    designator pattern demanded `[\w:]+` immediately after the `=`, so the cast made the
    field read as ABSENT; the positional fallback then ran, found no `{`-prefixed group in
    a designated initialiser and recovered nothing, and the descriptor reported `dmark=-`.
    Every VALUE field of the wrapped struct then reported UNMARKED against a callback that
    marks them -- the whole struct mis-graded on a cast, and in the OVER-REPORTING
    direction only by luck: the mirror shape is a `.dcompact` cast, where a missing
    dcompact turns a correct movable mark into NO-COMPACT.

    The cast spelling is not exotic. `RUBY_DATA_FUNC` is what the legacy Data_Wrap_Struct
    API takes, and code migrated to TypedData keeps the casts it already had.

    A leading parenthesised group is stripped whether it is a cast (`(T)f`) or the value
    itself (`(f)`, which is what ffi's `.dcompact = (x)` macro expands to). Returns None
    for anything that is not a plain identifier once stripped -- an expression in a
    callback slot is not a name this pass can resolve, and inventing one would be worse
    than the `-` it prints.
    """
    t = (text or "").strip()
    for _ in range(4):
        if not t.startswith("("):
            break
        depth, close = 0, -1
        for i, ch in enumerate(t):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    close = i
                    break
        if close < 0:
            return None
        rest = t[close + 1:].strip()
        t = rest if rest else t[1:close].strip()
    t = t.lstrip("&").strip()
    return t if re.fullmatch(r"[\w:]+", t) else None



def split_args(text):
    """Split a macro argument list on top-level commas."""
    args, depth, cur = [], 0, []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            args.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        args.append("".join(cur).strip())
    return args


def call_args(src, name_end):
    """Given the index just past a macro name, return (args, index past the `)`)."""
    i = src.find("(", name_end)
    if i < 0:
        return None, name_end
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return split_args(src[i + 1:j]), j + 1
    return None, name_end


def rhs_after(src, eq_idx):
    """The assigned expression starting just past `=` at eq_idx, to the next top-level `,`/`;`.

    Stopping at a top-level comma is what makes a multi-declarator line readable:
    stackprof's `VALUE opts = Qnil, mode = Qnil, interval = Qnil, metadata =
    rb_hash_new(), out = Qfalse;` has to yield `Qnil` for `mode`, not the rest of the line.
    """
    depth, out = 0, []
    for ch in src[eq_idx + 1:]:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            if depth == 0:
                break
            depth -= 1
        elif depth == 0 and ch in ",;":
            break
        out.append(ch)
    return "".join(out).strip()


def line_at(src, idx):
    """1-based line number of a byte offset. strip_noise/strip_directives keep newlines."""
    return src.count("\n", 0, idx) + 1


def base_type(expr):
    """`&rb_mysql_result_type` -> rb_mysql_result_type; `foo->bar` -> foo->bar."""
    return expr.strip().lstrip("&").strip()


def type_name(expr):
    """`struct _sqlite3Ruby` -> _sqlite3Ruby; `mysql2_result_wrapper` -> itself."""
    e = re.sub(r"\b(struct|union|const|volatile)\b", " ", expr).strip()
    e = e.replace("*", " ").strip()
    return e.split()[-1] if e.split() else ""


def dtype_candidates(expr):
    """`view ? &a_type : &b_type` -> ["a_type", "b_type"]; anything else -> one name.

    msgpack selects its rb_data_type_t with a ternary (buffer_class.c:151). base_type()
    returned the whole expression, which matches no indexed dtype, so that wrap site was
    dropped -- and it did not even land in the `unresolved` tally, because the tally keys
    on the struct being unresolvable, not the dtype. Silence produced by the query.
    """
    e = expr.strip()
    q = e.find("?")
    if q >= 0:
        rest, depth = e[q + 1:], 0
        for i, ch in enumerate(rest):
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch == ":" and depth == 0:
                return [base_type(rest[:i]), base_type(rest[i + 1:])]
    return [base_type(e)]


# ------------------------------------------------------- marking primitives (round 5b)
#
# The clearing test is now "the field appears inside one of THESE calls' argument list",
# not "the field appears in the body". Everything below exists to make that test precise
# enough that a real mark is not missed (a false positive is a pass-2 discharge; a missed
# mark clears a broken struct, which is the whole failure mode).

MARK_PRIM = re.compile(
    r"^(?:rb_gc_mark|rb_gc_mark_maybe|rb_gc_mark_locations"
    r"|rb_gc_mark_movable|rb_gc_mark_and_move|RB_GC_MARK\w*)$")
MOVABLE_PRIM = re.compile(
    r"^(?:rb_gc_mark_movable|rb_gc_mark_and_move|RB_GC_MARK_MOVABLE\w*)$")
# rb_gc_mark_and_move is BOTH: registering one function as .dmark and .dcompact is the
# modern idiom, and it is safe. Listing it here is what stops that shape reporting
# NO-COMPACT -- the same body scanned under LOC_PRIM makes `in_compact` true.
LOC_PRIM = re.compile(
    r"^(?:rb_gc_location|rb_gc_mark_and_move|rb_gc_update\w*|RB_GC_UPDATE\w*)$")

# Control keywords take parenthesised operands and would otherwise read as calls.
# `__attribute__` is on this list for the same reason the walk below crosses it: with the
# crossing hand-rolled, `void mark(void *p) __attribute__((noinline)) { ... }` indexed
# NOTHING; with it shared, the same line indexes the body TWICE unless the attribute is
# refused a name of its own. A missing function and an invented one out of one construct.
NOT_CALLS = {"if", "for", "while", "switch", "return", "sizeof", "defined", "do", "else",
             "case", "typeof", "alignof", "static_assert", "catch", "__attribute__",
             "__declspec", "__asm__", "asm", "noexcept", "alignas", "_Alignas"}

RANK = {None: 0, "pin": 1, "loc": 1, "movable": 2}


def prim_kind(name):
    """None | "pin" | "movable" -- how a marking primitive treats its argument."""
    if not MARK_PRIM.match(name):
        return None
    return "movable" if MOVABLE_PRIM.match(name) else "pin"


def loc_kind(name):
    """None | "loc" -- does this call update a reference for compaction?"""
    return "loc" if LOC_PRIM.match(name) else None


def stronger(a, b):
    """Movable beats pinning: a field that MIGHT be marked movable still needs a dcompact."""
    return a if RANK[a] >= RANK[b] else b


def find_calls(body):
    """[(name, args, recv, op)] for every call in a body, nested calls included.

    ZERO-ARGUMENT CALLS COUNT. The old guard was `if args:` on a list that is EMPTY for
    `collector->mark()`, so a member call taking no arguments was not merely resolved to
    the wrong body -- it was never walked AT ALL. vernier's entire marking path is that
    shape (`collector_mark` does nothing but `collector->mark()`), which is why
    `stack_table_value` reported UNMARKED while `BaseCollector::mark` marks it two
    screens up. `call_args` returns None only when there is no `(`, so `is not None` is
    the test that separates "no arguments" from "not a call".

    The receiver comes back WITH the call, because it is the only handle a text scan has
    on WHICH `mark` runs: `recv->m()`, `recv.m()` and `C::m()`. 12 of the 23 corpus trees
    carry at least one name with more than one definition.
    """
    out = []
    for m in re.finditer(
            r"(?:\b([A-Za-z_]\w*)\s*(->|::|\.)\s*)?\b([A-Za-z_]\w*)\s*(?=\()", body):
        if m.group(3) in NOT_CALLS:
            continue
        args, _ = call_args(body, m.end())
        if args is not None:
            out.append((m.group(3), args, m.group(1), m.group(2)))
    return out


def arg_tokens(args):
    """Identifiers appearing anywhere in an argument list -- `c->functions` -> {c, functions}."""
    return set(re.findall(r"[A-Za-z_]\w*", " ".join(args)))


MEMBER_CHAIN = re.compile(r"[A-Za-z_]\w*(?:\s*(?:->|\.)\s*[A-Za-z_]\w*)+")


def arg_paths(args):
    """MEMBER-ACCESS PATHS in an argument list, base pointer dropped: `w->left.held` ->
    {"left.held"}.

    ROUND 9: the companion to arg_tokens, and the reason it exists is that arg_tokens
    THROWS THE PATH AWAY. `rb_gc_mark(w->left.held)` credited the bare token `held`, and
    the field lookup falls back to the leaf, so one member's mark cleared its sibling:
    `outer { left_t left; right_t right; }` with both inner types holding a `VALUE held`
    reported ZERO suspects when only `w->left.held` was marked. The deep enumeration that
    made `right.held` visible and this loss of the path shipped in the same round, so the
    recall improvement was cancelled by an over-clear on exactly the fields it added.

    A one-component result is not returned: `w->held` says nothing the bare token does not
    already say, and recording it as a path would make every unqualified mark look like
    evidence about a specific nesting.
    """
    out = set()
    for m in MEMBER_CHAIN.finditer(" ".join(args)):
        parts = [p.strip() for p in re.split(r"->|\.", m.group(0))]
        if len(parts) > 2:
            out.add(".".join(parts[1:]))
    return out


# ---------------------------------------------------------------- tree model


class Tree:
    """One gem's C sources, indexed for cross-file resolution."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.files = {}
        self.macros = {}         # function-like #define name -> concatenated bodies
        self.macro_defs = {}     # name -> [(params, body)], for token-paste expansion
        self.predirective = {}   # path -> comment-stripped text WITH directives intact
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and p.suffix in C_EXT and ".git" not in p.parts:
                try:
                    # Macros are indexed BEFORE the directives are blanked: a gem's own
                    # marking macro lives entirely inside a directive line.
                    decommented = strip_noise(p.read_text(errors="replace"))
                except OSError:
                    continue
                self._index_macros(decommented)
                # Kept undirectived for _index_get_struct_types: date reads its payload
                # back through `#define get_d1(x) TypedData_Get_Struct(x, union DateData,
                # &d_lite_type, dat)`, and strip_directives blanks the whole line, so the
                # only place the union is ever named is inside a directive.
                self.predirective[p] = decommented
                self.files[p] = strip_directives(decommented)
        self.all = "\n".join(self.files.values())
        self.structs = {}        # name -> body text
        self.struct_file = {}    # name -> path (for reporting)
        self.aliases = {}        # typedef name -> underlying name
        self.dtypes = {}         # rb_data_type_t name -> {"dmark":fn, "dcompact":fn, ...}
        self.funcs = {}          # function name (or "Cls::name") -> body text
        self.sigs = {}           # same key -> parameter-list text
        self.methods = set()     # (class, method) pairs declared in-tree
        self.bases = {}          # class -> [base classes named in its base-clause]
        self.type_of_dtype = {}  # (path, dtype) -> wrapped struct type name
        self.wrap_sites = []     # (path, dtype, struct_type, macro)
        # Per-file companions to the three first-wins indexes above. A `static` struct,
        # function or descriptor is file-local, so two translation units may each define
        # one under the same name and the tree-wide `setdefault` binds every use to
        # whichever file `rglob` reached first. Resolution therefore PREFERS a definition
        # in the file the use was seen in and falls back to the tree-wide index, which is
        # what a struct declared in a .h and wrapped in a .c still needs (mysql2's
        # `mysql2_result_wrapper` lives in result.h). See `shadowed`.
        self.structs_at = {}     # (path, name) -> body text
        self.funcs_at = {}       # (path, bare name) -> body text
        self.func_sites = {}     # bare name -> [(tu_scope.Scope, path)], in index order
        self.sigs_at = {}        # (path, bare name) -> parameter-list text
        self.func_spans_at = {}  # (path, bare name) -> (body_start, body_end)
        self.dtypes_at = {}      # (path, name) -> {"dmark": fn, ...}
        self.dtype_file = {}     # name -> the first file declaring it
        self.shadowed = {}       # kind -> {name: how many files define it}
        self.cross_picks = {}    # kind -> {name: how often a lookup fell back tree-wide}
        self._helper_memo = {}   # ("dmark"|"dcompact", at) -> {fn -> kind}, cycle-guarded
        self.func_spans = {}     # function name -> (path, body_start, body_end) offsets
        self.func_defs = {}      # function name -> how many definitions carry that name
        self.ambiguous = {}      # bare name -> how often a call FELL BACK to first-wins
        self.static_values = set()   # file-scope `static VALUE name;` identifiers
        self._src_memo = {}      # predicate A: token -> [rhs], memoised
        self._local_memo = {}    # scope key -> {identifier: class name}
        self._member_memo = {}   # class -> {member: class name}
        for path, src in self.files.items():
            self._index_structs(path, src)
            self._index_aliases(src)
            self._index_funcs(path, src)
            self._index_dtypes(path, src)
            self._index_statics(src)
        for path, src in self.files.items():
            self._index_wraps(path, src)
        self._index_get_struct_types()
        self.pasted = self._expand_pastes()

    # -- file-scoped resolution (round 7) ------------------------------------
    #
    # ROUND 7: THREE FIRST-WINS INDEXES BOUND A NAME TO THE WRONG TRANSLATION UNIT
    #
    # `structs`, `funcs` and `dtypes` were all keyed by BARE NAME with `setdefault`, so the
    # first file `rglob` returned owned the name for the whole tree. That is round-5 (a)
    # again -- a verdict decided by iteration order -- but across files rather than across
    # dtypes, and it is not a hypothetical: nokogiri 1.19.4 defines `static void mark` in
    # BOTH xml_document.c and xslt_stylesheet.c. `xml_document.c` sorts first, so
    # `nokogiri_xslt_stylesheet_tuple_type.dmark = mark` resolved to the DOCUMENT's mark
    # and `func_instances` reported UNMARKED -- a false positive on a field that
    # xslt_stylesheet.c's own `mark` marks with a plain `rb_gc_mark`, three lines down.
    #
    # The mirror of that false positive is the over-clear the reviewer described, and it is
    # the reason this is a fix rather than a nuisance: swap the file order and it is the
    # UNMARKED field that gets cleared by an unrelated same-named callback. The same holds
    # for a struct body (a later `wrapper` holding a VALUE analysed against an earlier
    # `wrapper` holding an int) and for a file-local `static const rb_data_type_t`.
    #
    # THE RULE IS "PREFER THIS FILE, THEN FALL BACK TREE-WIDE", NOT "FILE-SCOPE EVERYTHING".
    # A non-static definition genuinely is tree-wide, and so is every struct declared in a
    # header -- mysql2 wraps `mysql2_result_wrapper` in result.c and declares it in
    # result.h, which is the whole reason this script is a tree-wide resolver at all. The
    # fallback is therefore load-bearing, and the preference only ever fires when the
    # SAME file defines the name, which is exactly the `static` case. Measured over the
    # 99-tree corpus: one gem changes (nokogiri, above), because one gem is the only one
    # where a shadowed name reaches a wrap site at all.
    #
    # The C++ `Cls::method` index is left tree-wide. Round 6 resolves those by receiver
    # type, no corpus tree declares one class name in two files, and a same-named class in
    # two translation units is a different hazard from a `static` callback.

    def _shadow(self, kind, name):
        """Tally how many files define `name`. A count above 1 is printed by report()."""
        seen = self.shadowed.setdefault(kind, {})
        seen[name] = seen.get(name, 0) + 1

    def _cross_pick(self, kind, name, at):
        """Record that a lookup FELL BACK to the tree-wide first-wins entry for a name
        more than one file defines -- i.e. an arbitrary choice actually got made.

        The shadowed tally beside it is a hazard count: it says the tree contains the
        shape. This is the other signal, and round 6 already learned they are not the
        same one -- `11 overloaded name(s)` printed on every vernier run for two rounds
        while the pick that decided the verdict went unreported, which is why the
        `first-wins pick(s)` counter exists at all. The cross-file resolver reintroduces
        exactly one such path (a file that does NOT define the name, resolving to
        whichever file `rglob` reached first), so it gets its own counter rather than
        being folded into the hazard tally that hid the last one.
        """
        if at is not None and self.shadowed.get(kind, {}).get(name, 0) > 1:
            got = self.cross_picks.setdefault(kind, {})
            got[name] = got.get(name, 0) + 1

    def struct_body(self, name, at=None):
        """The struct body `name` denotes, as seen from file `at`."""
        if at is not None and (at, name) in self.structs_at:
            return self.structs_at[(at, name)]
        body = self.structs.get(name)
        if body is not None:
            self._cross_pick("struct", name, at)
        return body

    def dtype_key(self, name, at=None):
        """(declaring file, name) for the rb_data_type_t `name` names, seen from `at`.

        The key, not the bare name, is what `sweep` de-dupes on: two translation units
        each declaring `static const rb_data_type_t data_type` are two descriptors and
        two verdicts, and the bare name collapsed them into one.
        """
        if at is not None and (at, name) in self.dtypes_at:
            return (at, name)
        if self.dtype_file.get(name) is not None:
            self._cross_pick("dtype", name, at)
        return (self.dtype_file.get(name), name)

    def dtype_entry(self, key):
        return self.dtypes_at.get(key, {})

    # -- function-like macros -----------------------------------------------

    def _index_macros(self, src):
        """`#define rb_mysql2_gc_location(ptr) ptr = rb_gc_location(ptr)` -> a callee.

        mysql2 updates every one of its fields through that macro. strip_directives blanks
        the definition, so without this index the name resolves to neither a primitive nor
        an in-tree function and all six fields report NO-COMPACT on the PATCHED tree. The
        v2 script cleared them only because its compaction test was bare name presence in
        the dcompact body -- defect (b) was silently covering for this gap, so fixing (b)
        alone turned m2-green red.

        All arms are concatenated rather than first-wins: mysql2 defines this macro twice,
        as the real thing and as a no-op for Rubies without compaction. Marking on any arm
        counts as marking, which is right -- on a Ruby taking the no-op arm there is no
        compaction to go stale under.
        """
        for m in re.finditer(r"^[ \t]*#[ \t]*define[ \t]+(\w+)\(", src, re.M):
            params, j = call_args(src, m.end() - 1)
            k = j
            while True:
                nl = src.find("\n", k)
                if nl < 0:
                    nl = len(src)
                    break
                if not src[k:nl].rstrip().endswith("\\"):
                    break
                k = nl + 1
            self.macros[m.group(1)] = \
                self.macros.get(m.group(1), "") + "\n" + src[j:nl]
            self.macro_defs.setdefault(m.group(1), []).append(
                ([p for p in (params or []) if p], src[j:nl]))

    DESIGNATOR_RE = re.compile(r"\.(?:dmark|dfree|dsize|dcompact)\s*=")

    def _expand_designator_macros(self, body):
        """Expand a function-like macro that SUPPLIES a `.dcompact =` designator.

        ffi writes every one of its twenty dtypes as

            .dmark = buffer_mark, .dfree = ..., .dsize = ...,
            ffi_compact_callback( buffer_compact )

        with `#define ffi_compact_callback(x) .dcompact = (x),` in compat.h. The designator
        regex cannot see through the call, so `.dcompact` reads as absent and **all thirty**
        movable fields in the tree report NO-COMPACT -- the largest single block of false
        positives in the corpus, on a gem that does compaction correctly. ffi is loaded by
        all five apps, so this is thirty rows a human re-triages every round.

        NOT AN OVER-CLEAR, and the reason is the shape of the #ifdef rather than trust in
        the macro. compat.h defines the pair together:

            #ifdef HAVE_RB_GC_MARK_MOVABLE
            #  define ffi_compact_callback(x) .dcompact = (x),
            #else
            #  define rb_gc_mark_movable(x) rb_gc_mark(x)      <- pinning
            #  define ffi_compact_callback(x)                  <- empty
            #endif

        The arm that drops the dcompact is the same arm that makes the mark PINNING, so
        there is no configuration in which a movable field lacks an update. Taking any arm
        that supplies the designator is therefore right for the same reason `_index_macros`
        concatenates all arms rather than picking the first. Settled on the artifact, per
        this file's rule: `nm` on 1.17.x imports `_rb_gc_location`, on 1.15.5 it does not.

        Only macros whose replacement CONTAINS a designator are expanded -- expanding the
        rest costs time and invents text, the same bound `_expand_pastes` draws.
        """
        for name, defs in self.macro_defs.items():
            if not any(self.DESIGNATOR_RE.search(b) for _p, b in defs):
                continue
            if name not in body:
                continue
            for m in reversed(list(re.finditer(r"\b%s\s*(?=\()" % re.escape(name), body))):
                args, past = call_args(body, m.end())
                if args is None:
                    continue
                repl = ""
                for params, text in defs:
                    if not self.DESIGNATOR_RE.search(text):
                        continue
                    repl = text
                    for p, a in zip(params, args):
                        repl = re.sub(r"\b%s\b" % re.escape(p.strip()), a.strip(), repl)
                    # `.dcompact = (buffer_compact),` -- the macro parenthesises its
                    # parameter, and the designator regex accepts `[\w:]+`, which a `(`
                    # is not. Without this the expansion is textually correct and changes
                    # nothing, which is the most expensive kind of correct.
                    repl = re.sub(r"(\.\w+\s*=\s*)\(\s*([\w:]+)\s*\)", r"\1\2", repl)
                    break
                body = body[:m.start()] + repl + body[past:]
        return body

    # -- token-paste expansion (predicate A) --------------------------------

    def _expand_pastes(self):
        """Expand invocations of `##`-pasting macros, so the assignment they hide is visible.

        stackprof writes its 28 static symbols as `#define S(name) sym_##name =
        ID2SYM(rb_intern(#name));` then `S(wall);`. Nothing in the tree ever textually
        assigns to `sym_wall`, so predicate A's immediate-value analysis found no source
        for it, could not call it a static symbol, and graded `_stackprof.mode` UNGRADED
        instead of IMMEDIATE-ONLY. Measured: without this, 1 of the 4 target grades on
        stackprof 0.2.28 is wrong. Only `##` macros are expanded -- those are exactly the
        ones a textual scan cannot see, and expanding the rest costs time and invents text.
        """
        out = []
        pasters = [n for n, defs in self.macro_defs.items()
                   if any("##" in b for _, b in defs)]
        for name in pasters:
            pat = re.compile(r"\b%s\s*(?=\()" % re.escape(name))
            for src in self.files.values():
                for m in pat.finditer(src):
                    args, _ = call_args(src, m.end())
                    if args is None:
                        continue
                    for params, body in self.macro_defs[name]:
                        if len(params) != len(args) or "##" not in body:
                            continue
                        # The paste operator has to be masked BEFORE stringify, or
                        # `#\s*name` matches the second `#` of `sym_##name` and the
                        # expansion comes out as `sym_#"wall"` -- no assignment to
                        # sym_wall, so `mode` silently graded UNGRADED. Measured.
                        t = re.sub(r"\s*##\s*", "\x00", body)
                        for p, a in zip(params, args):
                            t = re.sub(r"#\s*%s\b" % re.escape(p), '"%s"' % a, t)
                            t = re.sub(r"\b%s\b" % re.escape(p), a, t)
                        out.append(t.replace("\x00", ""))
        return "\n".join(out)

    # -- file-scope statics (predicate A) -----------------------------------

    def _index_statics(self, src):
        """`static VALUE sym_object, sym_wall, ...;` -- candidates for "provably immediate".

        The `(){}=;` exclusion in the declarator list is what keeps `static VALUE
        stackprof_start(int argc, ...)` -- a function definition, not a variable -- out.
        """
        for m in re.finditer(r"^[ \t]*static\s+VALUE\s+([^;=(){}]+);", src, re.M):
            for decl in split_args(m.group(1)):
                d = decl.replace("*", " ").split("[")[0].strip()
                if d.isidentifier():
                    self.static_values.add(d)

    def value_sources(self, name):
        """Every RHS assigned to a bare identifier anywhere in the tree, pastes included."""
        if name not in self._src_memo:
            hay = self.all + "\n" + self.pasted
            out = []
            for m in re.finditer(r"(?<![\w.>])%s\s*=(?![=])" % re.escape(name), hay):
                out.append(rhs_after(hay, m.end() - 1))
            self._src_memo[name] = out
        return self._src_memo[name]

    def body_of(self, name, at=None):
        """The body of an in-tree callee, function or function-like macro.

        `at` is the file the call was resolved FROM, and resolution is `tu_scope.bind` --
        the one linkage rule all four predicates share. A definition in `at` wins; a
        `static` definition in ANOTHER translation unit is not a candidate at all; a
        non-static one, or one in a header, stays tree-wide. Round 7 shipped the first
        half of that ("prefer this file"); the exclusion is the second half, and it is
        what stops a file-local callback in a.c answering for a call in b.c that cannot
        see it -- the same defect predicates B, C and D were each patched for separately.

        `funcs` remains the fall-back: it also carries the `Cls::method` keys the C++
        receiver resolution builds, and macros are not functions with linkage at all.
        """
        sites = self.func_sites.get(name)
        if sites:
            picks = tu_scope.bind(sites, at, key=lambda d: d)
            for _scope, path in picks:
                body = self.funcs_at.get((path, name))
                if body is not None:
                    if path != at:
                        self._cross_pick("callback", name, at)
                    return body
            if picks:
                # Every candidate is visible but none has a body indexed here. Fall
                # through rather than invent one.
                pass
            elif at is not None:
                # Declared in this tree and NOT visible from `at`. That is a real answer,
                # and the caller reads None as unresolved -- resolving it anyway is the
                # defect this rule exists to remove.
                return self.macros.get(name)
        body = self.funcs.get(name, self.macros.get(name))
        if body is not None:
            self._cross_pick("callback", name, at)
        return body

    # -- structs ------------------------------------------------------------

    def _index_structs(self, path, src):
        # `class` as well as struct/union, with an optional base-clause before the brace.
        # vernier is C++ and declares `class Thread { public: ... VALUE ruby_thread; ... }`
        # plus `class TimeCollector : public BaseCollector {`. Matching only struct|union
        # made every one of its classes invisible, so the gem measured 0 suspects with 3
        # unresolved sites while holding three genuinely unmarked VALUEs -- Thread::mark()
        # is an EMPTY BODY that ThreadTable::mark() dutifully calls. A human found those;
        # the sweep could not. vernier was the strongest candidate in the round-5 corpus
        # precisely because it shares stackprof's architecture, and the query could not see it.
        for m in re.finditer(
                r"\b(?:typedef\s+)?(struct|union|class)\s+(\w+)?\s*(:[^{;]*)?\{", src):
            open_idx = src.index("{", m.end() - 1)
            close = match_brace(src, open_idx)
            if close < 0:
                continue
            body = src[open_idx + 1:close]
            names = []
            if m.group(2):
                names.append(m.group(2))
                # The base-clause is what makes `TimeCollector : public BaseCollector`
                # resolvable: a method not declared on the derived class is looked up on
                # its bases. Access specifiers and `virtual` are dropped; a template
                # argument list is left alone by the `\w+` token scan, which is right --
                # `std::vector<Thread>` is not a base we have a body for anyway.
                if m.group(3):
                    self.bases.setdefault(m.group(2), [
                        b for b in re.findall(r"[A-Za-z_]\w*", m.group(3))
                        if b not in ("public", "private", "protected", "virtual")])
                self._index_methods(m.group(2), body)
            # typedef declarator list after the closing brace: `} A, *APtr;`
            semi = src.find(";", close)
            if semi > 0 and src[close + 1:semi].strip():
                for decl in split_args(src[close + 1:semi]):
                    d = decl.strip().lstrip("*").strip()
                    if d.isidentifier():
                        names.append(d)
            for nm in names:
                self.structs.setdefault(nm, body)
                self.struct_file.setdefault(nm, path)
                if (path, nm) not in self.structs_at:
                    self.structs_at[(path, nm)] = body
                    self._shadow("struct", nm)

    # -- C++ methods (round 6, defect B1) -----------------------------------

    NESTED = re.compile(r"\b(?:struct|union|class)\s+(\w+)?\s*(?::[^{;]*)?\{")
    DEFN = re.compile(r"\b([A-Za-z_]\w*)\s*\(")

    def _nested_spans(self, body):
        """[(start, end)] of nested class/struct/union declarations inside a class body.

        A nested type's members and methods belong to IT, not to the enclosing class:
        vernier's `TimeCollector` contains `class TimeCollectorThread : public
        PeriodicThread`, whose `TimeCollector &time_collector;` would otherwise register
        as a member of TimeCollector and type a receiver wrongly. Each nested type is
        indexed in its own right by the enclosing finditer, so skipping the span here
        loses nothing.
        """
        spans = []
        for m in self.NESTED.finditer(body):
            o = body.index("{", m.end() - 1)
            c = match_brace(body, o)
            if c > 0:
                spans.append((m.start(), c))
        return spans

    def _index_methods(self, cls, body):
        """Index `cls`'s method bodies under the qualified key `Cls::name`.

        This is round-6 defect B1. `_index_funcs` keys every definition by its BARE name,
        first-wins, so `collector->mark()` bound to whichever `mark()` body came first in
        file order. vernier declares FIVE (Thread, ThreadTable, BaseCollector,
        TimeCollector, HeapTracker), two of which mark nothing the call site cares about,
        and the pick was decided by the order `rglob` happened to hand back the files.

        Definitions are taken at the class body's TOP LEVEL only: the scan resumes past
        each accepted body, so a `foo(x) { ... }` sitting inside a method is never
        mistaken for a sibling method, and nested types are skipped outright.
        """
        skip = self._nested_spans(body)
        i = 0
        while True:
            m = self.DEFN.search(body, i)
            if not m:
                return
            if any(a <= m.start() < b for a, b in skip):
                i = max(b for a, b in skip if a <= m.start() < b)
                continue
            if m.group(1) in NOT_CALLS:
                i = m.end()
                continue
            args, j = call_args(body, m.end() - 1)
            # A constructor's member-initialiser list sits between `)` and `{`, and it is
            # the shape BaseCollector's own constructor takes -- so does `__attribute__`,
            # `noexcept`, `const` and a trailing return type, which this walk crossed for
            # exactly one of the five. tu_scope carries all of them, with the initialiser
            # list opt-in; the hand-rolled version jumped to the next `{` in the file,
            # which reads `c ? f(a) : g(b)` as an initialiser list.
            k = tu_scope.skip_post_declarator(body, j, ctor_init=True)
            if k < len(body) and body[k] == "{":
                close = match_brace(body, k)
                if close > 0:
                    key = "%s::%s" % (cls, m.group(1))
                    self.methods.add((cls, m.group(1)))
                    self.funcs.setdefault(key, body[k + 1:close])
                    self.sigs.setdefault(key, " ".join(args or []))
                    i = close + 1
                    continue
            i = m.end()

    def _members(self, cls):
        """{data member: class name} for `cls`, so `threads.mark()` resolves.

        Only members whose declared type is a class we HAVE a body for are kept, which is
        what keeps `VALUE stack_table_value` and `int n` out without a type table.
        """
        if cls not in self._member_memo:
            body = self.structs.get(cls, "")
            for a, b in reversed(self._nested_spans(body)):
                body = body[:a] + blank(body[a:b]) + body[b:]
            out = {}
            for frag in re.split(r"[;{}]", body):
                m = re.match(r"\s*(?:(?:const|volatile|static|mutable)\s+)*"
                             r"([A-Za-z_]\w*)\s*[*&]?\s*([A-Za-z_]\w*)\s*$", frag)
                if m and m.group(1) in self.structs:
                    out.setdefault(m.group(2), m.group(1))
            self._member_memo[cls] = out
        return self._member_memo[cls]

    def member_type(self, cls, name):
        """The declared class of member `name` on `cls` or one of its bases."""
        for c in self.mro(cls):
            t = self._members(c).get(name)
            if t:
                return t
        return None

    def mro(self, cls, depth=8):
        """`cls` then its bases, breadth-first, cycle-guarded."""
        out, queue, seen = [], [cls], set()
        while queue and len(out) < depth:
            c = queue.pop(0)
            if not c or c in seen:
                continue
            seen.add(c)
            out.append(c)
            queue.extend(self.bases.get(c, ()))
        return out

    def method_body_key(self, cls, name):
        """`Cls::name` resolved against `cls` and then its bases, or None."""
        for c in self.mro(cls):
            if (c, name) in self.methods:
                return "%s::%s" % (c, name)
        return None

    # A declaration or parameter naming a type we have a class body for. The type test is
    # what makes these two loose patterns safe: `VALUE obj = ...`, `int i = 0` and
    # `delete collector;` all parse as (type, name) and are all dropped for having no
    # struct body, so only a real in-tree class ever types a receiver.
    DECL_PTR = re.compile(r"\b(?:(?:const|volatile|static)\s+)*"
                          r"([A-Za-z_]\w*)\s*[*&]+\s*([A-Za-z_]\w*)\s*(?==|;|,|\))")
    DECL_VAL = re.compile(r"\b(?:(?:const|volatile|static)\s+)*"
                          r"([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*(?==|;|,|\))")
    # `auto` hides the type in the cast, which is exactly how a dmark callback is written.
    DECL_AUTO = re.compile(r"\bauto\s*[*&]?\s*([A-Za-z_]\w*)\s*=\s*"
                           r"(?:static_cast|dynamic_cast|reinterpret_cast|const_cast)"
                           r"\s*<\s*(?:const\s+)?([A-Za-z_]\w*)")

    def local_types(self, key):
        """{identifier: class name} for locals and parameters visible in `key`'s body.

        `collector_mark` is the whole reason this exists:

            BaseCollector *collector = static_cast<BaseCollector *>(data);
            collector->mark();

        The parameter list is scanned with the body, since a helper that marks through a
        typed parameter (`static void mark_one(Foo *f) { f->mark(); }`) is the same shape
        one frame out.
        """
        if key not in self._local_memo:
            text = self.sigs.get(key, "") + ";\n" + (self.body_of(key) or "")
            out = {}
            for pat in (self.DECL_PTR, self.DECL_VAL):
                for m in pat.finditer(text):
                    if m.group(1) in self.structs:
                        out.setdefault(m.group(2), m.group(1))
            for m in self.DECL_AUTO.finditer(text):
                if m.group(2) in self.structs:
                    out.setdefault(m.group(1), m.group(2))
            self._local_memo[key] = out
        return self._local_memo[key]

    def callee_key(self, name, recv, op, scope):
        """Which body `recv op name(...)` runs, seen from inside function `scope`.

        The four handles, in the order they are tried:

          C::name()     the receiver IS the class
          recv->name()  the receiver's type, from a local declaration, a parameter, or a
          recv.name()   data member of the enclosing class -- and `this` is that class
          name()        unqualified inside a member function: the enclosing class, bases next

        FALL BACK TO FILE-ORDER FIRST-WINS ONLY WHEN THE RECEIVER TYPE IS GENUINELY
        UNDETERMINABLE -- `thread->mark()` over a `std::unique_ptr` in a range-for is the
        corpus case -- and COUNT it, because an arbitrary pick that nothing counts is the
        round-5 (a) disease: a verdict decided by iteration order, invisibly. The count
        rides in the report's coverage line next to the overloaded-name tally.

        A resolved receiver whose class does not declare the method also falls back
        (`list.push_back(...)` on a std:: type): refusing to descend would be recall-safe
        but silently drops the marking evidence the pre-B1 script had, and the fallback is
        counted either way.
        """
        cls = scope.split("::")[0] if "::" in scope else None
        target = None
        if op == "::":
            target = recv
        elif op:
            target = cls if recv == "this" else \
                (self.local_types(scope).get(recv) or self.member_type(cls, recv))
        elif cls:
            target = cls
        if target:
            k = self.method_body_key(target, name)
            if k:
                return k
        if self.func_defs.get(name, 0) > 1:
            self.ambiguous[name] = self.ambiguous.get(name, 0) + 1
        return name

    # -- typedef aliases ----------------------------------------------------

    def _index_aliases(self, src):
        """`typedef struct _sqlite3Ruby sqlite3Ruby;` as a STANDALONE statement.

        sqlite3 declares `struct _sqlite3Ruby { ... };` and names it in a separate
        typedef, so the tag-and-declarator indexing above never registers `sqlite3Ruby`
        -- which is the name `TypedData_Make_Struct` actually passes. Without this the
        whole gem resolves to "struct type unresolved" and reports 0 suspects: a clean
        sheet produced by the query failing, which is the exact silence §3 warns about.
        """
        for m in re.finditer(
                r"\btypedef\s+(?:(?:struct|union)\s+)?(\w+)\s+\*?\s*(\w+)\s*;", src):
            old, new = m.group(1), m.group(2)
            if old != new:
                self.aliases.setdefault(new, old)

    def resolve(self, name, at=None, depth=6):
        """Follow typedef aliases to something we have a struct body for, seen from `at`."""
        while name and self.struct_body(name, at) is None and depth > 0:
            name = self.aliases.get(name)
            depth -= 1
        return name

    # -- functions ----------------------------------------------------------

    def _index_funcs(self, path, src):
        # A definition, not a prototype: identifier + parens + `{` before any `;`.
        for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", src):
            name = m.group(1)
            if name in NOT_CALLS:
                continue
            depth, j = 0, m.end() - 1
            while j < len(src):
                if src[j] == "(":
                    depth += 1
                elif src[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            # THE CROSSING FROM `)` TO `{` IS tu_scope's, NOT A WHITESPACE SKIP.
            # Fifth appearance of the same gap, and the first in THIS predicate: a dmark
            # written `static void mark(void *p) __attribute__((noinline))` was not
            # indexed at all, so its marking calls were never read and every field of the
            # struct it marks reported UNMARKED. Worse than a dropped row -- the walk
            # indexed the body under the name `__attribute__`, so the tree carried an
            # invented function as well as a missing one. `ctor_init` is on because this
            # index also takes OUT-OF-LINE constructors (`Foo::Foo(int x) : a(x) {`).
            k = tu_scope.skip_post_declarator(src, j + 1, ctor_init=True)
            if k < len(src) and src[k] == "{":
                close = match_brace(src, k)
                if close > 0:
                    self.funcs.setdefault(name, src[k + 1:close])
                    self.sigs.setdefault(name, src[m.end():j])
                    if (path, name) not in self.funcs_at:
                        self.funcs_at[(path, name)] = src[k + 1:close]
                        self.sigs_at[(path, name)] = src[m.end():j]
                        self.func_spans_at[(path, name)] = (k + 1, close)
                        self._shadow("callback", name)
                        # The LINKAGE of this definition, for tu_scope.bind. The
                        # declaration specifiers run back to the previous statement
                        # boundary, which is the only place `static` can be.
                        head = src[max(0, m.start(1) - 300):m.start(1)]
                        head = head[max(head.rfind(";"), head.rfind("}"),
                                        head.rfind("{")) + 1:]
                        self.func_sites.setdefault(name, []).append(
                            (tu_scope.declared_scope(
                                path, re.search(r"\bstatic\b", head)), path))
                    # Offsets, not just text: predicate A reports the file:line of the
                    # coercion it found, and a body extracted into a string has none.
                    self.func_spans.setdefault(name, (path, k + 1, close))
                    # An OUT-OF-LINE definition -- `void HeapTracker::mark() { ... }` --
                    # is a method too, and indexing it only by its bare name puts it back
                    # in the first-wins pool that defect B1 exists to drain.
                    q = re.search(r"([A-Za-z_]\w*)\s*::\s*$", src[:m.start(1)])
                    if q:
                        key = "%s::%s" % (q.group(1), name)
                        self.methods.add((q.group(1), name))
                        self.funcs.setdefault(key, src[k + 1:close])
                        self.sigs.setdefault(key, src[m.end():j])
                    # C++ overloads collide on the bare name, and first-wins then decides
                    # a verdict by file order -- the same disease as the round-5 (a)
                    # de-dupe defect. Resolution is now by (class, method) with base-clause
                    # lookup, so this count is no longer the verdict-maker it was; it stays
                    # because a name with more than one definition is still where a
                    # FALLBACK pick can land, and `tree.ambiguous` counts those.
                    self.func_defs[name] = self.func_defs.get(name, 0) + 1

    # -- rb_data_type_t -----------------------------------------------------

    def _index_dtypes(self, path, src):
        for m in re.finditer(r"\brb_data_type_t\s+(\w+)\s*=\s*\{", src):
            open_idx = src.index("{", m.end() - 1)
            close = match_brace(src, open_idx)
            if close < 0:
                continue
            body = self._expand_designator_macros(src[open_idx + 1:close])
            entry = {}
            for f in re.finditer(r"\.(dmark|dfree|dsize|dcompact)\s*=\s*([^,}]+)", body):
                # `[^,}]+` then callback_name(), not `[\w:]+` in the pattern: a CAST is the
                # commonest thing between the `=` and the name and it made the whole
                # designator invisible. See callback_name.
                v = callback_name(f.group(2))
                if v:
                    entry[f.group(1)] = v
            if not entry:
                # Two positional forms, and json 2.20.0 uses the *hybrid* one:
                #   { "name", { dmark, dfree, dsize, dcompact }, ... }   -- fully positional
                #   { .wrap_struct_name = "n", .function = { dmark, ... }, .flags = ... }
                # A designated `.function` holding a positional list matched neither the
                # designated regex above nor a bare `{`-prefixed part, so
                # JSON_ResumableParser_type resolved to dmark=- and reported its `buffer`
                # field UNMARKED. It is in fact marked with the PINNING rb_gc_mark and
                # updated in the dcompact -- a false positive on the safest struct in the
                # gem, and the reason pass 2 exists.
                parts = split_args(body)
                grp = next((p for p in parts if p.strip().startswith("{")), None)
                if grp is None:
                    fn = next((p for p in parts
                               if re.match(r"\.function\s*=", p.strip())), None)
                    if fn:
                        grp = fn.split("=", 1)[1].strip()
                if grp and grp.startswith("{"):
                    fns = split_args(grp.strip()[1:-1])
                    for key, val in zip(("dmark", "dfree", "dsize", "dcompact"), fns):
                        v = callback_name(val)
                        if v:
                            entry[key] = v
            self.dtypes.setdefault(m.group(1), entry)
            if (path, m.group(1)) not in self.dtypes_at:
                self.dtypes_at[(path, m.group(1))] = entry
                self.dtype_file.setdefault(m.group(1), path)
                self._shadow("dtype", m.group(1))

    # -- wrap / get sites ---------------------------------------------------

    # (macro, index of the struct-type arg, index of the rb_data_type_t arg)
    TYPED = {
        "TypedData_Make_Struct": (1, 2),
        "TypedData_Get_Struct": (1, 2),
        "TypedData_Make_Struct0": (2, 4),
        "TypedData_Wrap_Struct": (None, 1),
        "rb_data_typed_object_zalloc": (None, 2),
        "rb_data_typed_object_alloc": (None, 2),
        "rb_data_typed_object_wrap": (None, 2),
        "rb_data_typed_object_make": (None, 1),
    }
    # Legacy untyped forms name the mark function inline; no rb_data_type_t exists.
    UNTYPED = {"Data_Make_Struct": (1, 2), "Data_Wrap_Struct": (None, 1)}

    def _index_wraps(self, path, src):
        for macro, (ti, di) in self.TYPED.items():
            for m in re.finditer(r"\b%s\s*(?=\()" % macro, src):
                args, _ = call_args(src, m.end())
                if not args or len(args) <= max(di, ti or 0):
                    continue
                st = type_name(args[ti]) if ti is not None else None
                if st is None:
                    st = self.sizeof_arg(args, di, path)
                # A ternary picks between two dtypes; register BOTH, or the wrap site
                # silently disappears (msgpack buffer_class.c:151).
                for dtype in dtype_candidates(args[di]):
                    self.wrap_sites.append((path, dtype, st, macro))
                    # Keyed by the DESCRIPTOR, not its name: two files may each declare a
                    # `static const rb_data_type_t data_type` wrapping different structs.
                    dk = self.dtype_key(dtype, path)
                    if st and dk not in self.type_of_dtype:
                        self.type_of_dtype[dk] = st
        for macro, (ti, mi) in self.UNTYPED.items():
            for m in re.finditer(r"\b%s\s*(?=\()" % macro, src):
                args, _ = call_args(src, m.end())
                if not args or len(args) <= mi:
                    continue
                st = type_name(args[ti]) if ti is not None else None
                # Synthesise a pseudo-dtype so legacy gems are covered by the same walk.
                key = "<inline:%s>" % base_type(args[mi])
                self.dtypes.setdefault(key, {"dmark": base_type(args[mi])})
                self.dtypes_at.setdefault((path, key), {"dmark": base_type(args[mi])})
                self.dtype_file.setdefault(key, path)
                self.wrap_sites.append((path, key, st, macro))
                dk = self.dtype_key(key, path)
                if st and dk not in self.type_of_dtype:
                    self.type_of_dtype[dk] = st

    SIZEOF = re.compile(r"\bsizeof\s*\(\s*(?:struct\s+)?(\w+)\s*\)")

    def sizeof_arg(self, args, skip, at=None):
        """Recover the wrapped struct from a `sizeof(...)` in the call's OTHER arguments.

        The function forms carry no struct-type argument, so TYPED registers them with
        `None` -- but `rb_data_typed_object_zalloc(klass, sizeof(foo_t), &foo_type)` names
        the struct right there, one argument over. Dropping it is only survivable while
        `struct_type_for`'s fallback has a callback body to scan, and the case that
        matters most is exactly the one where it does not: a dtype whose callbacks are
        `0`/`RUBY_DEFAULT_FREE` has no body carrying a `sizeof` or a cast, and a NULL
        dmark is also what makes every VALUE field in the wrapper a suspect. So the
        sweep went blind precisely where its own predicate is loudest, and reported the
        site as `struct type unresolved, 0 suspects`.

        Only a name that resolves to a struct we have a body for is accepted:
        `sizeof(VALUE)`, `sizeof(*p)` and friends must not become the wrapped type.
        """
        for i, a in enumerate(args):
            if i == skip:
                continue
            for m in self.SIZEOF.finditer(a):
                if self.struct_body(self.resolve(m.group(1), at), at) is not None:
                    return m.group(1)
        return None

    # -- resolution ---------------------------------------------------------

    def _index_get_struct_types(self):
        """(path, dtype) -> {struct type names it is READ BACK as}. See struct_types_for()."""
        self.get_struct_types = {}
        for path, src in self.predirective.items():
            for m in re.finditer(
                    r"TypedData_Get_Struct\s*\(\s*[^,]+?,\s*(?:struct\s+|union\s+|class\s+)?"
                    r"(\w+)\s*,\s*&\s*(\w+)\s*,", src):
                dk = self.dtype_key(m.group(2), path)
                self.get_struct_types.setdefault(dk, set()).add(m.group(1))

    def struct_type_for(self, dkey):
        """Infer the wrapped struct even when only Wrap_Struct is used.

        `dkey` is a (declaring file, name) pair, and its file is the scope every name
        below resolves in -- the callbacks a descriptor names are its own file's.
        """
        at = dkey[0]
        if dkey in self.type_of_dtype:
            r = self.resolve(self.type_of_dtype[dkey], at)
            if self.struct_body(r, at) is not None:
                return r
        # Fall back to the type's own dfree/dsize/dmark, which must cast the payload.
        for key in ("dsize", "dfree", "dmark", "dcompact"):
            fn = self.dtype_entry(dkey).get(key)
            # `funcs`, not `body_of`: a macro body has never been a source of the payload
            # cast here, and widening the lookup while file-scoping it would fold two
            # changes into one measurement.
            body = (self.funcs_at.get((at, fn)) or self.funcs.get(fn, "")) if fn else ""
            for pat in (r"sizeof\s*\(\s*(?:struct\s+)?(\w+)\s*\)",
                        r"\(\s*(?:const\s+)?(?:struct\s+)?(\w+)\s*\*\s*\)"):
                for cm in re.finditer(pat, body):
                    r = self.resolve(cm.group(1), at)
                    if self.struct_body(r, at) is not None:
                        return r
        return self.type_of_dtype.get(dkey)

    def params_of(self, fn, at=None):
        """[parameter name] for an in-tree function or function-like macro, in order."""
        sig = self.sigs_at.get((at, fn)) if at is not None else None
        if sig is None:
            sig = self.sigs.get(fn)
        if sig is None:
            defs = self.macro_defs.get(fn)
            return [p.strip() for p in defs[0][0]] if defs else []
        out = []
        for a in split_args(sig):
            # The declarator's last identifier is the name: `const VALUE *ary` -> ary.
            toks = re.findall(r"[A-Za-z_]\w*", a.split("=")[0])
            out.append(toks[-1] if toks and toks[-1] not in ("void",) else None)
        return out

    def helper_kind(self, fn, key, at=None, depth=1):
        """How in-tree function `fn` marks (key=dmark) / updates (key=dcompact), and WHAT.

        Returns (kind, marked parameter indices).

        One level of onward callees, which is the transitivity the v2 `mark_text` folded
        in wholesale. sqlite3 PR #723 marks three of its six fields through
        `rb_sqlite3_pin_array_and_contents(c->functions)`; without this tier the round-5
        (b) fix would flag all three and acceptance item 4 would fail. That fixture is
        the control which separates the correct fix from the merely plausible one.

        ROUND 7: WHICH PARAMETER, NOT JUST "SOMETHING". The kind alone said only that the
        callee contains a marking primitive somewhere, and `_collect_marks` then credited
        EVERY token in the call's argument list. So

            static void note(VALUE v) { rb_gc_mark(g_root); }   /* marks a GLOBAL */
            static void w_mark(void *p) { ...; note(w->cb); }

        cleared `cb` as "marked pin (via helper)" on a field ordinary GC can free -- the
        over-clear family this script exists to prevent, reached through the one tier that
        was still crediting by association. A parameter counts as marked when its name
        appears as a token inside a marking primitive's own argument list, which credits
        `rb_gc_mark(ary)` (sqlite3, bare) and `rb_gc_mark(pk->buffer_ref)` (msgpack,
        through a member) alike -- the second is loose, and deliberately so: it is the
        clearing direction only for a token that is already reached by the DIRECT tier one
        frame in, so nothing rests on it.

        A helper whose marks attribute to no parameter at all returns an empty index set
        and therefore credits nothing, which is the same conservative direction round-5
        (c) took for the movable axis: report, do not clear.

        Conservative on disagreement: a helper with any movable path counts as movable,
        because an unwarranted NO-COMPACT costs a pass-2 discharge while a missed one
        ships a stale pointer.
        """
        memo = self._helper_memo.setdefault((key, at), {})
        if fn in memo:
            return memo[fn]
        memo[fn] = (None, frozenset())        # cycle guard: recursion resolves to nothing
        params = self.params_of(fn, at)
        index = {p: i for i, p in enumerate(params) if p}
        kinds, marked = set(), set()
        for name, args, recv, op in find_calls(self.body_of(fn, at) or ""):
            k = loc_kind(name) if key == "dcompact" else prim_kind(name)
            if k:
                for tok in arg_tokens(args):
                    if tok in index:
                        marked.add(index[tok])
            elif depth > 0:
                callee = self.callee_key(name, recv, op, fn)
                if callee != fn and self.body_of(callee, at) is not None:
                    k, inner = self.helper_kind(callee, key, at, depth - 1)
                    # Map the inner call's marked positions back onto OUR parameters, so
                    # a two-hop helper still says which argument it was that got marked.
                    for i in inner:
                        if i < len(args):
                            for tok in arg_tokens([args[i]]):
                                if tok in index:
                                    marked.add(index[tok])
            if k:
                kinds.add(k)
        best = None
        for k in kinds:
            best = stronger(best, k)
        memo[fn] = (best, frozenset(marked))
        return memo[fn]

    def _collect_marks(self, fn, key, direct, helper, mentioned, at=None, depth=1):
        """Walk a mark function and one level of in-tree callees, crediting fields.

        Two ways a callee marks, and BOTH have a real instance in the corpus:

          msgpack   Packer_mark calls `msgpack_packer_mark(pk)` -- the whole struct. The
                    field names appear only inside the CALLEE, on its own parameter, so
                    the callee's marking calls have to be walked or `buffer_ref` and
                    `to_msgpack_arg` read as UNMARKED. (They are marked. Eight msgpack
                    fields were false positives on the first cut of this fix.)
          sqlite3   database_mark calls `rb_sqlite3_pin_array_and_contents(c->functions)`
                    -- the FIELD. The name is at the call site and the primitive is one
                    frame in, so the field is credited at the helper tier.

        The second is why the helper tier is kept distinct: the primitive's kind is known
        but which argument it applied to is only as good as `helper_kind`'s parameter
        mapping, which is the whole of round-5 defect (c) and round-7's refinement of it.
        """
        body = self.body_of(fn, at) or ""
        mentioned |= set(re.findall(r"[A-Za-z_]\w*", body))
        for name, args, recv, op in find_calls(body):
            kind = loc_kind(name) if key == "dcompact" else prim_kind(name)
            if kind:
                # BOTH keys. The bare tokens are the index as it has always been -- an
                # unqualified field resolves through them and nothing about that changes.
                # The dotted paths are what tells `left.held` from `right.held`, which the
                # leaf-name fallback in sweep() cannot do on its own. See arg_paths.
                for tok in arg_tokens(args) | arg_paths(args):
                    direct[tok] = stronger(direct.get(tok), kind)
                continue
            if depth <= 0:
                continue
            # Round 6 (B1): the CALLEE, not the bare name. `collector->mark()` runs
            # BaseCollector::mark, and binding it to whichever `mark()` the file order
            # produced is how vernier's `stack_table_value` reported UNMARKED.
            callee = self.callee_key(name, recv, op, fn)
            if callee == fn or self.body_of(callee, at) is None:
                continue
            hk, marked = self.helper_kind(callee, key, at)
            if hk:
                # Round 7: only the arguments the callee actually marks. Crediting the
                # whole list cleared a field passed alongside a marked one, or passed to
                # a helper that marks a global.
                for i in marked:
                    if i < len(args):
                        for tok in arg_tokens([args[i]]) | arg_paths([args[i]]):
                            helper[tok] = stronger(helper.get(tok), hk)
            self._collect_marks(callee, key, direct, helper, mentioned, at, depth - 1)

    def mark_index(self, dkey):
        """Per-key marking evidence, in three tiers. {key: (direct, helper, mentioned)}

        direct    token named inside a marking PRIMITIVE's own argument list
        helper    token passed BY NAME to an in-tree function that marks
        mentioned every identifier seen, so "named but never marked" is nameable

        The v2 test was `word.search(body)` over the dmark concatenated with every callee
        body, which cleared any field the dmark so much as touched -- defect (b), where
        `if (w->callback) rb_gc_mark(w->other);` cleared `callback`. It also attributed
        movability with an `[^;]*` window, and that window spans a comma, so
        `rb_gc_mark_movable(w->a), rb_gc_mark(w->b)` mis-attributed. Parsing argument
        lists removes both, and costs nothing in recall because the walk still descends.
        """
        idx = {}
        for key in ("dmark", "dcompact"):
            fn = self.dtype_entry(dkey).get(key)
            direct, helper, mentioned = {}, {}, set()
            if fn and fn not in ("NULL", "0", "RUBY_DEFAULT_FREE"):
                self._collect_marks(fn, key, direct, helper, mentioned, dkey[0])
            idx[key] = (direct, helper, mentioned)
        return idx


# ---------------------------------------------------------------- the predicate

FIELD = re.compile(
    r"^[ \t]*(?:const\s+|volatile\s+)*VALUE\s+([^;{}()=]+);", re.M)


METHOD_HEAD = re.compile(r"\b([A-Za-z_]\w*)\s*\(")


def blank_method_bodies(body):
    """Blank the `{...}` of any function defined inside a struct/class body.

    Length and newlines are preserved, so the brace split below still reaches an
    ANONYMOUS struct/union member -- which is a `{` with no `name(...)` in front of it and
    is therefore never blanked. A method body is a `{` that follows a parameter list,
    optional cv/ref qualifiers and, for a constructor, a `:` member-initialiser list;
    `VALUE (*cb)(VALUE)` is not, because what follows ITS parameter list is another `(`.
    """
    i = 0
    while True:
        m = METHOD_HEAD.search(body, i)
        if not m:
            return body
        if m.group(1) in NOT_CALLS:
            i = m.end()
            continue
        args, j = call_args(body, m.end() - 1)
        if args is None:
            i = m.end()
            continue
        # METHOD_TAIL was a CLOSED WORD LIST -- `const|volatile|noexcept|override|final` --
        # which is the shape tu_scope's docstring records as the first cut of the shared
        # walk and the reason it was reopened: a list that has to be extended once per
        # spelling reports a clean sheet once per spelling. A method body that is not
        # blanked here leaks its LOCALS into value_fields as phantom struct members.
        k = tu_scope.skip_post_declarator(body, j, ctor_init=True)
        if k < len(body) and body[k] == "{":
            close = match_brace(body, k)
            if close > 0:
                body = body[:k + 1] + blank(body[k + 1:close]) + body[close:]
                i = close + 1
                continue
        i = m.end()


def value_fields(body):
    """VALUE fields of a struct body, as (name, is_pointer).

    Declarations are split on `;`, `{` and `}` -- NOT matched from the start of a line.
    The old pattern was `^`-anchored under re.M, so it saw a field only when VALUE opened
    the line. Measured on `typedef struct { int n; VALUE held; } compact_t;` with a NULL
    dmark: 0 suspects, 0 CLEARED. Zero fields makes a struct read as structurally out of
    scope, so a genuinely unmarked VALUE printed the same clean sheet as a gem that wraps
    nothing -- the round-5 failure family again, a broken struct wearing a green tick, and
    the one shape the coverage line cannot distinguish because the counts really are zero.

    Splitting on braces as well as semicolons is what reaches the members of a nested
    anonymous struct/union, which share the allocation and were the reason the old comment
    said "scan whole".

    `VALUE*` with the star against the type is accepted too: `VALUE\\s+` demanded
    whitespace and missed `VALUE* items;` entirely.
    """
    fields = []
    # A C++ class body contains its METHOD BODIES, and their locals are not fields.
    # Blanking them is what the old `if "=" in rest` test was doing by accident: it threw
    # away every `VALUE x = ...;` in the class, which suppressed nine of vernier 1.10.1's
    # locals along with the real members it was also losing. Measured with the `=` test
    # removed and this pass absent: `HeapTracker::compact`'s `VALUE reloc_obj` and
    # `BaseCollector::build_collector_result`'s `VALUE result` reported as UNMARKED FIELDS
    # of the wrapped struct. A local is not a field in any language this parses.
    body = blank_method_bodies(body)
    # C++ access specifiers are labels, not statements, so no `;` separates them from the
    # member that follows. `class C { public: VALUE held; }` put `public:` and `VALUE held`
    # in ONE fragment and lost the field -- i.e. the FIRST member after any access
    # specifier was invisible. Dropping the labels is what makes the split see it.
    body = re.sub(r"\b(?:public|private|protected)\s*:", " ", body)
    for frag in re.split(r"[;{}]", body):
        m = re.match(r"\s*(?:(?:const|volatile|_Atomic)\s+)*VALUE(?![A-Za-z0-9_])(.*)",
                     frag, re.S)
        if not m:
            continue
        rest = m.group(1)
        for decl in split_args(rest):
            # ROUND 7: A C++ DEFAULT MEMBER INITIALISER IS STILL A FIELD.
            #
            # The test used to be `if "(" in rest or "=" in rest: continue`, which threw
            # away the whole declaration on sight of an `=`. So `struct wrapper { VALUE
            # held = Qnil; };` enumerated ZERO fields and, with a null dmark, the sweep
            # printed one wrap site and no suspects -- the RED_COMPACT_DECL failure
            # family exactly: a struct reading as "holds no VALUE, structurally out of
            # scope" while holding a genuinely unmarked one. `= Qnil` is where a C++
            # wrapper STARTS; what it holds later is what dmark has to mark.
            #
            # Unlike a wrong severity grade, this dropped the ROW. Splitting the
            # initialiser off the declarator keeps the function-pointer exclusion intact,
            # because `VALUE (*cb)(...)` puts its parens in the DECLARATOR, before any
            # `=`, while `VALUE held = f(x)` puts them after it.
            d = decl.split("=", 1)[0].strip()
            if "(" in d:
                continue
            ptr = "*" in d or "[" in d
            nm = d.replace("*", " ").split("[")[0].strip()
            if nm.isidentifier():
                fields.append((nm, ptr))
    return fields


NAMED_AGGREGATE_MEMBER = re.compile(
    r"\b(?:struct|union|class)\s+(\w+)\s+(\w+)\s*(?:\[[^\]]*\])?\s*;")


TYPED_GET = re.compile(
    r"TypedData_Get_Struct\s*\(\s*[^,]+,\s*(?:struct|union|class)\s+(\w+)\s*,")


def struct_types_for(tree, dkey, primary):
    """EVERY struct type this dtype is used with, not just the first one resolved.

    Round 6's residual again, and the diagnosis is not the one it looked like. date's
    `d_lite_type` is *allocated* as `struct SimpleDateData` and *read back* as
    `union DateData` on every single access:

        TypedData_Make_Struct(klass, struct SimpleDateData, &d_lite_type, dat)
        TypedData_Get_Struct(x, union DateData, &d_lite_type, dat)

    struct_type_for() resolves the allocation type and stops, so the union -- and with it
    `ComplexDateData.nth` and `.sf`, the whole other arm of the payload -- was never
    enumerated. That is the round-5 over-clear family mirrored: there, one struct wrapped
    by two dtypes let iteration order pick the verdict; here, one dtype used with two
    struct types does the same thing. Taking the UNION of the fields is the only answer
    that does not depend on which site the parser reached first.
    """
    out = []
    if primary:
        out.append(primary)
    for name in tree.get_struct_types.get(dkey, ()):
        r = tree.resolve(name, dkey[0])
        if tree.struct_body(r, dkey[0]) is not None and r not in out:
            out.append(r)
    return out


VALUE_FIELDS_DEPTH = 6


def value_fields_deep(tree, struct_name, at=None, _stack=()):
    """value_fields(), plus the VALUE fields of NAMED aggregate members, recursively.

    Round 6's residual, found by breadth rather than by the self-test: date wraps
    `union DateData { unsigned flags; struct SimpleDateData s; struct ComplexDateData c; }`
    and the flat scan enumerated **one** field. `ComplexDateData.nth` and `.sf` -- the
    other arm of the union -- were never looked at. Benign in date, because
    `d_lite_gc_mark` marks all three with the pinning `rb_gc_mark`, but a struct member
    is an ordinary way to organise a wrapped payload and every VALUE inside one was
    invisible.

    Only NAMED members recurse. Anonymous struct/union members are already reached by
    value_fields()'s brace split, and going through them again would double-count.

    ROUND 9: THE GUARD IS THE RECURSION STACK, NOT A VISITED SET.

    A shared `_seen` set was added once and never removed, so the FIRST member of a given
    type consumed it and every later member of that same type enumerated NOTHING:

        struct outer { struct inner left; struct inner right; };

    yielded `left.held` alone. A dmark marking `left.held` and forgetting `right.held`
    then reported ZERO suspects -- the field was not merely mis-graded, it never entered
    the funnel, which is the failure this file's coverage counters exist to make visible
    and the one shape they cannot: the count really is smaller and nothing says why. Two
    same-typed members is the ordinary way to write a pair.

    A stack guard still terminates. `_stack` holds only the types on the path from the
    root, so a cycle -- `struct a { struct b b; }; struct b { struct a a; };`, which no
    conforming C program can instantiate but a parser can certainly read -- stops at the
    repeat instead of recursing forever; the explicit cycle fixture in self_test() is what
    keeps that true. VALUE_FIELDS_DEPTH bounds the work a legal but deep DAG can cost,
    since a stack guard alone re-walks a shared sub-aggregate once per path to it.
    """
    body = tree.struct_body(struct_name, at)
    if body is None or struct_name in _stack or len(_stack) >= VALUE_FIELDS_DEPTH:
        return []
    _stack = _stack + (struct_name,)
    out = list(value_fields(body))
    for m in NAMED_AGGREGATE_MEMBER.finditer(body):
        inner, member = m.group(1), m.group(2)
        for nm, ptr in value_fields_deep(tree, inner, at, _stack):
            out.append(("%s.%s" % (member, nm), ptr))
    return out


def sweep(tree, verbose=False):
    suspects, clears = [], []
    seen, reported, typed_seen = set(), set(), set()
    # Typed dtypes first, so the `<inline:>` legacy pseudo-dtype is the one coalesced away
    # and never the other way round.
    sites = sorted(tree.wrap_sites, key=lambda s: s[1].startswith("<inline:"))
    for path, dtype, _st, macro in sites:
        # Round 7: the DESCRIPTOR, not its bare name. Two translation units each
        # declaring `static const rb_data_type_t data_type` are two descriptors; keyed by
        # name they collapsed into one and the second wrap site was skipped as already
        # seen -- a whole struct dropped without so much as a cleared line.
        dkey = tree.dtype_key(dtype, path)
        at = dkey[0]
        st = tree.struct_type_for(dkey)
        if not st or tree.struct_body(st, at) is None:
            if (dkey, st) not in seen:
                seen.add((dkey, st))
                clears.append((dtype, st or "?", "-",
                               "struct type unresolved (%s in %s)" % (macro, path.name)))
            continue
        if (dkey, st) in seen:
            continue
        seen.add((dkey, st))
        inline = dtype.startswith("<inline:")
        idx = tree.mark_index(dkey)
        m_direct, m_helper, m_named = idx["dmark"]
        c_direct, c_helper, _ = idx["dcompact"]
        decl_in = at if (at, st) in tree.structs_at else tree.struct_file.get(st, path)
        fields, seen_field = [], set()
        for sub in struct_types_for(tree, dkey, st):
            for f in value_fields_deep(tree, sub, at):
                if f[0] not in seen_field:
                    seen_field.add(f[0])
                    fields.append(f)
        for field, is_ptr in fields:
            # Round 5 (a): the key carries the dtype. The old (struct, field) key let
            # msgpack's marking `buffer_data_type` clear `msgpack_buffer_t` on behalf of
            # `buffer_view_data_type`, whose .dmark is NULL. Two wrappers of one struct
            # are two verdicts, and the safe one must not speak for the unsafe one.
            if (dkey, st, field) in reported:
                continue
            # The ONE case the de-dupe was added for: a gem under an #ifdef carrying both
            # TypedData_Make_Struct and legacy Data_Make_Struct wraps the same struct
            # twice, and the pseudo-dtype has no dcompact by construction, so reporting it
            # would double every line and invent a NO-COMPACT on a gem that has one.
            if inline and (st, field) in typed_seen:
                continue
            reported.add((dkey, st, field))
            if not inline:
                typed_seen.add((st, field))

            # A qualified field (`c.sf`, from a NAMED aggregate member) is written
            # `dat->c.sf` at the mark site. mark_index now keys BOTH the bare tokens and
            # the member-access path (arg_paths), so the qualified name is tried first and
            # matches exactly.
            #
            # ROUND 9: THE LEAF FALLBACK IS CONDITIONAL, AND THE CONDITION IS THE WHOLE FIX.
            # Falling back to the leaf unconditionally let one member's mark clear its
            # sibling: `outer { left_t left; right_t right; }`, both inner types holding a
            # `VALUE held`, a dmark marking only `w->left.held` -- and `right.held`
            # discharged on the token `held`, so the tree reported ZERO suspects on a field
            # ordinary GC can free. The fallback now stands down whenever the index holds a
            # DIFFERENT qualified path ending in the same leaf: that is positive evidence
            # that the mark was about some other member, and it is exactly the case the
            # leaf name cannot distinguish.
            #
            # It is kept otherwise, and that is not a hedge. A helper marking `p->held` on
            # a pointer to the inner struct records the leaf and no path at all -- msgpack's
            # shape -- and without the fallback every field reached through the deep
            # enumeration would report UNMARKED on code that marks it. The residual, stated:
            # a dmark that marks one member by path AND another through such a helper loses
            # the fallback for the second and over-REPORTS it. That is the direction this
            # predicate is allowed to be wrong in.
            leaf = field.rsplit(".", 1)[-1]
            def _pick(d):
                if d.get(field):
                    return d[field]
                if leaf == field:
                    return None
                if any(k != field and "." in k and k.rsplit(".", 1)[-1] == leaf
                       for k in d):
                    return None
                return d.get(leaf)
            kind = stronger(_pick(m_direct), _pick(m_helper))
            via_helper = not _pick(m_direct) and bool(_pick(m_helper))
            in_compact = bool(_pick(c_direct) or _pick(c_helper))
            cat = None
            if kind is None:
                # Round 5 (b): presence in the body is not a mark. Separating these two
                # keeps the recall honest -- MENTIONED says "we saw the name and it was
                # not in a marking call", which is a question for pass 2, not a verdict.
                cat = ("MENTIONED" if field in m_named or leaf in m_named
                       else "UNMARKED")
            elif is_ptr:
                cat = "VALUE*"
            elif kind == "movable" and not in_compact:
                # Round 5 (c): reached through a callee, so which argument the movable
                # primitive applied to is not resolved here. Report, do not clear.
                cat = "NO-COMPACT-UNKNOWN" if via_helper else "NO-COMPACT"
            if cat:
                suspects.append((cat, decl_in, st, field, dtype,
                                 tree.dtype_entry(dkey)))
            else:
                how = "via helper" if via_helper else "direct"
                clears.append((dtype, st, field, "marked %s (%s)%s"
                               % (kind, how, "+dcompact" if in_compact else "")))
    return suspects, clears


# --------------------------------------------------- predicate A: severity grading
#
# A SEVERITY GRADER ON EXISTING UNMARKED HITS, NOT A DISCOVERY PASS. It adds a COLUMN to
# rows sweep() already produced and must never add or remove one -- grade_suspects()
# asserts its keys are a subset of the suspect keys, and --verify-column re-runs the whole
# sweep from a fresh Tree and compares the (struct, field) sets.
#
# The instance: stackprof `_stackprof.interval` (tmm1/stackprof#244). `NUM2INT` falls
# through to `rb_to_int`, which converts a TEMPORARY; the code then stores the UNCONVERTED
# ORIGINAL, so an unmarked field holding it points at a heap object nobody marked whenever
# the caller passed a Rational, a Complex, or any `#to_int` duck type. Integer and Float
# are immediates, which is the whole reason it is latent rather than a daily crash.
#
# Grades, most severe first, and the ORDER is the design:
#
#   IMMEDIATE-ONLY    every store is DIRECTLY an immediate constructor -- outranks
#                     HEAP-IF-COERCED, because NUM2LONG-ing a field that only ever holds
#                     INT2FIX is not a defect
#   HEAP-IF-COERCED   the stored value is a bare identifier that a NUM2*/rb_num2* touches
#   REGISTERED        rb_global_variable roots the slot. A DOWNGRADE, NOT A CLEAR
#   IMMEDIATE-ONLY    (weak tier) all local sources immediate, or narrowed by an equality
#                     chain against immediates in a function that can raise
#   (ungraded)        none of the above -- full severity, pass 2 decides. mysql2's
#                     fieldTypes and msgpack's io/io_buffer land here, which is correct
#
# REGISTERED stays a downgrade rather than a clear for two reasons, one of them structural:
# registration is per-slot (round 4 measured stackprof's registered `empty_string` pinned
# while its unregistered sibling `objtracer` was not), and clearing would delete a row,
# which is the one thing this pass is forbidden to do.

GRADABLE = {"UNMARKED", "MENTIONED"}

# Every one of these funnels into rb_to_int/rb_to_flo and converts a temporary. FIX2INT
# is in the list because it is not the Fixnum-only fast path its name suggests:
# ruby/internal/arithmetic/int.h:158-169 has rb_num2int_inline fall through to rb_num2int
# for anything non-FIXNUM, and rb_fix2int does the same.
COERCE_PRIM = re.compile(
    r"^(?:RB_)?(?:NUM2(?:INT|UINT|LONG|ULONG|SHORT|USHORT|CHR|LL|ULL|SIZET|SSIZET|DBL"
    r"|OFFT|MODET|PIDT|UIDT|GIDT|TIMET|DEVT)"
    r"|FIX2(?:INT|UINT|LONG|ULONG|SHORT|USHORT))$"
    r"|^rb_(?:num2|fix2)\w+$"
    r"|^rb_(?:to_int|to_integer|check_to_int|check_to_integer|Integer|Float)$")

REGISTER_PRIM = re.compile(
    r"^(?:rb_global_variable|rb_gc_register_address|rb_gc_register_mark_object)$")

RAISES = re.compile(r"\b(?:rb_raise|rb_fatal|rb_bug|rb_exc_raise|rb_throw)\s*\(")

IMMEDIATE_CONST = {"Qnil", "Qfalse", "Qtrue", "Qundef",
                   "RUBY_Qnil", "RUBY_Qfalse", "RUBY_Qtrue", "RUBY_Qundef"}
# INT2FIX is immediate; INT2NUM IS NOT, and the difference is the whole grade. Measured in
# ruby/internal/arithmetic/int.h:239 -- rb_int2num_inline returns RB_INT2FIX(v) only when
# RB_FIXABLE(v), and rb_int2big(v) otherwise, which is a heap Bignum. Same for LONG2NUM
# (long.h:308). DBL2NUM is excluded for the same reason: flonums are conditional.
IMMEDIATE_CALL = re.compile(r"^(?:RB_)?(?:INT2FIX|LONG2FIX|UINT2FIX|ULONG2FIX|CHR2FIX)$")
ID2SYM_CALL = re.compile(r"^(?:RB_)?(?:STATIC_)?ID2SYM$")
# `ID2SYM(rb_intern(...))` is immediate; `ID2SYM(rb_to_symbol(str))` and rb_str_intern are
# NOT -- a dynamic symbol is a collectable heap object. The split is documented per
# function in ruby/internal/symbol.h: rb_intern/rb_intern2/rb_intern_str/rb_to_id each say
# "would become static ones; i.e. would never be garbage collected", and rb_to_symbol
# (:226) says "would become dynamic ones; i.e. would be garbage collected". So the
# discriminator is the interning function, NOT rb_intern-vs-rb_intern_str.
STATIC_INTERN = re.compile(r"^(?:rb_intern|rb_intern2|rb_intern3|rb_intern_str"
                           r"|rb_intern_const|rb_intern_str_const|rb_to_id)$")

CAST = re.compile(r"^\(\s*(?:const\s+|unsigned\s+|signed\s+)*"
                  r"(?:VALUE|ID|long|int|unsigned|uintptr_t|intptr_t)\s*\*?\s*\)")


def unwrap(expr):
    """Strip casts and redundant outer parens: `(VALUE)(x)` -> `x`."""
    e = expr.strip()
    while True:
        m = CAST.match(e)
        if m:
            e = e[m.end():].strip()
            continue
        if e.startswith("(") and call_args("f" + e, 1)[1] == len(e) + 1:
            e = e[1:-1].strip()
            continue
        return e


def split_call(expr):
    """`foo(a, b)` -> ("foo", ["a","b"]); anything else -> (None, None)."""
    m = re.match(r"^([A-Za-z_]\w*)\s*\(", expr)
    if not m:
        return None, None
    args, end = call_args(expr, m.end() - 1)
    return (m.group(1), args) if args is not None and end == len(expr) else (None, None)


def is_immediate(tree, expr, depth=3, seen=None):
    """Does this expression provably yield an immediate VALUE -- one GC never collects?

    Conservative by construction: unknown means False. This is the only test in the file
    whose FALSE answer is the safe one, because a wrong True downgrades a live suspect.
    """
    seen = seen if seen is not None else set()
    e = unwrap(expr)
    if not e:
        return False
    if "?" in e:
        arms = dtype_candidates(e)
        if len(arms) == 2:
            return all(is_immediate(tree, a, depth, seen) for a in arms)
    if e.isidentifier():
        if e in IMMEDIATE_CONST:
            return True
        if depth <= 0 or e in seen or e not in tree.static_values:
            return False
        seen.add(e)
        srcs = tree.value_sources(e)
        return bool(srcs) and all(is_immediate(tree, s, depth - 1, seen) for s in srcs)
    fn, args = split_call(e)
    if fn is None:
        return False
    if IMMEDIATE_CALL.match(fn):
        return True
    if ID2SYM_CALL.match(fn) and args and len(args) == 1:
        inner, iargs = split_call(unwrap(args[0]))
        return bool(inner and STATIC_INTERN.match(inner) and iargs)
    return False


class Grader:
    """Predicate A, over one tree. One instance per tree; nothing here mutates the tree."""

    def __init__(self, tree):
        self.tree = tree
        self._by_path = {}
        for fn, (path, a, b) in tree.func_spans.items():
            self._by_path.setdefault(path, []).append((a, b, fn))
        # `func_spans` is keyed by bare name and holds the FIRST definition only, so in a
        # tree where two files define `mark` the second file's body had no span at all and
        # every tier-4 lookup inside it returned "no enclosing function". The per-file
        # index has both.
        for (path, fn), (a, b) in self.tree.func_spans_at.items():
            if (a, b, fn) not in self._by_path.get(path, ()):
                self._by_path.setdefault(path, []).append((a, b, fn))
        for v in self._by_path.values():
            v.sort()

    def enclosing(self, path, idx):
        for a, b, fn in self._by_path.get(path, ()):
            if a <= idx < b:
                return fn, a, b
        return None, None, None

    def stores(self, field):
        """[(path, offset, owner, rhs)] for every `X.field =` / `X->field =` in the tree.

        Deliberately not scoped to the struct type: the owner token is all a text scan
        has. A same-named field on an unrelated struct therefore adds stores, which makes
        IMMEDIATE-ONLY (an all-stores test) strictly HARDER to earn -- the safe direction.
        REGISTERED is protected differently, by requiring the registration to name an
        owner token that some store also names.
        """
        pat = re.compile(r"([A-Za-z_]\w*)\s*(?:\.|->)\s*%s\b\s*(?:\[[^\[\]]*\])?\s*=(?!=)"
                         % re.escape(field))
        out = []
        for path, src in self.tree.files.items():
            for m in pat.finditer(src):
                out.append((path, m.end() - 1, m.group(1), rhs_after(src, m.end() - 1)))
        return out

    def coercions(self, field, stores):
        """[(prim, path, offset)] -- evidence the stored VALUE is run through a conversion.

        Two routes, and stackprof's `interval` has both:

          source  the store's RHS is a BARE IDENTIFIER (so the field gets the original,
                  not a conversion result) and some NUM2* in the SAME function takes it.
                  stackprof.c:213 NUM2INT(interval), :238 NUM2UINT(interval), stored :251.
          read    a NUM2* elsewhere takes `.field` / `->field` by name. stackprof.c:704.

        The bare-identifier requirement on the source route is what keeps the safe form
        `w->f = rb_to_int(arg);` out: there the field holds the CONVERTED object, which is
        the fix, not the bug.
        """
        out = []
        want = set()
        for path, off, _owner, rhs in stores:
            r = unwrap(rhs)
            if re.fullmatch(r"[A-Za-z_]\w*", r):
                fn, a, b = self.enclosing(path, off)
                if fn:
                    want.add((path, a, b, r))
        fpat = re.compile(r"(?:\.|->)\s*%s\b" % re.escape(field))
        for path, src in self.tree.files.items():
            for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?=\()", src):
                if not COERCE_PRIM.match(m.group(1)):
                    continue
                args, _ = call_args(src, m.end())
                if not args:
                    continue
                text = " ".join(args)
                if fpat.search(text):
                    out.append((m.group(1), path, m.start()))
                    continue
                toks = arg_tokens(args)
                for wpath, a, b, name in want:
                    if wpath == path and a <= m.start() < b and name in toks:
                        out.append((m.group(1), path, m.start()))
                        break
        return out

    def registrations(self, field, stores):
        owners = {o for _, _, o, _ in stores}
        fpat = re.compile(r"(?:\.|->)\s*%s\b" % re.escape(field))
        out = []
        for path, src in self.tree.files.items():
            for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?=\()", src):
                if not REGISTER_PRIM.match(m.group(1)):
                    continue
                args, _ = call_args(src, m.end())
                if not args:
                    continue
                text = " ".join(args)
                if fpat.search(text) and (arg_tokens(args) & owners):
                    out.append((m.group(1), path, m.start()))
        return out

    def _chain_rejects(self, body, at):
        """Does the if/else-if chain whose condition sits at `at` END in a raise?

        ROUND 7: THE RAISE HAS TO BELONG TO THE CHAIN. The test used to be
        `RAISES.search(body)` over the WHOLE function, so any unrelated argument check
        elsewhere satisfied it:

            if (input == Qnil) return Qfalse;      /* rejects nothing */
            if (bad()) rb_raise(rb_eArgError, "");  /* nothing to do with `input` */
            w->held = input;                        /* every heap object reaches here */

        graded IMMEDIATE-ONLY on a field that holds an arbitrary caller object. A
        comparison narrows only if the value that matched NO arm is rejected, which
        textually means the chain's final `else` raises -- so walk the chain forward from
        the `if` containing the comparison, through each `else if`, and require a terminal
        `else` whose body raises. stackprof's `else rb_raise(...)` is exactly that shape
        and still qualifies; the two-`if` sequence above no longer does.
        """
        # Back up to the `if (` whose condition contains the comparison.
        head = body.rfind("if", 0, at)
        while head >= 0:
            args, past = call_args(body, head + 2)
            if args is not None and head + 2 <= at < past:
                break
            head = body.rfind("if", 0, head)
        if head < 0:
            return False
        i = past
        while True:
            # Step over the arm: a braced block, or a single statement to its `;`.
            while i < len(body) and body[i] in " \t\r\n":
                i += 1
            if i < len(body) and body[i] == "{":
                close = match_brace(body, i)
                if close < 0:
                    return False
                i = close + 1
            else:
                semi = body.find(";", i)
                if semi < 0:
                    return False
                i = semi + 1
            m = re.match(r"\s*else\b", body[i:])
            if not m:
                return False                  # chain ends with no else: nothing rejected
            i += m.end()
            nxt = re.match(r"\s*if\b", body[i:])
            if not nxt:
                # The terminal `else`. Its arm is the path where no comparison matched.
                j = i
                while j < len(body) and body[j] in " \t\r\n":
                    j += 1
                if j < len(body) and body[j] == "{":
                    close = match_brace(body, j)
                    arm = body[j:close + 1] if close > 0 else body[j:]
                else:
                    semi = body.find(";", j)
                    arm = body[j:semi + 1] if semi > 0 else body[j:]
                return bool(RAISES.search(arm))
            i += nxt.end()
            args, past = call_args(body, i)
            if args is None:
                return False

    def narrowed(self, path, a, b, name):
        """Is local `name` constrained to immediates by an equality chain that rejects?

        stackprof's `mode` starts as `rb_hash_aref(opts, sym_mode)` -- arbitrary -- and is
        then run through `if (mode == sym_object) ... else if (mode == sym_wall || mode ==
        sym_cpu) ... else if (mode == sym_custom) ... else rb_raise(...)`. Nothing in the
        assignment set proves it is a symbol; the else-raise does.

        All three conditions are load-bearing. A comparison with no rejection path narrows
        nothing, so the raise is required; one comparison against a non-immediate means
        the chain admits a heap object, so ALL of them must be immediate. Round 7 added
        the fourth: the raise must terminate the chain the comparison is IN -- see
        _chain_rejects.
        """
        src = self.tree.files[path]
        body = src[a:b]
        if not RAISES.search(body):
            return None
        pat = re.compile(r"(?:\b%s\s*[=!]=\s*([A-Za-z_]\w*)"
                         r"|([A-Za-z_]\w*)\s*[=!]=\s*%s\b)"
                         % (re.escape(name), re.escape(name)))
        hits, first = [], None
        rejected = False
        for m in pat.finditer(body):
            tok = m.group(1) or m.group(2)
            if not is_immediate(self.tree, tok):
                return None
            hits.append(tok)
            first = first if first is not None else a + m.start()
            rejected = rejected or self._chain_rejects(body, m.start())
        return (sorted(set(hits)), first) if hits and rejected else None

    def grade(self, field):
        """(grade, [evidence], n_stores). grade is None when nothing applies."""
        st = self.stores(field)
        if not st:
            return None, ["no store found"], 0

        def where(path, off):
            ln = line_at(self.tree.files[path], off)
            return "@:%d" % ln, "@%s:%d" % (path.name, ln)

        # Tier 1. Every store is DIRECTLY an immediate, so no heap object ever lands here
        # and a NUM2* read of it proves nothing. This tier outranks HEAP-IF-COERCED.
        if all(is_immediate(self.tree, rhs) for _, _, _, rhs in st):
            kinds = sorted({unwrap(rhs).split("(")[0].strip() or "?"
                            for _, _, _, rhs in st})
            return "IMMEDIATE-ONLY", ["stores only %s" % "|".join(kinds)], len(st)

        co = self.coercions(field, st)
        if co:
            ev, seen = [], set()
            for prim, path, off in sorted(co, key=lambda c: (c[1].name, c[2])):
                same, other = where(path, off)
                key = (prim, same)
                if key in seen:
                    continue
                seen.add(key)
                ev.append("%s %s" % (prim, same if len(self.tree.files) == 1 else other))
            return "HEAP-IF-COERCED", ev[:3], len(st)

        reg = self.registrations(field, st)
        if reg:
            prim, path, off = reg[0]
            same, other = where(path, off)
            return "REGISTERED", ["%s %s"
                                  % (prim, same if len(self.tree.files) == 1 else other)], \
                len(st)

        # Tier 4. Weaker than tier 1: it needs the local flow inside the storing function,
        # or an equality chain, and it loses to HEAP-IF-COERCED on purpose.
        #
        # ALL stores, not the first one that qualifies. A field written from a narrowed
        # symbol in one function and from an arbitrary object in another is not
        # IMMEDIATE-ONLY, and a first-match loop calls it one -- the downgrade direction,
        # which is the only direction that can make a broken field read as safe.
        #
        # ROUND 7, TWO WAYS THE LOCAL-SOURCES ROUTE READ THE WRONG ASSIGNMENTS.
        #
        # It scanned the WHOLE function body for `r = ...` and asked whether every hit was
        # immediate, with no regard for where the store sits among them. So
        #
        #     w->held = input;    /* the arbitrary incoming object */
        #     input = Qnil;       /* ...reused AFTERWARDS */
        #
        # graded the field IMMEDIATE-ONLY on an assignment that cannot reach the store.
        # Only assignments BEFORE the store are sources of it.
        #
        # And a PARAMETER's real source is the caller, which no scan of this body can see.
        # `srcs` came back empty for a parameter, which the `srcs and` guard already
        # handled -- but a parameter that is also reassigned before the store looked
        # fully-sourced while the incoming value remained arbitrary. A parameter is
        # therefore never discharged by this route; the narrowing route below still
        # applies to it, because an equality chain constrains a parameter as well as a
        # local. stackprof's `mode` is a LOCAL assigned from rb_hash_aref, so the grade
        # that motivated the tier is unaffected.
        why = []
        for path, off, _owner, rhs in st:
            r = unwrap(rhs)
            if is_immediate(self.tree, r):
                continue
            fn, a, b = (self.enclosing(path, off)
                        if re.fullmatch(r"[A-Za-z_]\w*", r) else (None, None, None))
            if not fn:
                return None, [], len(st)
            body = self.tree.files[path][a:b]
            srcs = [rhs_after(body, m.end() - 1) for m in
                    re.finditer(r"(?<![\w.>])%s\s*=(?![=])" % re.escape(r), body)
                    if m.end() - 1 < off - a]
            if srcs and r not in self.tree.params_of(fn, path) \
                    and all(is_immediate(self.tree, s) for s in srcs):
                why.append("all local sources immediate in %s" % fn)
                continue
            nar = self.narrowed(path, a, b, r)
            if not nar:
                return None, [], len(st)
            toks, at = nar
            why.append("== %s %s" % ("|".join(toks), where(path, at)[0]))
        return ("IMMEDIATE-ONLY", why[:2], len(st)) if why else (None, [], len(st))


def grade_suspects(tree, suspects):
    """{(dtype, struct, field): (grade, evidence, n_stores)} for the GRADABLE suspects.

    Adds a column, never a row: the assertion below is the cheap always-on half of that
    guarantee (--verify-column and the self-test do the expensive whole-tree half).
    """
    g = Grader(tree)
    memo, out = {}, {}
    for cat, _path, st, field, dtype, _dt in suspects:
        if cat not in GRADABLE:
            continue
        if field not in memo:
            memo[field] = g.grade(field)
        out[(dtype, st, field)] = memo[field]
    keys = {(d, s, f) for _c, _p, s, f, d, _t in suspects}
    assert set(out) <= keys, "predicate A invented a row: %s" % (set(out) - keys)
    return out


def report(name, tree, suspects, clears, verbose, grades=None):
    for cat, path, st, field, dtype, dt in suspects:
        col, ev = cat, ""
        if grades is not None and (dtype, st, field) in grades:
            grade, why, _n = grades[(dtype, st, field)]
            col = "%s/%s" % (cat, grade) if grade else cat
            if grade:
                ev = "  [%s]" % ", ".join(why)
        print("%-26s %s: struct %s: VALUE %s  (%s: dmark=%s dcompact=%s)%s"
              % (col, path, st, field, dtype,
                 dt.get("dmark", "-"), dt.get("dcompact", "-"), ev))
    if verbose:
        for dtype, st, field, why in clears:
            print("  cleared: %s.%s (%s) -- %s" % (st, field, dtype, why),
                  file=sys.stderr)
    # Coverage, so a zero is readable without re-deriving it. "0 suspects, 0 cleared"
    # means one of two completely different things -- the gem wraps nothing (structurally
    # out of scope: rinku, bcrypt, bootsnap) or the wrapped struct holds no VALUE at all
    # (nio4r's ByteBuffer, mittens' stemmer_t) -- versus the third possibility this line
    # exists to expose: the query failed to resolve the wrap sites. Silence is a property
    # of the query until the counts say otherwise.
    unresolved = sum(1 for _, _, f, why in clears
                     if f == "-" and why.startswith("struct type unresolved"))
    # Predicate A's own zero, same rule. "0 HEAP-IF-COERCED" is only meaningful next to
    # how many rows the grader was OFFERED and how many it could find a store for at all:
    # `0/0 gradable` is a tree with no UNMARKED rows, `4/4 graded` is a real answer, and
    # `0/4 graded, 4 no-store` is the grader failing to resolve, wearing the same zero.
    if grades is None:
        cov = ", grader off"
    else:
        counts, nostore = {}, 0
        for grade, _why, n in grades.values():
            counts[grade] = counts.get(grade, 0) + 1
            nostore += (n == 0)
        got = sum(v for k, v in counts.items() if k)
        cov = (", grader %d/%d graded [%s%d no-store]"
               % (got, len(grades),
                  "".join("%d %s, " % (counts[g], g) for g in
                          ("HEAP-IF-COERCED", "REGISTERED", "IMMEDIATE-ONLY")
                          if counts.get(g)),
                  nostore))
    # C++ overloads that collide on the bare name -- every one of them a place where a
    # callee body COULD be picked by file order. Since round 6 (B1) the pick is made from
    # the receiver's static type, so this count is a hazard tally, not a verdict tally;
    # the number that says an arbitrary pick actually HAPPENED is the second one.
    # Non-zero `first-wins` means some verdict in this tree still rests on file order.
    overloaded = sum(1 for n, c in tree.func_defs.items() if c > 1)
    amb = ", %d overloaded name(s)" % overloaded if overloaded else ""
    picks = sum(tree.ambiguous.values())
    amb += (", %d first-wins pick(s) over %d name(s)"
            % (picks, len(tree.ambiguous))) if picks else ""
    # Names two translation units both define. Since round 7 these RESOLVE per file rather
    # than first-wins, so this is a hazard tally like the overload count and not a verdict
    # tally -- but it is the number that says a tree contains the shape at all, and
    # nokogiri (two `static void mark`s) is the corpus instance that made it worth
    # printing. A tree with a non-zero count and a surprising verdict is worth a second
    # look at WHICH file the sweep resolved in.
    shadow = {k: sum(1 for n in v.values() if n > 1) for k, v in tree.shadowed.items()}
    amb += "".join(", %d shadowed %s name(s)" % (n, k)
                   for k, n in sorted(shadow.items()) if n)
    # ...and the pick that a shadowed name actually caused, which is a different signal.
    # A file that does not define the name still falls back to the tree-wide first-wins
    # entry, so a non-zero count here says a verdict in this tree rests on file order and
    # names WHICH symbol to go and look at. Zero across the corpus, including nokogiri.
    for kind, picked in sorted(tree.cross_picks.items()):
        amb += (", %d cross-file pick(s) over %d shadowed %s name(s): %s"
                % (sum(picked.values()), len(picked), kind,
                   ",".join(sorted(picked)[:4])))
    print("%s: %d suspect(s), %d field(s) cleared "
          "[%d wrap site(s), %d dtype(s), %d unresolved%s]%s"
          % (name, len(suspects), len([c for c in clears if c[2] != "-"]),
             len(tree.wrap_sites), len(tree.dtypes), unresolved, amb, cov),
          file=sys.stderr)
    return len(suspects)


# ---------------------------------------------------------------- acceptance


# Round-5 red controls. Each is a MINIMAL tree written at test time, not a checked-in
# file: a hand-edited control drifts from the defect it was cut to prove, and these four
# each encode one over-clear the shipped script actually committed on real gems.
#
# Every one of them measured "0 suspects, N cleared" before the fix.

RED_A = """
#include <ruby.h>
typedef struct { VALUE payload; } buf_t;
static void buf_mark(void *p) { buf_t *b = (buf_t *)p; rb_gc_mark(b->payload); }
static void buf_free(void *p) { xfree(p); }
/* Two wrappers, one struct. The view marks NOTHING and must not be spoken for by the
   owner. This is msgpack buffer_class.c:127/:137 reduced to its bones. */
static const rb_data_type_t buf_data_type      = { "buf",  { buf_mark, buf_free, }, };
static const rb_data_type_t buf_view_data_type = { "view", { NULL,     buf_free, }, };
static VALUE a1(VALUE k) { buf_t *b; return TypedData_Make_Struct(k, buf_t, &buf_data_type, b); }
static VALUE a2(VALUE k) { buf_t *b; return TypedData_Make_Struct(k, buf_t, &buf_view_data_type, b); }
"""

RED_B = """
#include <ruby.h>
typedef struct { VALUE callback; VALUE other; } wrapper_t;
static void w_free(void *p) { xfree(p); }
/* `callback` is READ, never MARKED. Presence in the body is not a mark. */
static void w_mark(void *p) { wrapper_t *w = (wrapper_t *)p; if (w->callback) rb_gc_mark(w->other); }
static const rb_data_type_t w_type = { "wrapper", { w_mark, w_free, }, };
static VALUE w_alloc(VALUE k) { wrapper_t *w; return TypedData_Make_Struct(k, wrapper_t, &w_type, w); }
"""

RED_C = """
#include <ruby.h>
typedef struct { VALUE cb; } holder_t;
static void h_free(void *p) { xfree(p); }
/* The movable primitive is one frame away, so the field name sits nowhere near it. */
static void mark_value(VALUE v) { rb_gc_mark_movable(v); }
static void h_mark(void *p) { holder_t *h = (holder_t *)p; mark_value(h->cb); }
static const rb_data_type_t h_type = { "holder", { h_mark, h_free, }, };   /* no dcompact */
static VALUE h_alloc(VALUE k) { holder_t *h; return TypedData_Make_Struct(k, holder_t, &h_type, h); }
"""

RED_TERNARY = """
#include <ruby.h>
typedef struct { VALUE payload; } tbuf_t;
static void t_mark(void *p) { tbuf_t *b = (tbuf_t *)p; rb_gc_mark(b->payload); }
static void t_free(void *p) { xfree(p); }
static const rb_data_type_t t_data_type      = { "t",     { t_mark, t_free, }, };
static const rb_data_type_t t_view_data_type = { "tview", { NULL,   t_free, }, };
/* The dtype is chosen by a ternary -- msgpack buffer_class.c:151. Both arms are wrap
   sites; taking the expression whole matches no dtype and the site disappears. */
static tbuf_t *t_get(VALUE o, int view) {
    tbuf_t *b;
    TypedData_Get_Struct(o, tbuf_t, view ? &t_view_data_type : &t_data_type, b);
    return b;
}
"""

# A fifth over-clear, same family: the struct type was discarded at the wrap site rather
# than mis-read. The two halves have to appear TOGETHER to reproduce it -- a `zalloc` form
# whose struct type lives only inside `sizeof`, and a dtype with no callback bodies for
# `struct_type_for` to fall back on. Either alone still resolves. Measured before the fix:
# "0 suspect(s), 0 cleared [1 wrap site(s), 1 dtype(s), 1 unresolved]" on a struct whose
# every VALUE field is a suspect by construction.
RED_ZALLOC = """
#include <ruby.h>
typedef struct { VALUE held; } zbox_t;
/* Callbacks all default: nothing here casts the payload or takes its sizeof. */
static const rb_data_type_t z_type = { "zbox", { 0, RUBY_DEFAULT_FREE, 0, 0, }, };
static VALUE z_alloc(VALUE k) { return rb_data_typed_object_zalloc(k, sizeof(zbox_t), &z_type); }
"""

RED_CXX_CLASS = """
#include <ruby.h>
/* C++, with a base clause. vernier is written this way and every one of its classes was
   invisible: `struct|union` does not match `class`, so the gem measured 0 suspects with
   3 unresolved sites while holding three genuinely unmarked VALUEs. */
class Base { public: int n; };
class Collector : public Base {
    public:
        VALUE held;
        void mark() { }
};
static void c_free(void *p) { delete (Collector *)p; }
static void c_mark(void *p) { Collector *c = static_cast<Collector *>(p); c->mark(); }
static const rb_data_type_t c_type = { "collector", { c_mark, c_free, }, };
static VALUE c_wrap(VALUE k, Collector *c) { return TypedData_Wrap_Struct(k, &c_type, c); }
"""

# Round 6, defect B1: the callee of a member call was indexed by BARE NAME, first-wins, so
# `collector->mark()` bound to whichever `mark()` body `rglob` happened to put first. This
# is vernier reduced to that shape -- FOUR `mark()` bodies, the empty one first in file
# order, the real one on the BASE class, and a call site whose receiver is declared right
# there. It is a GREEN and a RED at once, and both halves are load-bearing:
#
#   stack_table_value  MUST NOT be reported. BaseCollector::mark marks it; only the empty
#                      Thread::mark makes it look unmarked. Before B1 it WAS reported --
#                      and on the real gem that was a false positive on the safest field
#                      in the file.
#   start_thread       MUST still be reported. Nothing marks it, and a fix that resolves
#                      the receiver by clearing everything the call touches would hide it.
#
# Two further things the shape pins down. `TimeCollector::mark` is an OVERRIDE that is
# never walked, because the receiver's STATIC type is BaseCollector -- the resolution is
# by declared type, not by the set of bodies that share a name. And `threads.mark()`
# resolves through a DATA MEMBER's type, which is the second receiver form.
RED_CXX_OVERLOAD = """
#include <ruby.h>
class Thread {
    public:
        VALUE ruby_thread;
        /* FIRST in file order, and EMPTY. */
        void mark() { }
};
class ThreadTable {
    public:
        std::vector<Thread *> list;
        void mark() {
            for (auto t : list) { t->mark(); }
        }
};
class BaseCollector {
    public:
    VALUE stack_table_value;
    VALUE start_thread;
    virtual void mark() {
        rb_gc_mark(stack_table_value);
    }
};
class TimeCollector : public BaseCollector {
    public:
    ThreadTable threads;
    void mark() {
        rb_gc_mark(stack_table_value);
        threads.mark();
    }
};
static void collector_mark(void *data) {
    BaseCollector *collector = static_cast<BaseCollector *>(data);
    collector->mark();
}
static void collector_free(void *data) { delete (BaseCollector *)data; }
static const rb_data_type_t rb_collector_type = {
    .wrap_struct_name = "collector",
    .function = { .dmark = collector_mark, .dfree = collector_free, },
};
static BaseCollector *get_collector(VALUE obj) {
    BaseCollector *collector;
    TypedData_Get_Struct(obj, BaseCollector, &rb_collector_type, collector);
    return collector;
}
"""

RED_COMPACT_DECL = """
#include <ruby.h>
/* Two ordinary C spellings the ^-anchored field matcher could not see. Both dmarks are
   NULL, so both fields are genuinely unmarked -- and with zero fields found, the struct
   read as "holds no VALUE, structurally out of scope" and printed 0 suspects, 0 cleared.
   The coverage line cannot catch this one: the counts really are zero. */
typedef struct { int n; VALUE held; } compact_t;
typedef struct { VALUE* items; long len; } starred_t;
static void c_free(void *p) { xfree(p); }
static const rb_data_type_t compact_type = { "compact", { NULL, c_free, }, };
static const rb_data_type_t starred_type = { "starred", { NULL, c_free, }, };
static VALUE c_alloc(VALUE k) { compact_t *c; return TypedData_Make_Struct(k, compact_t, &compact_type, c); }
static VALUE s_alloc(VALUE k) { starred_t *s; return TypedData_Make_Struct(k, starred_t, &starred_type, s); }
"""

# GREEN control for the macro tier: a gem that updates through its OWN #define is safe,
# and fixing (b) turned mysql2's patched tree red until macros were indexed as callees.
GREEN_MACRO = """
#include <ruby.h>
#define my_gc_location(ptr) ptr = rb_gc_location(ptr)
typedef struct { VALUE held; } mbox_t;
static void m_free(void *p) { xfree(p); }
static void m_mark(void *p) { mbox_t *m = (mbox_t *)p; rb_gc_mark_movable(m->held); }
static void m_compact(void *p) { mbox_t *m = (mbox_t *)p; my_gc_location(m->held); }
static const rb_data_type_t m_type = { "mbox", { m_mark, m_free, NULL, m_compact, }, };
static VALUE m_alloc(VALUE k) { mbox_t *m; return TypedData_Make_Struct(k, mbox_t, &m_type, m); }
"""


# -- predicate A controls ---------------------------------------------------------------
#
# stackprof reduced to its bones, and to the three grades it exercises. The registered
# field and the wrapped-by-memsize resolution are copied from the real gem rather than
# simplified: `TypedData_Wrap_Struct(rb_cObject, &stackprof_type, &_stackprof)` names no
# struct type, so the ONLY thing that resolves the payload is `stackprof_memsize` returning
# `sizeof(_stackprof)`. A fixture that resolved some easier way would not be testing the
# path the real gem takes.

_COERCE_BODY = """
/* One field per LINE. `struct { VALUE a; VALUE b; }` all on one line yields only `a`:
   FIELD is anchored with `^[ \\t]*VALUE`, and only the first declaration in a body sits at
   the start of one. Every pre-existing fixture happens to have a single field, so this
   cost two of predicate A's three generated reds before it was noticed. */
static struct {
    VALUE mode;
    VALUE interval;
    VALUE empty;
    VALUE bound;
    VALUE pick;
} _p;
static void p_mark(void *d) { }
static void p_free(void *d) { }
static size_t p_memsize(const void *d) { return sizeof(_p); }
static const rb_data_type_t p_type = { "p", { p_mark, p_free, p_memsize, }, };
static VALUE p_start(VALUE self, VALUE opts) {
    VALUE mode = Qnil, interval = Qnil;
    mode = rb_hash_aref(opts, ID2SYM(rb_intern("mode")));
    interval = rb_hash_aref(opts, ID2SYM(rb_intern("interval")));
    if (!RTEST(mode)) mode = sym_wall;
    if (!NIL_P(interval) && NUM2INT(interval) < 1)
        rb_raise(rb_eArgError, "interval out of range");
    if (mode == sym_wall || mode == sym_cpu) { }
    else if (mode == sym_custom) { }
    else rb_raise(rb_eArgError, "unknown profiler mode");
    _p.mode = mode;         /* narrowed to static symbols by the else-raise */
    _p.interval = interval;   /* the NUM2INT'd ORIGINAL, not the conversion result */
    _p.bound = INT2FIX(1000);
    _p.pick = mode;
    return self;
}
/* `pick` gets the narrowed `mode` here and an ARBITRARY object over there. One qualifying
   store is not a grade: a tier-4 loop that returns on its first match downgrades a field
   that is only sometimes safe, which is the one direction that hides a defect. */
static VALUE p_reset(VALUE self, VALUE any) { _p.pick = any; return self; }
/* `bound` only ever holds an INT2FIX and is nevertheless read back through a NUM2*. That
   combination has to grade IMMEDIATE-ONLY, not HEAP-IF-COERCED: coercing a field that
   cannot hold a heap object is not a defect, which is why tier 1 outranks the coercion
   evidence instead of losing to it. */
static long p_bound(void) { return NUM2LONG(_p.bound); }
"""

_COERCE_TAIL = """
void Init_p(void) {
    VALUE hook = TypedData_Wrap_Struct(rb_cObject, &p_type, &_p);
    _p.empty = rb_str_new_cstr("");
    rb_global_variable(&_p.empty);
    rb_gc_mark(hook);
}
"""

RED_COERCE = ("#include <ruby.h>\nstatic VALUE sym_wall, sym_cpu, sym_custom;\n"
              + _COERCE_BODY + _COERCE_TAIL
              + 'void p_syms(void) { sym_wall = ID2SYM(rb_intern("wall"));'
                ' sym_cpu = ID2SYM(rb_intern("cpu"));'
                ' sym_custom = ID2SYM(rb_intern("custom")); }\n')

# Identical except the symbols are initialised through a `##` macro, which is how
# stackprof actually writes them (`#define S(name) sym_##name = ID2SYM(rb_intern(#name));`,
# stackprof.c:960). Nothing assigns to `sym_wall` textually, so without _expand_pastes the
# immediate analysis finds no source for it and `mode` grades UNGRADED instead of
# IMMEDIATE-ONLY -- one of the four target grades, wrong, silently.
RED_PASTE = ("#include <ruby.h>\nstatic VALUE sym_wall, sym_cpu, sym_custom;\n"
             "#define S(name) sym_##name = ID2SYM(rb_intern(#name));\n"
             + _COERCE_BODY + _COERCE_TAIL
             + "void p_syms(void) { S(wall); S(cpu); S(custom); }\n")

# GREEN: the shape of the upstream fix. `interval` stops being a VALUE at all, so the field
# leaves the sweep entirely and there is no HEAP-IF-COERCED left anywhere in the tree.
GREEN_COERCE = RED_COERCE \
    .replace("    VALUE interval;\n", "    long interval;   /* 0 == unset */\n") \
    .replace("_p.interval = interval;",
             "_p.interval = NIL_P(interval) ? 0 : NUM2LONG(interval);")

# The three refinements the corpus forced, each as a field that must stay UNGRADED. All
# three are downgrades that a plausible first cut grants and that are wrong:
#   INT2NUM        allocates a Bignum above FIXNUM_MAX -- not immediate
#   rb_to_symbol   makes a DYNAMIC symbol, a collectable heap object -- not immediate
#   Check_Type     asserts a type, it does not convert one -- not evidence of anything
RED_NOT_IMMEDIATE = """
#include <ruby.h>
static VALUE sym_a, sym_b;
typedef struct {
    VALUE n;
    VALUE sym;
    VALUE str;
    VALUE conv;
} nb_t;
static void n_mark(void *p) { }
static void n_free(void *p) { xfree(p); }
static const rb_data_type_t n_type = { "nb", { n_mark, n_free, }, };
/* No rb_raise anywhere in this function. `str` is COMPARED against two static symbols and
   is still unconstrained, because nothing rejects the values that match neither -- which
   is the difference between a comparison and a narrowing, and the reason the raise is a
   condition of the weak IMMEDIATE-ONLY tier rather than a decoration on it. */
static VALUE n_set(VALUE self, VALUE arg, long len) {
    nb_t *w;
    TypedData_Get_Struct(self, nb_t, &n_type, w);
    w->n = INT2NUM(len);
    w->sym = ID2SYM(rb_to_symbol(arg));
    Check_Type(arg, T_STRING);
    if (arg == sym_a || arg == sym_b) { }
    w->str = arg;
    return self;
}
/* THE FIX SHAPE, in its own function so it does not contaminate `str`. The field holds the
   CONVERSION RESULT, so it is exactly what a patched site looks like; a source route that
   only asks "is this token coerced somewhere near" flags it, and would then flag every
   correctly-written call site in the corpus. Only a store whose RHS is a BARE IDENTIFIER
   can be storing the unconverted original. */
static VALUE n_conv(VALUE self, VALUE arg) {
    nb_t *w;
    TypedData_Get_Struct(self, nb_t, &n_type, w);
    if (NUM2LONG(arg) < 0) return Qnil;
    w->conv = rb_to_int(arg);
    return self;
}
static VALUE n_alloc(VALUE k) { nb_t *w; return TypedData_Make_Struct(k, nb_t, &n_type, w); }
void n_syms(void) { sym_a = ID2SYM(rb_intern("a")); sym_b = ID2SYM(rb_intern("b")); }
"""


# -- round 7: three first-wins indexes that bound a name to the wrong file --------------
#
# Each is TWO files, because one file cannot express the defect: the whole shape is that a
# name defined in both resolves to whichever `rglob` returned first. In each, `a.c` is the
# innocent file whose definition was winning and `b.c` holds the field that must report.
# All three measured "0 suspect(s)" before the fix, two of them with no cleared line at
# all -- an over-clear with nothing printed to audit.
#
# nokogiri 1.19.4 is the corpus instance of the callback one, in the mirror direction: two
# `static void mark`s, `xml_document.c` first in file order, so `func_instances` reported
# UNMARKED while xslt_stylesheet.c's own `mark` marks it. Swap the file order and it is
# the unmarked field that gets cleared instead.

RED_TU_STRUCT = {"a.c": """
#include <ruby.h>
typedef struct wrapper { int n; } wrapper_t;
static void a_mark(void *p) { }
static void a_free(void *p) { xfree(p); }
static const rb_data_type_t a_type = { "a", { a_mark, a_free, }, };
static VALUE a_alloc(VALUE k) { wrapper_t *w; return TypedData_Make_Struct(k, wrapper_t, &a_type, w); }
""", "b.c": """
#include <ruby.h>
/* The SAME tag and typedef names, a different payload, and nothing marks it. Bound to
   a.c's `{ int n; }`, this struct enumerated zero VALUE fields. */
typedef struct wrapper { VALUE held; } wrapper_t;
static void b_mark(void *p) { }
static void b_free(void *p) { xfree(p); }
static const rb_data_type_t b_type = { "b", { b_mark, b_free, }, };
static VALUE b_alloc(VALUE k) { wrapper_t *w; return TypedData_Make_Struct(k, wrapper_t, &b_type, w); }
"""}

RED_TU_CALLBACK = {"a.c": """
#include <ruby.h>
typedef struct { VALUE held; } abox_t;
static void mark(void *p) { abox_t *b = (abox_t *)p; rb_gc_mark(b->held); }
static void a_free(void *p) { xfree(p); }
static const rb_data_type_t a_type = { "a", { mark, a_free, }, };
static VALUE a_alloc(VALUE k) { abox_t *b; return TypedData_Make_Struct(k, abox_t, &a_type, b); }
""", "b.c": """
#include <ruby.h>
typedef struct { VALUE held; } bbox_t;
/* A file-local callback of the same name that forgets `held` entirely. `.dmark = mark`
   resolved to a.c's body, so this wrapper cleared on the strength of an unrelated file. */
static void mark(void *p) { }
static void b_free(void *p) { xfree(p); }
static const rb_data_type_t b_type = { "b", { mark, b_free, }, };
static VALUE b_alloc(VALUE k) { bbox_t *b; return TypedData_Make_Struct(k, bbox_t, &b_type, b); }
"""}

RED_TU_DTYPE = {"a.c": """
#include <ruby.h>
typedef struct { VALUE held; } abox_t;
static void a_mark(void *p) { abox_t *b = (abox_t *)p; rb_gc_mark(b->held); }
static void a_free(void *p) { xfree(p); }
static const rb_data_type_t data_type = { "a", { a_mark, a_free, }, };
static VALUE a_alloc(VALUE k) { abox_t *b; return TypedData_Make_Struct(k, abox_t, &data_type, b); }
""", "b.c": """
#include <ruby.h>
typedef struct { VALUE held; } bbox_t;
/* A second file-local descriptor of the same name, with a NULL dmark. Keyed by bare
   name the two collapsed into one, so this wrap site was skipped as already seen and
   `bbox_t` was never enumerated at all -- no row, and no cleared line either. */
static void b_free(void *p) { xfree(p); }
static const rb_data_type_t data_type = { "b", { NULL, b_free, }, };
static VALUE b_alloc(VALUE k) { bbox_t *b; return TypedData_Make_Struct(k, bbox_t, &data_type, b); }
"""}

# A C++ default member initialiser is where a wrapper STARTS, not what it holds. Dropping
# the declaration on sight of `=` enumerated zero fields and printed one wrap site, zero
# suspects -- RED_COMPACT_DECL's failure family, and this one drops the ROW rather than
# mis-grading it. The class carries the green half too: `local` and `ctor_local` are
# LOCALS inside method bodies, and the `=` test had been suppressing them by accident.
RED_CXX_INIT = """
#include <ruby.h>
class Box {
    public:
        VALUE held = Qnil;      /* a member, unmarked: MUST report */
        VALUE marked = Qnil;    /* a member, marked: must clear */
        Box(VALUE v) : held(v) { VALUE ctor_local = Qnil; rb_gc_mark(ctor_local); }
        VALUE build() const {
            VALUE local = rb_hash_new();
            return local;
        }
        void mark() { rb_gc_mark(marked); }
};
static void b_mark(void *p) { Box *b = static_cast<Box *>(p); b->mark(); }
static void b_free(void *p) { delete (Box *)p; }
static const rb_data_type_t b_type = { "box", { b_mark, b_free, }, };
static VALUE b_wrap(VALUE k, Box *b) { return TypedData_Wrap_Struct(k, &b_type, b); }
"""

# The helper tier credited EVERY token in a call to a callee that marks ANYTHING. `note`
# marks a global and touches its parameter not at all, and `cb` cleared as
# "marked pin (via helper)" -- an over-clear reached through the one tier still crediting
# by association. `other` is the green half: a direct mark in the same body must survive.
# THE SAME FIXTURE WITH A TEMPLATE-ID IN THE MEMBER-INITIALISER LIST (#30 review, P2).
# `Derived() : Base<T>()` names a TYPE, and a type may be a template-id, which the shared
# walk's qualified-name pattern did not spell -- it stopped at the `<`, found neither `(`
# nor `{`, and REJECTED the constructor. A rejected constructor is not blanked, so its body
# is scanned as class scope and `ctor_local` reports as a phantom member.
#
# THE TEMPLATE-ID MUST BE THE LAST OR THE ONLY INITIALISER, and the first cut of this
# fixture got that wrong and therefore asserted NOTHING -- it passed with the walk deleted.
# When another initialiser FOLLOWS the template-id, `blank_method_bodies` rejects the
# constructor, resumes its scan mid-list, and mistakes that trailing `held(v)` for a method
# head: `(v)` parses as a parameter list and the next token is the body `{`, so the body is
# blanked BY ACCIDENT and the defect hides. Measured, with the walk mutated out:
#
#     : Base<VALUE>(), held(v)   ctor_local leaks = False   <- accidentally rescued
#     : held(v), Base<VALUE>()   ctor_local leaks = True
#     : Base<VALUE>()            ctor_local leaks = True    <- this fixture
#
# So the idiomatic spelling is the one that hides it, which is worth knowing on its own.
# No tree in the 99 spells any of them, so this is pinned here or nowhere.
RED_CXX_TMPL_INIT = """
#include <ruby.h>
template <typename T> class Base { public: Base() {} };
class TBox : public Base<VALUE> {
    public:
        VALUE held = Qnil;      /* a member, unmarked: MUST report */
        VALUE marked = Qnil;    /* a member, marked: must clear */
        TBox(VALUE v) : Base<VALUE>() { VALUE ctor_local = v; rb_gc_mark(ctor_local); }
        void mark() { rb_gc_mark(marked); }
};
static void t_mark(void *p) { TBox *b = static_cast<TBox *>(p); b->mark(); }
static void t_free(void *p) { delete (TBox *)p; }
static const rb_data_type_t t_type = { "tbox", { t_mark, t_free, }, };
static VALUE t_wrap(VALUE k, TBox *b) { return TypedData_Wrap_Struct(k, &t_type, b); }
"""

# ...and the variadic spelling (#30 review, second P2). `X(T... t) : T(t)... {` puts a PACK
# EXPANSION between the initialiser group and the comma-or-end, so a walk accepting only
# those two rejects the constructor exactly as the template-id did. Same symptom, same
# assertion, and it needs no trailing initialiser to bite: `...` IS the last thing in the
# list, which is the position the accidental rescue above cannot reach.
RED_CXX_PACK_INIT = """
#include <ruby.h>
template <typename... T> class PBox : public T... {
    public:
        VALUE held = Qnil;      /* a member, unmarked: MUST report */
        VALUE marked = Qnil;    /* a member, marked: must clear */
        PBox(T... t) : T(t)... { VALUE ctor_local = Qnil; rb_gc_mark(ctor_local); }
        void mark() { rb_gc_mark(marked); }
};
static void p_mark(void *p) { PBox<> *b = static_cast<PBox<> *>(p); b->mark(); }
static void p_free(void *p) { delete (PBox<> *)p; }
static const rb_data_type_t p_type = { "pbox", { p_mark, p_free, }, };
static VALUE p_wrap(VALUE k, PBox<> *b) { return TypedData_Wrap_Struct(k, &p_type, b); }
"""

RED_HELPER_PARAM = """
#include <ruby.h>
static VALUE g_root;
typedef struct { VALUE cb; VALUE other; } wbox_t;
static void note(VALUE v) { rb_gc_mark(g_root); }
static void w_free(void *p) { xfree(p); }
static void w_mark(void *p) { wbox_t *w = (wbox_t *)p; note(w->cb); rb_gc_mark(w->other); }
static const rb_data_type_t w_type = { "wbox", { w_mark, w_free, }, };
static VALUE w_alloc(VALUE k) { wbox_t *w; return TypedData_Make_Struct(k, wbox_t, &w_type, w); }
"""

# The reviewer named TWO shapes for that tier and RED_HELPER_PARAM is only the first. Here
# the helper does mark a parameter -- just not this one. It is the fixture that separates a
# parameter-INDEX mapping from the cheaper "does the callee touch any parameter at all"
# fix, which passes the global case above and clears `b` here exactly as before.
RED_HELPER_OTHER_PARAM = """
#include <ruby.h>
typedef struct { VALUE a; VALUE b; } pbox_t;
static void pair(VALUE x, VALUE y) { rb_gc_mark(x); }
static void p_free(void *p) { xfree(p); }
static void p_mark(void *p) { pbox_t *w = (pbox_t *)p; pair(w->a, w->b); }
static const rb_data_type_t p_type = { "pbox", { p_mark, p_free, }, };
static VALUE p_alloc(VALUE k) { pbox_t *w; return TypedData_Make_Struct(k, pbox_t, &p_type, w); }
"""

# Two grader defects in one function, both of which downgraded a field that holds an
# arbitrary caller object. `held` takes a PARAMETER whose only assignment comes AFTER the
# store; the whole-function source scan saw `input = Qnil` and graded IMMEDIATE-ONLY.
# `guard` is compared with an immediate in a chain that rejects nothing, while an
# unrelated `rb_raise` sits elsewhere in the body -- which the function-wide raise test
# accepted as a narrowing.
RED_STORE_FLOW = """
#include <ruby.h>
static int bad(void);
typedef struct { VALUE held; VALUE guard; } sbox_t;
static void s_mark(void *p) { }
static void s_free(void *p) { xfree(p); }
static const rb_data_type_t s_type = { "sbox", { s_mark, s_free, }, };
static VALUE s_set(VALUE self, VALUE input) {
    sbox_t *w;
    TypedData_Get_Struct(self, sbox_t, &s_type, w);
    w->held = input;      /* the arbitrary incoming object */
    input = Qnil;         /* ...reused AFTERWARDS. Not a source of the store above. */
    return self;
}
static VALUE s_guard(VALUE self, VALUE arg) {
    sbox_t *w;
    TypedData_Get_Struct(self, sbox_t, &s_type, w);
    if (arg == Qnil) { return Qfalse; }        /* rejects nothing */
    if (bad()) rb_raise(rb_eArgError, "unrelated");
    w->guard = arg;       /* every non-nil heap object reaches here */
    return self;
}
static VALUE s_alloc(VALUE k) { sbox_t *w; return TypedData_Make_Struct(k, sbox_t, &s_type, w); }
"""


def _first_dir(*candidates):
    """The first candidate that exists, or the first one, so SKIP names a real path."""
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]



# ---------------------------------------------------------------- round 9

RED_CAST_DMARK = """
#include <ruby.h>
/* A CAST ON A CALLBACK FIELD. `.dmark = (RUBY_DATA_FUNC)w_mark` is what code migrated from
   the legacy Data_Wrap_Struct API looks like, and the designator pattern demanded an
   identifier immediately after the `=`. The descriptor therefore reported dmark=- and the
   positional fallback recovered nothing (there is no `{`-prefixed group in a designated
   initialiser), so the field reported UNMARKED against a dmark that marks it with the
   PINNING rb_gc_mark. */
typedef struct { VALUE held; } cast_t;
static void w_mark(void *p) { cast_t *w = (cast_t *)p; rb_gc_mark(w->held); }
static void w_free(void *p) { xfree(p); }
static const rb_data_type_t cast_type = {
    .wrap_struct_name = "cast",
    .function = { .dmark = (RUBY_DATA_FUNC)w_mark, .dfree = (RUBY_DATA_FUNC)w_free },
};
static VALUE c_alloc(VALUE k) { cast_t *c; return TypedData_Make_Struct(k, cast_t, &cast_type, c); }
"""

# Two members of ONE inner type. `_seen` was a shared visited set, so the first member
# consumed the type and the second enumerated nothing at all. Both are marked here BY
# PATH, so the check is about the FUNNEL -- 2 fields enumerated, 0 suspects -- and it is
# red before the guard became a recursion stack whatever the leaf-fallback rule does.
RED_REPEATED_MEMBER = """
#include <ruby.h>
struct inner { VALUE held; };
struct outer { struct inner left; struct inner right; };
static void o_mark(void *p)
{
    struct outer *w = (struct outer *)p;
    rb_gc_mark(w->left.held);
    rb_gc_mark(w->right.held);
}
static void o_free(void *p) { xfree(p); }
static const rb_data_type_t outer_type = { "outer", { o_mark, o_free, }, };
static VALUE o_alloc(VALUE k) { struct outer *o; return TypedData_Make_Struct(k, struct outer, &outer_type, o); }
"""

# The same two members, and only ONE of them marked. Leaf-name matching cleared both.
RED_SIBLING_LEAF = RED_REPEATED_MEMBER.replace(
    "    rb_gc_mark(w->right.held);\n", "")

# A genuinely cyclic pair. No conforming C program can instantiate this -- a struct cannot
# contain itself by value -- but a parser reads it happily, and a recursion-stack guard has
# to terminate on it. The check is that the sweep RETURNS.
RED_CYCLIC_MEMBER = """
#include <ruby.h>
struct b_t;
struct a_t { VALUE held; struct b_t b; };
struct b_t { VALUE also; struct a_t a; };
static void a_mark(void *p) { struct a_t *w = (struct a_t *)p; rb_gc_mark(w->held); }
static void a_free(void *p) { xfree(p); }
static const rb_data_type_t a_type = { "a", { a_mark, a_free, }, };
static VALUE a_alloc(VALUE k) { struct a_t *a; return TypedData_Make_Struct(k, struct a_t, &a_type, a); }
"""

# GREEN for the leaf fallback. A helper that marks the inner struct through a POINTER to it
# records the leaf and no path at all -- msgpack's shape -- and both members must still
# clear through it. Without this, "stop falling back to the leaf" passes the sibling red
# and turns every helper-marked nested field in the corpus into a row.
GREEN_NESTED_HELPER = """
#include <ruby.h>
struct inner { VALUE held; };
struct outer { struct inner left; struct inner right; };
static void inner_mark(struct inner *i) { rb_gc_mark(i->held); }
static void o_mark(void *p)
{
    struct outer *w = (struct outer *)p;
    inner_mark(&w->left);
    inner_mark(&w->right);
}
static void o_free(void *p) { xfree(p); }
static const rb_data_type_t outer_type = { "outer", { o_mark, o_free, }, };
static VALUE o_alloc(VALUE k) { struct outer *o; return TypedData_Make_Struct(k, struct outer, &outer_type, o); }
"""


def self_test(base, siblings=()):
    """Fail loudly rather than let a broken query clear the corpus by accident.

    THE ARGUMENT IS THE CORPUS DIRECTORY, not a list of trees -- and that asymmetry with
    predicates B, C and D, whose `--self-test` takes a glob, cost two rounds a false
    failure. `--self-test ~/.cache/truffle-hunt-corpus/*` makes argparse bind the FIRST
    expanded path (`bcrypt_pbkdf-1.1.2`) to this parameter and the other 58 to `dirs`, so
    `base / "m2-red"` does not exist, the sweep of a missing directory returns an empty
    set, and two acceptance items report FAIL. Round 7 recorded those two as stale
    fixtures and verified them "identical with and without the change" -- which they were,
    because the invocation was wrong both times. The fixtures were correct throughout:
    m2-red flags `fieldTypes`, m2-green clears it, and the pair differs by exactly that.

    So this now RESOLVES the glob form rather than punishing it, and aborts loudly if the
    fixtures are nowhere to be found. A suite that reports FAIL because it was pointed at
    the wrong directory is worse than one that refuses to run: the first trains people to
    ignore two red checks, and that is how a real regression gets through.
    """
    base = pathlib.Path(base)
    if not (base / "m2-red").is_dir():
        for cand in (base.parent, *(pathlib.Path(s).parent for s in siblings)):
            if (cand / "m2-red").is_dir():
                base = cand
                break
        else:
            print("ABORT: no acceptance fixtures under %s (looked for m2-red/).\n"
                  "       Pass the CORPUS DIRECTORY, not a glob of trees:\n"
                  "           --self-test ~/.cache/truffle-hunt-corpus" % base)
            return 2
    # A HALF-built acceptance dir is the same failure wearing a different hat, and it is
    # the one the round-9 sweeps hit in two sibling predicates: fixture absent, "missing"
    # printed, exit 0. Here `fields_flagged` on a directory that is not there sweeps an
    # empty tree and returns an empty set, so "sqlite3 pr-723 clears all six" -- a check
    # phrased as `not leaked` -- would PASS on nothing at all before the de-marked mutant
    # a few lines later died on `cp -r`. Name the missing tree and refuse to run: a
    # fixture that is not there is a failed run, never a negative result.
    missing = [n for n in ("m2-red", "m2-green", "sqlite3-pr723")
               if not (base / n / "ext").is_dir()]
    if missing:
        print("ABORT: acceptance fixtures missing under %s: %s\n"
              "       Rebuild them per the module docstring; a missing fixture is a\n"
              "       failed run, not a pass." % (base, ", ".join(missing)))
        return 2
    ok = True
    tally = [0, 0, 0]        # pass, total, skipped

    def check(hit, label, detail=""):
        # `% (detail,)`, not `% detail`: three of the predicate-A mutants report a TUPLE,
        # and `"%s" % ("a", "b")` is a TypeError that kills the run mid-suite. The suite
        # then printed a traceback and no verdict -- the round-4 "no verdict line => FAILED
        # RUN" trap, reached from inside the failure REPORTER rather than the test.
        nonlocal ok
        ok &= bool(hit)
        tally[0] += bool(hit)
        tally[1] += 1
        print("%s %s%s" % ("PASS" if hit else "FAIL", label,
                           "" if hit else "  [%s]" % (detail,)))

    def skip(label, why):
        tally[2] += 1
        print("SKIP %s  [%s]" % (label, why))

    def fields_flagged(tree_dir):
        s, _ = sweep(Tree(tree_dir))
        return {(st, f) for _, _, st, f, _, _ in s}

    def graded(tree_dir):
        """(struct, field) -> (category, grade, evidence). Predicate A's whole output."""
        tree = Tree(tree_dir)
        s, _ = sweep(tree)
        g = grade_suspects(tree, s)
        return {(st, f): (cat,) + tuple(g.get((d, st, f), (None, [], 0))[:2])
                for cat, _p, st, f, d, _t in s}

    def graded_from_source(src):
        with tempfile.TemporaryDirectory() as tmp:
            ext = pathlib.Path(tmp) / "ext"
            ext.mkdir()
            (ext / "t.c").write_text(src)
            return graded(ext)

    def flagged_from_source(src, suffix=".c"):
        """(categories, fields) for a tree generated from one C file."""
        return flagged_from_sources({"t" + suffix: src})

    def flagged_from_sources(files):
        """(categories, fields) for a tree generated from SEVERAL files.

        Round 7's three cross-translation-unit reds need two files by construction: the
        defect is that a name defined in both binds to whichever one came first.
        """
        with tempfile.TemporaryDirectory() as tmp:
            ext = pathlib.Path(tmp) / "ext"
            ext.mkdir()
            for name, text in files.items():
                (ext / name).write_text(text)
            s, _ = sweep(Tree(ext))
            return {c for c, _, _, _, _, _ in s}, {f for _, _, _, f, _, _ in s}

    def funnel_from_sources(files):
        """(wrap sites, VALUE fields ENUMERATED) for a generated tree.

        The counters, not the verdict. Every green check below is satisfied just as
        well by a parser that resolved NOTHING -- "no local reported as a field" and
        "the other file's field still clears" both hold trivially at zero fields --
        and the round-7 fixtures are exactly the shapes where an index can come back
        empty: C++ class bodies and two-file trees. So each fixture pins the width of
        the funnel it walked as well as what came out of it. Two of them are red on
        the counter alone: RED_TU_STRUCT enumerated 0 fields before the struct fix and
        RED_CXX_INIT 0 before the initialiser fix, which is the same zero a bundled-gem
        glob produced in round 5.
        """
        with tempfile.TemporaryDirectory() as tmp:
            ext = pathlib.Path(tmp) / "ext"
            ext.mkdir()
            for name, text in files.items():
                (ext / name).write_text(text)
            tree = Tree(ext)
            s, c = sweep(tree)
            return (len(tree.wrap_sites),
                    len(s) + len([x for x in c if x[2] != "-"]))

    def cleared_from_source(src, suffix=".c"):
        """{field: why} for everything a one-file tree CLEARED."""
        with tempfile.TemporaryDirectory() as tmp:
            ext = pathlib.Path(tmp) / "ext"
            ext.mkdir()
            (ext / ("t" + suffix)).write_text(src)
            _, c = sweep(Tree(ext))
            return {f: why for _d, _st, f, why in c}

    # mysql2: the known instance. Declared in result.h -- the file v1 never opened.
    red = fields_flagged(base / "m2-red" / "ext")
    green = fields_flagged(base / "m2-green" / "ext")
    for label, want, got in (
        ("m2-red flags fieldTypes", True,
         ("mysql2_result_wrapper", "fieldTypes") in red),
        ("m2-green clears fieldTypes", False,
         ("mysql2_result_wrapper", "fieldTypes") in green),
    ):
        check(want == got, label)
    check(red - green == {("mysql2_result_wrapper", "fieldTypes")},
          "red/green differ by exactly fieldTypes", red - green)

    # sqlite3 PR #723: all six VALUE fields marked, three of them via helpers.
    s3 = base / "sqlite3-pr723" / "ext"
    got = fields_flagged(s3)
    six = {"busy_handler", "functions", "collations", "aggregators",
           "trace_handler", "authorizer"}
    leaked = {f for st, f in got if f in six}
    check(not leaked, "sqlite3 pr-723 clears all six", sorted(leaked))

    # ...and a mutated tree that stops marking one of them must be flagged. The control
    # is generated here rather than kept as a second fixture: a hand-edited control is a
    # different program and proves less.
    with tempfile.TemporaryDirectory() as tmp:
        mut = pathlib.Path(tmp) / "ext"
        subprocess.run(["cp", "-r", str(s3), str(mut)], check=True)
        db = mut / "sqlite3" / "database.c"
        txt = db.read_text()
        assert "rb_gc_mark(c->authorizer);" in txt
        db.write_text(txt.replace("rb_gc_mark(c->authorizer);", "/* removed */"))
        mgot = {f for st, f in fields_flagged(mut)}
        check("authorizer" in mgot, "de-marked sqlite3 tree flags authorizer", sorted(mgot))

    # -- round 5: the four over-clears, each with its own generated red ------------
    for label, src, want_cat, want_field in (
        ("(a) second dtype wrapping one struct is judged separately",
         RED_A, "UNMARKED", "payload"),
        ("(b) field named in dmark but never marked is not cleared",
         RED_B, "MENTIONED", "callback"),
        ("(c) movable mark reached through a helper is not cleared",
         RED_C, "NO-COMPACT-UNKNOWN", "cb"),
        ("(ternary) both arms of a dtype ternary are wrap sites",
         RED_TERNARY, "UNMARKED", "payload"),
        ("(zalloc) a struct named only inside sizeof is still the wrapped type",
         RED_ZALLOC, "UNMARKED", "held"),
        ("(compact-decl) a VALUE that does not open its line is still a field",
         RED_COMPACT_DECL, "UNMARKED", "held"),
        ("(starred) VALUE* with the star against the type is still a field",
         RED_COMPACT_DECL, "UNMARKED", "items"),
        ("(c++) a class, with a base clause, is a wrapped struct too",
         RED_CXX_CLASS, "UNMARKED", "held"),
    ):
        cats, fields = flagged_from_source(src)
        check(want_cat in cats and want_field in fields, "red " + label,
              "%s %s" % (sorted(cats), sorted(fields)))

    cats, fields = flagged_from_source(GREEN_MACRO)
    check(not fields,
          "green (macro) a gem's own gc_location #define counts as an update",
          sorted(fields))

    # -- round 6 (B1): a member call resolves by (class, method), not by file order ----
    #
    # ONE fixture, TWO assertions, and the pair is the point: a fix that resolved the
    # receiver by simply crediting everything the call touches passes the first and fails
    # the second. Measured before B1: {"stack_table_value", "start_thread"} -- the false
    # positive and the real finding, indistinguishable.
    _cats, fields = flagged_from_source(RED_CXX_OVERLOAD)
    check("stack_table_value" not in fields,
          "green (B1) a member call binds to the receiver's class, not the first "
          "same-named body", sorted(fields))
    check("start_thread" in fields,
          "red (B1) the genuinely unmarked field of that same class still reports",
          sorted(fields))

    # -- round 7: resolution is per FILE, then tree-wide ---------------------------
    #
    # Three indexes, three two-file reds. Each asserts the same two things: the shadowed
    # field REPORTS, and the innocent same-named one in the other file still CLEARS. A fix
    # that simply stopped resolving through a shadowed name would pass the first half and
    # fail the second, and would have turned nokogiri's 13 shadowed callbacks into rows.
    for label, files, want_field, want_cleared in (
        ("(tu-struct) a second file's same-named struct is its own body",
         RED_TU_STRUCT, "held", None),
        ("(tu-callback) a file-local callback resolves in its own file",
         RED_TU_CALLBACK, "held", "held"),
        ("(tu-dtype) two file-local descriptors of one name are two verdicts",
         RED_TU_DTYPE, "held", "held"),
    ):
        cats, fields = flagged_from_sources(files)
        check("UNMARKED" in cats and want_field in fields, "red " + label,
              "%s %s" % (sorted(cats), sorted(fields)))
    for label, files in (("(tu-callback)", RED_TU_CALLBACK), ("(tu-dtype)", RED_TU_DTYPE)):
        _c, fields = flagged_from_sources(files)
        check(len(fields) == 1, "green %s the marking file's own field still clears" % label,
              sorted(fields))

    # -- round 9: a cast, a repeated member type, and a leaf-name match ------------
    #
    # THREE DEFECTS THAT COMPOUND, SO EACH GETS A CHECK THAT FAILS ON ITS OWN. The `_seen`
    # visited set stops `right.held` being ENUMERATED; the leaf-name fallback stops it being
    # counted as unmarked even when it is. One fixture asserting only the suspect list would
    # have gone green on either fix alone, which is why the repeated-member check asserts
    # the FUNNEL (2 fields) on a fixture where both members are marked, and the sibling
    # check asserts the SUSPECT on a fixture where only one is. Measured with each fix
    # reverted alone: cast -> `UNMARKED held`, `_seen` -> `1 field` on both, leaf ->
    # `0 suspects, both cleared`.
    cast_clear = cleared_from_source(RED_CAST_DMARK)
    cast_cats, cast_fields = flagged_from_source(RED_CAST_DMARK)
    _cs, cast_n = funnel_from_sources({"t.c": RED_CAST_DMARK})
    check(cast_n == 1 and not cast_fields
          and "marked pin" in cast_clear.get("held", ""),
          "red (cast-dmark) `.dmark = (RUBY_DATA_FUNC)w_mark` is a dmark: the descriptor "
          "resolves it and the field it marks CLEARS (%d field(s) enumerated, cleared %s)"
          % (cast_n, cast_clear.get("held", "NOTHING")),
          "%s %s" % (sorted(cast_cats), sorted(cast_fields)))

    _rs, rep_n = funnel_from_sources({"t.c": RED_REPEATED_MEMBER})
    rep_clear = cleared_from_source(RED_REPEATED_MEMBER)
    _rc, rep_fields = flagged_from_source(RED_REPEATED_MEMBER)
    check(rep_n == 2 and not rep_fields and set(rep_clear) == {"left.held", "right.held"},
          "red (repeated-member) two members of ONE inner type enumerate TWO fields -- the "
          "recursion guard is the stack, not a visited set (%d field(s): %s)"
          % (rep_n, sorted(rep_clear) or "none"), sorted(rep_fields))

    sib_cats, sib_fields = flagged_from_source(RED_SIBLING_LEAF)
    _ss, sib_n = funnel_from_sources({"t.c": RED_SIBLING_LEAF})
    check(sib_n == 2 and sib_fields == {"right.held"},
          "red (sibling-leaf) marking `w->left.held` does not clear `w->right.held` -- the "
          "mark index keeps the member path (%d field(s) enumerated, suspects %s)"
          % (sib_n, sorted(sib_fields) or "NONE"),
          "%s %s" % (sorted(sib_cats), sorted(sib_fields)))

    # ...and the GREEN that stops "keep the path" turning into "never match the leaf". A
    # helper marking `i->held` through a POINTER to the inner struct records no path at
    # all, which is how msgpack marks, and both members must still clear through it.
    hlp_clear = cleared_from_source(GREEN_NESTED_HELPER)
    _hs, hlp_n = funnel_from_sources({"t.c": GREEN_NESTED_HELPER})
    check(hlp_n == 2 and set(hlp_clear) == {"left.held", "right.held"},
          "green (nested-helper) a mark reached through a pointer to the inner struct has "
          "no member path, so the leaf still clears BOTH members (%d field(s): %s)"
          % (hlp_n, sorted(hlp_clear) or "none"), sorted(hlp_clear))

    # TERMINATION. A stack guard is only correct if it stops, and the pair below is
    # genuinely cyclic -- illegal C that a parser reads without complaint. The check is
    # that the sweep RETURNS at all; the field list is asserted beside it so that a guard
    # which terminates by enumerating nothing fails too.
    cyc_cats, cyc_fields = flagged_from_source(RED_CYCLIC_MEMBER)
    _ys, cyc_n = funnel_from_sources({"t.c": RED_CYCLIC_MEMBER})
    check(cyc_n == 2 and "b.also" in cyc_fields,
          "cycle: `struct a { struct b b; }; struct b { struct a a; }` terminates and "
          "still enumerates one level of each (%d field(s), suspects %s)"
          % (cyc_n, sorted(cyc_fields)), "%s %s" % (sorted(cyc_cats), sorted(cyc_fields)))

    # C++ default member initialisers: the member reports, the method-body locals do not.
    cats, fields = flagged_from_source(RED_CXX_INIT, ".cc")
    cleared = cleared_from_source(RED_CXX_INIT, ".cc")
    check("held" in fields, "red (c++ init) `VALUE held = Qnil;` is still a field",
          sorted(fields))
    check("marked" in cleared, "green (c++ init) a marked member with an initialiser clears",
          cleared)
    check(not ({"local", "ctor_local"} & (fields | set(cleared))),
          "green (c++ init) a local inside a method body is not a field",
          sorted(fields | set(cleared)))

    # ...and the same three claims when the member-initialiser list names a TEMPLATE-ID.
    # `ctor_local` is the one that moves: a rejected constructor is never blanked, so its
    # body is scanned as class scope and the local reports as a member. The two real
    # members are asserted beside it so that a regression which indexes NOTHING -- and
    # therefore also reports no `ctor_local` -- fails here rather than reading as green.
    tcats, tfields = flagged_from_source(RED_CXX_TMPL_INIT, ".cc")
    tcleared = cleared_from_source(RED_CXX_TMPL_INIT, ".cc")
    check("held" in tfields and "marked" in tcleared
          and not ({"ctor_local"} & (tfields | set(tcleared))),
          "green (c++ template ctor-init) `TBox(VALUE v) : Base<VALUE>()` is a "
          "constructor: its body is blanked, so `ctor_local` is not a field, while `held` "
          "still reports and `marked` still clears",
          "%s | fields %s | cleared %s"
          % (sorted(tcats), sorted(tfields), sorted(tcleared)))

    # The pack case is asserted on `blank_method_bodies` DIRECTLY rather than through
    # flagged/cleared, and the reason is a recall limit worth writing down: this predicate
    # does not index a VARIADIC TEMPLATE class at all. Measured on this very fixture,
    # `flagged_from_source` returns no fields and no categories, so an assertion phrased
    # like the template-id one above would be satisfied by the sweep seeing nothing --
    # exactly the "passes because it indexed zero" failure the funnel assertions elsewhere
    # exist to stop. Blanking is the behaviour the walk actually governs, so it is the
    # behaviour asserted. `VALUE held` is the control: it sits outside every method body,
    # so a blanker that blanked the whole class would fail here too.
    packed = blank_method_bodies(RED_CXX_PACK_INIT)
    check("ctor_local" not in packed and "VALUE held" in packed,
          "green (c++ pack ctor-init) `PBox(T... t) : T(t)...` is a constructor: the pack "
          "expansion ends the initialiser list, so the body is blanked and `ctor_local` "
          "cannot reach value_fields, while the member declaration outside it survives",
          "ctor_local present=%s, held present=%s"
          % ("ctor_local" in packed, "VALUE held" in packed))

    # The helper tier credits only the arguments the callee actually marks.
    cats, fields = flagged_from_source(RED_HELPER_PARAM)
    cleared = cleared_from_source(RED_HELPER_PARAM)
    check("cb" in fields, "red (helper-param) a helper that marks a GLOBAL clears nothing",
          sorted(fields))
    check("other" in cleared, "green (helper-param) a direct mark beside it still clears",
          cleared)

    cats, fields = flagged_from_source(RED_HELPER_OTHER_PARAM)
    cleared = cleared_from_source(RED_HELPER_OTHER_PARAM)
    check("b" in fields,
          "red (helper-param) a helper that marks its OTHER parameter clears nothing",
          sorted(fields))
    check("a" in cleared,
          "green (helper-param) the parameter it does mark still clears", cleared)

    # A half-built acceptance dir must ABORT. Before the guard above this ran the first
    # three checks against empty sweeps and then died inside `cp -r` -- an exception, no
    # verdict line, and one green (`not leaked`) earned on a tree that was not there.
    with tempfile.TemporaryDirectory() as tmp:
        half = pathlib.Path(tmp) / "acceptance"
        (half / "m2-red" / "ext").mkdir(parents=True)
        (half / "m2-green" / "ext").mkdir(parents=True)
        try:
            rc = self_test(half)
        except Exception as e:                                   # noqa: BLE001
            rc = "raised %r" % (e,)
        check(rc == 2, "a missing fixture ABORTS (2); it never reports a pass", rc)

    # -- the funnel each round-7 fixture walked, not just what came out of it -------
    #
    # Measured, both versions, so the numbers are a control and not a transcription:
    #
    #   fixture         wrap sites   fields enumerated
    #                                before      after
    #   tu-struct           2          0           1     <- the whole defect
    #   tu-callback         2          2           2
    #   tu-dtype            2          1           2     <- second descriptor invisible
    #   c++ init            1          0           2     <- the whole defect
    #   helper-param        1          2           2
    #   helper-other-param  1          2           2
    #   store-flow          3          2           2     <- a GRADE defect: same rows
    #
    # The last row is the point of grading against the funnel too: threads 4 and 5 move
    # no row at all, so a suite that watched only the row count could not tell their fix
    # from their absence, and the two rows it must NOT lose are asserted here by count.
    for label, files, want in (
        ("(tu-struct)", RED_TU_STRUCT, (2, 1)),
        ("(tu-callback)", RED_TU_CALLBACK, (2, 2)),
        ("(tu-dtype)", RED_TU_DTYPE, (2, 2)),
        ("(c++ init)", {"t.cc": RED_CXX_INIT}, (1, 2)),
        ("(helper-param)", {"t.c": RED_HELPER_PARAM}, (1, 2)),
        ("(helper-other-param)", {"t.c": RED_HELPER_OTHER_PARAM}, (1, 2)),
        ("(store-flow)", {"t.c": RED_STORE_FLOW}, (3, 2)),
    ):
        got = funnel_from_sources(files)
        check(got == want,
              "funnel %s %d wrap site(s), %d field(s) enumerated" % ((label,) + want),
              "got %s" % (got,))

    # -- predicate A: a severity COLUMN on the rows above --------------------------
    #
    # Grades are asserted with their evidence, not just their names: "HEAP-IF-COERCED"
    # produced by a grader that found the wrong call is the same string as one produced by
    # a grader that found NUM2INT, and only the second is a result.

    g = graded_from_source(RED_COERCE)
    check(g.get(("_p", "interval"), (None, None, None))[1] == "HEAP-IF-COERCED",
          "A red: a NUM2-coerced original stored into an unmarked field is HEAP-IF-COERCED",
          g.get(("_p", "interval")))
    check(g.get(("_p", "mode"), (None, None, None))[1] == "IMMEDIATE-ONLY",
          "A red: a field narrowed to static symbols by an else-raise is IMMEDIATE-ONLY",
          g.get(("_p", "mode")))
    check(g.get(("_p", "empty"), (None, None, None))[1] == "REGISTERED"
          and ("_p", "empty") in g,
          "A red: rb_global_variable is a DOWNGRADE and the row survives it",
          g.get(("_p", "empty")))
    check(g.get(("_p", "bound"), (None, None, None))[1:] ==
          ("IMMEDIATE-ONLY", ["stores only INT2FIX"]),
          "A red: an INT2FIX-only field read back through NUM2LONG stays IMMEDIATE-ONLY",
          g.get(("_p", "bound")))
    check(g.get(("_p", "pick"), (None, "?", None))[1] is None,
          "A red: one narrowed store out of two is not IMMEDIATE-ONLY",
          g.get(("_p", "pick")))

    gp = graded_from_source(RED_PASTE)
    check(gp.get(("_p", "mode"), (None, None, None))[1] == "IMMEDIATE-ONLY",
          "A red: symbols defined through a `##` paste macro still resolve as immediate",
          gp.get(("_p", "mode")))

    gg = graded_from_source(GREEN_COERCE)
    check(not [k for k, v in gg.items() if v[1] == "HEAP-IF-COERCED"],
          "A green: the upstream fix (field stops being a VALUE) leaves no HEAP-IF-COERCED",
          sorted(gg))

    # Round 7, tier 4's two source-analysis defects. Both graded a field IMMEDIATE-ONLY
    # while it held an arbitrary caller object -- the downgrade direction. The green half
    # is the RED_COERCE `mode` item above: stackprof's real else-raise chain must still
    # earn the grade, and a fix that merely demanded the raise be nearer the store would
    # accept `s_guard` too, because its unrelated raise sits between the comparison and
    # the store as well. Only "the chain's own else rejects" separates them.
    gs = graded_from_source(RED_STORE_FLOW)
    check(gs.get(("sbox_t", "held"), (None, "?", None))[1] is None,
          "A red: an assignment AFTER the store is not a source of it",
          gs.get(("sbox_t", "held")))
    check(gs.get(("sbox_t", "guard"), (None, "?", None))[1] is None,
          "A red: an unrelated rb_raise does not make a comparison chain rejecting",
          gs.get(("sbox_t", "guard")))

    gn = graded_from_source(RED_NOT_IMMEDIATE)
    bad = {f: v[1] for (st, f), v in gn.items() if v[1]}
    check(len(gn) == 4 and not bad,
          "A red: INT2NUM / rb_to_symbol / Check_Type / raise-less == / rb_to_int-result "
          "are none of them grades", bad or sorted(gn))

    # Adds a COLUMN, never a ROW. Cheap version is the assert inside grade_suspects; this
    # is the expensive one -- re-sweep every fixture tree from a fresh Tree and compare the
    # (struct, field) sets. Run it over the real trees, because a generated one-file
    # fixture exercises none of the cross-file resolution the invariant could break.
    drift = []
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        tree = Tree(d)
        s, _ = sweep(tree)
        before = {(st, f) for _, _, st, f, _, _ in s}
        grade_suspects(tree, s)
        after = {(st, f) for _, _, st, f, _, _ in sweep(Tree(d))[0]}
        if before != after:
            drift.append((d.name, before ^ after))
    check(not drift, "A: grading adds a column, never a row (%d tree(s))"
          % len(list(base.iterdir())), drift)

    # -- round-9 follow-up: the `)`-to-`{` crossing, fifth appearance (#29 item 2) ---
    #
    # The issue filed this against predicate B. B was already wired; the gap was HERE, in
    # all THREE of this file's declarator crossings, and it is the same measured symptom
    # every host has had -- not a dropped row but an EMPTIED INDEX, reading as a verdict.
    #
    #   _index_funcs         `static void mark(void *p) __attribute__((noinline))` was not
    #                        indexed, so its rb_gc_mark calls were never read and the field
    #                        it marks reported UNMARKED. The body was indexed under the
    #                        name `__attribute__` instead: one missing function and one
    #                        invented one out of a single construct.
    #   _index_methods       whitespace and a member-initialiser list only -- so a C++
    #                        `int size() const {` was invisible, which is the commonest
    #                        method qualifier there is.
    #   blank_method_bodies  a CLOSED word list (`const|volatile|noexcept|override|final`),
    #                        the exact shape tu_scope's docstring records as the first cut
    #                        of the shared walk and the reason it was reopened.
    #
    # THE MARK IS THE FLAG. Every arm marks `obj` correctly, so a conforming index CLEARS
    # it; the pre-fix behaviour is a suspect raised against a dmark that does mark it.
    xing_c = """#include <ruby.h>

struct holder {
    VALUE obj;
};

static void mark_holder(void *p)%s
{
    struct holder *h = (struct holder *)p;
    rb_gc_mark(h->obj);
}

static void free_holder(void *p) { xfree(p); }

static const rb_data_type_t holder_type = {
    "holder",
    { mark_holder, free_holder, 0 },
    0, 0, 0
};

static VALUE alloc(VALUE klass)
{
    struct holder *h;
    return TypedData_Make_Struct(klass, struct holder, &holder_type, h);
}

void Init_t(void) { rb_define_alloc_func(rb_cObject, alloc); }
"""
    xing = {tag: (flagged_from_source(xing_c % suffix, sfx),
                  cleared_from_source(xing_c % suffix, sfx))
            for tag, suffix, sfx in (("plain", "", ".c"),
                                     ("attr", " __attribute__((noinline))", ".c"),
                                     ("noexcept", " noexcept", ".cpp"),
                                     ("const", " const", ".cpp"),
                                     ("trailing-attr", " __attribute__((noinline)) noexcept",
                                      ".cpp"))}
    check(all(cats == set() and cleared.get("obj") == "marked pin (direct)"
              for (cats, _f), cleared in xing.values()),
          "#29 item 2 RED: a dmark carrying `__attribute__((...))`, `noexcept` or a C++ "
          "`const` qualifier is indexed and its rb_gc_mark is read -- unfixed the walk "
          "skipped whitespace only, dropped the dmark whole and raised UNMARKED on a "
          "field that IS marked",
          {t: (sorted(c), cl) for t, ((c, _f), cl) in xing.items()})

    # ...and the caller-coverage question itself, which is what four of the five follow-ups
    # had in common. Two assertions, because they catch different omissions:
    #
    #   BEHAVIOURAL -- every function index in this file is driven through tu_scope's own
    #   accept table and rejection table. Opening the crossing up is what once made a sweep
    #   INVENT four functions out of X-macro lists and `__declspec(...)`, so the rejection
    #   half is not optional decoration.
    #
    #   SOURCE -- the gap has never presented as a wrong answer, it presents as a
    #   hand-rolled whitespace skip two lines above a `== "{"`. The lint finds those; the
    #   allow-list is what makes a remaining one a decision with a reason beside it.
    def index_names(src):
        with tempfile.TemporaryDirectory() as tmp:
            ext = pathlib.Path(tmp) / "ext"
            ext.mkdir()
            (ext / "t.cpp").write_text(src)
            return set(Tree(ext).funcs)

    check(not tu_scope.declarator_conformance(index_names),
          "#29 item 2: predicate A's function index conforms to tu_scope's declarator "
          "table -- every accepted spelling indexed, every rejected one refused, K&R "
          "indexing nothing (the stated recall limit shared by all four predicates)",
          tu_scope.declarator_conformance(index_names))
    # The survivors are the `if`-arm walks in _chain_rejects, which step over a STATEMENT
    # and not a declarator: no parameter list behind them and no definition in front, so
    # the shared walk is the wrong tool and the lint is matching on shape alone. The
    # allow-list is by ENCLOSING FUNCTION rather than by line number -- a line number
    # allow-list fails on the next edit above it, and the cheapest way to make that green
    # again is to update the number, which is how a tripwire stops being one.
    own = pathlib.Path(__file__).read_text().splitlines()
    def _enclosing_def(lineno):
        for i in range(lineno - 1, -1, -1):
            m = re.match(r"\s*def (\w+)", own[i])
            if m:
                return m.group(1)
        return "<module>"
    unshared = {_enclosing_def(n) for n in tu_scope.unshared_declarator_crossings(
        "\n".join(own))}
    check(unshared == {"_chain_rejects"},
          "#29 item 2: no hand-rolled `)`-to-`{` crossing left in this file bar the "
          "if-arm walks in _chain_rejects, which are statement walks and not declarator "
          "walks", sorted(unshared))

    # -- predicate A against the real gem, when the fixtures are present ------------
    #
    # The generated controls above prove the mechanism; only stackprof proves the ANSWER.
    # A missing fixture prints SKIP rather than nothing, because the round-4 rule is that
    # absence of a failure signal is not a negative result.
    # `base` FIRST, then the pre-round-7 layout. The durable corpus moved to
    # `~/.cache/truffle-hunt-corpus` and this path was left pointing at `~/.cache/corpus`,
    # so the one item that proves the ANSWER rather than the mechanism had been printing
    # SKIP ever since -- against a fixture that is sitting in the corpus directory.
    sp = _first_dir(base / "stackprof-0.2.28",
                    base.parent / "corpus" / "stackprof-0.2.28")
    want = {("_stackprof", "interval"): "HEAP-IF-COERCED",
            ("_stackprof", "mode"): "IMMEDIATE-ONLY",
            ("_stackprof", "empty_string"): "REGISTERED",
            ("_stackprof", "fake_frame_names"): "REGISTERED",
            ("_stackprof", "frames_buffer"): None}
    if sp.is_dir():
        gs = graded(sp)
        got = {k: v[1] for k, v in gs.items()}
        ev = gs.get(("_stackprof", "interval"), (None, None, []))[2][:2]
        check(got == want and ev == ["NUM2INT @:213", "NUM2UINT @:238"],
              "A: stackprof 0.2.28 grades all five as measured", (got, ev))
    else:
        skip("A: stackprof 0.2.28 grades all five as measured", "absent: %s" % sp)

    pris = _first_dir(base / "sp-pristine", base.parent / "fixtest" / "sp-pristine")
    fixed = _first_dir(base / "sp-fixed", base.parent / "fixtest" / "sp-fixed")
    if pris.is_dir() and fixed.is_dir():
        gr = {k: v[1] for k, v in graded(pris).items()}
        gf = {k: v[1] for k, v in graded(fixed).items()}
        check(gr.get(("_stackprof", "interval")) == "HEAP-IF-COERCED"
              and "HEAP-IF-COERCED" not in gf.values(),
              "A: sp-pristine is RED on interval and the patched sp-fixed is GREEN",
              (gr, gf))
    else:
        skip("A: sp-pristine is RED on interval and the patched sp-fixed is GREEN",
             "absent: %s" % fixed)

    print("\nself-test: %s (%d/%d, %d skipped)"
          % ("PASS" if ok else "FAIL", tally[0], tally[1], tally[2]))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every cleared field and why (audit the recall)")
    ap.add_argument("--self-test", metavar="ACCEPTANCE_DIR",
                    help="run the acceptance test and exit")
    ap.add_argument("--no-grade", action="store_true",
                    help="suppress predicate A's severity column")
    ap.add_argument("--verify-column", action="store_true",
                    help="re-sweep each tree from a fresh Tree and assert the grader "
                         "changed no (struct, field) pair")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(self_test(a.self_test, a.dirs))
    total = 0
    for d in a.dirs:
        tree = Tree(d)
        s, c = sweep(tree)
        g = None if a.no_grade else grade_suspects(tree, s)
        if a.verify_column:
            before = {(st, f) for _, _, st, f, _, _ in s}
            after = {(st, f) for _, _, st, f, _, _ in sweep(Tree(d))[0]}
            if before != after:
                print("COLUMN-INVARIANT VIOLATED in %s: %s"
                      % (d, before ^ after), file=sys.stderr)
                sys.exit(2)
        total += report(pathlib.Path(d).name, tree, s, c, a.verbose, g)
    print("\n%d suspect field(s) across %d tree(s)" % (total, len(a.dirs)),
          file=sys.stderr)


if __name__ == "__main__":
    main()

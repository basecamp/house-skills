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

Then: python3 sweep_unmarked.py --self-test acceptance   (expects 27/27 PASS)

Two of the twenty-seven run against the REAL gem rather than a generated reduction, and look for
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
NOT_CALLS = {"if", "for", "while", "switch", "return", "sizeof", "defined", "do", "else",
             "case", "typeof", "alignof", "static_assert"}

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


# ---------------------------------------------------------------- tree model


class Tree:
    """One gem's C sources, indexed for cross-file resolution."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.files = {}
        self.macros = {}         # function-like #define name -> concatenated bodies
        self.macro_defs = {}     # name -> [(params, body)], for token-paste expansion
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and p.suffix in C_EXT and ".git" not in p.parts:
                try:
                    # Macros are indexed BEFORE the directives are blanked: a gem's own
                    # marking macro lives entirely inside a directive line.
                    decommented = strip_noise(p.read_text(errors="replace"))
                except OSError:
                    continue
                self._index_macros(decommented)
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
        self.type_of_dtype = {}  # rb_data_type_t name -> wrapped struct type name
        self.wrap_sites = []     # (path, dtype, struct_type, macro)
        self._helper_memo = {}   # ("dmark"|"dcompact") -> {fn -> kind}, with cycle guard
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
            self._index_dtypes(src)
            self._index_statics(src)
        for path, src in self.files.items():
            self._index_wraps(path, src)
        self.pasted = self._expand_pastes()

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

    def body_of(self, name):
        """The body of an in-tree callee, function or function-like macro."""
        return self.funcs.get(name, self.macros.get(name))

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
            k = j
            while k < len(body) and body[k] in " \t\r\n":
                k += 1
            # A constructor's member-initialiser list sits between `)` and `{`, and it is
            # the shape BaseCollector's own constructor takes.
            if k < len(body) and body[k] == ":":
                brace = body.find("{", k)
                k = brace if brace >= 0 else k
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

    def resolve(self, name, depth=6):
        """Follow typedef aliases to something we have a struct body for."""
        while name and name not in self.structs and depth > 0:
            name = self.aliases.get(name)
            depth -= 1
        return name

    # -- functions ----------------------------------------------------------

    def _index_funcs(self, path, src):
        # A definition, not a prototype: identifier + parens + `{` before any `;`.
        for m in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", src):
            name = m.group(1)
            if name in ("if", "for", "while", "switch", "return", "sizeof", "defined"):
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
            k = j + 1
            while k < len(src) and src[k] in " \t\r\n":
                k += 1
            if k < len(src) and src[k] == "{":
                close = match_brace(src, k)
                if close > 0:
                    self.funcs.setdefault(name, src[k + 1:close])
                    self.sigs.setdefault(name, src[m.end():j])
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

    def _index_dtypes(self, src):
        for m in re.finditer(r"\brb_data_type_t\s+(\w+)\s*=\s*\{", src):
            open_idx = src.index("{", m.end() - 1)
            close = match_brace(src, open_idx)
            if close < 0:
                continue
            body = src[open_idx + 1:close]
            entry = {}
            for f in re.finditer(r"\.(dmark|dfree|dsize|dcompact)\s*=\s*([\w:]+)", body):
                entry[f.group(1)] = f.group(2)
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
                        v = val.strip()
                        if re.fullmatch(r"[\w:]+", v):
                            entry[key] = v
            self.dtypes.setdefault(m.group(1), entry)

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
                    st = self.sizeof_arg(args, di)
                # A ternary picks between two dtypes; register BOTH, or the wrap site
                # silently disappears (msgpack buffer_class.c:151).
                for dtype in dtype_candidates(args[di]):
                    self.wrap_sites.append((path, dtype, st, macro))
                    if st and dtype not in self.type_of_dtype:
                        self.type_of_dtype[dtype] = st
        for macro, (ti, mi) in self.UNTYPED.items():
            for m in re.finditer(r"\b%s\s*(?=\()" % macro, src):
                args, _ = call_args(src, m.end())
                if not args or len(args) <= mi:
                    continue
                st = type_name(args[ti]) if ti is not None else None
                # Synthesise a pseudo-dtype so legacy gems are covered by the same walk.
                key = "<inline:%s>" % base_type(args[mi])
                self.dtypes.setdefault(key, {"dmark": base_type(args[mi])})
                self.wrap_sites.append((path, key, st, macro))
                if st and key not in self.type_of_dtype:
                    self.type_of_dtype[key] = st

    SIZEOF = re.compile(r"\bsizeof\s*\(\s*(?:struct\s+)?(\w+)\s*\)")

    def sizeof_arg(self, args, skip):
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
                if self.resolve(m.group(1)) in self.structs:
                    return m.group(1)
        return None

    # -- resolution ---------------------------------------------------------

    def struct_type_for(self, dtype):
        """Infer the wrapped struct even when only Wrap_Struct is used."""
        if dtype in self.type_of_dtype:
            r = self.resolve(self.type_of_dtype[dtype])
            if r in self.structs:
                return r
        # Fall back to the type's own dfree/dsize/dmark, which must cast the payload.
        for key in ("dsize", "dfree", "dmark", "dcompact"):
            fn = self.dtypes.get(dtype, {}).get(key)
            body = self.funcs.get(fn, "") if fn else ""
            for pat in (r"sizeof\s*\(\s*(?:struct\s+)?(\w+)\s*\)",
                        r"\(\s*(?:const\s+)?(?:struct\s+)?(\w+)\s*\*\s*\)"):
                for cm in re.finditer(pat, body):
                    r = self.resolve(cm.group(1))
                    if r in self.structs:
                        return r
        return self.type_of_dtype.get(dtype)

    def helper_kind(self, fn, key, depth=1):
        """Does in-tree function `fn` mark (key=dmark) or update (key=dcompact)?

        One level of onward callees, which is the transitivity the v2 `mark_text` folded
        in wholesale. sqlite3 PR #723 marks three of its six fields through
        `rb_sqlite3_pin_array_and_contents(c->functions)`; without this tier the round-5
        (b) fix would flag all three and acceptance item 4 would fail. That fixture is
        the control which separates the correct fix from the merely plausible one.

        Conservative on disagreement: a helper with any movable path counts as movable,
        because an unwarranted NO-COMPACT costs a pass-2 discharge while a missed one
        ships a stale pointer.
        """
        memo = self._helper_memo.setdefault(key, {})
        if fn in memo:
            return memo[fn]
        memo[fn] = None                       # cycle guard: recursion resolves to None
        kinds = set()
        for name, _args, recv, op in find_calls(self.body_of(fn) or ""):
            k = loc_kind(name) if key == "dcompact" else prim_kind(name)
            if k is None and depth > 0:
                callee = self.callee_key(name, recv, op, fn)
                if callee != fn and self.body_of(callee) is not None:
                    k = self.helper_kind(callee, key, depth - 1)
            if k:
                kinds.add(k)
        best = None
        for k in kinds:
            best = stronger(best, k)
        memo[fn] = best
        return best

    def _collect_marks(self, fn, key, direct, helper, mentioned, depth=1):
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
        but which argument it applied to is not, which is the whole of round-5 defect (c).
        """
        body = self.body_of(fn) or ""
        mentioned |= set(re.findall(r"[A-Za-z_]\w*", body))
        for name, args, recv, op in find_calls(body):
            kind = loc_kind(name) if key == "dcompact" else prim_kind(name)
            if kind:
                for tok in arg_tokens(args):
                    direct[tok] = stronger(direct.get(tok), kind)
                continue
            if depth <= 0:
                continue
            # Round 6 (B1): the CALLEE, not the bare name. `collector->mark()` runs
            # BaseCollector::mark, and binding it to whichever `mark()` the file order
            # produced is how vernier's `stack_table_value` reported UNMARKED.
            callee = self.callee_key(name, recv, op, fn)
            if callee == fn or self.body_of(callee) is None:
                continue
            hk = self.helper_kind(callee, key)
            if hk:
                for tok in arg_tokens(args):
                    helper[tok] = stronger(helper.get(tok), hk)
            self._collect_marks(callee, key, direct, helper, mentioned, depth - 1)

    def mark_index(self, dtype):
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
            fn = self.dtypes.get(dtype, {}).get(key)
            direct, helper, mentioned = {}, {}, set()
            if fn and fn not in ("NULL", "0", "RUBY_DEFAULT_FREE"):
                self._collect_marks(fn, key, direct, helper, mentioned)
            idx[key] = (direct, helper, mentioned)
        return idx


# ---------------------------------------------------------------- the predicate

FIELD = re.compile(
    r"^[ \t]*(?:const\s+|volatile\s+)*VALUE\s+([^;{}()=]+);", re.M)


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
        # `VALUE (*cb)(...)` is a function pointer, not a reference the GC can mark; `=`
        # only appears here in a C++ default member initialiser.
        if "(" in rest or "=" in rest:
            continue
        for decl in split_args(rest):
            d = decl.strip()
            ptr = "*" in d or "[" in d
            nm = d.replace("*", " ").split("[")[0].strip()
            if nm.isidentifier():
                fields.append((nm, ptr))
    return fields


def sweep(tree, verbose=False):
    suspects, clears = [], []
    seen, reported, typed_seen = set(), set(), set()
    # Typed dtypes first, so the `<inline:>` legacy pseudo-dtype is the one coalesced away
    # and never the other way round.
    sites = sorted(tree.wrap_sites, key=lambda s: s[1].startswith("<inline:"))
    for path, dtype, _st, macro in sites:
        st = tree.struct_type_for(dtype)
        if not st or st not in tree.structs:
            if (dtype, st) not in seen:
                seen.add((dtype, st))
                clears.append((dtype, st or "?", "-",
                               "struct type unresolved (%s in %s)" % (macro, path.name)))
            continue
        if (dtype, st) in seen:
            continue
        seen.add((dtype, st))
        inline = dtype.startswith("<inline:")
        idx = tree.mark_index(dtype)
        m_direct, m_helper, m_named = idx["dmark"]
        c_direct, c_helper, _ = idx["dcompact"]
        decl_in = tree.struct_file.get(st, path)
        for field, is_ptr in value_fields(tree.structs[st]):
            # Round 5 (a): the key carries the dtype. The old (struct, field) key let
            # msgpack's marking `buffer_data_type` clear `msgpack_buffer_t` on behalf of
            # `buffer_view_data_type`, whose .dmark is NULL. Two wrappers of one struct
            # are two verdicts, and the safe one must not speak for the unsafe one.
            if (dtype, st, field) in reported:
                continue
            # The ONE case the de-dupe was added for: a gem under an #ifdef carrying both
            # TypedData_Make_Struct and legacy Data_Make_Struct wraps the same struct
            # twice, and the pseudo-dtype has no dcompact by construction, so reporting it
            # would double every line and invent a NO-COMPACT on a gem that has one.
            if inline and (st, field) in typed_seen:
                continue
            reported.add((dtype, st, field))
            if not inline:
                typed_seen.add((st, field))

            kind = stronger(m_direct.get(field), m_helper.get(field))
            via_helper = field not in m_direct and field in m_helper
            in_compact = bool(c_direct.get(field) or c_helper.get(field))
            cat = None
            if kind is None:
                # Round 5 (b): presence in the body is not a mark. Separating these two
                # keeps the recall honest -- MENTIONED says "we saw the name and it was
                # not in a marking call", which is a question for pass 2, not a verdict.
                cat = "MENTIONED" if field in m_named else "UNMARKED"
            elif is_ptr:
                cat = "VALUE*"
            elif kind == "movable" and not in_compact:
                # Round 5 (c): reached through a callee, so which argument the movable
                # primitive applied to is not resolved here. Report, do not clear.
                cat = "NO-COMPACT-UNKNOWN" if via_helper else "NO-COMPACT"
            if cat:
                suspects.append((cat, decl_in, st, field, dtype,
                                 tree.dtypes.get(dtype, {})))
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

    def narrowed(self, path, a, b, name):
        """Is local `name` constrained to immediates by an equality chain that can raise?

        stackprof's `mode` starts as `rb_hash_aref(opts, sym_mode)` -- arbitrary -- and is
        then run through `if (mode == sym_object) ... else if (mode == sym_wall || mode ==
        sym_cpu) ... else if (mode == sym_custom) ... else rb_raise(...)`. Nothing in the
        assignment set proves it is a symbol; the else-raise does.

        All three conditions are load-bearing. A comparison with no rejection path narrows
        nothing, so the raise is required; one comparison against a non-immediate means
        the chain admits a heap object, so ALL of them must be immediate.
        """
        src = self.tree.files[path]
        body = src[a:b]
        if not RAISES.search(body):
            return None
        pat = re.compile(r"(?:\b%s\s*[=!]=\s*([A-Za-z_]\w*)"
                         r"|([A-Za-z_]\w*)\s*[=!]=\s*%s\b)"
                         % (re.escape(name), re.escape(name)))
        hits, first = [], None
        for m in pat.finditer(body):
            tok = m.group(1) or m.group(2)
            if not is_immediate(self.tree, tok):
                return None
            hits.append(tok)
            first = first if first is not None else a + m.start()
        return (sorted(set(hits)), first) if hits else None

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
                    re.finditer(r"(?<![\w.>])%s\s*=(?![=])" % re.escape(r), body)]
            if srcs and all(is_immediate(self.tree, s) for s in srcs):
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


def self_test(base):
    """Fail loudly rather than let a broken query clear the corpus by accident."""
    base = pathlib.Path(base)
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

    def flagged_from_source(src):
        """(categories, fields) for a tree generated from one C file."""
        with tempfile.TemporaryDirectory() as tmp:
            ext = pathlib.Path(tmp) / "ext"
            ext.mkdir()
            (ext / "t.c").write_text(src)
            s, _ = sweep(Tree(ext))
            return {c for c, _, _, _, _, _ in s}, {f for _, _, _, f, _, _ in s}

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

    # -- predicate A against the real gem, when the fixtures are present ------------
    #
    # The generated controls above prove the mechanism; only stackprof proves the ANSWER.
    # A missing fixture prints SKIP rather than nothing, because the round-4 rule is that
    # absence of a failure signal is not a negative result.
    sp = base.parent / "corpus" / "stackprof-0.2.28"
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

    pris, fixed = base.parent / "fixtest" / "sp-pristine", \
        base.parent / "fixtest" / "sp-fixed"
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
        sys.exit(self_test(a.self_test))
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

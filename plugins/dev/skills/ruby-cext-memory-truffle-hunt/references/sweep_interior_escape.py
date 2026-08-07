#!/usr/bin/env python3
"""Predicate D: an interior pointer held across a window that can move or free its String.

    python3 sweep_interior_escape.py <gem-dir> [<gem-dir> ...]
    python3 sweep_interior_escape.py --self-test <gem-dir> [<gem-dir> ...]

THE ARGV POLARITY RULE -- READ THIS BEFORE THE CODE THAT DEPENDS ON IT
======================================================================
Two existing sources disagree, and neither is wrong. Predicate B's docstring says
`argv[i]` is "the canonical RED shape, not a discharge". Round 6's okra, prism and
iconv write-ups all used `argv` as a *measured* liveness discharge. They are talking
about different objects. The reconciliation this predicate commits to:

    **`argv` pins the object it HOLDS -- the un-coerced original.**

    It discharges liveness only when no coercion can have produced a different
    object: the argument is provably already a String at the derive. The moment a
    `to_str` / `to_s` / `StringValue` coercion may have replaced it, `argv` pins the
    original and NOT the object the pointer came from -> RED.

Worked both ways, from filed bugs:

  rmagick#1846   `rm_str2cstr(argv[0], &len)` -> `StringValue(str)` inside the callee.
                 The callee's parameter is a COPY; the conversion may hand back a
                 different String; `argv[0]` still pins the original. RED. Predicate B
                 records that an early cut had this backwards and discharged the bug.
  okra           `if (!rb_respond_to(string, to_str)) string = to_s(string);` then
                 `RSTRING_PTR(string)`. `argv[0]` pins the *argument*; the parsed
                 String is the `to_s` result, which nothing roots. RED, reproduced.
  prism          `input_load_string` is reached with a value that `RB_TYPE_P(.., T_STRING)`
                 has already proven to be a String. No coercion is possible, so the
                 object the pointer came from IS the object `argv` pins. Liveness
                 discharged -- and only liveness. See the next rule.
  iconv          source is `argv[2]`, type-checked, never re-assigned. Same as prism.

**Liveness is a COLUMN, not a discharge.** An `argv`-pinned source is alive for the
whole call and can still be *embedded*, and an embedded String's bytes live in the
object slot, so compaction moves them out from under a raw `char *`. Predicate A's
`REGISTERED` grade is the precedent: a downgrade, never a clear. So an `argv`-pinned
row is reported as `MOBILITY-ONLY` rather than removed. Round 7's in-call probe
settles what that is worth: on ruby 4.0.6 / 3.4.10 / 3.4.7 an embedded String that
relocates inside a call reads back as NUL bytes **every time** -- CRuby zero-fills the
vacated slot -- so `relocated but read clean` is not an outcome that exists.

THE SHAPE
=========
An ordered triple inside one function body:

    derive   a `char *` into a String's bytes -- RSTRING_PTR, StringValuePtr,
             StringValueCStr, RSTRING_GETMEM, RSTRING_END -- from ANY expression,
             at ANY storage class. Not just a by-value parameter: cgi's
             `VALUE str = argv[0]; StringValue(str);` is a LOCAL, and predicate B
             cannot see it by construction. That gap is this predicate's charter.
    window   something between the derive and the read that can trigger a GC or a
             compaction. Classified, never assumed -- see WINDOWS below.
    deref    the pointer is read, or escapes the frame entirely.

Predicate B starts from an in-place conversion of a by-value parameter and asks
whether its result escapes. This one starts from the derivation and asks what happens
to the pointer next. The two overlap on rmagick and bootsnap and nowhere else: B's
101-parameter funnel never sees prism, date, okra, mittens, rinku or cgi, because none
of those converts a by-value parameter.

CFUNC ENTRY POINTS: THE POLARITY IS INVERTED FROM PREDICATE B
=============================================================
B excludes cfunc entry points, correctly: neither of its sub-shapes can exist there
(no C-level caller, and it returns VALUE). For THIS predicate a cfunc body is
*precisely* where okra, cgi, rinku, iconv and erb all live. Same machinery, opposite
meaning: a cfunc entry point is where the `argv`-pinned liveness rule APPLIES, so it
is a liveness downgrade, not an exclusion. Deleting the `tree.cfuncs` check would lose
five of the twelve positive controls.

WINDOWS -- WHAT COUNTS, AND THE ONE JUDGEMENT CALL
==================================================
    GVL-RELEASE     rb_thread_call_without_gvl[_2], rb_nogvl
    RUBY-ALLOC      rb_str_new*, rb_ary_*, rb_hash_*, rb_enc_str_new*, rb_obj_alloc, ...
    RUBY-REENTRY    rb_funcall*, rb_yield*, rb_protect, rb_rescue, rb_obj_call_init,
                    rb_class_new_instance, rb_proc_call
    RAISE           rb_raise, rb_exc_raise, rb_sys_fail, rb_bug -- mittens' ONLY window:
                    `rb_raise(rb_eArgError, "unknown language: %s", algorithm)` formats a
                    Ruby String from a pointer derived long before it.
    IMPLICIT-COERCE StringValue*, rb_String, rb_check_string_type, NUM2* on a non-immediate
    ALLOCV          ALLOCV / ALLOCV_N **at or above RUBY_ALLOCV_LIMIT = 1024**. Below the
                    limit it is `alloca` and there is no GC-visible object at all. erb
                    6.0.4's window is exactly this and exists only for 171-615-byte input.
    XREALLOC        ruby_xrealloc / ruby_xmalloc / xrealloc / xmalloc -- GC-visible.
                    NOT libc realloc. trilogy 2.12.x builds with -DTRILOGY_XALLOCATOR and
                    2.9.0 does not, and the two spell the call identically in the source.
                    **Settle that one on the artifact with `nm`, never on the header.**

The judgement call, made explicitly rather than left implicit: **a window containing no
Ruby allocation is not a window** -- rinku is only reachable *with* a block, and date's
`tmx_m_zone` is 0/75,000 because its margin is exactly one allocation wide. But requiring
a *visible* allocating call misses the mobility case where the allocation happens inside
a non-copying library that keeps reading the buffer (prism, nokogiri). **This predicate
picks RECALL:** an escape into a non-copying library counts as a window even with no
visible allocator, and the `library` column says which regime it is. The cost is
false positives on libraries that copy; the alternative is silence on the two gems whose
findings were the hardest to get.

ORDERING
========
Predicate B is explicitly path-insensitive. Here derive/window/deref is an ORDERED
triple and the offsets are compared, so `RSTRING_PTR(x)` after the last allocating call
is not a hit. It inherits B's blind spot unchanged and it must be stated: there is no
CFG, so a defect on the ELSE-BRANCH of a conversion is wrongly cleared, and a window on
a branch that the deref cannot reach is wrongly counted.

WHAT IS A DISCHARGE AND WHAT IS ONLY A COLUMN
=============================================
Discharges REMOVE the row. Each is named in the output and each has a `--disable-rule`
mutation in the self-test, because a discharge rule with no generated red is a rule
nobody has tested.

    guarded            RB_GC_GUARD(src) at or after the last deref, naming the SOURCE or a
                       copy of it -- and only while that name still holds what it held at
                       the derivation. `guard = str; guard = other; RB_GC_GUARD(guard)`
                       guards `other`; the row stands.
    no-window          nothing between derive and last deref that can trigger GC,
                       and the pointer does not leave the frame
    last-use-after     the source VALUE is READ again at or after the last deref.

                       WHAT COUNTS AS THAT READ IS ONE PREDICATE, source_reads(), SHARED
                       WITH `guarded` -- three reviews found three ways to accept a bare
                       token occurrence as evidence the name still held the object, and
                       they are one question with three disqualifiers rather than three
                       rules: a write is not a read (`str = other;`), a read after a
                       rebinding write reads something else, and a read of an inner
                       `VALUE str` inside a nested block reads a different variable.
                       RECALL-BIASED BY CONSTRUCTION: the compiler may drop the VALUE
                       *before* its last syntactic use -- that is RB_GC_GUARD's own
                       documented rationale (include/ruby/internal/memory.h). This rule
                       therefore over-clears, and it is kept only because without it
                       every correct in-call use in the corpus reports. Round 6's okra
                       finding is the proof it can be wrong: `x19` held the VALUE up to
                       the pointer load and was then overwritten by the GumboOutput*.
    copies-immediately the derive feeds a copying call with no window in between
    pinning-mark       the source is a struct field that the owning type's REGISTERED dmark
                       marks with a PINNING mark -- `rb_gc_mark`, never
                       `rb_gc_mark_movable`. The only rule here that is a fact about
                       another function entirely, and the only one that clears an ESCAPE:
                       a pinned String is neither collected nor relocated for as long as
                       the wrapper is reachable, which outlasts any window this file can
                       classify. Written down in round 8, built in round 10, and the two
                       rounds in between are the deferral: the discharge turns on ONE
                       token, and a version that reads `rb_gc_mark_movable` as pinning
                       clears the mobility bug along with the safe case. Three of the five
                       arms of its fixture are reds for exactly that.

Columns, which never remove a row:

    liveness   UNROOTED | ARGV-PINNED | STACK-FIELD | GUARDED
    size       EMBEDDED-POSSIBLE | HEAP-GUARANTEED. This was MEANT to be a discharge --
               a String at or above the 616-byte boundary keeps its bytes in a malloc'd
               buffer that compaction does not move, which is the cheapest true clear
               available. It is not one, and the reason is worth carrying: over 55 trees
               the rule cleared **zero** rows. zstd's 131,591-byte frame buffer is
               `rb_str_new(NULL, ZSTD_compressBound(input_size))` and trilogy's 32768
               threshold is a runtime comparison -- both are computed, neither is a
               source constant a sweep can read. A discharge that never fires is a rule
               nobody has tested, so it was demoted rather than kept as decoration.
    library    the non-copying table below. **A severity column, never a verdict.**
               `xmlReaderForMemory` has had four buffer regimes across libxml2
               versions -- alias, eager copy, lazy retain, eager copy again -- so the
               gem version does not decide it and neither does the API name. Check the
               LINKED library version and settle it by mutation.

A ZERO MUST BE READABLE
=======================
Every run prints the funnel in both units, (function, derivation) pairs and distinct
functions, and prints every discharge with the rule that cleared it. racc is the control
for this: it has zero `RSTRING_PTR`/`StringValue`/`char *`-from-String in the whole tree,
so its zero is `0 derivations`, not `0 hits after 40 discharges`, and the counters are
the only thing that tells those two apart.

NAME RESOLUTION IS SHARED, AND LIVES IN tu_scope.py
---------------------------------------------------
Every lookup that turns a NAME at a use site into a DEFINITION goes through
`tu_scope.bind`, which states C's linkage rule once for all four predicates: a use binds
to a definition in its own file first, a `static` definition in another .c/.cc/.cpp/.cxx
is not a candidate at all, and everything else -- non-static definitions, and anything
declared in a HEADER -- stays tree-wide. THE SAME RULE ANSWERS FOR SLOTS: which persistent
objects a store in this file can be naming is `declared_scope` applied to a declaration
rather than to a definition, which is how a header-declared `extern const char *saved;`
became visible here without another translation unit's `static` becoming visible with it.
`tu_scope.alias_set` is the fourth rule and the one that is not lexing: which OTHER locals
carry the pointer a derivation produced -- predicate B needed the same closure on its own
alias set and neither file states it now. That module is a sibling file and these scripts
will not run without it; references/ is the unit that ships.

ACCEPTANCE (--self-test): see self_test(). Twelve positive controls, four clean negative
controls, seventeen pinned-residue trees, a per-rule mutation table, and generated reds rather
than a green-only suite. Twelve of the reds are SYNTHETIC TREES WRITTEN BY THE TEST, because
the corpus is neutral on those shapes and a corpus-neutral fix is exactly the one a green
suite cannot tell from no fix at all: `RSTRING_GETMEM`'s output pointer; a definition at
namespace or `extern "C"` scope; a definition carrying a trailing `__attribute__((...))` or
`noexcept`; a read through a second pointer local; a rebound guard variable; a store into a
file-scope scalar; a store into a slot declared in a HEADER and defined in another
translation unit; an adjusted pointer (`RSTRING_END(str) - 1`) stored into a file static;
a trailing write to the source mistaken for a use of it; a `dmark` that marks the
derivation's own field with the MOVABLE mark instead of the pinning one -- the corpus has
no such tree, which is precisely why the discharge that reads that token was deferred for
two rounds rather than shipped against a green corpus; a pointer PINNED by that dmark and
then stored somewhere the WRAPPER does not own, which is the pin outliving nothing; a
pinning call that runs only on one arm of an `if`, which is a pin on some executions and a
dangling `char *` on the rest; the same condition moved onto the CALL EDGE of an
unconditional mark helper, which is that fix routed around one scope out; and a source
field or destination base REBOUND after the derivation, where the name the discharge looked
up no longer denotes what it looked up. Those last four are #32's five review findings and
every one of them is corpus-neutral except the escape rule, which moves two rows -- so the
generated reds are the entire evidence that four of the five fixes happened at all. Each pins
the FUNNEL COUNTERS and not only the hit count -- an untracked pointer and an empty index
both end in `hit 0`, and they are different failures. The three that narrow a DISCHARGE ship
with a green as well: a rule that stops clearing has to be shown still clearing the case it
was written for, or the fix is indistinguishable from a deletion. Run it before trusting any
result: silence is a property of the query until the counts say otherwise, and a missing
fixture is a FAIL rather than a quietly smaller suite.
"""
import argparse
import pathlib
import re
import shutil
import sys
import tempfile

# The linkage rule, shared with the other three predicates. Sibling module, so
# `python3 .../sweep_interior_escape.py` finds it wherever it is run from; references/ is
# the unit that ships, and a script copied out of it on its own will not import.
import tu_scope

C_EXT = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp")

# ---------------------------------------------------------------- lexing helpers
#
# Verbatim from sweep_escaped_conversion.py, which took them from sweep_unmarked.py. A
# third copy is the established pattern: these three scripts are meant to be readable and
# runnable one at a time, and a shared module would make each one unreadable on its own.
# `strip_noise` blanks comments and string bodies but KEEPS NEWLINES, and
# `strip_directives` keeps both line count and byte length, so byte offsets AND line
# numbers into the stripped text both match the original file.


def blank(span):
    """Spaces of the same length, but NEWLINES KEPT so line numbers survive."""
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
            out.append(c + blank(src[i + 1:j - 1]) + c if j - i >= 2 else blank(src[i:j]))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def strip_directives(src):
    """Blank out preprocessor directive lines, keeping the code inside conditionals."""
    out, i, n = [], 0, len(src)
    while i < n:
        j = src.find("\n", i)
        j = n if j < 0 else j
        line = src[i:j]
        if line.lstrip().startswith("#"):
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


match_brace = tu_scope.match_brace


# WHERE A DECLARATOR ENDS AND A BODY BEGINS -- one implementation, in tu_scope.py, beside
# the linkage rule and the transparent-scope walk the same scripts kept re-deriving. A
# definition may put tokens between the parameter list's `)` and the body's `{`, in both C
# and C++:
#
#     static VALUE bad(VALUE str) __attribute__((noinline)) { ... }   /* C     */
#     static VALUE bad(VALUE str) noexcept { ... }                    /* C++   */
#     static auto bad(VALUE str) -> VALUE { ... }                     /* C++11 */
#
# _index_funcs used to skip WHITESPACE ONLY and then require `{`, so it never reached the
# brace and dropped the whole function -- derivations, windows and escapes with it. A tree
# whose extension is written that way reported `0 fn(s) | derive 0/0 -> hit 0`: the empty
# index again, the shape of zero this file's ZERO MUST BE READABLE section exists to make
# impossible, and the same failure the namespace port fixed from the other direction.
#
# This file fixed it twice in one round -- once by naming `__attribute__` and `noexcept` in
# a closed list, and once by opening the words and closing the parentheses when the trailing
# return type broke the list again. Predicate C then reported the FOURTH appearance of the
# same gap in its own function index, which is why the walk now has one home. The rejection
# table, the reason the words are open, and the recall limits that remain are all in
# tu_scope's docstring; the assertions for them are in this file's self-test at 8v/8w and in
# predicate C's.
match_paren = tu_scope.match_paren
POST_DECL_PAREN = tu_scope.POST_DECL_PAREN
POST_DECL_STOP = tu_scope.POST_DECL_STOP
POST_DECL_PUNCT = tu_scope.POST_DECL_PUNCT
skip_post_declarator = tu_scope.skip_post_declarator


# C++ SCOPE HEADS AND THE WALK THAT TREATS THEM AS TRANSPARENT -- one implementation, in
# tu_scope.py, beside the linkage rule the same four scripts kept re-deriving. Three brace
# dispositions, not two: a depth-0 `{` belongs to the CURRENT statement when it opens an
# aggregate or an initialiser, ENDS it when it opens a function body, and -- the disposition
# C++ adds -- opens a scope with no storage duration of its own when it follows
# `namespace X` or `extern "C"`.
#
# This predicate only ever had the first two until round 9, so every definition in a
# namespaced or `extern "C"`-wrapped tree sat at nonzero brace depth and _index_funcs
# skipped it: `0 fn(s)`, `0 derivations`, `0 hits` -- a clean sheet on an empty index, which
# is exactly the shape of zero this file's ZERO MUST BE READABLE section exists to make
# impossible. Predicate C shipped the identical hole in its own function index a round
# later, which is why the walk now has one home rather than three.
NAMESPACE_HEAD = tu_scope.NAMESPACE_HEAD
LINKAGE_HEAD = tu_scope.LINKAGE_HEAD
top_level_units = tu_scope.top_level_units
scope_zero_braces = tu_scope.scope_zero_braces


def split_args(text):
    """Split an argument list on top-level commas."""
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
    """Given the index just past a call name, return (args, index past the `)`)."""
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


NOT_CALLS = {"if", "for", "while", "switch", "return", "sizeof", "defined", "do", "else",
             "case", "typeof", "alignof", "static_assert", "catch", "__attribute__",
             "assert", "RB_LIKELY", "RB_UNLIKELY", "LIKELY", "UNLIKELY"}


def arg_spans(src, name_end):
    """[(start, end)] for each top-level argument of the call whose name ends at `name_end`.

    `call_args` returns the argument TEXTS, which is enough to ask what an argument says and
    not enough to ask WHERE it says it. An alias that has been reassigned still matches its
    own spelling, so the question "which argument slot carries the pointer" has to be asked
    by offset -- see alias_reads().
    """
    i = src.find("(", name_end)
    if i < 0:
        return []
    depth, start, out = 0, i + 1, []
    for j in range(i, len(src)):
        c = src[j]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                out.append((start, j))
                return out
        elif c == "," and depth == 1:
            out.append((start, j))
            start = j + 1
    return []


def find_calls(body):
    """[(name, args, name_start, past_close)] for every call in `body`.

    No `if args:` guard. vernier's `collector->mark()` has an EMPTY argument list, and
    guarding on truthiness dropped it before resolution ever ran -- which presented as a
    C++ overload-resolution bug and was not one. Round 6 fixed it in the other two sweeps;
    do not reintroduce it here.
    """
    out = []
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?=\()", body):
        if m.group(1) in NOT_CALLS:
            continue
        args, end = call_args(body, m.end())
        if args is not None:
            out.append((m.group(1), args, m.start(), end))
    return out


def line_of(text, off):
    """1-based line number of a byte offset. Exact only because blank() keeps newlines."""
    return text.count("\n", 0, off) + 1


# ------------------------------------------------------- the vocabulary

# Interior derivers: a `char *` into a String's bytes. Exactly predicate B's set -- it is
# already the derivation set this predicate keys on, which is why it is reused rather than
# re-derived.
INTERIOR = {"RSTRING_PTR", "RSTRING_END", "RSTRING_GETMEM", "StringValuePtr",
            "StringValueCStr", "rb_string_value_ptr", "rb_string_value_cstr",
            "RSTRING_PTRZ"}
LENGTH = {"RSTRING_LEN", "RSTRING_LENINT", "RSTRING_EMBED_LEN"}
DEREF = INTERIOR | LENGTH

# In-place conversions. On a LOCAL these create a new object that only that local roots;
# that is the cgi shape and predicate B cannot reach it.
LVALUE_CONV = {"StringValue", "StringValuePtr", "StringValueCStr", "SafeStringValue",
               "ExportStringValue", "FilePathValue", "FilePathStringValue"}
ADDR_CONV = {"rb_string_value", "rb_string_value_ptr", "rb_string_value_cstr",
             "rb_file_path_value", "rb_check_string_type_ptr"}

# Coercions that MAY REPLACE the object -- the hinge of the argv polarity rule. A type
# CHECK is not here: it proves the object already is a String, so nothing was replaced.
MAY_REPLACE = LVALUE_CONV | ADDR_CONV | {
    "rb_String", "rb_str_to_str", "rb_obj_as_string", "rb_check_string_type",
    "rb_str_new_frozen", "rb_str_dup_frozen", "rb_str_dup", "rb_str_conv_enc",
    "rb_external_str_new", "rb_get_path", "rb_str_export", "rb_str_export_locale"}
TYPE_CHECK = {"Check_Type", "rb_check_type", "RB_TYPE_P", "TYPE"}

# ---- windows. Each is a named class, printed on the hit, never merged into "unsafe".
W_GVL = re.compile(r"^(rb_thread_call_without_gvl2?|rb_nogvl|rb_thread_blocking_region)$")
W_ALLOC = re.compile(
    r"^(rb_str_(new|buf_new|cat|catf|concat|append|resize|times|substr|subseq|dup|freeze"
    r"|new_cstr|new_static|new_frozen|new_shared|tmp_new|to_str|plus|format)\w*"
    r"|rb_enc_str_new\w*|rb_utf8_str_new\w*|rb_usascii_str_new\w*|rb_external_str_new\w*"
    r"|rb_locale_str_new\w*|rb_filesystem_str_new\w*|rb_sprintf|rb_vsprintf"
    r"|rb_ary_(new|push|store|cat|concat|unshift|entry_set|resize|assoc|dup|freeze"
    r"|new_capa|new_from_args|new_from_values|join)\w*"
    r"|rb_hash_(new|aset|update|dup|new_capa)\w*|rb_obj_alloc|rb_class_new_instance"
    r"|rb_struct_new|rb_float_new|rb_int_new|rb_ll2inum|rb_ull2inum|rb_uint2big"
    r"|rb_big_new|rb_rational_new|rb_complex_new|rb_obj_dup|rb_obj_clone"
    r"|rb_gc_start|rb_gc|rb_gc_compact|rb_gc_mark_locations)$")
W_REENTRY = re.compile(
    r"^(rb_funcall\w*|rb_yield\w*|rb_protect|rb_rescue2?|rb_ensure|rb_obj_call_init"
    r"|rb_proc_call\w*|rb_block_call|rb_iterate|rb_eval_string\w*|rb_apply"
    r"|rb_method_call\w*|rb_check_funcall\w*|rb_respond_to)$")
W_RAISE = re.compile(r"^(rb_raise|rb_exc_raise|rb_fatal|rb_sys_fail\w*|rb_bug"
                     r"|rb_warn|rb_warning|rb_notimplement|rb_loaderror)$")
W_COERCE = re.compile(r"^(StringValue\w*|FilePathValue|FilePathStringValue|rb_String"
                      r"|rb_check_string_type|rb_str_to_str|rb_obj_as_string"
                      r"|rb_string_value\w*|NUM2\w+|rb_num2\w+)$")
W_ALLOCV = re.compile(r"^ALLOCV(_N)?$")
W_XALLOC = re.compile(r"^(ruby_xmalloc\w*|ruby_xrealloc\w*|ruby_xcalloc\w*"
                      r"|xmalloc\w*|xrealloc\w*|xcalloc\w*|ALLOC|ALLOC_N|REALLOC_N)$")

# `RUBY_ALLOCV_LIMIT` is 1024. Below it ALLOCV is alloca and there is no GC-visible
# object; at or above it, it allocates an imemo_tmpbuf. erb 6.0.4's whole window is this
# distinction, and it is why erb is only exposed for 171-615-byte inputs.
ALLOCV_LIMIT = 1024

# The measured embedded boundary. Identical -- 616 -- on ruby 4.0.6 and 3.4.10
# (arm64-darwin) and 4.0.5 and 3.4.10 (x86_64-linux). At or above it a String's bytes are
# in a malloc'd buffer, which compaction does not move.
EMBED_BOUNDARY = 616

# Non-copying / retaining library entry points. **A SEVERITY COLUMN, NEVER A VERDICT.**
# Every one of these has at least one version that copies. xmlReaderForMemory alone has
# four regimes: <=2.10 aliases, 2.11 eager copy, 2.12 lazy retain, 2.13+ eager copy.
NONCOPYING = {
    "xmlReaderForMemory": "libxml2: 4 buffer regimes; <=2.10 aliases, 2.12 retains lazily",
    "xmlReaderForIO": "libxml2: caller buffer via callback",
    "xmlCreateIOParserCtxt": "libxml2: caller buffer via callback",
    "xmlParseInNodeContext": "libxml2: caller buffer",
    "pm_string_constant_init": "prism: aliases, no copy (its own DEBUG build memcpy's first)",
    "pm_parser_init": "prism: parses the aliased buffer",
    "BIO_new_mem_buf": "openssl: aliases unless BIO_FLAGS_MEM_RDONLY is cleared",
    "SSL_CTX_set_default_passwd_cb_userdata": "openssl: stored, read at handshake",
    "SSL_CTX_set_alpn_select_cb": "openssl: stored",
    "SSL_CTX_set_next_proto_select_cb": "openssl: stored",
    "sqlite3_bind_text": "SQLITE_STATIC binds without copying; SQLITE_TRANSIENT copies",
    "sqlite3_bind_blob": "same, check the destructor argument",
    "sqlite3_prepare_v2": "does not retain the SQL text, but the tail pointer aliases it",
    "yajl_parse": "non-copying, and re-enters Ruby from its callbacks",
    "sass_make_data_context": "takes ownership of the buffer",
    "curl_easy_setopt": "CURLOPT_POSTFIELDS aliases; CURLOPT_COPYPOSTFIELDS copies",
    "gumbo_parse_with_options": "gumbo: every GumboStringPiece points into the input",
    "gumbo_parse": "same",
    "sb_stemmer_new": "snowball: the algorithm name is read later on the raise path",
    "upb_StringView_FromDataAndSize": "protobuf: aliases when the arena is NULL",
}

# Calls that copy the bytes immediately, so the pointer is dead by the next statement.
#
# `strlcpy`/`strlcat` and `xstrdup`/`ruby_strdup` were added in round 8. The first pair is
# rmagick's spelling and the second is trilogy's, and both were missing, so a copy that a
# human reads at a glance was invisible to the sweep. NOTE what is deliberately NOT here:
# `CloneString` (rmagick/MagickCore) and `magick_clone_string`, which copy by every account
# of their documentation. The skill's rule is "never conclude copies from the API name",
# and an in-tree wrapper whose body is one call to an external function is exactly the case
# where the name is all you have. Leaving them out costs rmagick four rows that a human
# triage clears in a minute; putting them in would make this rule a list of names that is
# only as good as the day it was last extended.
#
# MEMBERSHIP HERE IS NOT A DISCHARGE ON ITS OWN. The entries that allocate a Ruby object --
# rb_str_new* and friends, rb_intern, and `xstrdup`/`ruby_strdup`, which are ruby_xmalloc --
# are filtered back out at the use site by classify_window(), because they can run a GC
# before they copy. They stay in the list so that `copies-in-callee` can still recognise a
# copy several frames down, where the allocation is the callee's problem and the window is
# reported against the callee.
COPIES = re.compile(r"^(memcpy|memmove|strn?cpy|strl?cpy|strl?cat|strn?cat|strn?dup"
                    r"|xstrdup|ruby_strdup|snprintf|vsnprintf"
                    r"|rb_str_new|rb_str_new_cstr|rb_utf8_str_new|rb_utf8_str_new_cstr"
                    r"|rb_enc_str_new|rb_usascii_str_new|rb_str_buf_cat|rb_str_cat"
                    r"|rb_str_cat_cstr|rb_str_append|rb_intern|rb_intern2|rb_intern3"
                    r"|rb_id_intern|rb_check_id_cstr|rb_str_hash_cmp|strcmp|strncmp"
                    r"|memcmp|strlen|atoi|atol|strtol|strtod|strtoul)$")

DEFINE_RE = re.compile(r"^rb_define_(method|singleton_method|module_function|"
                       r"global_function|private_method|protected_method|method_id|"
                       r"protected_method_id|private_method_id|alloc_func)$")

PARAM_VALUE_RE = re.compile(r"^(?:const\s+|volatile\s+|register\s+)*VALUE\s+(\w+)$")
PARAM_VALUE_PTR_RE = re.compile(r"^(?:const\s+|volatile\s+|register\s+)*VALUE\s*\*\s*(\w+)$")
LVALUE_RE = re.compile(r"[A-Za-z_]\w*(\s*(->|\.)\s*[A-Za-z_]\w*|\s*\[[^\]]*\])*")


def param_name(decl):
    """Best-effort declarator name: `char (* cache_path)[N]` -> cache_path."""
    d = decl.split("[")[0]
    ids = re.findall(r"[A-Za-z_]\w*", d)
    kw = {"const", "volatile", "register", "struct", "union", "enum", "unsigned",
          "signed", "long", "short", "int", "char", "void", "float", "double", "static"}
    ids = [i for i in ids if i not in kw]
    return ids[-1] if ids else ""


def base_name(expr):
    """The root identifier of an lvalue expression: `w->path[i]` -> `w`, `str` -> `str`."""
    m = re.match(r"^\s*\*?\s*&?\s*([A-Za-z_]\w*)", expr)
    return m.group(1) if m else ""


# WRITES, BLOCKS, DECLARATIONS AND source_reads ARE tu_scope's FIFTH RULE NOW.
#
# This file is where the predicate was unified, out of three separately-patched discharge
# defects (:1224 a rebound guard alias, :1288 a write counted as a read, :1614 a read of a
# shadowing redeclaration). The FOURTH member of the family turned up in tu_scope's own
# alias set -- `p = RSTRING_PTR(str); p = "safe"; return p;` -- so the predicate moved down
# beside rule 4 rather than gaining a fourth caller here and a first special case there.
# The names are re-exported unchanged, and every call site below is untouched; what the move
# added is the `kill` argument, because the alias set sits on the other side of the
# discharge. See tu_scope.source_reads.
writes = tu_scope.writes
source_reads = tu_scope.source_reads
ANY_WRITE = tu_scope.ANY_WRITE
DOMINATING_WRITE = tu_scope.DOMINATING_WRITE


# PERSISTENT-STORAGE SINKS. Ported from sweep_escaped_conversion.py's file_scope_objects,
# and the ports are not redundant: B keys on a by-value `VALUE` PARAMETER being converted in
# place and asks where the result goes, so it can only see this sink when a conversion
# happened. D keys on the DERIVATION and sees it whether or not anything was converted --
# `static const char *saved; ... saved = RSTRING_PTR(str);` in a function that converts
# nothing is outside B's walk by construction, exactly as cgi's converted local is.
#
# `typedef`/`using`/`template`/`namespace` declare no slot of their own. `extern VALUE x;` is
# NOT skipped: it names a persistent slot defined elsewhere, which is the sink we want.
DECL_NOT_OBJECT = re.compile(r"^(?:typedef|using|template|namespace)\b")
# ...and neither does a bare elaborated type specifier. `struct zone;` -- date's zonetab.h
# forward-declares the tag -- has no declarator at all, so param_name falls back to the TAG
# and `zone` became a persistent slot matching every local of that name in the tree.
TAG_ONLY = re.compile(r"\s*(?:struct|union|enum|class)\s+[A-Za-z_]\w*\s*\Z")
# A function-local `static` has the same storage duration as a file-scope one and the same
# consequence for an interior pointer, so it is collected too -- by declaration, in the body.
# One declaration statement: from `static` to the `;` that ends it.
LOCAL_STATIC_DECL = re.compile(r"\bstatic\b[^;{}]*;")


def local_statics(body):
    """Names declared `static` inside a function body -- BY IDENTITY, NOT BY TYPE.

    THE SINK IS A LIFETIME, AND THE DECLARED TYPE IS NOT THE LIFETIME. This collector used
    to require a `*` in the declaration (`\\bstatic\\b[^;{}()]*?\\*\\s*(name)`), so

        static uintptr_t saved;
        saved = (uintptr_t)RSTRING_PTR(str);      /* read back on a LATER invocation */

    was not a persistent sink at all: no `->`, no `[`, no pointer-parameter base and no
    entry in `statics`, so both escape branches declined the store, the row discharged
    `no-window`, and the sweep reported `derive 1/1 -> windowed 0/0 -> hit 0` on an address
    that outlives the String by every call the process makes afterwards.

    The same evasion is already in the scent library on the Class A side -- an object
    "stored as an integer, key, handle or index, not as a `void *`", which is
    prometheus-client-mmap keying a WeakMap on `str.as_raw()`. Same laundering, one class
    down: the predicate was keyed on the SPELLING of the store rather than on the fact that
    something outliving the frame now holds the address. A `long`, a `size_t`, a
    `uintptr_t`, an array index into a table of buffers -- all of them keep the bytes
    reachable and none of them writes a `*`.

    file_scope_objects() has always been type-agnostic; only this half asked about the
    type, and the asymmetry is what made the defect invisible while its file-scope twin was
    a shipped positive control. Both halves now collect the same way: split the declarator
    list, drop initialisers and prototypes, keep the name.
    """
    names = set()
    for m in LOCAL_STATIC_DECL.finditer(body):
        decl = m.group(0)
        decl = decl[decl.index("static") + len("static"):-1]
        for d in split_args(decl):
            d = d.split("=")[0]
            # a prototype declares no object; a function POINTER does, and is spelled
            # `(*fp)(...)`, so only the un-parenthesised declarator is dropped
            if "(" in d and not re.search(r"\(\s*\*", d):
                continue
            nm = param_name(d)
            if nm:
                names.add(nm)
    return names


def file_scope_objects(src):
    """{name: is_static} for every object declared at file or namespace scope.

    A slot at this scope outlives every frame that can reach it, so an interior pointer
    stored into one escapes the deriving frame exactly as a return value does. The sink is
    recognised POSITIVELY, by name: the inverted form ("any store to something not provably
    frame-local") would read `char *p = RSTRING_PTR(str);` as an escape, and that is the
    single commonest local declaration in the corpus.

    THE STATIC FLAG IS THE REACH, AND IT IS tu_scope's FIRST RULE APPLIED TO SLOTS RATHER
    THAN TO FUNCTIONS. This walk was already reading every file in the tree, but Tree kept
    the results in a per-file dict and `escapes` looked in ONE entry -- the deriving
    function's own file. A slot declared `extern const char *saved;` in a header and defined
    in another translation unit is invisible that way: the store `saved = RSTRING_PTR(str)`
    in x.c found nothing named `saved` in x.c, both escape branches declined it and the row
    discharged `no-window` on an address any later call reads. `declared_scope` is the same
    rule predicate C's header carve-out is: a header declaration reaches the whole tree, a
    `.c` `static` reaches its own file and nothing else. One rule, both directions.

    AN AGGREGATE BODY DECLARES MEMBERS, NOT SLOTS, and that had to be fixed before the reach
    could be widened. `struct pinned_data { VALUE ptr; };` is ONE unit ending in `;`, so the
    declarator handed to param_name was the whole thing and param_name returns the LAST
    identifier in it -- the last MEMBER. Confined to one file that was inert; made visible
    tree-wide it is not, and it produced four rows on the corpus immediately: fiddle's
    `struct pinned_data`'s member `ptr` matched a local `ptr` in pointer.c, rmagick's
    `char name[1]` inside a Draw struct matched a local `name` in rminfo.cpp and rmpixel.cpp,
    and date's zonetab.h member `zone` matched a local in date_parse.c. So the body is cut
    away and what follows the `}` is the declarator: `struct S { ... } obj;` declares `obj`,
    and `struct S { ... };` declares no object at all.
    """
    names = {}
    anon = tu_scope.anonymous_namespace_spans(src)
    for _off, unit in top_level_units(src):
        u = unit.strip()
        if not u.endswith(";"):
            continue                    # a function body or a class body, not a slot
        u = u[:-1].strip()
        if not u or DECL_NOT_OBJECT.match(u):
            continue
        # INTERNAL LINKAGE, BOTH SPELLINGS. `namespace { const char *saved; }` is internal
        # from the NAMESPACE with no `static` on the declaration, so a scope decision that
        # reads only the declaration text makes two translation units' slots one tree-wide
        # name -- the same rule predicate C's item-4 over-clear was, asked here in the
        # REPORTING direction (a merged slot makes a store in the other file look like a
        # sink). One function, tu_scope.internal_linkage, for both.
        is_static = tu_scope.internal_linkage(u, _off, anon)
        for d in split_args(u):
            d = d.split("=")[0]
            if "}" in d:
                d = d[d.rfind("}") + 1:]
            if not d.strip() or TAG_ONLY.match(d):
                continue                # a tag or a type definition, not an object
            # a prototype declares no object; a function POINTER does, and is spelled
            # `(*fp)(...)`, so only the un-parenthesised declarator is dropped
            if "(" in d and not re.search(r"\(\s*\*", d):
                continue
            nm = param_name(d)
            if nm:
                # two declarations of one name in one file: internal linkage only if every
                # one of them says so, which is the reporting direction
                names[nm] = names.get(nm, True) and is_static
    return names


# ------------------------------------- the pinning-mark index (round 10)
#
# THE DISCHARGE THIS SECTION EXISTS FOR: an interior pointer into a String that the owning
# type's `dmark` marks with a PINNING mark cannot be invalidated by either leg of this
# predicate. `rb_gc_mark` marks *and pins*, so the String is neither collected (liveness)
# nor relocated (mobility) for as long as the wrapper is reachable -- and the wrapper is
# what the pointer is stored in, so it is reachable for exactly as long as the pointer is.
# That is the skill's first safe idiom, and until round 10 this predicate could not read it:
# every one of the rows below was cleared by hand, every round, from a note in TRIAGED.
#
# THE WHOLE RULE TURNS ON ONE TOKEN, AND GETTING IT BACKWARDS OVER-CLEARS A CLASS IN ONE
# STEP. `rb_gc_mark(x)` pins. `rb_gc_mark_movable(x)` does NOT -- it is half of the
# *relocate* idiom, and it is safe only when a `dcompact` calls `rb_gc_location` on every
# stored copy, which is a property of a function this predicate never looks at. A movable
# mark keeps the String alive and lets compaction move its bytes out from under the raw
# `char *`, which is the mobility bug this predicate was built to find. So a movable mark
# must leave the row STANDING, and the two spellings differ by one suffix. That is why the
# rule was deferred through rounds 8 and 9 rather than written, and why it ships with a
# generated red for the movable arm (self-test 8q) that is byte-identical to the pinning
# one except for that suffix.
#
# THE TABLE IS PREDICATE A's, RESTATED RATHER THAN IMPORTED, AND THE SELF-TEST ASSERTS THE
# TWO AGREE. A grades pinning versus movable for its own UNMARKED verdict and has done since
# round 5b; importing it here would drag in a second whole-tree index (macro expansion, wrap
# sites, dtype-to-struct resolution) for three regexes. The established pattern in this
# family is a third copy of the LEXING with the semantics stated once -- so the semantics are
# stated once, in A, and 8s drives A's `prim_kind` and this file's over the same table. A
# divergence between the two is a FAIL here, not a silent disagreement about what pins.
MARK_PRIM = re.compile(
    r"^(?:rb_gc_mark|rb_gc_mark_maybe|rb_gc_mark_locations"
    r"|rb_gc_mark_movable|rb_gc_mark_and_move|RB_GC_MARK\w*)$")
MOVABLE_PRIM = re.compile(
    r"^(?:rb_gc_mark_movable|rb_gc_mark_and_move|RB_GC_MARK_MOVABLE\w*)$")


def prim_kind(name):
    """None | "pin" | "movable" -- how a marking primitive treats its argument.

    Verbatim from sweep_unmarked.prim_kind, and asserted equal to it in the self-test.
    The order matters: `RB_GC_MARK\\w*` matches `RB_GC_MARK_MOVABLE` too, so the movable
    test has to run second and win.
    """
    if not MARK_PRIM.match(name):
        return None
    return "movable" if MOVABLE_PRIM.match(name) else "pin"


# A callback slot naming one of these registers nothing. `Qnil` is not a function
# pointer either, and a slot spelled that way is a typo that would otherwise enter
# the index as a mark function named `Qnil` and bind to nothing.
NULLISH = frozenset(("NULL", "0", "nullptr", "Qnil"))


def callback_name(text):
    """The function a dtype callback slot names, with casts and parentheses removed.

    Ported from sweep_unmarked, for the reason recorded there: `.dmark =
    (RUBY_DATA_FUNC)mark_wrap` is a cast on a callback field, and a pattern demanding an
    identifier immediately after the `=` reads the slot as ABSENT. Here that failure has the
    opposite sign to A's -- an unread `.dmark` means the mark index is empty and the rows
    stand -- but a rule that silently stops firing is still a rule nobody is testing.
    """
    t = (text or "").strip()
    for _ in range(4):
        if not t.startswith("("):
            break
        close = match_paren(t, 0)
        if close < 0:
            return None
        rest = t[close + 1:].strip()
        t = rest if rest else t[1:close].strip()
    t = t.lstrip("&").strip()
    return t if re.fullmatch(r"[\w:]+", t) else None


# (macro, index of the argument naming the mark callback). The legacy untyped forms name
# the function inline and no `rb_data_type_t` exists for them at all.
UNTYPED_MARK = {"Data_Wrap_Struct": 1, "Data_Make_Struct": 2, "rb_data_object_wrap": 2,
                "rb_data_object_make": 1}


def mark_callbacks(src):
    """[(name, offset)] -- every function this file registers as a GC mark callback.

    A NAME IS NOT A MARK FUNCTION BECAUSE IT CONTAINS `rb_gc_mark`. The discharge below
    claims that every GC-managed instance of a struct type has the named field pinned, and
    the only thing that makes that true is REGISTRATION: a `.dmark` slot, or the mark
    argument of one of the legacy wrap macros. A helper that calls `rb_gc_mark` and is never
    registered marks nothing, and reading it as evidence would clear rows against a mark
    that never runs. 8q's `unregistered` arm is the red for exactly that: the same pinning
    call under a `.dmark` of NULL must leave the row standing.

    Both `rb_data_type_t` initialiser forms, because zlib uses one and json the other:

        static const rb_data_type_t zstream_data_type = {
            "zstream", { zstream_mark, zstream_free, zstream_memsize, }, ... };  positional
        static const rb_data_type_t JSON_ResumableParser_type = {
            .wrap_struct_name = "...", .function = { .dmark = ..., ... }, ... };  designated

    The designated scan runs over the WHOLE initialiser rather than over its top-level
    parts, so `.function = { .dmark = X }` is reached without unwrapping the group first;
    the positional fallback runs only when that found nothing, which is A's disposition and
    for A's reason -- a designated `.function` holding a POSITIONAL list matches neither.

    ONE TYPE CAN HAVE SEVERAL INITIALISERS AND ONLY SOME OF THEM MARK, which is the third
    review round's second finding and the same `#ifdef` family as the zstd trap one level
    up. `strip_directives` keeps the code inside conditionals -- deliberately, and predicate
    A depends on it in the opposite direction -- so mutually exclusive variants of the SAME
    `rb_data_type_t` are both retained:

        #ifdef HAVE_PINNING
        static const rb_data_type_t wrap_type = { "wrap", { wrap_mark, 0, 0, }, ... };
        #else
        static const rb_data_type_t wrap_type = { "wrap", { NULL, 0, 0, }, ... };
        #endif

    The named callback registered unconditionally, so in a build selecting the second
    variant the derivations cleared with NO MARK RUNNING AT ALL. Registration is graded per
    VARIABLE now: a variant naming no callback, or naming `NULL`, makes every variant of
    that variable UNTRUSTED, exactly as one movable mark makes a field unpinned. Two
    genuinely different types in one file are two variables and are graded apart.

    UNTRUSTED IS NOT DROPPED, and that distinction is the whole reason this returns a flag
    rather than filtering. Removing the registration would take its mark function out of the
    closure and out of `movable` with it -- and a movable mark lost that way lets a pin
    registered by some OTHER type answer for the same `(type, field)` key. That is
    `gate-whole-closure` one scope out. So an untrusted registration still seeds the
    closure; it just cannot contribute to `pinned`.

    The legacy `Data_Wrap_Struct` family is graded per CALL SITE and always trusted: each
    call is its own registration of its own object, with no second variant to disagree.
    """
    out, variants = [], {}
    for m in re.finditer(r"\brb_data_type_t\s+(\w+)\s*=\s*\{", src):
        open_idx = src.index("{", m.end() - 1)
        close = match_brace(src, open_idx)
        if close < 0:
            continue
        body = src[open_idx + 1:close]
        got, named = [], False
        for f in re.finditer(r"\.dmark\s*=\s*([^,}]+)", body):
            v = callback_name(f.group(1))
            if v:
                named = True
                got.append((v, open_idx + 1 + f.start(1)))
        if not named:
            parts = split_args(body)
            grp = next((p for p in parts if p.strip().startswith("{")), None)
            if grp is None:
                fnpart = next((p for p in parts if re.match(r"\.function\s*=", p.strip())),
                              None)
                if fnpart:
                    grp = fnpart.split("=", 1)[1].strip()
            if grp and grp.startswith("{"):
                fns = split_args(grp.strip()[1:-1])
                if fns:
                    v = callback_name(fns[0])
                    if v:
                        got.append((v, open_idx))
        variants.setdefault(m.group(1), []).append(got)
    # A variant PINS when it names at least one callback that is not a null constant. A
    # variable is trusted only when every retained variant does.
    trusted = {var: all(any(n not in NULLISH for n, _o in got) for got in vs)
               for var, vs in variants.items()}
    for var, vs in variants.items():
        for got in vs:
            out += [(n, o, trusted[var]) for n, o in got if n not in NULLISH]
    for macro, mi in UNTYPED_MARK.items():
        for m in re.finditer(r"\b%s\s*(?=\()" % macro, src):
            args, _ = call_args(src, m.end())
            if not args or len(args) <= mi:
                continue
            v = callback_name(args[mi])
            if v and v not in NULLISH:
                out.append((v, m.start(), True))
    return out


def unconditional_mark(body, off, within=None):
    """Does the mark call at `off` run on EVERY execution of this mark function?

    THE DISCHARGE CLAIMS A PIN, AND A PIN THAT RUNS ONLY SOMETIMES IS NOT ONE.

        static void wrap_mark(void *p) {
            struct wrap *w = p;
            if (w->pins) rb_gc_mark(w->buf);      /* the String is unmarked otherwise */
        }

    On the `!w->pins` path `w->buf` is neither marked nor pinned, so it may be collected or
    relocated while a `char *` into its bytes is live -- both legs of this predicate, from a
    line that reads as a pin to a scan that only asks whether the primitive's name and the
    member path match. Recording it in `pinned` clears the row that the branch creates.

    THREE tu_scope RULES ANSWER THIS, AND ALL THREE ARE NEEDED -- each one is a shape the
    other two pass. They are composed rather than re-derived, for the reason tu_scope exists:

      *  `conditional_stmt` -- the BRACELESS arm. `if (c) rb_gc_mark(x);` has no block of
         its own, so a brace test sees the whole function and reads it as unconditional.
         It also covers `switch` arms, `for`/`while` headers, `if constexpr`, statement
         attributes and the operator forms `c && rb_gc_mark(x);` / `c ? rb_gc_mark(x) : 0;`.
         date's `d_lite_gc_mark` is the corpus instance and the ONLY key this arm drops.
      *  `innermost_block` -- the BRACED arm, which `conditional_stmt` cannot see by
         construction: its head is `{`, so there is nothing for the arm test to read. A mark
         inside any nested block -- an `if` body, a `while` body, a `for` body -- runs on
         some executions and not others. msgpack's `msgpack_unpacker_mark_stack` is the
         honest case: the loop marks `stack->data[0 .. depth)` and the entries past `depth`
         are marked by nothing.
      *  `straight_line` -- the EARLY RETURN, which neither of the other two sees because
         the mark really is at depth zero with no conditional head. mysql2's
         `rb_mysql_stmt_mark` is `if (!stmt_wrapper) return;` followed by an unindented
         `rb_gc_mark(stmt_wrapper->client);` (statement.c:15).

    THE POLARITY IS INVERTED FROM conditional_stmt's OTHER CALLERS AND THAT IS THE WHOLE
    REASON THE OTHER TWO ARE HERE. Everywhere else in this directory an unrecognised guard
    KILLS a live row, so the recogniser is deliberately narrow and anything it cannot read
    stays UNCONDITIONAL. Here an unrecognised guard CLEARS a live row instead, so narrow is
    the dangerous direction and the test is composed to over-report rather than under-report.

    MEASURED COST, arm64-darwin (Darwin 27.0.0, Apple libc), CPython 3.11.11, the 99-tree
    corpus: 33 of 148 (type, field) pin keys are dropped -- 1 by `conditional_stmt`, 30 more
    by `innermost_block`, 2 more by `straight_line`, counted by enabling the three
    cumulatively and re-indexing. NOT ONE of them is a key any discharged row joins on, so
    the corpus verdicts do not move; what moves is the number of pins the index is willing
    to claim.

    AND NOT ONE MOVABLE KEY IS DROPPED -- 69 before, 69 after, measured on the same run.
    That is the half of this asymmetry with teeth. A gem that spells both marks for one
    field across a feature test presents both to the index (zstd's `#ifdef
    HAVE_RB_GC_MARK_MOVABLE`, and the cross-file `#define rb_gc_mark_movable(x)
    rb_gc_mark(x)` in ffi's `compat.h:65` and mysql2's `mysql2_ext.h:43`), and it is the
    subtraction that keeps the dead arm from answering. Gating `movable` on this test would
    let a mark inside `if (...)` fall out of `movable`, and the surviving pin would then
    clear the row -- the round-10 over-clear, rebuilt out of the round-11 fix.
    The largest single loser is the `if (w) { ... }` null-guard on the struct pointer itself
    (mysql2, and 7 keys of it), which is a *sound* pin -- a NULL `w` has no instance for a
    derivation to read either -- and is dropped anyway. Exempting it needs the condition
    parsed and matched against the marked expression's own base, which is a fourth rule for
    zero corpus rows, in the round whose subject is discharges that fire when they should
    not. It is named here so the next round can price it rather than re-derive it.
    """
    return (not tu_scope.conditional_stmt(body, off)
            and tu_scope.innermost_block(body, off, within) is None
            and tu_scope.straight_line(body, 0, off))


KEYWORD_TYPES = {"const", "volatile", "register", "static", "extern", "struct", "union",
                 "enum", "class", "unsigned", "signed", "long", "short", "int", "char",
                 "void", "float", "double", "inline", "typedef", "auto"}


def type_tag(decl):
    """The named type in a declaration, or None: `const struct zstream *z` -> "zstream".

    The FIRST non-keyword identifier, not the last -- the last one is the declarator. A
    declaration whose only identifier IS the declarator (`long ungetc`, `unsigned flags`)
    names no type this index can join on and returns None rather than the variable's own
    name, which would make every such member look like a struct of its own name.
    """
    text = decl.split("[")[0]
    text = text.split("*")[0] if "*" in text else text
    ids = [i for i in re.findall(r"[A-Za-z_]\w*", text) if i not in KEYWORD_TYPES]
    return ids[0] if ids else None


AGGREGATE_HEAD = re.compile(r"\b(typedef\s+)?(?:struct|union)\s+([A-Za-z_]\w*)?\s*(?=\{)")


def _member_decls(body):
    """{member name: named type or None} for one aggregate body.

    Split on SEMICOLONS AT DEPTH ZERO, so a nested aggregate (`const struct zstream_funcs
    { int (*run)(z_streamp, int); } *func;` inside `struct zstream`) is one member and not
    three. The nested body is then cut away before the declarator is read, exactly as
    file_scope_objects cuts one away: `struct S { ... } obj;` declares `obj`.
    """
    out, depth, start = {}, 0, 0
    for i, ch in enumerate(body):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == ";" and depth == 0:
            frag, start = body[start:i], i + 1
            if "}" in frag:
                frag = frag[frag.rfind("}") + 1:]
            t = type_tag(frag)
            for d in split_args(frag):
                # a member function prototype declares no slot; a member function POINTER
                # does, and is spelled `(*fp)(...)` -- the same cut file_scope_objects makes
                if "(" in d and not re.search(r"\(\s*\*", d):
                    continue
                nm = param_name(d)
                if nm and nm != t:
                    out[nm] = t
    return out


def struct_index(files):
    """{type name: {member: named type}} over a whole tree, tags and typedef aliases both.

    Keyed TREE-WIDE, and that is a stated limit rather than an oversight: tu_scope's first
    rule says a `static` FUNCTION in another translation unit is not a candidate, and the
    same argument applies to a `struct` tag, which is also file-local in C. Two files each
    defining a different `struct buf` would merge here. Predicate A carries the same limit
    on the same index; the cost of getting it wrong is bounded by the join, since a member
    name has to match as well as the tag, and it is written down so the next round can
    measure it rather than re-derive it.

    Both spellings of the name a variable is declared with are indexed to the same member
    map -- the tag (`struct zstream`, zlib) and the typedef alias (`JSON_ResumableParser`,
    json) -- because the deriving file and the marking file need not use the same one.
    """
    out = {}
    for src in files.values():
        for m in AGGREGATE_HEAD.finditer(src):
            open_idx = m.end()                      # the lookahead ends ON the `{`
            close = match_brace(src, open_idx)
            if close < 0:
                continue
            members = _member_decls(src[open_idx + 1:close])
            if not members:
                continue
            names = [m.group(2)] if m.group(2) else []
            # ONLY A `typedef` PUTS A TYPE NAME AFTER THE `}`. `struct S { ... } obj;`
            # declares an OBJECT, and zlib's nested `const struct zstream_funcs { ... }
            # *func;` declares a member -- indexing either as a type name would put a
            # variable's spelling into the type index, where a join could then find it.
            if m.group(1):
                semi = src.find(";", close)
                for d in split_args(src[close + 1:semi] if semi > 0 else ""):
                    nm = param_name(d)
                    if nm and "(" not in d and "*" not in d:
                        names.append(nm)          # `} JSON_ResumableParser;` -- the alias
            for nm in names:
                out.setdefault(nm, {}).update(members)
    return out


# A declaration is the first thing in its statement. Without that anchor `total = count *
# size;` parses as a pointer declaration of `size` with type `count`, and a wrong entry in
# this map is a wrong join key on the discharge side.
STMT_START = ";{}(),"          # ...or the start of the body, which is `k < 0`
PTR_DECL = re.compile(
    r"((?:(?:const|volatile|struct|union|enum)\s+)*[A-Za-z_]\w*)\s*\*+\s*([A-Za-z_]\w*)"
    r"\s*(?=[=;,)])")


def var_types(fn):
    """{local or parameter name: the struct type it points AT}, pointer declarations only.

    Pointers only, because the discharge requires the member path to start with `->`; see
    member_key for why. Parameters first, then body declarations in source order, and the
    first binding for a name wins -- a declaration precedes its uses, and a later textual
    match on the same name is more likely to be the multiplication this regex cannot tell
    from a declaration than a second declaration of the same variable.
    """
    out = {}
    for decl, nm in fn.params:
        if nm and "*" in decl:
            t = type_tag(decl)
            if t and t != nm:
                out[nm] = t
    body = fn.body
    for m in PTR_DECL.finditer(body):
        k = m.start() - 1
        while k >= 0 and body[k] in " \t\r\n":
            k -= 1
        if k >= 0 and body[k] not in STMT_START:
            continue
        t = type_tag(m.group(1))
        if t and t != m.group(2):
            out.setdefault(m.group(2), t)
    return out


# A PLAIN member path and nothing else -- no leading `*`, no cast, no subscript. `*w->buf`
# dereferences the field rather than naming it, and a cast is an expression whose type this
# index has not resolved; either one would build a key out of a spelling.
MEMBER_PATH = re.compile(
    r"\s*([A-Za-z_]\w*)((?:\s*(?:->|\.)\s*[A-Za-z_]\w*)+)\s*\Z")


def member_key(structs, vtypes, expr):
    """`gz->z.input` -> ("zstream", "input"), or None if the path does not resolve.

    THE PATH MUST START AT A POINTER. `->` says the aggregate the field lives in is
    somewhere other than this frame, which is the precondition the discharge needs: a
    `struct zstream z;` on the stack has no wrapper, no dtype and no dmark, so its `buf`
    field is marked by nothing and pinned by nothing. Requiring the first hop to be `->` is
    the cheapest true statement of that, and it costs nothing in the corpus -- every witness
    reaches its field through a pointer parameter or a pointer local.

    EVERY HOP MUST RESOLVE, INCLUDING THE LAST. A member that is not in the indexed body of
    its own type returns None rather than a key built out of a guess: the join is the whole
    safety of this rule, and an unfounded key can only ever make it fire where it should not.
    """
    m = MEMBER_PATH.fullmatch(expr)
    if not m:
        return None
    hops = re.findall(r"(->|\.)\s*([A-Za-z_]\w*)", m.group(2))
    if not hops or hops[0][0] != "->":
        return None
    t = vtypes.get(m.group(1))
    for _op, mem in hops[:-1]:
        fields = structs.get(t)
        if not fields or mem not in fields:
            return None
        t = fields[mem]
    fields = structs.get(t)
    if not fields or hops[-1][1] not in fields:
        return None
    return (t, hops[-1][1])


class Func:
    __slots__ = ("name", "path", "src", "params", "hdr", "bstart", "bend", "is_static",
                 "scope")

    def __init__(self, name, path, src, params, hdr, bstart, bend, is_static=False):
        self.name = name
        self.path = path
        self.src = src            # the whole stripped file text
        self.params = params      # [(decl, name)] in order
        self.hdr = hdr            # offset of the function name
        self.bstart = bstart      # offset just past `{`
        self.bend = bend          # offset of the matching `}`
        self.is_static = is_static   # internal linkage: this name is this file's alone
        # Where a CALL can bind to this definition. See tu_scope: one rule, four
        # predicates. A `static` in a .c/.cc/.cpp/.cxx is that file's alone; a header
        # definition, or any non-static one, stays tree-wide.
        self.scope = tu_scope.declared_scope(path, is_static)

    @property
    def body(self):
        return self.src[self.bstart:self.bend]

    def line(self, off=None):
        return line_of(self.src, self.hdr if off is None else off)

    def value_params(self):
        """[(index, name)] for parameters declared as a bare by-value `VALUE`."""
        out = []
        for i, (decl, _nm) in enumerate(self.params):
            m = PARAM_VALUE_RE.match(decl.strip())
            if m:
                out.append((i, m.group(1)))
        return out

    def ptr_params(self):
        return {nm for decl, nm in self.params if nm and ("*" in decl or "[" in decl)}

    def is_argc_argv(self):
        """`int argc, VALUE *argv, VALUE self` -- the varargs cfunc signature."""
        names = [nm for _d, nm in self.params]
        return len(names) >= 2 and "argv" in names


class Tree:
    """One gem's C sources, indexed for whole-tree caller resolution."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.files = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and p.suffix in C_EXT and ".git" not in p.parts:
                try:
                    self.files[p] = strip_directives(
                        strip_noise(p.read_text(errors="replace")))
                except OSError:
                    continue
        self.funcs = []
        self.by_name = {}
        self.cfuncs = set()
        self.statics = {}               # path -> names at file/namespace scope
        self.tree_slots = set()         # ...of those, the ones visible tree-wide
        for path, src in self.files.items():
            self._index_funcs(path, src)
            self._index_cfuncs(src)
            decls = file_scope_objects(src)
            self.statics[path] = set(decls)
            for nm, is_static in decls.items():
                if tu_scope.declared_scope(path, is_static) == tu_scope.TREE:
                    self.tree_slots.add(nm)
        for f in self.funcs:
            self.by_name.setdefault(f.name, []).append(f)
        self.ranges = {}
        for f in self.funcs:
            self.ranges.setdefault(f.path, []).append(f)
        for v in self.ranges.values():
            v.sort(key=lambda f: (f.bstart, -f.bend))
        # LAST, because both of these need the function index and the name table that the
        # loops above build. See the pinning-mark section: `structs` is the join, `pinned`
        # and `movable` are what the join is asked about.
        self.structs = struct_index(self.files)
        self.mark_fns, self.pin_reachable = self._index_mark_fns()
        self.pinned, self.movable = self._index_pins()

    def _index_mark_fns(self):
        """(every function a mark callback reaches, ids of those reached UNCONDITIONALLY).

        THE CLOSURE IS NOT DECORATION. zlib's `zstream_mark` is registered directly, but
        `gzfile_mark` reaches the same fields a second way -- `zstream_mark(&gz->z)` -- and
        json's `JSON_ResumableParser_mark` delegates three of its five marks to helpers. A
        dmark that hands the work to a helper is the commonest shape there is, and stopping
        at the registered function would make the discharge fire on whichever gems happen
        not to use it. Bound through tu_scope.bind, so a call in b.c does not reach a.c's
        `static` mark helper of the same name.

        TWO REACHABILITIES, NOT ONE, AND THE SECOND ONE IS #32's THIRD REVIEW FINDING. The
        closure used to answer a single question -- can a mark callback reach this function
        -- and `unconditional_mark` then asked whether each mark call is conditional
        *inside its own body*. That routes around the conditional-pin fix one level up:

            static void mark_fields(struct wrap *w) { rb_gc_mark(w->buf); }
            static void wrap_mark(void *p) {
                struct wrap *w = p;
                if (w->pins) mark_fields(w);      /* the condition lives HERE */
            }

        `rb_gc_mark(w->buf)` is at depth zero in `mark_fields`, so the body test says
        unconditional and the row cleared on the `!w->pins` path exactly as before. The
        condition is a property of the CALL EDGE, and an edge is not somewhere a per-body
        test can look.

        So `uncond` is propagated along the edges: a registered callback is entered
        unconditionally by the GC, and a callee inherits that only across a call the SAME
        test would accept in the caller. One conditional edge anywhere on the path is
        enough to drop it -- `pinned` wants an unconditional path, not an unconditional
        function, and a function reachable both ways keeps the stronger answer.

        BOTH SETS ARE RETURNED BECAUSE `movable` MUST NOT BE GATED. Narrowing the whole
        closure would have been the smaller edit and it rebuilds the round-10 over-clear:
        a movable mark reached only through `if (c) helper(w)` would fall out of `movable`,
        the subtraction in `_index_pins` would stop firing, and a surviving unconditional
        pin on the same key would answer for it. A hazard on one path is still a hazard.
        """
        frontier, seen, out, uncond = [], set(), [], set()
        for path, src in self.files.items():
            for name, off, trusted in mark_callbacks(src):
                frontier += [(fn, trusted) for fn in
                             tu_scope.bind(self.by_name.get(name, ()), path, off)]
        while frontier:
            fn, u = frontier.pop()
            # Revisit only to UPGRADE a function to unconditional reachability; `uncond`
            # only ever grows, so this terminates.
            if id(fn) in seen and not (u and id(fn) not in uncond):
                continue
            if id(fn) not in seen:
                seen.add(id(fn))
                out.append(fn)
            if u:
                uncond.add(id(fn))
            blks = tu_scope.blocks(fn.body)
            for cname, _a, s, _e in find_calls(fn.body):
                edge = u and unconditional_mark(fn.body, s, blks)
                frontier += [(c, edge) for c in
                             tu_scope.bind(self.by_name.get(cname, ()), fn.path,
                                           fn.bstart + s)]
        return out, uncond

    def _index_pins(self):
        """({(type, field): where}, {(type, field): where}) -- pinned, then movable.

        A FIELD NAMED BY BOTH IS NOT PINNED. `stronger()` in predicate A says movable beats
        pinning for the same reason in the mirror direction: if any path marks the field
        movable, compaction may relocate it, and the pinning call on the other path proves
        nothing about that one. Subtracting `movable` from `pinned` is that rule, and it is
        also what makes the movable red (8q) impossible to pass by accident -- a fixture
        that spelled both would otherwise discharge on the pinning half.

        A CONDITIONAL CALL IS A PIN ON THE PINNED SIDE ONLY, and the asymmetry is
        deliberate. `unconditional_mark` gates what may enter `pinned`, because a pin that
        runs on some executions clears rows on all of them. It does NOT gate `movable`: a
        movable mark is a HAZARD, and a hazard that fires on one path is still a hazard, so
        recording it unconditionally is what keeps the subtraction above from letting an
        unconditional `rb_gc_mark` answer for a conditional `rb_gc_mark_movable` on the same
        field. Both dispositions push the same way -- fewer keys in `pinned`.

        THE GATE IS NOW TWO TESTS AND BOTH ARE THE SAME QUESTION at different scales: is
        this call reached on EVERY execution of the registered callback. `pin_reachable`
        answers it for the path of call edges that got here (#32's third finding -- a
        conditional call to an unconditional helper), `unconditional_mark` answers it for
        the position of the call inside this body. Either one failing drops the pin.
        """
        pinned, movable = {}, {}
        for fn in self.mark_fns:
            vt = var_types(fn)
            rel = str(fn.path.relative_to(self.root))
            blks = tu_scope.blocks(fn.body)
            reached = id(fn) in self.pin_reachable
            for name, args, s, _e in find_calls(fn.body):
                kind = prim_kind(name)
                if kind is None:
                    continue
                if kind == "pin" and not (reached
                                          and unconditional_mark(fn.body, s, blks)):
                    continue
                for a in args:
                    key = member_key(self.structs, vt, a.strip())
                    if key is None:
                        continue
                    (movable if kind == "movable" else pinned).setdefault(
                        key, "%s(%s) in %s at %s:%d"
                             % (name, a.strip(), fn.name, rel,
                                line_of(fn.src, fn.bstart + s)))
        return {k: v for k, v in pinned.items() if k not in movable}, movable

    def _index_funcs(self, path, src):
        """Top-level definitions only.

        The depth check is not cosmetic: a macro invocation followed by a block --
        `RB_VM_LOCK_ENTER() { ... }` -- parses as a definition nested inside a real
        function, and the innermost-frame lookup would then bound a scan by the macro's
        block instead of the function's body. That is a false NEGATIVE generator.

        Depth is counted over STORAGE scopes, not braces. `namespace X {` and
        `extern "C" {` nest their contents without giving them a new storage duration, so
        scope_zero_braces marks that pair and the count skips it -- otherwise a C++ tree
        that wraps its extension in either one indexes zero functions, every later stage
        walks an empty list, and the run reports `0 fn(s) | derive 0/0 -> hit 0`. That zero
        is indistinguishable from racc's, which is the one this file's counters were added
        to tell apart. sweep_static_values.py needed the same three dispositions first and
        sweep_escaped_conversion.py ported them the same round -- and predicate C then
        shipped the raw-brace count in its OWN function index for a round after fixing its
        slot walk, which is why the walk is now tu_scope.storage_depth and not a fourth
        opinion in a fourth file.
        """
        depth_at = tu_scope.storage_depth(src)
        for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?=\()", src):
            if depth_at(m.start()) != 0 or m.group(1) in NOT_CALLS:
                continue
            args, past = call_args(src, m.end())
            if args is None:
                continue
            k = skip_post_declarator(src, past)
            if k >= len(src) or src[k] != "{":
                continue
            close = match_brace(src, k)
            if close < 0:
                continue
            params = [(a, param_name(a)) for a in args
                      if a.strip() and a.strip() not in ("void", "...")]
            # The declaration specifiers, back to the previous statement boundary -- the
            # only place `static` can be, and the whole of what decides linkage.
            head = src[max(0, m.start() - 300):m.start()]
            head = head[max(head.rfind(";"), head.rfind("}"), head.rfind("{")) + 1:]
            self.funcs.append(Func(m.group(1), path, src, params, m.start(), k + 1, close,
                                   bool(re.search(r"\bstatic\b", head))))

    def _index_cfuncs(self, src):
        for name, args, _s, _e in find_calls(src):
            if DEFINE_RE.match(name):
                self.cfuncs.update(re.findall(r"[A-Za-z_]\w*", " ".join(args)))

    def visible_slots(self, path):
        """Persistent-storage names a store in `path` can be naming.

        Its own file's declarations, plus every declaration the tree makes visible
        everywhere -- a header's, and any non-`static` file-scope object. ANOTHER
        translation unit's `static` is deliberately absent: nothing in this file can name
        it, so treating it as a sink would read a plain local of the same spelling as an
        escape. That is the mirror of the header carve-out, from the same rule.
        """
        return self.statics.get(path, set()) | self.tree_slots

    def enclosing(self, path, off):
        """The innermost top-level function whose body contains `off`, or None."""
        best = None
        for f in self.ranges.get(path, ()):
            if f.bstart > off:
                break
            if f.bend > off and (best is None or f.bstart > best.bstart):
                best = f
        return best


def call_sites(tree, fn):
    """[(caller, arg_list, offset_of_call, offset_past_call)] for in-tree calls of `fn`.

    Linkage-scoped like predicate B's namesake (tu_scope.bind): a textual match on the
    name in b.c is a call to b.c's own `static` definition, not to this one.
    """
    out = []
    peers = tree.by_name.get(fn.name, ())
    for path, src in tree.files.items():
        for m in re.finditer(r"\b%s\s*(?=\()" % re.escape(fn.name), src):
            caller = tree.enclosing(path, m.start())
            if caller is None or caller is fn:
                continue            # a prototype, the definition header, or self-recursion
            if fn not in tu_scope.bind(peers, path, m.start()):
                continue            # this file's call binds to a different definition
            args, past = call_args(src, m.end())
            if args is None:
                continue
            out.append((caller, args, m.start(), past))
    return out


# ------------------------------------------------------- stage 1: derivations


def derivations(fn):
    """[(off, macro, src_expr, src_var)] -- every interior derivation in the body.

    ANY storage class, deliberately. Predicate B keys on by-value parameters, which is
    why cgi's `VALUE str = argv[0]` is outside its walk by construction; that gap is this
    predicate's charter, so there is no filter here at all beyond "the argument names
    something".
    """
    out = []
    for name, args, s, _e in find_calls(fn.body):
        if name not in INTERIOR or not args:
            continue
        expr = args[0].strip()
        var = expr if LVALUE_RE.fullmatch(expr) else None
        out.append((fn.bstart + s, name, expr, var))
    return out


def converted_locals(fn):
    """[(off, macro, name)] -- an in-place conversion applied to a LOCAL.

    `VALUE str = argv[0]; StringValue(str);` (cgi escape.c:404-406). The conversion may
    return a DIFFERENT object than argv[0] holds, and only this local roots it. Predicate
    B sees the by-value-parameter version of this and nothing else.
    """
    out, body = [], fn.body
    params = {nm for _d, nm in fn.params}
    for name, args, s, _e in find_calls(body):
        if not args:
            continue
        a0 = args[0].strip()
        if name in LVALUE_CONV:
            target = a0
        elif name in ADDR_CONV and a0.startswith("&"):
            target = a0[1:].strip()
        else:
            continue
        if re.fullmatch(r"[A-Za-z_]\w*", target) and target not in params:
            out.append((fn.bstart + s, name, target))
    seen, uniq = set(), []
    for off, macro, nm in out:
        if nm in seen:
            continue
        seen.add(nm)
        uniq.append((off, macro, nm))
    return uniq


# ------------------------------------------------------- stage 2: what the pointer does


# The partial statement ending at an offset -- how both predicates find the left-hand side
# of an assignment. Shared, with the copy propagation it feeds; see tu_scope's fourth rule.
statement_before = tu_scope.statement_before


def is_indexed(fn, deriv_off, macro):
    """`RSTRING_PTR(s)[i]` reads ONE BYTE. The pointer is not kept and cannot escape.

    stringio's `strio_getbyte` is the corpus case: `c = RSTRING_PTR(ptr->string)[pos++]`
    assigns an `int`, and treating `c` as a pointer alias made `return CHR2FIX(c)` look
    like ESCAPES-BY-RETURN. That is a false positive with a very ordinary spelling.
    """
    rel = deriv_off - fn.bstart
    _args, past = call_args(fn.body, rel + len(macro))
    tail = fn.body[past:past + 4].lstrip()
    return tail.startswith("[")


def pointer_alias(fn, deriv_off, macro):
    """The POINTER local the interior is assigned to, or None.

        const char *p = RSTRING_PTR(str);
        ...
        return p;

    Matching only the direct uses misses that completely ordinary two-line spelling, and
    it fails SILENT -- the funnel reports the derivation, finds no escape, prints clean.

    The target must be pointer-typed. Without that check `c = RSTRING_PTR(s)[i]` makes an
    `int` look like an alias of the buffer, and every later mention of `c` reads as an
    escape.

    `RSTRING_GETMEM` DOES NOT RETURN THE POINTER, IT WRITES IT. `RSTRING_GETMEM(str, p,
    len)` expands to an assignment to `p` and one to `len`, so there is no `p =` in the
    source for the scan below to find and the alias came back None -- which means
    `last_use` came back None, which means the window scan had nothing to bound and every
    such row discharged `no-window`. A function that derives with the macro, opens a
    compaction window and then reads `p` produced ZERO hits: the derivation was counted in
    the funnel and then silently cleared, which is the failure mode this predicate is most
    biased against. The macro's SECOND argument is the derived pointer, so it is named here
    and joins escape and window analysis on the same footing as an explicit alias.
    (Predicate B has the sibling of this gap on the same macro, as an alias source for its
    escape analysis; the two are fixed separately because the walks start in different
    places.) A non-identifier output -- json's `RSTRING_GETMEM(obj, search.ptr, len)` --
    is left unaliased rather than guessed at, because every downstream user of `alias`
    builds a `\\b...\\b` word-boundary regex from it.
    """
    if macro == "RSTRING_GETMEM":
        args, _past = call_args(fn.body, deriv_off - fn.bstart + len(macro))
        out = args[1].strip() if args and len(args) > 1 else ""
        return out if re.fullmatch(r"[A-Za-z_]\w*", out) else None
    if is_indexed(fn, deriv_off, macro):
        return None
    stmt = statement_before(fn.body, deriv_off - fn.bstart).replace("\n", " ")
    # `p = (const char *)RSTRING_PTR(str)` -- a cast between the `=` and the derivation left
    # the statement ending in `)` instead of `=`, so no alias was recorded, so `last_use`
    # returned None, so every later read of `p` was invisible and the row discharged
    # `no-window`. Strip trailing casts and redundant parentheses before matching; the loop
    # is bounded because each pass removes one group.
    while True:
        stripped = re.sub(r"\(\s*[A-Za-z_][\w\s*]*\)\s*$", "", stmt)
        if stripped == stmt:
            break
        stmt = stripped
    m = re.search(r"(?:^|[\s*(])([A-Za-z_]\w*)\s*=\s*$", stmt)
    if not m:
        return None
    name = m.group(1)
    if "*" in stmt:                       # `char *p =` / `const char *p =`
        return name
    # assigned to something declared a pointer earlier in the body
    if re.search(r"\*\s*%s\b" % re.escape(name), fn.body[:deriv_off - fn.bstart]):
        return name
    return None


def alias_names(fn, deriv_off, alias):
    """Every local that carries the derived pointer, following local-to-local copies.

        p = RSTRING_PTR(str);
        q = p;                    /* q IS the interior pointer */
        rb_funcall(...);          /* the window */
        return q;                 /* the escape */

    Tracking only `p` reads `q = p` as `p`'s final use, so the window scan stops before the
    `rb_funcall` and the row discharges `no-window` -- a clean sheet on a live derivation,
    which is the failure mode this predicate is most biased against.

    THE PROPAGATION ITSELF IS tu_scope's FOURTH RULE, not this file's. Predicate B carried
    the same defect on the same shape (`p = RSTRING_PTR(str); q = p; return q;` reported one
    converted non-cfunc and zero hits), so the transitive closure, the pointer-typed test,
    the arithmetic base-left rule and the offset ordering are stated once there. What stays
    here is the SEED: this predicate has exactly one, the local the derivation was assigned
    to, at the derivation's own offset.
    """
    if not alias:
        return []
    return tu_scope.alias_set(fn.body, {alias: deriv_off - fn.bstart})


def alias_reads(fn, deriv_off, alias):
    """Body-relative offsets of every occurrence that STILL carries the derived pointer.

    A NAME IS NOT AN ALIAS FOR EVER, and the set above cannot say so: `p = RSTRING_PTR(str);
    p = other; rb_funcall(...); use(p);` puts `p` in the alias set once and every later
    mention of it then reads as a carrier -- a window bounded by a use of something else,
    which grows a spurious one exactly as predicate B grew a spurious escape on the same
    shape. The kill is tu_scope's fifth rule, the same predicate the guard alias and the
    shadowing redeclaration already ask, with the kill mode this polarity needs: here a
    disqualified occurrence DISCHARGES, so only a write that runs on every path counts.
    """
    if not alias:
        return set()
    return tu_scope.alias_reads(fn.body, {alias: deriv_off - fn.bstart})


def _names_re(names):
    """A word-boundary alternation over an alias set, or None when the set is empty."""
    if not names:
        return None
    return re.compile(r"\b(?:%s)\b" % "|".join(re.escape(n) for n in names))


def consumer(fn, deriv_off, macro):
    """(name, args, start) of the innermost call that immediately receives the pointer.

    `sb_stemmer_new(algorithm, NULL)` consumes `algorithm`; `memcpy(dst, RSTRING_PTR(s),
    n)` consumes the derivation in the same statement, with no window in between and no
    way for the pointer to outlive it. Knowing WHICH is the whole difference between
    "held across a window" and "read and finished with".
    """
    rel = deriv_off - fn.bstart
    best = None
    for name, args, s, e in find_calls(fn.body):
        if name == macro or s > rel or e <= rel:
            continue
        if best is None or s > best[2]:
            best = (name, args, s)
    return best


def carrier_sites(fn, deriv_off, alias):
    """[(off, statement_text)] where the derived pointer appears.

    Either the derivation itself, or a later mention of the local it was assigned to.
    Bounded to the frame, and ordered -- everything downstream compares offsets.
    """
    body = fn.body
    rel = deriv_off - fn.bstart
    sites = [rel]
    if alias:
        for m in re.finditer(r"\b%s\b" % re.escape(alias), body):
            if m.start() > rel:
                sites.append(m.start())
    return sorted(set(sites))


# The base of an assignment's left-hand side, allowing a C++ qualified name. `Cache::saved`
# and `prof::inner::cache` are single OBJECTS with a `::` in the spelling, not a member
# access -- so the qualifier is part of group 1 and group 2 stays empty, which is what puts
# them on the bare-scalar branch where a static-storage sink belongs.
#
# THE NORMALISATION IS DELIBERATELY LOOSE HERE, AND DELIBERATELY STRICT IN PREDICATE C, and
# the two are not inconsistent -- they are the same bias pointing at opposite rules. What C
# matches with a qualified name is a REGISTRATION, and a registration DISCHARGES: matching
# `rb_global_variable(&Registry::cache)` against a bare `cache` would clear an unrelated
# file-scope slot, so C keys members qualified, fails to match `&prof::cache` against a
# namespace-scope `cache`, and OVER-REPORTS -- which is why C's docstring records that miss
# as a known limit rather than a bug. What D matches is a SINK, and a sink REPORTS: failing
# to match here loses the finding outright. So both files resolve the ambiguity toward
# reporting, and that is exactly why one drops the qualifier and the other keeps it.
LVALUE_BASE = re.compile(
    r"^\*?\s*((?:[A-Za-z_]\w*\s*::\s*)*[A-Za-z_]\w*)\s*(->|\[|\.|$)")


def _pointer_operand(body, text):
    """Is `text` a bare local this frame declared a pointer?

    Only the bare-identifier form, and deliberately: it exists to answer "could this be the
    right operand of a pointer difference", where a wrong YES costs a row and a wrong NO
    reports one. Frame-wide rather than up-to-an-offset, because a subtraction says nothing
    about where the operand was declared.
    """
    t = text.strip()
    return bool(re.fullmatch(r"[A-Za-z_]\w*", t)) \
        and tu_scope.pointer_typed(body, t, len(body))


def _top_level_minus(text):
    """Offsets of the BINARY `-` operators at bracket depth 0 in `text`.

    Three spellings are not one: `->` is a member access, `--` is a decrement, and a `-`
    with no operand to its left is a unary sign. Only a `-` following an identifier, a `)`
    or a `]` subtracts, and only one outside every bracket subtracts from the value of the
    whole expression -- `f(a - b)` yields whatever `f` returns.
    """
    out, depth, prev = [], 0, ""
    for i, c in enumerate(text):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "-" and depth == 0:
            nxt = text[i + 1:i + 2]
            # `prev` is "" at the start of the text, and `"" in "_)]"` is True in Python --
            # which made a leading unary `-RSTRING_LEN(x)` parse as a subtraction.
            if nxt not in ("-", ">") and prev != "-" and prev \
                    and (prev.isalnum() or prev in "_)]"):
                out.append(i)
        if not c.isspace():
            prev = c
    return out


def escapes(fn, deriv_off, expr, names, cons, tree=None, reads=()):
    """[(kind, off, text, extra)] -- ways the pointer outlives or leaves this frame.

    `names` is the whole alias set from alias_names(), not one name: `q = p; return q;`
    escapes through `q`, and a scan that knows only `p` sees no escape at all.
    """
    body, found = fn.body, []
    rel = deriv_off - fn.bstart
    ptrp = fn.ptr_params()
    # `reads` is alias_reads(): the OFFSETS at which an alias still carries the pointer,
    # rather than the names, because a name that has been reassigned still matches its own
    # spelling. An empty `reads` with a non-empty `names` is a real answer -- every carrier
    # was overwritten -- and must not fall back to the name match.
    reads = set(reads)

    def carried(text, base):
        return any(base <= o < base + len(text) for o in reads)

    statics = set(tree.visible_slots(fn.path) if tree is not None else ())
    statics.update(local_statics(body))

    def holds(text, base):
        """Does `text`, taken whole, evaluate to something carrying the buffer?"""
        if carried(text, base):
            return True
        return any(nm in INTERIOR for nm, _a, _s, _e in find_calls(text))

    def carries(text, base):
        # POINTER MINUS INTEGER IS STILL A POINTER; POINTER MINUS POINTER IS NOT.
        # The first cut of this rejected every expression containing a `-`, which kept
        # stringio's `ptr->pos = e - RSTRING_PTR(ptr->string)` out -- a `ptrdiff_t`, the one
        # gem in the corpus safe by design -- at the cost of rejecting `RSTRING_END(str) - 1`
        # with it. That is a valid pointer into the String's final byte and stores into a
        # file static exactly as the bare derivation does; the row discharged `no-window`,
        # zero hits. The distinction is which operand carries the buffer: if anything to the
        # RIGHT of a top-level `-` does, the result is an integer and cannot dangle.
        #
        # "Carries the buffer" is too narrow for the right operand and that is not a detail:
        # `pos = e - p` is scanned once per derivation, and on the `RSTRING_END` row the
        # alias set is `{e}`, so `p` -- the other derivation's alias, a pointer -- read as an
        # integer and the difference read as an adjusted pointer. So the right operand asks
        # the wider question, "is this pointer-valued at all", with the frame-wide form of
        # the same declaration test the copy scan uses.
        cuts = _top_level_minus(text)
        if not cuts:
            return holds(text, base)
        parts, last = [], 0
        for c in cuts:
            parts.append((text[last:c], base + last))
            last = c + 1
        parts.append((text[last:], base + last))
        if any(holds(p, b) or _pointer_operand(body, p) for p, b in parts[1:]):
            return False
        return holds(*parts[0])

    # 1. returned -- either directly, or via the local it was aliased into.
    for m in re.finditer(r"\breturn\b", body):
        if m.start() < rel - 200:
            continue
        semi = body.find(";", m.end())
        if semi < 0:
            continue
        if carries(body[m.end():semi], m.end()):
            found.append(("ESCAPES-BY-RETURN", fn.bstart + m.start(),
                          body[m.start():semi + 1].strip(), ""))
            break

    # 2. stored through an out-parameter, or into a container the caller owns.
    #    `*out = p`, `out->field = p`, `arr[i] = p`. Predicate B's escapes_by_return
    #    covers the first two and NOT a heap container -- rinku writes RSTRING_PTR into
    #    an xmalloc'd C array (rinku_load_tags:79) and prism into a library struct.
    for m in re.finditer(r"(?<![=!<>])=(?!=)", body):
        if m.start() < rel - 200:
            continue
        stmt = statement_before(body, m.start()).strip()
        b = LVALUE_BASE.match(stmt)
        if not b:
            continue
        lv = re.sub(r"\s+", "", b.group(1))
        # THE IDENTITY OF A STATIC-STORAGE SINK IS ITS LAST COMPONENT, on BOTH sides.
        # `Cache::saved = RSTRING_PTR(str)` used to yield `Cache` here while
        # file_scope_objects recorded the object as `saved` -- the same sink, computed two
        # ways, so the membership test could never fire and a C++ static data member
        # outliving the deriving call discharged `no-window`. Same defect as the
        # integer-typed local static: the sink was real and the identity did not match.
        sink = lv.split("::")[-1]
        semi = body.find(";", m.end())
        if semi < 0:
            continue
        rhs = body[m.end():semi]
        if not carries(rhs, m.end()):
            continue
        if not b.group(2) and sink in statics:
            # A BARE SCALAR AT STATIC-STORAGE SCOPE IS A SINK, AND IT HAD NO BRANCH.
            # `saved = RSTRING_PTR(str);` where `saved` is a file static or a global has no
            # pointer-parameter base and no `->`/`[`/`.`, so the STORES-INTERIOR test and the
            # ESCAPES-INTO-CONTAINER test both declined it and the row went on to discharge
            # `no-window`: one derivation, zero windowed sites, zero hits, on a pointer any
            # later call can read. The slot outlives every frame in the file, so the store is
            # an escape for the same reason a return is. Checked BEFORE the alias skip below,
            # because a function-local `static char *saved` is pointer-typed and therefore
            # also reads as an alias -- and being an alias must not excuse being a sink.
            found.append(("ESCAPES-INTO-STATIC", fn.bstart + m.start(),
                          (stmt + " =" + rhs).strip(), "static-storage lifetime"))
            continue
        if lv in names:
            continue                      # an aliasing assignment, not an escape
        if lv in ptrp and (stmt.startswith("*") or b.group(2) in ("->", "[")):
            found.append(("STORES-INTERIOR", fn.bstart + m.start(),
                          (stmt + " =" + rhs).strip(), ""))
        elif b.group(2) in ("->", "[", "."):
            # `.` belongs here, not with the plain locals. trilogy's
            # `connopt.hostname = StringValueCStr(val)` writes into a stack struct whose
            # ADDRESS is then handed to try_connect, which releases the GVL and reads it;
            # rinku's `skip_tags[i] = StringValueCStr(tag)` writes into an xmalloc'd C
            # array read on every `<`. Requiring `->` or `[` lost trilogy -- the best
            # red/green pair in the corpus -- and predicate B's escapes_by_return covers
            # neither of these two shapes at all.
            found.append(("ESCAPES-INTO-CONTAINER", fn.bstart + m.start(),
                          (stmt + " =" + rhs).strip(), ""))

    # 3. handed to a library that does not copy. This is the recall choice stated in the
    #    docstring: it counts as an escape even with no visible allocator, because the
    #    allocation happens inside the library.
    def library_call(name, args, s):
        if name not in NONCOPYING:
            return None
        joined = " ".join(args)
        _a, past = call_args(body, s + len(name))
        if (past is not None and any(s < o < past for o in reads)) or \
                any(d + "(" in re.sub(r"\s+", "", joined) for d in INTERIOR):
            return ("ESCAPES-INTO-LIBRARY", fn.bstart + s,
                    re.sub(r"\s+", " ", "%s(%s)" % (name, joined))[:110],
                    NONCOPYING[name])
        return None

    if cons:
        hit = library_call(cons[0], cons[1], cons[2])
        if hit:
            found.append(hit)
        # 4. handed to an in-tree callee that itself has a window. The pointer outlives
        #    the derive statement because the CALLEE holds it, and the callee's own
        #    allocations are the window. bootsnap 1.24.5 is the corpus case:
        #    `bs_fetch(RSTRING_PTR(path_v), path_v, cache_path, handler, args)` -- and
        #    without this step the derive looks like an ordinary argument pass and gets
        #    discharged `no-window`, which is a silent false negative.
        elif tree is not None and cons[0] in tree.by_name:
            # Which argument slot carries the pointer.
            spans = arg_spans(body, cons[2] + len(cons[0]))
            idx = next((i for i, a in enumerate(cons[1])
                        if any(d + "(" in re.sub(r"\s+", "", a) for d in INTERIOR)
                        or (i < len(spans)
                            and any(spans[i][0] <= o < spans[i][1] for o in reads))), None)
            # The same linkage rule as `copied_in_callee`, in the REPORTING direction:
            # descending into another TU's namesake here invents an ESCAPES-INTO-CALLEE
            # against a body that never runs. Same table, same fix (tu_scope.bind).
            for callee in tu_scope.bind(tree.by_name[cons[0]], fn.path,
                                        fn.bstart + cons[2]):
                if callee is fn or idx is None or idx >= len(callee.params):
                    continue
                pname = callee.params[idx][1]
                if not pname:
                    continue
                # Apply the same ordered triple one level down: the callee only holds the
                # pointer across a window if its LAST use of that parameter comes AFTER a
                # window in its own body. Without this the rule fires on every callee that
                # allocates anywhere, which is most of them, and mysql2 and bcrypt go red
                # for no reason.
                uses = [m.start() for m in
                        re.finditer(r"\b%s\b" % re.escape(pname), callee.body)]
                if not uses:
                    continue
                w = [c for c in find_calls(callee.body)
                     if classify_window(c[0], c[1]) and c[2] < max(uses)]
                if w:
                    found.append(("ESCAPES-INTO-CALLEE", fn.bstart + cons[2],
                                  re.sub(r"\s+", " ",
                                         "%s(%s)" % (cons[0], ", ".join(cons[1])))[:100],
                                  "%s() still reads %s after %s at %s:%d"
                                  % (callee.name, pname,
                                     classify_window(w[0][0], w[0][1]),
                                     callee.path.name,
                                     line_of(callee.src, callee.bstart + w[0][2]))))
                    break
    if names:
        for nm, a, s, _e in find_calls(body):
            if s <= rel:
                continue
            hit = library_call(nm, a, s)
            if hit:
                found.append(hit)
    return found


# ------------------------------------------------------- stage 3: the window


def classify_window(name, args):
    """The window class of one call, or None. Ordered: the first match wins."""
    if W_GVL.match(name):
        return "GVL-RELEASE"
    if W_RAISE.match(name):
        return "RAISE"
    if W_REENTRY.match(name):
        return "RUBY-REENTRY"
    if W_ALLOCV.match(name):
        # Below RUBY_ALLOCV_LIMIT this is alloca and there is no GC-visible object at
        # all. Only a literal size can be decided here; anything computed is unknown and
        # counts, because recall is the bias.
        size = args[-1] if args else ""
        m = re.fullmatch(r"\s*(\d+)\s*", size)
        if m and int(m.group(1)) < ALLOCV_LIMIT:
            return None
        return "ALLOCV"
    if W_XALLOC.match(name):
        return "XREALLOC"
    if W_ALLOC.match(name):
        return "RUBY-ALLOC"
    if W_COERCE.match(name):
        return "IMPLICIT-COERCE"
    return None


def window_between(fn, lo, hi, after=None):
    """[(kind, name, off)] for every window call strictly between two body offsets.

    ORDERED, unlike predicate B, which is explicitly path-insensitive. The blind spot is
    inherited and stated in the docstring: there is no CFG, so a window on a branch the
    deref cannot reach still counts, and a defect on the else-branch of a conversion is
    still wrongly cleared.

    `after` -- offset just past the derivation's closing paren. A DERIVATION IS NOT A
    WINDOW FOR ITSELF, and without this it was: `lo` is the offset of the derive macro,
    `StringValueCStr` classifies IMPLICIT-COERCE, so every `p = StringValueCStr(x)` whose
    alias was used later reported a window consisting of nothing but its own derivation.
    That is 38 rows over the 59-tree corpus and **22 of rmagick's 27** -- the bulk of the
    noise round 8 was sent to fix, and it is an off-by-one in this scan rather than the
    missing interprocedural tier it was diagnosed as. Calls nested in the derive's own
    ARGUMENTS are skipped by the same bound, because they are evaluated before the pointer
    exists; a sibling call later in the same statement still counts, because evaluation
    order within a statement is unspecified.
    """
    body = fn.body
    a, b = lo - fn.bstart, hi - fn.bstart
    if b <= a:
        return []
    floor = 0 if after is None else max(0, after - fn.bstart - a)
    out = []
    for name, args, s, _e in find_calls(body[a:b]):
        if s < floor:
            continue
        kind = classify_window(name, args)
        if kind:
            out.append((kind, name, fn.bstart + a + s))
    return out


def derive_extent(fn, deriv_off, macro):
    """Offset just past the derivation's closing paren, for `window_between(after=)`."""
    rel = deriv_off - fn.bstart
    _args, past = call_args(fn.body, rel + len(macro))
    return fn.bstart + past


# ------------------------------------- the interprocedural copy tier (round 8)

CARRIER_DEPTH = 4


def _carrier_re(base, field):
    """`base` is one name or a tuple of them -- the whole alias set at the top frame.

    The DOMINANCE test in copied_in_callee() is what needs the set. `p = RSTRING_PTR(s);
    q = p; memcpy(d, p, n); rb_funcall(...); use(q);` has a copy that dominates `p` and does
    NOT dominate the pointer, so a regex over `p` alone discharges a live row.
    """
    names = (base,) if isinstance(base, str) else tuple(base)
    alt = "|".join(re.escape(n) for n in names)
    if field:
        return re.compile(r"\b(?:%s)\b\s*(?:->|\.)\s*%s\b" % (alt, re.escape(field)))
    return re.compile(r"\b(?:%s)\b" % alt)


def _map_carrier(args, base, field, callee):
    """Which parameter of `callee` carries (base, field), and as what.

    Two dispositions, and the difference decides whether the field survives the hop:
      `f(opts->hostname)`  -- the FIELD's value is passed, so the callee holds a bare
                              pointer: (param, None).
      `f(&connopt)` / `f(opts)` -- the AGGREGATE is passed, so the field name survives:
                              (param, field).
    """
    field_re = _carrier_re(base, field) if field else None
    base_re = _carrier_re(base, None)
    for i, a in enumerate(args):
        if i >= len(callee.params):
            break
        pname = callee.params[i][1]
        if not pname:
            continue
        if field_re is not None and field_re.search(a):
            return (pname, None)
        if base_re.search(a):
            return (pname, field)
    return None


def copied_in_callee(fn, start_off, base, field, tree, depth=0, seen=None):
    """The chain that copies the carrier before anything can move it, or None.

    THE MIRROR OF `ESCAPES-INTO-CALLEE`. That tier descends into an in-tree callee to find
    a WINDOW; this one descends to find a COPY. Both exist because the immediate consumer
    is the wrong unit: bootsnap hands the pointer to `bs_fetch` and the window is inside
    it, and trilogy hands a stack struct to `try_connect` and the copy is two frames down.

    SOUNDNESS, which is the whole rule. A copy discharges only if it DOMINATES: scanning
    forward from the derivation, the first thing that happens to the carrier must be the
    copy, and **no window call may occur before it, in this frame or in any frame on the
    path**. A copy that runs after an allocation is not a discharge -- it copies whatever
    the stale pointer now points at. This is what makes the rule refuse to clear trilogy,
    and refusing was the correct answer: `trilogy_sock_new` opens with
    `xmalloc(sizeof(struct trilogy_sock))`, which under `-DTRILOGY_XALLOCATOR` is
    `ruby_xmalloc` -- confirmed on the artifact with `nm -u`, `_ruby_xmalloc` present in
    2.12.x and absent in the fork. Thirteen live interior pointers sit in the caller's
    stack struct across that allocation and across each of the twelve `xstrdup`s that
    follow, every one of which is another `ruby_xmalloc`.

    Bounded at CARRIER_DEPTH frames and cycle-guarded. Returns a human-readable chain so
    the discharge names the callee that cleared it, like every other rule here.
    """
    if depth >= CARRIER_DEPTH:
        return None
    seen = set() if seen is None else seen
    key = (fn.path, fn.hdr, base, field)
    if key in seen:
        return None
    seen = seen | {key}

    carrier = _carrier_re(base, field)
    rel = start_off - fn.bstart

    def dominates(end):
        """No use of the carrier survives the call that ends at `end`.

        THE HALF OF "DOMINATES" THAT COSTS SOMETHING. Round 8 shipped this rule once
        without it and it over-cleared immediately: rmagick's `rm_str_to_pct` derives
        `pct_str`, hands it to `strtol` -- which is genuinely in COPIES -- and then reads it
        again in three separate `rb_raise(..., pct_str)` calls. That is the *mittens* shape,
        the one this predicate exists to catch, discharged by its own new rule. A copy that
        is not the LAST thing to touch the pointer discharges nothing.
        """
        return carrier.search(fn.body, end) is None

    for name, args, s, e in find_calls(fn.body):
        if s < rel:
            continue
        joined = " ".join(args)
        touches = bool(carrier.search(joined))
        if COPIES.match(name) and touches and dominates(e):
            return "%s() at %s:%d" % (name, fn.path.name,
                                      line_of(fn.src, fn.bstart + s))
        if touches and tree is not None and name in tree.by_name and dominates(e):
            # ROUND 9: THE CALLEE THIS FILE CAN SEE, NOT EVERY NAMESAKE IN THE TREE.
            #
            # `by_name` is keyed by the bare name, so a call in b.c descended into a.c's
            # same-named `static` helper -- and this is a DISCHARGE, so the wrong body did
            # not merely mis-describe a row, it CLEARED one: b.c's helper holds the pointer
            # across an rb_funcall, a.c's copies it immediately, and `copies-in-callee`
            # discharged b.c's derivation through a.c's body. Zero hits on a real finding.
            #
            # tu_scope.bind is the same rule predicates A, B and C resolve names by. The
            # tree-wide fall-back is kept where C keeps it: a non-static callee, or one
            # defined in a header, genuinely is visible from here.
            for callee in tu_scope.bind(tree.by_name[name], fn.path, fn.bstart + s):
                if callee is fn:
                    continue
                mapped = _map_carrier(args, base, field, callee)
                if mapped is None:
                    continue
                inner = copied_in_callee(callee, callee.bstart, mapped[0], mapped[1],
                                         tree, depth + 1, seen)
                if inner:
                    return "%s() at %s:%d -> %s" % (name, fn.path.name,
                                                    line_of(fn.src, fn.bstart + s), inner)
            # Handed to an in-tree callee that does not copy it. Keep scanning: the callee
            # may only be reading it. The window check below still bounds us.
        if classify_window(name, args):
            # Something that can move or free the String runs before any copy. Whatever
            # copies later copies a stale pointer.
            return None
    return None


CONTAINER_LVALUE_RE = re.compile(r"^\*?\s*([A-Za-z_]\w*)\s*(?:->|\.|\[)\s*([A-Za-z_]\w*)?")


def carrier_copy_chain(fn, deriv_end, names, esc, tree):
    """Name what holds the pointer after the derive statement, then look for the copy.

    Two carriers, and nothing else is allowed to reach the descent:

      no escape, but a local alias -- `char *p = RSTRING_PTR(s); f(p);`
      ESCAPES-INTO-CONTAINER into an aggregate THIS FRAME OWNS -- trilogy's `connopt`.

    A pointer PARAMETER is excluded on purpose (`fn.ptr_params()`): `STORES-INTERIOR` writes
    through storage the CALLER owns, so the caller may read it after this frame returns and
    a copy found here proves nothing about that read. Excluding it is the difference between
    a discharge and an over-clear, and over-clearing is the one failure mode this whole
    predicate is biased against.
    """
    ptrp = fn.ptr_params()
    if esc:
        base = None
        for kind, _eoff, text, _extra in esc:
            if kind != "ESCAPES-INTO-CONTAINER":
                return None
            m = CONTAINER_LVALUE_RE.match(text)
            if not m or m.group(1) in ptrp:
                return None
            if base is None:
                base = m.group(1)
            elif m.group(1) != base:
                return None          # the pointer went into two different containers
        field = CONTAINER_LVALUE_RE.match(esc[0][2]).group(2)
    elif names:
        base, field = tuple(names), None
    else:
        return None
    return copied_in_callee(fn, deriv_end, base, field, tree)


def last_use(fn, deriv_off, reads):
    """Offset of the last use of the derived pointer in this frame, or None.

    Only NAMED carriers are tracked. A bare `foo(RSTRING_PTR(s))` has no name for the
    pointer, so it cannot be used a second time: its only use is the consumer, in the same
    statement, with no room for a window. That is the difference between the two shapes
    and it is why this returns None rather than scanning forward.

    The whole alias set counts, not just the first name. `q = p;` is a use of `p` AND the
    birth of another carrier, so stopping at it puts the last use before the window and
    discharges `no-window` on a pointer that is still live -- see alias_names().

    AND IT COUNTS ONLY WHILE THE NAME STILL CARRIES THE POINTER. `reads` is alias_reads()
    rather than a regex over the names, because `p = other;` makes every later mention of
    `p` a use of something else -- a window bounded by an unrelated read is a SPURIOUS one,
    the same defect predicate B had as a spurious escape. Both are tu_scope's fifth rule.
    """
    rel = deriv_off - fn.bstart
    later = [o for o in reads if o > rel]
    return fn.bstart + max(later) if later else None


# ------------------------------------------------------- stage 4: liveness and size


ARGV_SEED = re.compile(r"\bargv\s*\[")


def liveness(fn, deriv_off, expr, tree, last_use_off=None):
    """(grade, why) for the SOURCE VALUE. A column, never a discharge -- see the docstring.

    Recall-biased by construction: `last-use-after` believes the compiler keeps the VALUE
    until its last syntactic use, and the compiler is under no such obligation. That is
    RB_GC_GUARD's own documented rationale, and okra is the corpus proof it can be wrong.
    """
    var = base_name(expr)
    if not var:
        return "UNROOTED", "source is not a named lvalue"
    body = fn.body
    rel = deriv_off - fn.bstart

    # The guard may name the SOURCE, or a copy of it. trilogy's fix is
    # `host_guard = val;` at the derive and `RB_GC_GUARD(host_guard)` at the end of the
    # function -- necessary there because `val` is reassigned for every option, so
    # guarding `val` would guard the wrong object. Matching only the source name lost the
    # green half of the best red/green pair in the corpus.
    # A COPY only stands in for the source if it carries the value present AT THE
    # DERIVATION. `p = RSTRING_PTR(val); val = other; guard = val; ... RB_GC_GUARD(guard)`
    # guards `other` and leaves the String behind `p` movable -- so the copy is rejected
    # when `var` is reassigned anywhere between the copy and the derive, in either order.
    # A GUARD VARIABLE THAT IS OVERWRITTEN GUARDS THE WRONG OBJECT -- and this rule DISCHARGES,
    # so getting it wrong loses a real finding rather than adding a row to triage:
    #
    #     guard = str;  p = RSTRING_PTR(str);  guard = other;  ...  RB_GC_GUARD(guard);
    #
    # The check used to look only at reassignments of the SOURCE, so `guard` stayed in the
    # set, the row discharged `guarded`, and a String whose bytes nothing roots read as
    # protected. `RB_GC_GUARD(x)` establishes liveness for whatever `x` HOLDS AT THE GUARD,
    # so a name only stands in for the source while it still carries the object that was
    # live at the derivation. `guardable` therefore records, per name, the offset from which
    # it provably does -- and the scan below rejects the guard if the name is rebound after
    # that. `var` itself is in the table under the same rule with the derivation as its
    # offset, which closes the sibling hole `str = other; RB_GC_GUARD(str);`.
    #
    # Both halves ask source_reads() rather than keeping their own rule. Here the question
    # is about the RIGHT-HAND SIDE of `guard = str;` -- that occurrence of `str` has to read
    # the object live at the derivation, or the copy carries something else -- which is the
    # same question the guard site below asks about `guard`, and the same one
    # `last-use-after` asks about the source. One predicate, three sites; see source_reads().
    src_reads = source_reads(body, var, rel)
    guardable = {var: rel}
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*=(?!=)\s*(%s)\s*;" % re.escape(var), body):
        if m.start(2) in src_reads:
            nm = m.group(1)
            guardable[nm] = max(guardable.get(nm, -1), m.start())

    # THE GUARD HAS TO OUTLIVE THE POINTER, NOT THE DERIVATION. RB_GC_GUARD establishes
    # liveness only up to its own position, so a guard placed after the derive but BEFORE
    # a later window and read protects nothing: `p = RSTRING_PTR(str); RB_GC_GUARD(str);
    # rb_funcall(...); use(p);` was being discharged. The RULES table has always said "at
    # or after the last deref" and the code said "after the derive"; the code is now what
    # the table says. Where there is no alias there is no later use, and the derivation
    # offset is the last use.
    floor = rel if last_use_off is None else max(rel, last_use_off - fn.bstart)
    for m in re.finditer(r"\bRB_GC_GUARD\s*\(\s*([A-Za-z_]\w*)\s*\)", body):
        nm = m.group(1)
        since = guardable.get(nm)
        if since is None or m.start() < floor:
            continue
        # rebound in between, or shadowed by an inner declaration: either way the name at
        # the guard is not holding the object the derivation came from.
        if m.start(1) not in source_reads(body, nm, since):
            continue
        return "GUARDED", ("RB_GC_GUARD(%s) at or after the last use" % nm
                           + ("" if nm == var else " (a copy of %s)" % var))

    # struct field on the stack: `w->path` where w is a local struct, not a pointer param
    if "->" in expr or "." in expr:
        return "STACK-FIELD", "source is a member of %s" % var

    # ---- the argv polarity rule, applied.
    params = {nm: i for i, nm in fn.value_params()}
    seeded_from_argv = False
    if fn.is_argc_argv():
        for m in re.finditer(r"\b%s\s*=(?!=)" % re.escape(var), body[:rel]):
            semi = body.find(";", m.end())
            if ARGV_SEED.search(body[m.end():semi if semi > 0 else len(body)]):
                seeded_from_argv = True
        if ARGV_SEED.search(expr):
            seeded_from_argv = True
    is_param = var in params
    if seeded_from_argv or (is_param and fn.name in tree.cfuncs):
        # argv pins the object it HOLDS. Did anything between the seeding and the derive
        # possibly replace that object with a different one? Two ways, and the second is
        # the one that matters: okra's coercion is `string = rb_funcall(string, to_s, 0)`,
        # and `rb_funcall` is not on any list of coercions -- it is an arbitrary Ruby
        # call. So ANY re-assignment of the variable breaks the argv guarantee, not just
        # a re-assignment from a call this file happens to recognise. Keying on a
        # known-coercions list here is the same "list of bad things" mistake predicate C
        # exists to avoid, and it discharges the filed okra bug.
        replaced = None
        for name, args, s, _e in find_calls(body[:rel]):
            if not args:
                continue
            a0 = args[0].strip()
            if name in TYPE_CHECK:
                continue                      # proves it IS a String; replaces nothing
            if name in MAY_REPLACE and (a0 == var or a0 == "&" + var):
                replaced = name
        for m in re.finditer(r"(?<![=!<>])\b%s\s*=(?!=)" % re.escape(var), body[:rel]):
            semi = body.find(";", m.end())
            rhs = body[m.end():semi if semi > 0 else rel]
            calls = find_calls(rhs)
            replaced = calls[0][0] if calls else "a re-assignment"
        if replaced:
            return "UNROOTED", ("argv pins the ORIGINAL; %s may have replaced it"
                                % replaced)
        return "ARGV-PINNED", "argv[] pins the un-coerced original and no coercion ran"

    # Last READ OF THIS OBJECT at or after the last deref.
    #
    # A whole-body token scan is not that, and got it wrong three ways -- a write counted as
    # a read, a read counted after the name was rebound, a read of an inner variable that
    # merely spells the same. All three are one question and source_reads() is where it is
    # asked; this rule is the most recall-biased in the file (the compiler may drop the
    # VALUE before its last syntactic use even when that use IS a genuine read of the right
    # object), so accepting anything weaker cleared rows on no evidence at all.
    #
    # DEFERRED, DELIBERATELY, WITH THE NUMBER: MEASURE A READ TO THE END OF ITS STATEMENT.
    # `>= ld` compares raw offsets, and evaluation order WITHIN a statement is unspecified
    # -- the reason window_between() gives for counting a sibling call in the same
    # statement. iconv's `val = rb_str_subseq(val, 0, slash - ptr);` reads the source and
    # makes the pointer's last use in ONE call's argument list, with the source's token 20
    # bytes to the left, so both operands are live at the call and the row is noise; it is
    # in the corpus as one, at iconv.c:216, rather than papered over.
    #
    # It is not built here because it WIDENS a discharge, in the round whose whole subject
    # is discharges that fire when they should not. Measured over the 99-tree corpus:
    # -1 iconv (the row above, correctly) and -5 bigdecimal (`BigDecimal_split`, where
    # `rb_str_resize(str, strlen(psz1))` reads the source in the same call as the last
    # deref -- also correct, and also five rows cleared by a rule shipped in the same pass
    # that wrote it). A discharge that clears six rows on its first run needs its own red
    # and its own green, and it is a separate decision from narrowing three over-clears.
    ld = last_use_off
    if ld is not None:
        uses = source_reads(body, var, rel)
        if uses and fn.bstart + max(uses) >= ld:
            return "LAST-USE-AFTER", "%s is read again at or after the last deref" % var
    return "UNROOTED", "nothing roots %s across the window" % var


# A destination that is STORAGE INSIDE the object the base identifier points at: exactly
# one pointer hop -- the leading `->` -- and then only in-object member hops. `->` again
# leaves for another object, and `[` cannot be told apart from indexing a pointer member
# without resolving the member's type, so both stop the match. See escape_stays_inside.
OWNED_DEST = re.compile(
    r"([A-Za-z_]\w*)\s*->\s*[A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*\Z")


def _path_write(expr):
    """A regex matching an assignment to the whole member path `expr` spells.

    `gz->z.input` -> `gz\\s*->\\s*z\\s*\\.\\s*input\\s*=(?!=)`. The separators are kept from
    the split so the two hops cannot be confused with each other, and the same lookbehind
    tu_scope.writes carries excludes a LONGER path ending in this one: `x->w->buf = p` is a
    write to some other object's `buf` and must not read as a write to `w->buf`.
    """
    parts = re.split(r"\s*(->|\.)\s*", expr.strip())
    return re.compile(r"(?<![=!<>+\-*/%&|^.:])\b"
                      + r"\s*".join(re.escape(p) for p in parts) + r"\s*=(?!=)")


def pin_source_stable(fn, deriv_off, expr):
    """Does `expr` still denote, after the derivation, what it denoted at it?

    #32's FIRST AND SECOND REVIEW FINDINGS, and they are ONE MECHANISM SPELLED TWICE. Both
    reviewer cases are a write, after the derivation, to a name the discharge's reasoning
    depends on -- and the discharge reads that name at two different moments, so there are
    two spellings and one rule:

        p = RSTRING_PTR(w->buf);  w->buf = other;   /* the MEMBER PATH is rebound */
        p = RSTRING_PTR(w->buf);  w = other;  w->held = p;   /* the BASE is rebound */

    THE MEMBER-PATH LEG breaks the pin itself. `pinning_mark` joins on `(type, field)` and
    the dmark marks whatever the field HOLDS at collection time -- so after `w->buf = other`
    the mark pins the REPLACEMENT, and the String `p` points into is referenced by nothing.
    A plain use-after-free, and the row does not need an escape to have it: the reviewer's
    case is `HELD-ACROSS-WINDOW`.

    THE BASE LEG breaks `escape_stays_inside`, which compares the destination's base
    IDENTIFIER against the derivation's. That comparison is textual, and after `w = other`
    the same six characters name a different object. The pin claim survives -- every
    `struct wrap` has `buf` pinned, so the SOURCE String is still fine -- but the argument
    that made the escape safe does not: `w->held = p` now stores into W2 while the String
    lives in W1, W2 outlives W1, W1 is collected, and the pointer dangles. The destination
    has to be inside the object whose lifetime bounds the pointer, not inside any object of
    the same TYPE.

    ANY WRITE AFTER THE DERIVATION COUNTS, on any path, and that is deliberate. This is the
    file's stated ORDERING blind spot -- no CFG -- and everywhere else in this file it is
    accepted in the direction that keeps a row. Here it is accepted in the direction that
    keeps a row too, because for THIS rule an unrecognised rebinding CLEARS one. A write on
    a branch the derivation cannot reach costs recall; a write missed costs a use-after-free
    reported clean. The other rules' polarity argument, applied to the rule that inverts it.

    THE BASE LEG FIRES EVEN WITH NO ESCAPE ON THE ROW, where strictly only the member-path
    leg is load-bearing. Measured cost over the 99 trees: zero. Keeping it unconditional is
    a rule with one clause rather than two, in the round whose whole subject is this
    discharge firing where it should not.

    THE PATH LEG ASKS tu_scope's ALIAS SET RATHER THAN THE LITERAL SPELLING, and choosing
    that over a third exact-path clause is the point of the round. `alias = w;` then
    `alias->buf = other;` rebinds the same field through a second name, and the first cut of
    this function -- which matched only `w->buf` -- discharged it. That is the SECOND time a
    fix in this rule was routed around by one level of indirection: `unconditional_mark` was
    exact-path too, and a call to a mark helper walked past it. Both bypasses had the same
    shape, so the answer is not a third exact-path clause but the general question, asked of
    the module that already answers it.

    `tu_scope.alias_map` is rule 4 -- which locals carry the same pointer -- and a
    `struct wrap *` copy is exactly its shape, so this needs no new machinery and inherits
    its three constraints (pointer-typed left-hand side, ordering, and rule 5's kill on a
    carrier that has been overwritten). Seeded at offset 0 rather than at the derivation,
    because an alias taken BEFORE the derive aliases the same object just as well as one
    taken after; that is the reviewer's own spelling.

    ONLY THE PATH LEG ITERATES ALIASES. The base leg exists to keep `escape_stays_inside`'s
    comparison honest, and that comparison is only ever made against the derivation's own
    base spelling -- so an alias being reassigned cannot mislead it. Widening the base leg
    to every alias would also make the establishing `alias = w;` itself read as a rebinding,
    which is a decline with nothing behind it.
    """
    rel = deriv_off - fn.bstart
    m = MEMBER_PATH.fullmatch(expr)
    if m is None:
        return True
    base, rest = m.group(1), m.group(2)
    if [o for o in tu_scope.writes(fn.body, base) if o > rel]:
        return False
    for name in tu_scope.alias_map(fn.body, {base: 0}):
        if [w.start() for w in _path_write(name + rest).finditer(fn.body)
                if w.start() > rel]:
            return False
    return True


def escape_stays_inside(base, kind, text):
    """Is this escape a store into the very object `base` points at?

    `text` is what escapes() built for the two store kinds -- `stmt + " =" + rhs` -- so the
    destination is everything left of the first `=`, and there is no other `=` it could be:
    the left-hand side of an assignment holds no comparison.

    ONLY THE TWO STORE KINDS CAN EVER QUALIFY. A return has no destination; a file static, a
    non-copying library and a callee that keeps reading all have destinations whose lifetime
    has nothing to do with the wrapper's. Listing the two that can rather than the four that
    cannot is the inversion this family uses everywhere: an escape kind added later is a HIT
    until someone argues it into this tuple.
    """
    if kind not in ("STORES-INTERIOR", "ESCAPES-INTO-CONTAINER"):
        return False
    m = OWNED_DEST.fullmatch(text.split("=", 1)[0].strip())
    return bool(m) and m.group(1) == base


def pinning_mark(tree, fn, expr, deriv_off, esc=()):
    """Where the owning type's dmark PINS this derivation's source String, or None.

    The one discharge in this file that is a fact about another function entirely, and the
    only one that can clear an ESCAPE. Every other rule here asks what happens inside the
    deriving frame; this one asks what the GC is told about the object the pointer points
    into, and the answer holds for as long as the wrapper is reachable.

    THE PIN'S LIFETIME IS THE WRAPPER'S, WHICH IS NOT "LONGER THAN ANY WINDOW". The first
    cut of this rule said it was, and cleared every escape on the strength of it. That is
    sound for a window BOUNDED BY THE FRAME and false the moment the pointer leaves for
    somewhere the wrapper does not own. The pin is `rb_gc_mark(w->buf)` inside the wrapper's
    own `dmark`; it runs only while the wrapper is reachable. So:

        static const char *saved;
        static VALUE leak(VALUE self) {
            struct wrap *w;
            TypedData_Get_Struct(self, struct wrap, &w_type, w);
            saved = RSTRING_PTR(w->buf);      /* outlives the wrapper */
            return Qnil;
        }

    the wrapper becomes unreachable, `w_mark` stops running, the String is collected, and
    `saved` dangles -- with the pinning mark present and correct the whole time. The rule
    cleared that, and it is a Class B use-after-free of exactly the kind this file exists
    to find.

    SO AN ESCAPE IS DISCHARGED ONLY WHEN THE ESCAPED POINTER CANNOT OUTLIVE THE PIN, and
    what makes that true is that the destination is storage INSIDE the pinned object: same
    base identifier, one pointer hop, then member hops that stay in the object. Freeing the
    wrapper frees the destination in the same step, so the dangling window never opens.
    That is every real escape in the corpus and it is not a coincidence -- the shape is a
    struct caching an interior pointer into a String the same struct owns and marks:

        z->stream.next_in = (Bytef*)RSTRING_PTR(z->input)      zlib, 44 rows
        parser->state.start = start                            json, 3 rows
        gz->orig_name = rb_str_new(RSTRING_PTR(gz->z.input), n) zlib -- a COPY, kept by the
                                                               same test for a lesser reason

    NOT "the destination is a struct field", which is what a rule keyed on the escape KIND
    would say: `out->held = p` for a pointer PARAMETER `out` is the same STORES-INTERIOR
    spelling writing into an aggregate the caller owns and the wrapper does not, and 8t's
    `caller` arm is the red for it. And not a bare base-name match either: `w->peer->held`
    starts at the same identifier and ends in a different object, which is 8t's `peer` arm.

    ALL escapes on the row must qualify, not any: a derivation that stores into the wrapper
    AND hands the pointer to a library has dangled by the second one.

    Deliberately NOT asked, and each of these is a row this rule leaves standing:

      *  whether the SOURCE VALUE is stored into a pinned field, rather than read out of
         one. msgpack's `_msgpack_buffer_append_reference` derives from a local and then
         writes `b->tail.mapped_string = mapped_string`, which the buffer's dmark pins with
         `rb_gc_mark(c->mapped_string)`. That is a true clear and a DIFFERENT rule: it needs
         the store to dominate every window, which is the `copies-in-callee` argument again
         and is a second decision. Two corpus rows, named in TRIAGED, still hand-cleared.
      *  `rb_gc_register_mark_object`, which also pins, and pins harder -- the object is
         rooted for the life of the process. unicorn's `init_unicorn_httpdate` registers a
         file-scope `buf` and then derives `buf_ptr = RSTRING_PTR(buf)` from it. It is not a
         dmark at all: there is no type, no field and no wrapper, so it shares no machinery
         with this rule, and registration is per-SLOT -- a later `buf = rb_str_new(...)`
         would leave the new String unregistered while every join key here still matched.
         Two corpus rows, named in TRIAGED, still hand-cleared. Scoped out on purpose.
      *  whether the instance the pointer came from is the one a dtype wraps. msgpack wraps
         `msgpack_buffer_t` with TWO descriptors and one of them has `.dmark = NULL`
         (buffer_class.c:137, and predicate A's round-5 iteration-order defect is the same
         pair). The join here is (type, field), so a second wrapper with no dmark is
         invisible to it. Reaching that needs A's wrap-site walk; it is a stated limit.
      *  whether an escape that COPIES the bytes is safe because the pin removes the
         allocation hazard `copies-immediately` refuses it for. It is -- `return
         rb_utf8_str_new(ptr + offset, len - offset)` in json's cResumableParser_rest stores
         no pointer anywhere, only bytes -- but it is a WIDENING, and this pass narrows.
         That row is a hit now and named in TRIAGED. `copies-immediately` does NOT pick it
         up instead: that rule requires `not esc`, and here the return IS the escape.
    """
    key = member_key(tree.structs, var_types(fn), expr)
    if key is None:
        return None
    where = tree.pinned.get(key)
    if where is None:
        return None
    if not pin_source_stable(fn, deriv_off, expr):
        return None
    base = MEMBER_PATH.fullmatch(expr).group(1)
    if any(not escape_stays_inside(base, kind, text) for kind, _o, text, _x in esc):
        return None
    return where


SIZE_HINT = re.compile(r"\b(\d{3,})\b")


def size_regime(fn, deriv_off, expr):
    """(regime, why). The cheapest true discharge in the whole predicate.

    A String at or above the measured 616-byte embedded boundary keeps its bytes in a
    malloc'd buffer, and compaction does not move a malloc'd buffer. zstd's 131,591-byte
    frame buffer and trilogy's >32768 threshold are both discharged by size alone.
    Clears MOBILITY only -- never liveness, and therefore never an escape.
    """
    body = fn.body
    rel = deriv_off - fn.bstart
    var = base_name(expr)
    ctx = body[max(0, rel - 400):rel]
    for m in SIZE_HINT.finditer(ctx):
        n = int(m.group(1))
        if n >= EMBED_BOUNDARY and var and var in ctx[max(0, m.start() - 200):]:
            return "HEAP-GUARANTEED", "size %d >= embedded boundary %d" % (n,
                                                                           EMBED_BOUNDARY)
    return "EMBEDDED-POSSIBLE", ""


# ------------------------------------------------------- the sweep


class Result:
    def __init__(self, name):
        self.name = name
        self.files = 0
        self.funcs = 0
        self.derivations = []      # (fn, off, macro, expr)
        self.with_window = []      # same
        self.hits = []             # (subshape, path, line, headline, detail)
        self.discharges = []       # (rule, path, line, text)

    @staticmethod
    def _fns(rows):
        return len({(f.path, f.hdr) for f, *_ in rows})

    @property
    def deriv_fns(self):
        return self._fns(self.derivations)

    @property
    def window_fns(self):
        return self._fns(self.with_window)


# `heap-guaranteed` is DELIBERATELY NOT HERE. See size_regime(): the sizes that would
# discharge by regime -- zstd's 131,591-byte frame buffer, trilogy's 32768 threshold --
# are runtime values, not source constants, so the rule cleared 0 rows over the whole
# 55-tree corpus. A discharge rule that never fires is a rule nobody has tested, and
# keeping it would be silence that reads as coverage. It stays as a COLUMN.
# DEFERRED, DELIBERATELY: THE PINNING-MARK DISCHARGE. Written down, not built.
#
# Three trees now carry rows whose ONLY defect is that this predicate cannot read a mark
# function: zlib's seven `z->buf` rows, json's `parser->buffer` (parser.c:2218,
# `rb_gc_mark(parser->buffer); // pin the buffer`), and msgpack's `c->mapped_string`
# (buffer.c:119,122). All three derive an interior pointer from a VALUE field of a wrapped
# struct whose dmark PINS that field, so the bytes cannot move and the object cannot be
# freed -- a true clear, and the largest remaining block of hand-cleared rows.
#
# It is not built here because the discharge turns on ONE token. `rb_gc_mark` pins;
# `rb_gc_mark_movable` does not, and a rule that cannot tell them apart would clear the
# movable case too -- over-clearing a whole class in a single step, which is the one failure
# mode this predicate is built against. So it may only ship with a generated red for BOTH
# marks, on one synthetic tree:
#
#   red A   dmark calls rb_gc_mark_movable(w->buf); the derive from w->buf must still HIT.
#   red B   dmark calls rb_gc_mark(w->buf);         the same derive must discharge, naming
#                                                   the mark function and the file:line.
#
# and a third arm for the case that decides whether it is worth building at all: the field
# named in NEITHER call, which must hit. Predicate A already parses dmark bodies and grades
# pinning versus movable; the work is reading that answer from here, not re-deriving it.
#
# ROUND 10: BUILT. `pinning-mark` is below, and the trap the deferral was written around is
# the reason it ships with three arms of one generated fixture rather than with one: the
# movable arm (8q) is byte-identical to the pinning arm except for the `_movable` suffix and
# must stay a HIT, and a fourth arm (8r) puts the same pinning call in a function no dtype
# registers, which must also stay a hit. The "named in neither" arm is the third. What the
# rule does NOT cover -- the store-side pin (msgpack) and the registration pin (unicorn) --
# is scoped out by name in pinning_mark()'s docstring, and both are still hand-cleared.
RULES = ("guarded", "no-window", "last-use-after", "copies-immediately",
         "copies-in-callee", "pinning-mark")


def sweep(tree, name, disabled=(), discharge=True):
    r = Result(name)
    r.files = len(tree.files)
    r.funcs = len(tree.funcs)
    off_rule = set(() if discharge else RULES) | set(disabled)

    for fn in tree.funcs:
        rel = str(fn.path.relative_to(tree.root))

        # ---- the cgi shape: an in-place conversion of a LOCAL, whose result only that
        # local roots. Reported at the conversion, because that is where the new object
        # appears; predicate B cannot see this at all.
        conv = {nm: (off, macro) for off, macro, nm in converted_locals(fn)}

        for off, macro, expr, var in derivations(fn):
            r.derivations.append((fn, off, macro, expr))
            alias = pointer_alias(fn, off, macro)
            names = alias_names(fn, off, alias)
            reads = alias_reads(fn, off, alias)
            cons = consumer(fn, off, macro)
            esc = escapes(fn, off, expr, names, cons, tree, reads)
            lu = last_use(fn, off, reads)
            dend = derive_extent(fn, off, macro)

            # AN ALLOCATING COPIER IS A WINDOW, NOT A DISCHARGE.
            # `rb_str_new(RSTRING_PTR(str), n)` allocates the destination String BEFORE it
            # copies the bytes, and that allocation can run a GC. Nothing took `&str`, so
            # nothing forces the source to keep a stack slot, and an embedded String can
            # move inside the consumer itself -- the copy then reads the vacated slot.
            # This is the same fact round 8 measured on trilogy, where `xstrdup` is a
            # `ruby_xmalloc` and the strdups are windows for each other; `xstrdup` had been
            # added to COPIES in that very round, which was wrong for the reason that round
            # established. So the consumer discharges only when it CANNOT allocate, and
            # otherwise it is reported as the window it is.
            cons_win = classify_window(cons[0], cons[1]) if cons else None
            if not esc and cons and COPIES.match(cons[0]) and not cons_win \
                    and "copies-immediately" not in off_rule:
                r.discharges.append(("copies-immediately", rel, line_of(fn.src, off),
                                     "%s(%s) in %s -- consumed by %s() in the same "
                                     "statement" % (macro, expr, fn.name, cons[0])))
                continue

            # The same question one or more call-graph hops down. See copied_in_callee():
            # the copy has to DOMINATE, so a copy that runs after an allocation does not
            # discharge, which is why this refuses to clear trilogy.
            if "copies-in-callee" not in off_rule:
                chain = carrier_copy_chain(fn, dend, names, esc, tree)
                if chain:
                    r.discharges.append(("copies-in-callee", rel, line_of(fn.src, off),
                                         "%s(%s) in %s -- copied by %s, with no window "
                                         "before it on the path"
                                         % (macro, expr, fn.name, chain)))
                    continue

            # An escape has no upper bound: the pointer outlives the frame, so the window
            # is everything that runs afterwards, in this frame and every caller's.
            #
            # ROUND 8: the escape branch used to REPLACE the classified window with the
            # literal `ESCAPE`, and the result was that **zero rows in the whole 59-tree
            # corpus carried GVL-RELEASE** -- including mysql2's and trilogy's, which
            # genuinely have one. The triage criterion "rank by GVL-RELEASE" therefore
            # selected nothing, and that was an artifact of this line, not a fact about the
            # corpus. A row now carries both: what runs before the escape, the escape, and
            # what runs after it in this frame -- which is where the nogvl call lives.
            if esc:
                esc_off = max(e[1] for e in esc)
                win = (window_between(fn, off, esc_off, after=dend)
                       + [("ESCAPE", esc[0][0], esc[0][1])]
                       + window_between(fn, esc_off, fn.bend, after=dend))
            elif lu is not None:
                win = window_between(fn, off, lu, after=dend)
            elif cons_win:
                # No alias and no escape, so the only use is the consumer -- and the
                # consumer is the window. Its call starts BEFORE the derive it encloses,
                # so window_between() cannot see it from the derivation offset.
                win = [(cons_win, cons[0], fn.bstart + cons[2])]
            else:
                # No name for the pointer and no escape: its only use is the consumer, in
                # the same statement. Nothing can run in between.
                win = []

            if not win:
                if "no-window" not in off_rule:
                    r.discharges.append(("no-window", rel, line_of(fn.src, off),
                                         "%s(%s) in %s -- nothing between the derive and "
                                         "the last use can trigger GC"
                                         % (macro, expr, fn.name)))
                    continue
            r.with_window.append((fn, off, macro, expr))

            live, why_live = liveness(fn, off, expr, tree, lu)
            size, why_size = size_regime(fn, off, expr)

            if live == "GUARDED" and "guarded" not in off_rule:
                r.discharges.append(("guarded", rel, line_of(fn.src, off),
                                     "%s(%s) in %s -- %s" % (macro, expr, fn.name,
                                                             why_live)))
                continue
            # AFTER `guarded`, BEFORE `last-use-after`, and the order is the strength of
            # the evidence. A guard is a statement in this frame about this pointer; a
            # pinning dmark is a statement about the object, made somewhere else and true
            # for longer; a last use is the weakest of the three and this file's own
            # docstring says so. Placing it here also keeps it out of `no-window`'s way --
            # a pinned derivation with no window at all is still discharged by no-window,
            # which is what the corpus diff has to be able to say.
            #
            # `esc` IS PASSED, NOT RE-DERIVED. The rule needs the escape's DESTINATION to
            # decide whether the pin outlives the pointer, and the escape set is already
            # computed here -- recomputing it inside the rule would put a second copy of
            # `escapes()`'s recall choices behind a discharge, where a divergence between
            # the two reads as a clean sheet. The other five rules take no escape argument
            # because none of them may fire on an escaping row at all.
            pin = None if "pinning-mark" in off_rule \
                else pinning_mark(tree, fn, expr, off, esc)
            if pin:
                r.discharges.append(("pinning-mark", rel, line_of(fn.src, off),
                                     "%s(%s) in %s -- pinned by %s"
                                     % (macro, expr, fn.name, pin)))
                continue
            if live == "LAST-USE-AFTER" and not esc and "last-use-after" not in off_rule:
                r.discharges.append(("last-use-after", rel, line_of(fn.src, off),
                                     "%s(%s) in %s -- %s" % (macro, expr, fn.name,
                                                             why_live)))
                continue

            subshape = esc[0][0] if esc else "HELD-ACROSS-WINDOW"
            kinds = sorted({w[0] for w in win})
            detail = ["def %s:%d %s" % (rel, fn.line(), fn.name),
                      "derive: %s(%s)" % (macro, expr),
                      "window: %s" % ", ".join(
                          "%s %s()" % (k, n) for k, n, _o in win[:4])]
            if len(win) > 4:
                detail[-1] += "  (+%d more)" % (len(win) - 4)
            detail.append("liveness: %-14s %s" % (live, why_live))
            detail.append("size: %-18s %s" % (size, why_size or
                                              "under the %d-byte boundary, so the bytes "
                                              "live in the object slot" % EMBED_BOUNDARY))
            for kind, eoff, text, extra in esc:
                detail.append("escape: %s %s:%d  %s%s"
                              % (kind, rel, line_of(fn.src, eoff),
                                 re.sub(r"\s+", " ", text)[:100],
                                 ("   [library: %s]" % extra) if extra else ""))
            if var in conv:
                coff, cmacro = conv[var]
                detail.append("converted local: %s(%s) at %s:%d -- the converted object "
                              "is rooted only by this local"
                              % (cmacro, var, rel, line_of(fn.src, coff)))
            r.hits.append((subshape, rel, line_of(fn.src, off),
                           "%s: %s(%s) held across %s" % (fn.name, macro, expr,
                                                          "/".join(kinds)),
                           detail))
    return r


def report(r, out=sys.stdout, verbose=False):
    for sub, path, line, headline, detail in sorted(r.hits, key=lambda h: (h[1], h[2])):
        print("%-24s %s:%d  %s" % (sub, path, line, headline), file=out)
        for d in detail:
            print("                         %s" % d, file=out)
    if verbose:
        for rule, path, line, why in sorted(r.discharges, key=lambda d: (d[1], d[2])):
            print("  discharged [%-18s] %s:%d  %s" % (rule, path, line, why), file=out)
    # Coverage. "0 hits" means one of three different things and only these counts tell
    # them apart: no C sources at all, no interior derivation anywhere (racc), or a query
    # that failed to resolve any function body.
    print("%-26s %3d file(s) %5d fn(s) | derive %3d/%-3d -> windowed %3d/%-3d -> hit %d "
          "(discharged %d)"
          % (r.name, r.files, r.funcs,
             len(r.derivations), r.deriv_fns, len(r.with_window), r.window_fns,
             len(r.hits), len(r.discharges)), file=out)
    return len(r.hits)


# ---------------------------------------------------------------- acceptance


def _find(pool, prefix):
    for d in pool:
        if pathlib.Path(d).name.startswith(prefix):
            return pathlib.Path(d)
    return None


def _sweep(root, disabled=(), discharge=True):
    root = pathlib.Path(root)
    return sweep(Tree(root), root.name, disabled, discharge)


def _hits(root, disabled=(), discharge=True):
    return _sweep(root, disabled, discharge).hits


def _synth(name, files):
    """Write a synthetic tree from the test itself and return its path.

    `_mutate` needs a real gem to start from, which is right for the polarity controls but
    wrong for a shape no tree in the corpus happens to contain. These fixtures are written
    here, in full, so the red is generated rather than checked in -- and so that reading the
    check tells you what it pins without opening another file.
    """
    tmp = pathlib.Path(tempfile.mkdtemp()) / name
    for rel, text in files.items():
        p = tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return tmp


def _mutate(src_tree, edits):
    """Copy a real tree and apply (relpath, old, new) edits. Returns a temp path.

    Generated at test time from the unedited tree, never checked in: a hand-edited
    control is a different program and proves less (round-4 rule).
    """
    tmp = pathlib.Path(tempfile.mkdtemp())
    dst = tmp / pathlib.Path(src_tree).name
    shutil.copytree(src_tree, dst)
    for rel, old, new in edits:
        p = dst / rel
        txt = p.read_text()
        assert old in txt, "mutation anchor absent: %r in %s" % (old[:60], rel)
        p.write_text(txt.replace(old, new))
    return dst


POSITIVES = [
    # (prefix, file-suffix, substring that must appear in a hit headline or detail)
    ("rmagick-", "rmutil.cpp", "rm_str2cstr"),
    ("bootsnap-1.24.5", "bootsnap.c", "bs_fetch"),
    ("trilogy-bc", "cext.c", "rb_trilogy_connect"),
    ("nokogiri-", "xml_reader.c", "from_memory"),
    ("mittens-", "ext.c", "stemmer_initialize"),
    ("rinku-2.0.6", "rinku_rb.c", "rinku_load_tags"),
    ("rinku-basecamp", "rinku_rb.c", "rinku_load_tags"),
    ("rinku-maxprokopiev", "rinku_rb.c", "rinku_load_tags"),
    ("okra-", "gumbo_parser.c", "okra_parse_document"),
    ("date-", "date_core.c", "tmx_m_zone"),
    ("prism-", "extension.c", "input_load_string"),
    ("cgi-", "escape.c", "unescape"),
]

# Negative controls that must come back with ZERO hits, each cleared by a named rule.
# racc is the "zero for the right reason" control: no interior derivation exists in the
# tree at all, so its zero has to show up as `derive 0/0`, not `hit 0 (discharged 40)`.
#
# ROUND 10: stringio MOVES IN, the mirror of round 9's json moving out, and for the opposite
# kind of reason. Its one row -- `strio_getline`'s `RSTRING_PTR(ptr->string)` -- was never
# noise; `strio_mark` pins that exact field with `rb_gc_mark(ptr->string)` (stringio.c:96,
# registered positionally at :114-120), so the row is discharged by `pinning-mark` rather
# than merely small. A tree whose zero is produced by a named rule is a stronger control
# than one whose zero is produced by finding nothing, which is what racc is here for.
#
# ROUND 11 MOVES IT STRAIGHT BACK OUT, and the round trip is the honest record of what the
# #32 review cost. The row is an ESCAPES-INTO-CALLEE -- `strio_substr(ptr, s -
# RSTRING_PTR(ptr->string), ...)`, a callee that still reads its argument after allocating --
# and the pin's lifetime is the WRAPPER's, which says nothing about a pointer the callee may
# keep past the wrapper. Round 10 cleared it on the blanket claim that a pin outlasts any
# window, which is exactly the P1 finding. Gaining a clean control on Monday and returning it
# on Friday is the shape of an over-clear, so it is recorded as one rather than smoothed over:
# NEGATIVES goes back to four and stringio back into TRIAGED at its round-9 count of 1.
NEGATIVES = ["erb-", "bcrypt-", "ed25519-", "racc-"]

# The rest of the round-6 negative set does NOT come back clean, and pinning the numbers
# is more honest than widening a rule until they do. Each entry is the count triaged by
# hand in round 7, and the self-test asserts the count has not grown -- a pass may add a
# column but never delete a row, and it may not quietly grow its own noise either.
#
#   mysql2 0.5.6   ** NOT NOISE. ** rb_mysql_connect stores StringValueCStr(host/user/
#                  pass/database/socket) into `struct nogvl_connect_args` and then calls
#                  rb_thread_call_without_gvl(nogvl_connect, &args, ...). That is the
#                  trilogy #312 shape exactly, and there is NO RB_GC_GUARD on any of the
#                  five -- the file's guards are on `sql` and `current` only. Round 6
#                  audited this gem for Class A (fieldTypes) and never looked at its
#                  Class B surface. Round-7 finding, not a false positive.
#   zlib           zstream_expand_buffer_into and friends store RSTRING_END(z->buf) into
#                  z->stream.next_out. The buffer VALUE is a marked field of the same
#                  struct and zlib re-derives the pointer after every expansion, but that
#                  refresh is a property of the call graph, not of any one statement, so
#                  no syntactic rule can see it. Triaged by hand, not cleared.
#   iconv          the iconv_convert loop, driven clean by execution in round 6 with the
#                  source measured PINNED. Liveness is a column here, so it reports.
#   zstd           RSTRING_PTR into ZSTD_compress with a rb_str_new for the output in
#                  between. Round 6 discharged it by size (131,591-byte buffer); the size
#                  rule only fires on a literal near the derive, and zstd computes it.
#   sqlite3        bind_param -> sqlite3_bind_text/blob. The destructor argument decides
#                  it, and SQLITE_TRANSIENT copies; the table is a severity column by
#                  design, so it cannot clear the row on its own.
#   stringio,      one row each, both the derive feeding a callee that keeps the pointer.
#   msgpack,
#   websocket-driver
#
# ROUND 8 re-pin. mysql2 11 -> 10: `set_charset_name` (client.c:1459) is discharged by the
# new `copies-in-callee` tier -- `mysql2_mysql_enc_name_to_rb()` reaches `strcmp()` in
# mysql_enc_name_to_ruby.h with no window on the path. The **five** connect-arg rows, which
# are the ones this pin exists to protect, are unchanged and separately asserted below.
# zlib stays 16: the seven `z->buf` rows are discharged in triage by `zstream_mark`'s
# PINNING `rb_gc_mark` (zlib.c:1181-1186), which is not a rule this predicate can apply --
# see the note on the copies-in-callee docstring.
#
# RE-PINNED AFTER THE ROUND-8 REVIEW FIXES, AND THE GROWTH IS NOT YET TRIAGED.
# Tightening `guarded` (the guard must outlive the POINTER, and a guard copy must carry the
# value present at the derivation) and refusing to discharge an ALLOCATING copier brought
# rows back: iconv 5->14, zlib 16->20, zstd 4->6, msgpack 1->2, mysql2 10->11. Some are
# plainly right -- iconv's `rb_sys_fail(RSTRING_PTR(msg))` is the mittens shape verbatim,
# an exception message formatted from the pointer. Some are plainly noise -- iconv's
# `rb_str_derive` derives only to compare pointers and to compute an offset. **Neither has
# been worked through.** The numbers are pinned at the new values so that FURTHER growth
# still trips the check; they are not a claim that the 24 added rows have been read.
#
# ROUND 9: TRACKING `RSTRING_GETMEM`'s OUTPUT POINTER ADDED FIVE ROWS, AND HERE IS EACH.
# json moves OUT of NEGATIVES, which is a real loss of a clean control and is recorded as
# one rather than papered over by widening a rule.
#
#   json 0 -> 2    parser.c:2410 `cResumableParser_feed` is the predicate's own shape,
#                  verbatim: RSTRING_GETMEM(parser->buffer, start, len) written straight
#                  into parser->state.start/end/cursor, a heap struct read on a LATER
#                  Ruby call. Cleared in triage by the PINNING mark -- json's
#                  JSON_ResumableParser_mark calls `rb_gc_mark(parser->buffer)` at
#                  parser.c:2218, with the comment `// pin the buffer`, while every other
#                  field in the same function uses rb_gc_mark_movable. That is a
#                  deliberate pin and it is correct. Same disposition as zlib's seven
#                  z->buf rows, and now the second corpus witness for the pinning-mark
#                  discharge that is deferred until it can ship with a generated red for
#                  BOTH marks.
#                  parser.c:140 `rstring_cache_cmp` is NOISE, from `carries()`: the
#                  function returns `int` and the pointer is only an ARGUMENT inside
#                  `return rstring_cache_memcmp(str, rstring_ptr, length)`. Not a new
#                  mechanism -- `return f(RSTRING_PTR(s))` has always read as an escape --
#                  and left alone deliberately, because narrowing `carries()` to reject an
#                  argument position would also reject `return strchr(p, ',')`, which
#                  really does return an interior pointer.
#   msgpack 2 -> 3 buffer.c `_msgpack_buffer_append_reference` is msgpack's zero-copy
#                  chunk: RSTRING_GETMEM(mapped_string, data, length) then
#                  `b->tail.first = data; b->tail.last = data + length`. It stores the
#                  string alongside the pointer and marks it with the PINNING
#                  `rb_gc_mark(c->mapped_string)` (buffer.c:119,122) -- the idiom this
#                  skill's safe-idiom table already cites msgpack's buffer for. Third
#                  witness for the deferred rule; both versions move together.
#   puma 5 -> 6    puma_http11.c:388 `HttpParser_execute` derives with the macro and the
#                  only window is `rb_raise` on the `from >= dlen` branch, which is
#                  mutually exclusive with the `puma_parser_execute(http, dptr, ...)`
#                  branch that reads the pointer. That is this file's stated ORDERING
#                  blind spot -- no CFG, so a window on a branch the deref cannot reach
#                  still counts -- and it is noise under a limit that is written down
#                  rather than a new one.
#
# ROUND 9, SECOND REVIEW PASS: +18 rows over the 99-tree corpus (436 -> 454), 0 removed,
# 1 column change. Every added row triaged, by cause:
#
#   mysql2 11 -> 14   `_mysql_client_options` (client.c:985/990/995 on 0.5.6, and the same
#   in all 5 trees      three cases in the other four mysql2 trees). Alias propagation
#                       follows `charval = StringValueCStr(value); retval = charval;` to the
#                       `mysql_options(wrapper->client, opt, retval)` at the bottom of the
#                       function -- and the only windows in between are the sibling
#                       `StringValueCStr(value)` calls in the OTHER arms of the same switch.
#                       Mutually exclusive branches: this file's stated ORDERING blind spot,
#                       the same disposition as puma's row, and NOISE. It is also safe for a
#                       second, independent reason -- `StringValueCStr(x)` expands to
#                       `rb_string_value_cstr(&x)`, and taking the address forces `value` a
#                       conservatively scanned stack slot (the round-8 measurement). Pinned
#                       here so it cannot grow, not accepted as a finding.
#   date 16 -> 17     date_parse.c:229, `s = RSTRING_PTR(d); ... bp = s; ... ALLOCV_N(...);
#                       memcpy(buf, bp, ...)`. The last of SIX identical blocks in `s3e`; the
#                       other five were already hits and this one was invisible only because
#                       `bp = s` was `s`'s final textual mention. A recall recovery, and the
#                       five accepted siblings are the argument that it is the same row.
#   unicorn 5 -> 6    httpdate.c:75, `static char *buf_ptr; ... buf_ptr = RSTRING_PTR(buf);`
#   (4.9.0 and 6.1.0)   at Init time, written through by every later `httpdate` call. The
#                       predicate's shape exactly, found by the new static-storage sink.
#                       Cleared in TRIAGE by `rb_gc_register_mark_object(buf)` on the line
#                       above, which pins: fourth corpus witness for the deferred
#                       pinning-mark discharge, and the first that pins by registration
#                       rather than by a dmark. Predicate B reports 0 hits on both trees --
#                       `init_unicorn_httpdate(void)` converts no by-value parameter, so the
#                       row has exactly one home and this is it.
#   prism             api_pack.c:197 keeps its row and gains RAISE beside RUBY-ALLOC in the
#                       window column: the alias set moved the last use later. A column
#                       change on an existing row, not a new row.
#
# `last-use-after` refusing a WRITE as evidence (thread 5) moved ZERO corpus rows. All 13 of
# its clears are genuine reads at or after the last deref. Recorded as corpus-neutral rather
# than left to look like coverage -- the generated red is the only thing testing it.
#
# ROUND 9, THIRD REVIEW PASS: +6 rows over the 99-tree corpus (454 -> 460), 0 removed, and
# every one of them is the same direction -- a discharge that stopped firing. Each triaged:
#
#   openssl 4 -> 5    ossl_asn1.c `to_der_internal`, in all four trees. `str` is written in
#   (3.3.0, 3.3.1,      BOTH arms of an if/else and read after the join, so the else-arm's
#    3.3.3, 4.0.0)      write disqualifies the then-arm's read although the two cannot both
#                       run. That is this file's stated ORDERING blind spot -- no CFG -- in
#                       the disqualifier direction, the same disposition as puma's row and
#                       mysql2's `_mysql_client_options` three. NOISE, and the alternative
#                       (ignore a write that is not provably on every path) re-opens
#                       `if (c) { str = other; } use(str);` as a clear.
#   iconv 14 -> 15    iconv.c:216 `strip_glibc_option`. `val = rb_str_subseq(val, 0, slash -
#                       ptr);` makes the pointer's last use and reads the source in ONE
#                       call's argument list, with the source's token 20 bytes to the left,
#                       so `max(uses) >= ld` compares the layout of the line. NOISE, with
#                       the fix written down and deliberately deferred at the `ld` comparison
#                       in liveness(): it would clear this row and five bigdecimal rows, and
#                       widening a discharge is not this pass's business.
#   date 17 -> 18     date_parse.c:1935 `parse_ddd_cb`, and the most interesting of the six.
#                       `s1 = cs5 + 1` is a pointer-arithmetic alias the copy scan could not
#                       see until this pass, and `*s1` is read after `rb_str_subseq()`
#                       allocates. There IS an `RB_GC_GUARD(s5)` at the end of the block --
#                       and it guards the REASSIGNED `s5` (`s5 = rb_str_subseq(s5, ...)`),
#                       not the String `cs5` points into. So it is the first corpus witness
#                       for the round-9 guard-rebind rule, produced by the round-9 alias
#                       rule. Not cleared: the source's last read before the rebind is the
#                       ARGUMENT of the allocating call itself, so nothing roots it at `*s1`.
#                       Pinned here rather than left floating in a positive-control tree.
#
# ROUND 9, FOURTH REVIEW PASS: +4 rows over the 99-tree corpus (460 -> 464), 0 removed, 0
# columns changed. All four come from ONE change -- `carries()` no longer rejecting every
# expression that contains a `-` -- and all four are the SAME shape as rows the predicate
# already reported, with a subtraction somewhere in the same statement. Proven, not asserted:
# deleting only the `-` term from each site makes the UNFIXED script report the same row at
# the same line, so the blanket rejection was discharging them and nothing else was.
#
#   json 2 -> 3       parser.c:2710 `cResumableParser_rest`, `return rb_utf8_str_new(ptr +
#                       offset, len - offset)`. Identical in kind to parser.c:140, already
#                       triaged four paragraphs up as NOISE from `carries()`: the pointer is
#                       an ARGUMENT of the returned call, and narrowing `carries()` to reject
#                       an argument position would also reject `return strchr(p, ',')`, which
#                       really does return an interior pointer. Same disposition.
#   zlib 3 -> 4       zlib.c:2921 (3.2.1), :2915 (3.2.3), :2788 (basecamp-patch),
#   in all three        `gz->crc = checksum_long(crc32, gz->crc, (Bytef*)RSTRING_PTR(str) +
#   trees               gz->ungetc, RSTRING_LEN(str) - gz->ungetc)`. The value stored is a
#                       CRC, not the pointer -- the same argument-position noise as json's,
#                       and the same shape as zlib.c:2841 (`gz->comment = rb_str_new(
#                       RSTRING_PTR(...), len)`), which has been a hit in every round. NOISE
#                       under the same stated limit.
#
# The change that produced them is a recall fix and its own red is generated (`tail =
# RSTRING_END(str) - 1` into a file static); these four are what it costs, and the exclusion
# it was protecting -- stringio's `ptr->pos = e - RSTRING_PTR(ptr->string)` -- still holds and
# still has its green.
#
# ROUND 9 FOLLOW-UP: THE TRIAGED RESIDUE DOES NOT MOVE, and it took two corpus rounds to
# keep it that way. The alias kill (tu_scope's fifth rule applied to the alias set) shipped
# in #29 item 1 and immediately over-cleared in two places, one in each direction of the
# same brace-counting mistake, and neither was caught by a suite:
#
#   date_parse.c:230    `ep = RSTRING_END(d); ... if (s >= ep) goto no_mday; ... ep = s + l;`
#   (this predicate)    -- the rebinding write is in the same block as the derivation and
#                       dominates it on the braces alone, but the `goto` in between says
#                       control need not arrive. tu_scope.straight_line. The same rule
#                       restores yajl_ext.c:255's RUBY-REENTRY window, lost the same way
#                       across the `break` between two `case` arms.
#   ossl_asn1.c/ossl_ts.c  `if (!a1obj) a1obj = OBJ_txt2obj(...)` -- a write with NO BLOCK
#   (predicate B, 8 rows)  of its own, so its innermost enclosing block is the whole
#                       function and it reads as unconditional. tu_scope.conditional_stmt.
#                       B is the caller that pays for this one; D's corpus does not move.
#
# Both holes are closed in tu_scope and all nine rows are back. Both rules have a generated
# red in this file's 8h3 -- the corpus staying green is not a test of a constant added to
# keep the corpus green.
#
# What the kill DOES remove, correctly, is five rows, all one shape, none of them in this
# table: bigdecimal's `BigDecimal_to_s` in 3.3.1/4.0.1/4.1.0/4.1.1/4.1.2. `psz =
# StringValueCStr(f)` is rebound 41 lines later -- the same 41 in all five trees -- by
# `psz = RSTRING_PTR(str)`, a different String freshly allocated by rb_usascii_str_new,
# and the reads AFTER that rebinding were what stretched the window back across that
# allocation, `NUM2INT(f)` and `rb_raise`. All three sit in the `else` arm and cannot run
# on the path the derivation ran on; on the path where it does run there is no window at
# all. The row was a window measured on a pointer the derivation never produced. The
# trees keep their other rows and no gem's verdict moves.
# PR #30 REVIEW: THIS TABLE DID NOT MOVE, AND THE ROUND-TRIP IS THE POINT. An earlier
# commit on this branch took `iconv-` to 14, discharging `strip_glibc_option`'s
# HELD-ACROSS-WINDOW through `last-use-after` on the grounds that `val` is read again after
# the derivation. It is -- but not the same `val`:
#
#     const char *ptr = RSTRING_PTR(val), *pend = RSTRING_END(val);
#     VALUE opt = rb_str_subseq(val, slash - ptr, pend - slash);   /* allocates */
#     val = rb_str_subseq(val, 0, slash - ptr);                    /* a DIFFERENT String */
#     *code = val;
#
# `tu_scope.self_derived` had been applied in both kill modes, so that self-assignment
# stopped counting as a write and the later reads of `val` were credited to the ORIGINAL
# String. A pointer walked onto itself still points into the same object; a VALUE assigned
# from itself names a new one. The rule is DOMINATING_WRITE only now, the row is back, and
# `iconv-` is 15 again -- so this predicate's corpus output is byte-identical across the
# whole review round.
#
# ROUND 10: THE PINNING-MARK DISCHARGE IS BUILT, AND IT TAKES 47 ROWS OFF THE 99-TREE
# CORPUS (459 -> 412), 0 ADDED, 0 COLUMNS CHANGED, AND NO OTHER DISCHARGE'S COUNT MOVED.
# Every one of the 47 is a derivation whose source is a struct field that the owning type's
# dmark marks with the PINNING `rb_gc_mark`. By tree, and each is a note above that this
# table has been carrying by hand since round 7:
#
#   zlib 3.2.1     24 -> 9   the seven `z->buf` rows, `z->input` in zstream_sync, six
#   zlib 3.2.3     24 -> 9     `gz->z.input` rows in gzfile_read_header, plus zstream_
#                              shift_buffer's extra pair that only stock zlib has. 15 each.
#   zlib fork      21 -> 7   the same rows, 14 of them: the fork's zstream_shift_buffer is
#                              two derivations shorter. `zstream_mark` (zlib.c:1181-1186)
#                              pins BOTH fields, and `gz->z.input` reaches `input` through
#                              `struct gzfile`'s `struct zstream z` member.
#   json            3 -> 1   parser.c:2410 is the row this rule was deferred for, and
#                              parser.c:2710 moves WITH it -- `cResumableParser_rest` reads
#                              the same pinned `parser->buffer`. :2710 was triaged as
#                              `carries()` argument-position noise, which it also is; it is
#                              now cleared for the stronger of the two reasons. The third
#                              row (parser.c:140) stands: `rstring_cache_cmp` derives from a
#                              plain `VALUE rstring` parameter and no field is involved.
#   stringio        1 -> 0   moved to NEGATIVES; see there.
#
# WHAT DID NOT MOVE, AND IT IS FOUR ROWS THE ROUND-9 LOG COUNTED AS WITNESSES OF THIS RULE:
#
#   msgpack 1.8.3/1.8.4 buffer.c:317/:340. `_msgpack_buffer_append_reference` derives from a
#     LOCAL `mapped_string` and then stores it into `b->tail.mapped_string`, which the
#     buffer's dmark pins at buffer.c:119. The pin is real; the shape is the store side of
#     it, and reading a store as evidence needs the store to dominate every window between
#     it and the derive -- the `copies-in-callee` argument again, and a second decision with
#     its own reds. Left standing, deliberately, and still hand-cleared.
#   unicorn 4.9.0/6.1.0 httpdate.c:74/:75. `rb_gc_register_mark_object(buf)` pins, and pins
#     harder than any dmark, but there is no type, no field and no wrapper -- it shares no
#     machinery with this rule and registration is per-SLOT rather than per-object. Scoped
#     out by name in pinning_mark()'s docstring. Still hand-cleared.
#
# ROUND 11: THE #32 REVIEW TIGHTENS THE DISCHARGE IN TWO PLACES AND TWO ROWS COME BACK
# (412 -> 414), 0 REMOVED, 0 COLUMNS CHANGED, AND NO OTHER DISCHARGE'S COUNT MOVED. Both
# returning rows are ESCAPES, both are the P1 finding, and both are OVER-REPORTS that this
# pass accepts rather than argues away -- over-reporting costs an hour, over-clearing makes a
# broken gem read as safe, and the rule that told the two apart was the one being fixed.
#
#   json      1 -> 2   parser.c:2710 `cResumableParser_rest`, `return rb_utf8_str_new(ptr +
#                        offset, len - offset)`. ESCAPES-BY-RETURN, which has no destination
#                        at all, so `escape_stays_inside` cannot qualify it. The row is
#                        genuinely safe -- the return COPIES the bytes and stores no pointer
#                        anywhere -- but saying so is `copies-immediately` widened to fire on
#                        an escaping row, which is a different rule with its own reds. It is
#                        back where round 9 had it, triaged as `carries()` argument-position
#                        noise, and named in pinning_mark()'s "deliberately NOT asked" list.
#                        parser.c:2410, the row this whole discharge was built for, is
#                        UNAFFECTED: `parser->state.start = start` is a store into the pinned
#                        object itself and still clears.
#   stringio  0 -> 1   stringio.c:1388, back out of NEGATIVES; see there.
#
# THE CONDITIONAL-MARK TIGHTENING (P2) MOVES NOTHING HERE, and that is measured rather than
# assumed: forcing `escape_stays_inside` to True with `unconditional_mark` still in place
# reproduces round 10's output byte for byte over all 99 trees. It drops 33 of 148 (type,
# field) pin keys and not one of them is a key a discharged row joins on. A corpus-neutral
# tightening is precisely the one a green suite cannot tell from no tightening at all, which
# is what 8u is for.
#
# ROUND 11, SECOND REVIEW PASS: THREE MORE OVER-CLEARS IN THE SAME DISCHARGE, AND THE CORPUS
# DOES NOT MOVE AT ALL -- 414 -> 414, 0 added, 0 removed, 0 columns changed, every discharge
# rule's count identical including `pinning-mark` at 45. Stated as the headline rather than
# buried, because it is the whole reason 8v and 8w exist: with the corpus flat, the fixtures
# are the ONLY evidence any of these three fixes happened.
#
#   the call edge     `if (c) helper(w)` where `helper` marks unconditionally. 18 mark
#                       functions across the 99 trees are NOT unconditionally reachable, and
#                       gating them costs zero pin keys -- every key they carry is reached a
#                       second way, unconditionally. So the corpus cannot distinguish the
#                       fixed rule from the broken one, and 8w is the entire test.
#   the rebound path  `w->buf = other` after deriving from `w->buf`. FOUR corpus instances,
#                       and this is the one place the round has something real to point at:
#                       json parser.c:2393 (`parser->buffer = new_buffer` four lines on) and
#                       zlib's `zstream_discard_input` in all three trees (`z->input = Qnil`
#                       ten lines on). All four are SAFE and all four were ALREADY cleared,
#                       by `copies-in-callee`, because the pointer is consumed by
#                       memcpy/memmove before the rebinding. That rule runs ahead of this one,
#                       so the verdicts never depended on the broken test.
#   the rebound base  `w = other` before `w->held = p`. ZERO corpus instances. Measured, not
#                       assumed: the base leg alone declines 0 of the pinned-key derivations
#                       in the 99 trees, which is why keeping it unconditional costs nothing.
#
# ROUND 11, THIRD REVIEW PASS: TWO MORE, AND THE CORPUS IS FLAT AGAIN -- 414 -> 414, every
# discharge count identical, pin keys 115 and movable keys 69 before and after. Both fixes
# are visible ONLY in the fixtures, and both were swept for rather than assumed flat:
#
#   the aliased path  `alias = w; ... alias->buf = other;`. The rebinding rule shipped one
#                       round earlier matched the literal `w->buf` spelling, so one pointer
#                       copy walked past it. Now asks tu_scope.alias_map. This is the SECOND
#                       exact-path fix in this rule to be routed around by one level of
#                       indirection -- unconditional_mark went the same way, via a mark
#                       helper -- which is why the answer here is the general question asked
#                       of the module that already answers it, and not a third clause.
#   the NULL variant  two `rb_data_type_t` initialisers for one variable, one naming a
#                       callback and one `NULL`. SWEPT: over the 99 trees there are 329
#                       initialisers in 274 files, 76 of them inside a preprocessor
#                       conditional, and ZERO variables declared twice. The shape is
#                       synthetic today. The companion sweep at the CALL level found 43
#                       marking primitives inside conditionals with only zstd's three
#                       `#else` pins and a msgpack shim dead, which is what 8r pins; this is
#                       the same question one level up and it comes back empty.
TRIAGED = {"mysql2-0.5.6": 14, "zlib-basecamp-patch-": 7, "iconv-": 15, "zstd-": 6,
           "sqlite3-2.9.5": 3, "websocket-driver-": 2, "stringio-": 1,
           "msgpack-1.8.4": 3, "msgpack-1.8.3": 3, "json-": 2, "puma-": 6,
           "unicorn-6.1.0": 6, "date-": 18, "openssl-3.3.0": 5, "openssl-3.3.1": 5,
           "openssl-3.3.3": 5, "openssl-4.0.0": 5}


def self_test(pool):
    ok = True
    log = []

    def check(cond, label, extra=""):
        nonlocal ok
        ok &= bool(cond)
        log.append("%s %s%s" % ("PASS" if cond else "FAIL", label,
                                "" if cond else "   [%s]" % extra))

    # 0. the strip pipeline preserves byte offsets AND line numbers. Every hit prints
    #    file:line, so this is a precondition, not a nicety.
    rmagick = _find(pool, "rmagick-")
    if rmagick is None:
        print("FAIL fixture missing: rmagick- (pass its directory as an argument)")
        return 1
    probe = rmagick / "ext" / "RMagick" / "rmutil.cpp"
    raw = probe.read_text(errors="replace")
    stripped = strip_directives(strip_noise(raw))
    anchor = "rm_str2cstr(VALUE str"
    off = stripped.index(anchor)
    raw_line = raw[:raw.index(anchor)].count("\n") + 1
    check(len(stripped) == len(raw) and line_of(stripped, off) == raw_line,
          "strip pipeline preserves byte offsets and line numbers",
          "len %d vs %d, line %d vs %d"
          % (len(stripped), len(raw), line_of(stripped, off), raw_line))

    # 1. the twelve positive controls
    missing, found, absent = [], [], []
    for prefix, fsuffix, needle in POSITIVES:
        d = _find(pool, prefix)
        if d is None:
            missing.append(prefix)
            continue
        hs = _hits(d)
        got = [h for h in hs if h[1].endswith(fsuffix)
               and (needle in h[3] or any(needle in x for x in h[4]))]
        (found if got else absent).append(
            "%s %s" % (prefix, ("%s:%d" % (got[0][1], got[0][2])) if got else "-"))
    check(not missing, "all 12 positive-control fixtures present", missing)
    check(not absent, "12/12 positive controls found: %d found" % len(found), absent)

    # 2. cgi is the NAMED recall gap: predicate B cannot see `VALUE str = argv[0];
    #    StringValue(str);` because it is a local, not a by-value parameter. It drove
    #    GREEN by execution, and it still has to be FOUND -- a predicate that cannot see
    #    a shape cannot clear it either.
    cgi = _find(pool, "cgi-")
    if cgi is not None:
        ch = _hits(cgi)
        check(any("escape.c" in h[1] for h in ch),
              "cgi: the argv-seeded LOCAL conversion is found (predicate B's blind spot)",
              [(h[0], h[1], h[2]) for h in ch])

    # 3. the argv polarity rule, in both directions, on the same tree.
    #    okra: argv[0] is coerced by to_s, so argv pins the ORIGINAL and not the parsed
    #    String -> UNROOTED. Delete the coercion and the same site becomes ARGV-PINNED.
    okra = _find(pool, "okra-")
    if okra is not None:
        oh = _hits(okra)
        ored = [h for h in oh if "gumbo_parser.c" in h[1]
                and any("UNROOTED" in d for d in h[4])]
        check(ored, "argv polarity: okra's post-to_s derive is UNROOTED, not discharged",
              [(h[1], h[2], h[4]) for h in oh if "gumbo_parser.c" in h[1]])
        nocoerce = _mutate(okra, [(
            "ext/okra/html/parsers/gumbo_parser/gumbo_parser.c",
            "  if (!rb_respond_to(string, rb_intern(\"to_str\"))) {\n"
            "    string = rb_funcall(string, rb_intern(\"to_s\"), 0);\n  }\n",
            "  Check_Type(string, T_STRING);\n")])
        nh = [h for h in _hits(nocoerce) if "gumbo_parser.c" in h[1]]
        check(all(not any("UNROOTED" in d for d in h[4]) for h in nh),
              "argv polarity: with the coercion replaced by a type check the same site "
              "grades ARGV-PINNED",
              [(h[1], h[2], [d for d in h[4] if d.startswith("liveness")]) for h in nh])

    # 4. cfunc polarity is INVERTED from predicate B. B excludes cfunc entry points;
    #    deleting that inversion here loses five of the twelve positives, so assert the
    #    set is non-empty rather than trusting the comment.
    if okra is not None:
        t = Tree(okra)
        check(any(f.name in t.cfuncs for f in t.funcs),
              "cfunc entry points are indexed (they are where 5 of 12 positives live)")

    # 5. the negative controls that must be clean
    flagged = []
    for prefix in NEGATIVES:
        d = _find(pool, prefix)
        if d is None:
            continue
        s = _sweep(d)
        if s.hits:
            flagged.append("%s: %s" % (prefix,
                                       ["%s:%d" % (h[1], h[2]) for h in s.hits][:4]))
    check(not flagged, "the %d clean negative controls (%s) are unflagged or cleared by a "
                       "named rule" % (len(NEGATIVES), ", ".join(NEGATIVES)), flagged)

    # 5b. the triaged residue. Pinned, so noise cannot grow unnoticed and a genuine new
    #     row cannot hide inside an existing pile. Growth is a FAIL even though the rows
    #     themselves are accepted -- see TRIAGED for what each one is.
    #
    #     AND the prefix must identify ONE tree. `_find` returns the first match in pool
    #     order, so the moment round 8 staged `zlib-3.2.1` and `zlib-3.2.3` beside the
    #     calibrated `zlib-basecamp-patch-1.1.1`, the key `zlib-` started resolving to a
    #     tree nobody had triaged -- and the pin read as a regression in the gem it was
    #     calibrated on. An ambiguous key is not a smaller version of a working key; it is
    #     a silent substitution, so it is a FAIL in its own right.
    grew = []
    for prefix, expected in sorted(TRIAGED.items()):
        matches = [pathlib.Path(d) for d in pool
                   if pathlib.Path(d).name.startswith(prefix)]
        if not matches:
            continue
        if len(matches) > 1:
            grew.append("%s is ambiguous over %s -- pin the exact tree"
                        % (prefix, sorted(m.name for m in matches)))
            continue
        n = len(_hits(matches[0]))
        if n != expected:
            grew.append("%s %d != %d" % (prefix, n, expected))
    check(not grew, "the triaged residue is unchanged: "
          + ", ".join("%s %d" % (k, v) for k, v in sorted(TRIAGED.items())), grew)

    # 5d. EVERY NAMED FIXTURE HAS TO RESOLVE. The negative controls, the triaged pins and the
    #     paired red/green trees are all reached through `if d is None: continue`, so a pool
    #     missing one of them ran a SMALLER suite and still printed PASS. The 12 positives
    #     were already asserted present and the rmagick probe already aborts; these were the
    #     silent half. A missing fixture is now a FAIL, as it is in predicate A.
    PAIRED = ["bootsnap-1.23.0", "trilogy-bc", "trilogy-green", "trilogy-2.12.6",
              "cgi-", "okra-", "mysql2-0.5.6", "racc-"]
    absent_fx = [p for p in list(NEGATIVES) + list(TRIAGED) + PAIRED
                 if _find(pool, p) is None]
    check(not absent_fx, "every named fixture resolved (%d negative, %d triaged, %d paired)"
          % (len(NEGATIVES), len(TRIAGED), len(PAIRED)), absent_fx)

    # 5c. mysql2's rows are the round-7 finding, not noise, and the assertion says which
    #     ones: the five connect-args stores that sit across rb_thread_call_without_gvl
    #     with no RB_GC_GUARD on any of them.
    m2 = _find(pool, "mysql2-0.5.6")
    if m2 is not None:
        conn = [h for h in _hits(m2) if "client.c" in h[1]
                and "rb_mysql_connect" in h[3]]
        check(len(conn) == 5,
              "mysql2 0.5.6: 5 connect-arg stores held across the nogvl connect "
              "(the trilogy #312 shape, unguarded)",
              ["%s:%d" % (h[1], h[2]) for h in conn])

    # 6. racc is the "zero for the right reason" control. Its zero must be `derive 0`,
    #    not `hit 0 after N discharges`: those two are indistinguishable without the
    #    counters, and round 4's `*: 0 suspects` on an unexpanded glob is the precedent.
    racc = _find(pool, "racc-")
    if racc is not None:
        rr = _sweep(racc)
        check(rr.files > 0 and rr.funcs > 0 and not rr.derivations,
              "racc: zero for the right reason -- %d files, %d fns, %d derivations"
              % (rr.files, rr.funcs, len(rr.derivations)))

    # 7. bootsnap: the VERSION BOUNDARY is the discriminator. 1.23.0 does the
    #    FilePathValue in the cfunc entry point, so the frame that converts is the frame
    #    that reads; 1.24.x moved it down into bs_cache_path and created the defect.
    b5, b3 = _find(pool, "bootsnap-1.24.5"), _find(pool, "bootsnap-1.23.0")
    if b5 and b3:
        h5 = [h for h in _hits(b5) if "bootsnap.c" in h[1]]
        h3 = [h for h in _hits(b3) if "bootsnap.c" in h[1]]
        check(len(h5) > len(h3),
              "bootsnap version boundary: 1.24.5 flags more than 1.23.0 (%d vs %d)"
              % (len(h5), len(h3)))

    # 8. trilogy: the best red/green pair in the corpus -- the same function, one with
    #    RB_GC_GUARD and one without.
    #
    #    WHAT THIS ASSERTS, AND WHAT IT DOES NOT -- restated in round 8, because the
    #    difference was being read the wrong way round. It asserts that the `guarded`
    #    discharge fires: 13 rows with no RB_GC_GUARD, 0 with one. That is a red/green pair
    #    for GUARD PRESENCE and for nothing else.
    #
    #    It was being cited as evidence that the 13 rows are false positives -- that
    #    `trilogy_sock_new` "strdups every option before the GVL is released", so the
    #    pointers are dead by then. **That reading is withdrawn.** `trilogy_sock_new` opens
    #    with `xmalloc(sizeof(struct trilogy_sock))` and every `xstrdup` is itself an
    #    `xmalloc`; under `-DTRILOGY_XALLOCATOR` -- which 2.12.6's extconf.rb sets and the
    #    fork's does not -- `xmalloc` comes from `trilogy_xallocator.h`, whose whole
    #    contents are `#include <ruby.h>`. Settled on the ARTIFACT, per this file's own
    #    rule about never settling it on the header:
    #
    #        upstream main / 2.12.x   nm -u  =>  _ruby_xmalloc _ruby_xcalloc _ruby_xrealloc
    #        the fork (f664f22)       nm -u  =>  _malloc
    #
    #    So on 2.12.x the copy is preceded by a compacting-GC opportunity with all thirteen
    #    interior pointers live in the caller's stack struct, and the window is WIDER than
    #    reported, not absent. `copies-in-callee` is deliberately built to refuse this case
    #    (the copy must dominate), and it does refuse it -- asserted below, so that a future
    #    relaxation of that rule fails here instead of silently clearing 13 real rows.
    tb, tg = _find(pool, "trilogy-bc"), _find(pool, "trilogy-green")
    if tb and tg:
        hb = [h for h in _hits(tb) if "cext.c" in h[1]]
        hg = [h for h in _hits(tg) if "cext.c" in h[1]]
        check(len(hb) == 13 and len(hg) == 0,
              "trilogy red/green pair: the guarded rule is the discriminator -- "
              "bc flags %d, green flags %d" % (len(hb), len(hg)),
              "expected 13/0, got %d/%d" % (len(hb), len(hg)))
    t212 = _find(pool, "trilogy-2.12.6")
    if t212:
        h212 = [h for h in _hits(t212)
                if "cext.c" in h[1] and "rb_trilogy_connect" in h[3]]
        check(len(h212) == 13,
              "trilogy 2.12.6: copies-in-callee does NOT clear rb_trilogy_connect -- the "
              "strdups are preceded by ruby_xmalloc, so the copy does not dominate "
              "(%d rows stand)" % len(h212),
              "expected 13, got %d" % len(h212))

    # 8b/8c. GENERATED RED: `RSTRING_GETMEM` WRITES ITS POINTER, IT DOES NOT RETURN ONE.
    #
    #    The corpus cannot host this control. Every RSTRING_GETMEM site in it is in a tree
    #    that is either pinned residue or a positive control for something else, so a green
    #    suite would have stayed green with the macro's output argument untracked -- which is
    #    what it did, for eight rounds. The fixture is written here in full.
    #
    #    The window is a FLAG, not a second file. Both trees derive with the macro; the red
    #    puts `rb_funcall(GC.compact)` between the derive and the read of `p` and the control
    #    does not. So the check measures window participation and not the macro's name.
    #    `probe_escape` rides along in both, because the escape half needs no window at all.
    #
    #    AND THE COUNTERS ARE ASSERTED, NOT ONLY THE HIT COUNT. If the output argument stops
    #    being aliased the derivation is still counted and the row discharges `no-window` --
    #    `derive 1/1 -> hit 0`, a clean sheet with a full funnel. If the INDEX regresses
    #    instead, `derive` goes to 0. Those are different failures and they must not present
    #    the same way, so both numbers are pinned.
    getmem_c = ("#include <ruby.h>\n"
                "\n"
                "static VALUE\n"
                "probe_window(VALUE self, VALUE str)\n"
                "{\n"
                "    const char *p;\n"
                "    long len;\n"
                "    int n;\n"
                "    RSTRING_GETMEM(str, p, len);\n"
                "%s"
                "    n = p[len - 1];\n"
                "    return INT2FIX(n);\n"
                "}\n"
                "\n"
                "static VALUE\n"
                "probe_escape(VALUE self, VALUE str)\n"
                "{\n"
                "    const char *q;\n"
                "    long len;\n"
                "    RSTRING_GETMEM(str, q, len);\n"
                "    return rb_str_new(q, len);\n"
                "}\n"
                "\n"
                "void Init_probe(void)\n"
                "{\n"
                "    rb_define_method(rb_cObject, \"win\", probe_window, 1);\n"
                "    rb_define_method(rb_cObject, \"esc\", probe_escape, 1);\n"
                "}\n")
    window_line = "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
    gred = _sweep(_synth("fx-getmem-red", {"ext/probe.c": getmem_c % window_line}))
    gctl = _sweep(_synth("fx-getmem-control", {"ext/probe.c": getmem_c % ""}))
    check(gred.funcs == 3 and len(gred.derivations) == 2 and len(gred.hits) == 2
          and {h[0] for h in gred.hits} == {"HELD-ACROSS-WINDOW", "ESCAPES-BY-RETURN"},
          "RSTRING_GETMEM red: the macro's OUTPUT argument is the derived pointer, so "
          "both the window and the escape are seen",
          "funcs %d, derive %d, hits %s" % (gred.funcs, len(gred.derivations),
                                            [(h[0], h[2]) for h in gred.hits]))
    check(gctl.funcs == 3 and len(gctl.derivations) == 2 and len(gctl.hits) == 1
          and gctl.hits[0][0] == "ESCAPES-BY-RETURN"
          and any(d[0] == "no-window" for d in gctl.discharges),
          "RSTRING_GETMEM control: with the window flag off the same derivation "
          "discharges no-window -- the row is produced by the window, not by the macro",
          "funcs %d, derive %d, hits %s, discharges %s"
          % (gctl.funcs, len(gctl.derivations), [(h[0], h[2]) for h in gctl.hits],
             [d[0] for d in gctl.discharges]))

    # 8d/8e. GENERATED RED: A NAMESPACE OR LINKAGE BLOCK IS NOT A FUNCTION BODY.
    #
    #    Before the port, a definition inside `namespace X { ... }` or `extern "C" { ... }`
    #    sat at nonzero brace depth and _index_funcs skipped it. The measured red on this
    #    fixture is `0 fn(s) | derive 0/0 -> hit 0` -- the shape of zero that racc's control
    #    exists to distinguish from a real one, produced here by an EMPTY INDEX. So the
    #    counters are the assertion and the hit count is the corollary.
    #
    #    The wrapper is the flag. The same source unwrapped must give the same funnel and
    #    the same row, because a namespace has no storage duration of its own: that is the
    #    claim the port makes, and comparing the two trees is the only thing that tests it
    #    rather than restating it.
    def _ns_tree(wrapped):
        ns_open, ns_close = ("namespace probe {\n", "}\n") if wrapped else ("", "")
        ln_open, ln_close = ("extern \"C\" {\n", "}\n") if wrapped else ("", "")
        return _synth("fx-ns-%s" % ("wrapped" if wrapped else "flat"), {"ext/probe.cpp":
            "#include <ruby.h>\n\n" + ns_open +
            "static VALUE\n"
            "held_across_window(VALUE self, VALUE str)\n"
            "{\n"
            "    const char *p = RSTRING_PTR(str);\n"
            "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
            "    return rb_str_new(p, RSTRING_LEN(str));\n"
            "}\n" + ns_close + "\n" + ln_open +
            "void Init_probe(void)\n"
            "{\n"
            "    rb_define_method(rb_cObject, \"held\", held_across_window, 1);\n"
            "}\n" + ln_close})
    nsw, nsf = _sweep(_ns_tree(True)), _sweep(_ns_tree(False))
    check(nsw.funcs == 2 and len(nsw.derivations) == 1 and len(nsw.hits) == 1
          and nsw.hits[0][0] == "ESCAPES-BY-RETURN",
          "namespace red: definitions inside `namespace X {` and `extern \"C\" {` are "
          "indexed -- an unported walk reports 0 fn(s), 0 derivations, a clean sheet on an "
          "empty index",
          "funcs %d, derive %d, hits %s" % (nsw.funcs, len(nsw.derivations),
                                            [(h[0], h[2]) for h in nsw.hits]))
    check((nsw.funcs, len(nsw.derivations), len(nsw.with_window),
           [(h[0], h[1]) for h in nsw.hits])
          == (nsf.funcs, len(nsf.derivations), len(nsf.with_window),
              [(h[0], h[1]) for h in nsf.hits]),
          "namespace transparency: the same source wrapped and unwrapped gives the same "
          "funnel and the same row",
          "wrapped %d/%d/%d vs flat %d/%d/%d"
          % (nsw.funcs, len(nsw.derivations), len(nsw.hits),
             nsf.funcs, len(nsf.derivations), len(nsf.hits)))

    # 8f/8g. GENERATED RED: A TRAILING ATTRIBUTE IS NOT THE END OF THE DEFINITION.
    #
    #    `bad(VALUE str) __attribute__((noinline)) { ... }` is valid C and C++ may write
    #    `noexcept` in the same place. The walk skipped whitespace only, never reached the
    #    `{`, and dropped the whole function -- `0 fn(s) | derive 0/0 -> hit 0`, the empty
    #    index again. So the counters are the assertion; the hit count is the corollary.
    #    The attribute is the FLAG: the same source with and without it must give the same
    #    funnel and the same row, which is the claim skip_post_declarator() makes.
    def _attr_tree(kind):
        tail = {"attr": " __attribute__((noinline))", "noexcept": " noexcept",
                "flat": ""}[kind]
        return _synth("fx-attr-%s" % kind, {"ext/probe.cpp":
            "#include <ruby.h>\n\n"
            "static VALUE\n"
            "held_across_window(VALUE self, VALUE str)" + tail + "\n"
            "{\n"
            "    const char *p = RSTRING_PTR(str);\n"
            "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
            "    return rb_str_new(p, RSTRING_LEN(str));\n"
            "}\n\n"
            "void Init_probe(void)\n"
            "{\n"
            "    rb_define_method(rb_cObject, \"held\", held_across_window, 1);\n"
            "}\n"})
    aa, an, af = (_sweep(_attr_tree(k)) for k in ("attr", "noexcept", "flat"))
    check(aa.funcs == 2 and len(aa.derivations) == 1 and len(aa.hits) == 1
          and aa.hits[0][0] == "ESCAPES-BY-RETURN",
          "trailing-attribute red: a definition carrying `__attribute__((...))` between the "
          "parameter list and the body is indexed -- the unfixed walk reports 0 fn(s), "
          "0 derivations, a clean sheet on an empty index",
          "funcs %d, derive %d, hits %s" % (aa.funcs, len(aa.derivations),
                                            [(h[0], h[2]) for h in aa.hits]))
    check(all((s.funcs, len(s.derivations), len(s.with_window),
               [(h[0], h[1]) for h in s.hits])
              == (af.funcs, len(af.derivations), len(af.with_window),
                  [(h[0], h[1]) for h in af.hits]) for s in (aa, an)),
          "post-declarator transparency: `__attribute__((noinline))` and C++ `noexcept` give "
          "the same funnel and the same row as the bare declarator",
          "attr %d/%d/%d, noexcept %d/%d/%d, flat %d/%d/%d"
          % (aa.funcs, len(aa.derivations), len(aa.hits),
             an.funcs, len(an.derivations), len(an.hits),
             af.funcs, len(af.derivations), len(af.hits)))

    # 8h/8i. GENERATED RED: POINTER IDENTITY SURVIVES A LOCAL-TO-LOCAL COPY.
    #
    #    `p = RSTRING_PTR(str); q = p; <window>; read q;` -- tracking only `p` reads `q = p`
    #    as `p`'s final use, so the window scan stops short of the window and the row
    #    discharges `no-window`. A clean sheet on a live derivation, which is the direction
    #    this predicate is most biased against. THE WINDOW IS THE FLAG, not the copy: the
    #    control keeps the copy and the read through it and drops only the `rb_funcall`, so
    #    the check measures window participation and cannot pass on the copy alone.
    alias_c = ("#include <ruby.h>\n\n"
               "static VALUE\n"
               "second_hand(VALUE self, VALUE str)\n"
               "{\n"
               "    const char *p = RSTRING_PTR(str);\n"
               "    const char *q = p;\n"
               "    int n;\n"
               "%s"
               "    n = q[0];\n"
               "    return INT2FIX(n);\n"
               "}\n\n"
               "void Init_probe(void)\n"
               "{\n"
               "    rb_define_method(rb_cObject, \"sh\", second_hand, 1);\n"
               "}\n")
    ared = _sweep(_synth("fx-alias-red", {"ext/probe.c": alias_c % window_line}))
    actl = _sweep(_synth("fx-alias-control", {"ext/probe.c": alias_c % ""}))
    check(ared.funcs == 2 and len(ared.derivations) == 1 and len(ared.with_window) == 1
          and len(ared.hits) == 1 and ared.hits[0][0] == "HELD-ACROSS-WINDOW",
          "alias-chain red: a read through a SECOND pointer local is still a read of the "
          "buffer, so the window between the copy and it counts",
          "funcs %d, derive %d, win %d, hits %s"
          % (ared.funcs, len(ared.derivations), len(ared.with_window),
             [(h[0], h[2]) for h in ared.hits]))
    check(actl.funcs == 2 and len(actl.derivations) == 1 and len(actl.with_window) == 0
          and not actl.hits and any(d[0] == "no-window" for d in actl.discharges),
          "alias-chain control: with the window flag off the same copy-and-read discharges "
          "no-window -- the row is produced by the window, not by the alias set",
          "funcs %d, derive %d, win %d, hits %s, discharges %s"
          % (actl.funcs, len(actl.derivations), len(actl.with_window),
             [(h[0], h[2]) for h in actl.hits], [d[0] for d in actl.discharges]))

    # 8h2. GENERATED RED AND GREEN: AN ALIAS STOPS CARRYING WHEN IT IS REASSIGNED (#29:1).
    #
    #    The mirror of 8h/8i, one review later, and it is the failure that propagation
    #    bought: a name that HELD the pointer is not a name that holds it. The window here
    #    is bounded by a read of `q` AFTER `q = other`, so it is a window measured on
    #    something else -- a SPURIOUS window, which is what this predicate grows where
    #    predicate B grows a spurious escape.
    #
    #    THREE ARMS, AND THE SECOND IS WHY THE KILL IS `DOMINATING_WRITE`:
    #      live      the alias is never reassigned          -- must still be a hit
    #      killed    `q = other;` before the read           -- the read is of `other`
    #      cond      `if (n) { q = other; }` before it      -- the write need not run, so
    #                                                          the row must SURVIVE. A
    #                                                          path-insensitive kill here
    #                                                          DISCHARGES a live row, which
    #                                                          is the inverted polarity the
    #                                                          shared predicate takes an
    #                                                          argument for.
    #    The funnel is asserted in all three: a regression that stops indexing the function
    #    prints `0 fn(s) | derive 0/0` and would read as two of the three passing.
    kill_c = ("#include <ruby.h>\n\n"
              "static VALUE\n"
              "rebound(VALUE self, VALUE str, VALUE n)\n"
              "{\n"
              "    const char *p = RSTRING_PTR(str);\n"
              "    const char *q = p;\n"
              "    const char *other = \"safe\";\n"
              "    int c;\n"
              "%s"
              "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
              "%s"
              "    c = q[0];\n"
              "    return INT2FIX(c);\n"
              "}\n\n"
              "void Init_probe(void)\n"
              "{\n"
              "    rb_define_method(rb_cObject, \"rb\", rebound, 2);\n"
              "}\n")
    kill_arms = {
        "live":   ("", ""),
        "killed": ("", "    q = other;\n"),
        "cond":   ("", "    if (n) { q = other; }\n"),
    }
    kv = {t: _sweep(_synth("fx-aliaskill-%s" % t, {"ext/probe.c": kill_c % arms}))
          for t, arms in kill_arms.items()}
    check(all(kv[t].funcs == 2 and len(kv[t].derivations) == 1 for t in kill_arms)
          and [len(kv[t].hits) for t in ("live", "killed", "cond")] == [1, 0, 1]
          and [len(kv[t].with_window) for t in ("live", "killed", "cond")] == [1, 0, 1]
          and any(d[0] == "no-window" for d in kv["killed"].discharges),
          "alias-kill red (#29 item 1): a read of `q` after `q = other;` no longer bounds "
          "the window -- the row it produced was a window measured on a different value, "
          "and with the only carrier overwritten there is nothing live across the window. "
          "A CONDITIONAL reassignment still hits, because a kill here loses a finding",
          [(t, kv[t].funcs, len(kv[t].derivations), len(kv[t].with_window),
            sorted(h[0] for h in kv[t].hits), sorted(d[0] for d in kv[t].discharges))
           for t in kill_arms])

    # 8h3. THE DOMINANCE TEST HAS TWO HOLES A BRACE COUNT CANNOT SEE (#29 item 1).
    #
    #    Both were found by the CORPUS and not by this suite, and both are over-clears:
    #    the first cost predicate B eight openssl RETURNS-INTERIOR rows, the second cost
    #    this predicate date_parse.c:230 and yajl_ext.c:255's RUBY-REENTRY window. They are
    #    pinned here because "the corpus stayed green" is not a test of a constant added to
    #    keep the corpus green -- both rules live in tu_scope and both are asserted here,
    #    including the one whose only corpus witness is another predicate's:
    #
    #      bare-arm   `if (!a1obj) a1obj = f(...);`  -- the write has NO BLOCK, so its
    #                 innermost enclosing block is the whole function and it reads as
    #                 unconditional. openssl's obj_to_asn1obj, exactly.
    #      switch-arm `case 0: p = ...; break; case 2: p = ...;` -- two arms share ONE pair
    #                 of braces, so the second arm's write is in the same block as the
    #                 first arm's derivation. yajl's encoder, and `goto` for date's.
    #
    #    THE UNCONDITIONAL ARM IS THE FLAG: `plain` and `same-arm` write the same name in
    #    the same block with nothing in front of it, and must still discharge, or the two
    #    rules above have simply switched the kill off.
    dom_c = ("#include <ruby.h>\n\n"
             "static VALUE\n"
             "dom(VALUE self, VALUE str, VALUE n)\n"
             "{\n"
             "    const char *p = RSTRING_PTR(str);\n"
             "    const char *other = \"safe\";\n"
             "    int c;\n"
             "%s"
             "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
             "    c = p[0];\n"
             "    return INT2FIX(c);\n"
             "}\n\n"
             "void Init_probe(void)\n"
             "{\n"
             "    rb_define_method(rb_cObject, \"d\", dom, 2);\n"
             "}\n")
    dom_arms = {
        "plain":      "    p = other;\n",
        "bare-arm":   "    if (n) p = other;\n",
        "bare-else":  "    if (!n) c = 1;\n    else p = other;\n",
    }
    dm = {t: _sweep(_synth("fx-dom-%s" % t, {"ext/probe.c": dom_c % arm}))
          for t, arm in dom_arms.items()}

    # 8h5. `self_derived` IS DOMINATING_WRITE ONLY, AND THIS STAGE IS WHY (#30 review).
    #
    #    A POINTER walked onto itself still points into the same object, so the alias set is
    #    right to keep carrying it. A `VALUE` assigned from itself does NOT -- it names a new
    #    object -- and this predicate's liveness stage asks the same shared question about
    #    the SOURCE under ANY_WRITE. Applied in both modes, the self-assignment stopped
    #    counting as a write, the later `*code = val` was credited to the ORIGINAL String,
    #    `last-use-after` fired, and the row DISCHARGED. An over-clear, the direction that
    #    loses findings silently, and it shipped once on this branch.
    #
    #    THE FIXTURE IS iconv's `strip_glibc_option` VERBATIM, and that is deliberate: three
    #    synthetic spellings of "self-assign, then read the source" all failed to reproduce
    #    it, discharging through `copies-in-callee` or `no-window` instead. What the real
    #    function has that they lacked is the last deref of `ptr` sitting INSIDE the
    #    allocating call (`slash - ptr` as an argument to `rb_str_subseq`), which is what
    #    puts the source read after it. A fixture that cannot reproduce the shape is not a
    #    smaller version of it.
    src_c = ('#include <ruby.h>\n\nstatic VALUE\n'
             'strip_glibc_option(VALUE *code)\n{\n'
             '    VALUE val = StringValue(*code);\n'
             '    const char *ptr = RSTRING_PTR(val), *pend = RSTRING_END(val);\n'
             '    const char *slash = memchr(ptr, 47, pend - ptr);\n\n'
             '    if (slash && slash < pend - 1 && slash[1] == 47) {\n'
             '\tVALUE opt = rb_str_subseq(val, slash - ptr, pend - slash);\n'
             '\tval = rb_str_subseq(val, 0, slash - ptr);\n'
             '\t*code = val;\n\treturn opt;\n    }\n    return 0;\n}\n\n'
             'void Init_probe(void)\n{\n'
             '    rb_define_method(rb_cObject, "s", strip_glibc_option, 1);\n}\n')
    sm = _sweep(_synth("fx-src-dup", {"ext/probe.c": src_c}))
    check(len(sm.hits) == 1 and any(d[0] == "no-window" for d in sm.discharges),
          "8h5 RED (#30 review): a VALUE assigned from itself names a NEW object, so it "
          "still kills the source's liveness -- iconv's strip_glibc_option keeps its "
          "HELD-ACROSS-WINDOW row, and the tree's unrelated no-window discharge still "
          "fires, so a sweep that simply discharges nothing fails here too",
          [len(sm.hits), sorted(h[0] for h in sm.hits),
           sorted(d[0] for d in sm.discharges)])
    # THE SWITCH SHAPE NEEDS ALL THREE OFFSETS INSIDE THE SWITCH BODY, and getting that
    # wrong is how a fixture ends up asserting nothing. The dominance test already skips
    # any write whose innermost block does not contain BOTH the derivation and the
    # occurrence, so a derivation before the switch, or a read after it, is saved by the
    # brace count alone -- the first cut of this fixture put the read after the closing
    # brace and passed with `straight_line` deleted. Derive in one arm, kill in a second,
    # read in a third, and the brace count says "dominates" for all three: only the
    # `break` between the arms says control need not arrive.
    #
    # yajl's `yajl_encode_part` is the shape, byte for byte -- `cptr = RSTRING_PTR(str)`
    # in the T_FLOAT arm, rewritten in the T_STRING and T_SYMBOL arms, read again in the
    # `default` arm after an `rb_funcall`. `same-arm` is the flag: the same write with no
    # transfer in front of it still kills, so the rule is narrowed and not switched off.
    sw_c = ("#include <ruby.h>\n\n"
            "static VALUE\n"
            "sw(VALUE self, VALUE str, VALUE n)\n"
            "{\n"
            "    const char *p = \"x\";\n"
            "    const char *other = \"safe\";\n"
            "    int c = 0;\n"
            "    switch (FIX2INT(n)) {\n"
            "      case 0:\n"
            "        p = RSTRING_PTR(str);\n"
            "%s"
            "        break;\n"
            "%s"
            "      default:\n"
            "        rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
            "        c = p[0];\n"
            "        break;\n"
            "    }\n"
            "    return INT2FIX(c);\n"
            "}\n\n"
            "void Init_probe(void)\n"
            "{\n"
            "    rb_define_method(rb_cObject, \"s\", sw, 2);\n"
            "}\n")
    sw_arms = {
        "no-write":  ("", ""),
        "other-arm": ("", "      case 2:\n        p = other;\n        break;\n"),
        "same-arm":  ("        p = other;\n", ""),
    }
    sw = {t: _sweep(_synth("fx-switch-%s" % t, {"ext/probe.c": sw_c % arm}))
          for t, arm in sw_arms.items()}
    check(all(dm[t].funcs == 2 for t in dom_arms)
          and len(dm["plain"].hits) == 0
          and any(d[0] == "no-window" for d in dm["plain"].discharges)
          and [len(dm[t].hits) for t in ("bare-arm", "bare-else")] == [1, 1]
          and all(sw[t].funcs == 2 for t in sw_arms)
          and [len(sw[t].hits) for t in ("no-write", "other-arm")] == [1, 1]
          and len(sw["same-arm"].hits) == 0
          and any(d[0] == "no-window" for d in sw["same-arm"].discharges),
          "#29 item 1, the dominance holes: a write in a BRACELESS `if`/`else` arm and a "
          "write in another `switch` case do not kill the alias -- neither has a block of "
          "its own to be conditional in. The same write at the frame's top level, and the "
          "same write in the deriving arm with no `break` in front of it, still do, so the "
          "kill is narrowed and not switched off",
          [(t, dm[t].funcs, len(dm[t].hits), sorted(d[0] for d in dm[t].discharges))
           for t in dom_arms]
          + [(t, sw[t].funcs, len(sw[t].hits), sorted(d[0] for d in sw[t].discharges))
             for t in sw_arms])

    # 8h4. AND A MEMBER OF THE SAME NAME IS NOT THE NAME. `parser->state.start = start;`
    #    counted as a write to the local `start`: `>` was already in writes()' lookbehind
    #    so the `->` spelling was excluded and the `.` spelling was not. One character
    #    between the two halves of one rule, and under the alias kill it dropped two real
    #    ESCAPES-INTO-CONTAINER rows in json's cResumableParser_feed.
    memb_c = ("#include <ruby.h>\n\n"
              "struct st { const char *start; const char *end; };\n"
              "static struct st g_state;\n\n"
              "static VALUE\n"
              "feed(VALUE self, VALUE str)\n"
              "{\n"
              "    const char *start = RSTRING_PTR(str);\n"
              "%s"
              "    g_state.start = start;\n"
              "    g_state.end = start + 1;\n"
              "    return Qnil;\n"
              "}\n\n"
              "void Init_probe(void)\n"
              "{\n"
              "    rb_define_method(rb_cObject, \"f\", feed, 1);\n"
              "}\n")
    mb = {t: _sweep(_synth("fx-memb-%s" % t, {"ext/probe.c": memb_c % arm}))
          for t, arm in {"member": "", "local": "    start = \"safe\";\n"}.items()}
    mb_esc = [d for d in (mb["member"].hits[0][4] if mb["member"].hits else [])
              if d.startswith("escape:")]
    check(mb["member"].funcs == 2 and len(mb["member"].hits) == 1 and len(mb_esc) == 2
          and mb["local"].funcs == 2 and not mb["local"].hits,
          "#29 item 1: `g_state.start = start;` is a write to the MEMBER, not to the local "
          "-- BOTH stores are still ESCAPES-INTO-STATIC, including the one AFTER that "
          "statement's `;`, which is where json lost two rows. A real `start = \"safe\";` "
          "before them still kills the alias",
          [(t, mb[t].funcs, [h[0] for h in mb[t].hits],
            sorted(d[0] for d in mb[t].discharges)) for t in mb] + mb_esc)

    # 8j/8k. GENERATED RED AND GREEN: A REBOUND GUARD VARIABLE GUARDS THE WRONG OBJECT.
    #
    #    `guarded` DISCHARGES, so this is the direction that loses findings rather than the
    #    one that adds noise, and a rule that stops clearing needs the green as much as the
    #    red: a genuine RB_GC_GUARD must still discharge, or the fix is just a deletion.
    #    Two rebind shapes, because they were one defect with two spellings -- the guard COPY
    #    overwritten, and the SOURCE itself rebound under a guard on its own name.
    guard_c = ("#include <ruby.h>\n\n"
               "static VALUE\n"
               "guarded_probe(VALUE self, VALUE str, VALUE other)\n"
               "{\n"
               "    VALUE %s = str;\n"
               "    VALUE out;\n"
               "    const char *p = RSTRING_PTR(str);\n"
               "%s"
               "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
               "    out = rb_str_new(p, 4);\n"
               "    RB_GC_GUARD(%s);\n"
               "    return out;\n"
               "}\n\n"
               "void Init_probe(void)\n"
               "{\n"
               "    rb_define_method(rb_cObject, \"g\", guarded_probe, 2);\n"
               "}\n")
    # (guard-variable name, what gets rebound, the name RB_GC_GUARD is given)
    guard_arms = [("copy-red", "guard", "    guard = other;\n", "guard"),
                  ("copy-green", "guard", "", "guard"),
                  ("src-red", "spare", "    str = other;\n", "str"),
                  ("src-green", "spare", "", "str")]
    gv = {tag: _sweep(_synth("fx-guard-%s" % tag,
                             {"ext/probe.c": guard_c % (nm, rebind, guarded)}))
          for tag, nm, rebind, guarded in guard_arms}
    reds = [gv["copy-red"], gv["src-red"]]
    greens = [gv["copy-green"], gv["src-green"]]
    check(all(len(s.hits) == 1 and not any(d[0] == "guarded" for d in s.discharges)
              and len(s.derivations) == 1 for s in reds),
          "guard-rebind red: `guard = str; guard = other; RB_GC_GUARD(guard)` and "
          "`str = other; RB_GC_GUARD(str)` both STAND -- the guard names a different object "
          "than the derive did",
          [(len(s.hits), [d[0] for d in s.discharges]) for s in reds])
    check(all(not s.hits and any(d[0] == "guarded" for d in s.discharges)
              and len(s.derivations) == 1 for s in greens),
          "guard-rebind green: with nothing rebound, a genuine RB_GC_GUARD after the last "
          "read still discharges -- the rule was narrowed, not deleted",
          [(len(s.hits), [d[0] for d in s.discharges]) for s in greens])

    # 8q. GENERATED REDS AND GREENS: THE PINNING-MARK DISCHARGE, AND THE ONE TOKEN IT TURNS
    #     ON. The rule this fixture exists for was WRITTEN DOWN in round 8 and deliberately
    #     not built in rounds 8 or 9, on the argument recorded above RULES: it clears rows
    #     on the strength of a single identifier, and a rule that cannot tell `rb_gc_mark`
    #     from `rb_gc_mark_movable` over-clears a whole class in one step, silently, in the
    #     unsafe direction. So the fixture is FIVE arms of one byte-identical tree, and
    #     three of the five are reds:
    #
    #       pinning       `rb_gc_mark(w->buf)` in the registered dmark      -> DISCHARGES
    #       helper        ...reached through `mark_fields(w)`               -> DISCHARGES
    #       movable       `rb_gc_mark_movable(w->buf)`, one suffix apart    -> STILL A HIT
    #       neither       the dmark pins `w->other` and never names `w->buf`-> STILL A HIT
    #       unregistered  the same pinning call, `.dmark` slot is NULL      -> STILL A HIT
    #
    #     `movable` is the load-bearing one and the reason for the two-round deferral: it is
    #     the *relocate* idiom, safe only with a `dcompact` calling rb_gc_location on every
    #     stored copy, which is a function this predicate never reads. A movable mark keeps
    #     the String alive and lets compaction move its bytes, which is the mobility half of
    #     this predicate's own charter -- so discharging it would clear the bug with the bug.
    #     `unregistered` is the second red and guards the other half of the claim: the
    #     discharge asserts that every GC-managed instance of the type has the field pinned,
    #     and only REGISTRATION makes that true. A helper full of `rb_gc_mark` that no dtype
    #     names marks nothing at all. `helper` is the green for the callee closure, which no
    #     corpus row needs today -- json's dmark delegates and zlib's `gzfile_mark` calls
    #     `zstream_mark`, but both reach the pinned fields by a registered route as well, so
    #     the closure fires nowhere in 99 trees and this is the only thing testing it.
    pin_c = ("#include <ruby.h>\n\n"
             "struct wrap { VALUE buf; VALUE other; };\n\n"
             "%s"
             "static void\n"
             "wrap_mark(void *p)\n"
             "{\n"
             "    struct wrap *w = p;\n"
             "%s"
             "}\n\n"
             "static const rb_data_type_t wrap_type = {\n"
             "    \"wrap\",\n"
             "    { %s, 0, 0, },\n"
             "    0, 0, RUBY_TYPED_FREE_IMMEDIATELY\n"
             "};\n\n"
             "static VALUE\n"
             "feed(VALUE self)\n"
             "{\n"
             "    struct wrap *w;\n"
             "    const char *p;\n"
             "    VALUE out;\n"
             "    TypedData_Get_Struct(self, struct wrap, &wrap_type, w);\n"
             "    p = RSTRING_PTR(w->buf);\n"
             "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
             "    out = rb_str_new(p, 4);\n"
             "    return out;\n"
             "}\n\n"
             "void Init_probe(void)\n"
             "{\n"
             "    rb_define_method(rb_cObject, \"f\", feed, 0);\n"
             "}\n")
    # (extra definitions before the dmark, the dmark's body, the dtype's mark slot)
    pin_arms = {
        "pinning":      ("", "    rb_gc_mark(w->buf);\n", "wrap_mark"),
        "movable":      ("", "    rb_gc_mark_movable(w->buf);\n", "wrap_mark"),
        "neither":      ("", "    rb_gc_mark(w->other);\n", "wrap_mark"),
        "unregistered": ("", "    rb_gc_mark(w->buf);\n", "NULL"),
        "helper":       ("static void\n"
                         "mark_fields(struct wrap *w)\n"
                         "{\n"
                         "    rb_gc_mark(w->buf);\n"
                         "}\n\n",
                         "    mark_fields(w);\n", "wrap_mark"),
    }
    pn = {t: _sweep(_synth("fx-pin-%s" % t, {"ext/probe.c": pin_c % a}))
          for t, a in pin_arms.items()}
    pin_green, pin_red = ("pinning", "helper"), ("movable", "neither", "unregistered")

    def _pinned(tag):
        return [d for d in pn[tag].discharges if d[0] == "pinning-mark"]
    # The FUNNEL is asserted on every arm and not only the hit count, for this file's
    # standing reason: an unparsed struct, an unindexed function and an unresolved member
    # path all end in `hit 0`, and only one of those is the rule firing. All five arms must
    # produce the same one derivation and the same one window; only the verdict may differ.
    check(all((pn[t].funcs, len(pn[t].derivations), len(pn[t].with_window))
              == (4 if t == "helper" else 3, 1, 1) for t in pin_arms)
          and all(not pn[t].hits and len(_pinned(t)) == 1 for t in pin_green)
          and all(len(pn[t].hits) == 1 and not _pinned(t) for t in pin_red),
          "pinning-mark red/green: `rb_gc_mark(w->buf)` in the type's REGISTERED dmark "
          "discharges the derive from `w->buf`, directly and through a callee -- and the "
          "byte-identical `rb_gc_mark_movable`, a dmark naming a DIFFERENT field, and the "
          "same pinning call under a NULL `.dmark` all leave the row standing. One suffix "
          "and one registration are the whole rule; a version that cannot see either "
          "over-clears the class in one step",
          [(t, pn[t].funcs, len(pn[t].derivations), len(pn[t].with_window),
            [h[0] for h in pn[t].hits], sorted(d[0] for d in pn[t].discharges))
           for t in sorted(pin_arms)])

    # 8r. AND THE CORPUS HAS THE MOVABLE ARM AFTER ALL -- zstd, which nobody had looked at
    #     for this. The synthetic arm above was written because no tree was believed to
    #     carry the shape; building the index found one, and it is better than the fixture:
    #
    #         static void streaming_decompress_mark(void *p) {
    #             struct streaming_decompress_t *sd = p;
    #         #ifdef HAVE_RB_GC_MARK_MOVABLE
    #             rb_gc_mark_movable(sd->buf);      /* the branch that compiles today */
    #         #else
    #             rb_gc_mark(sd->buf);              /* ...and the one that does not */
    #         #endif
    #         }
    #
    #     `strip_directives` keeps the code inside conditionals, so BOTH spellings name the
    #     same field in the same function and the index sees a pin and a movable mark on one
    #     key. Subtracting `movable` from `pinned` is the only thing that keeps
    #     streaming_decompress.c:133 -- `RSTRING_PTR(sd->buf)` with an `rb_str_new` before
    #     the read -- standing.
    #
    #     ROUND 11 CORRECTS WHAT THAT ROW IS. Round 10 called it "one of zstd's confirmed
    #     real rows"; it is not, and the claim is withdrawn rather than left to be cited.
    #     :133 is `TRIAGED["zstd-"]` residue. `sd->buf = rb_str_new(NULL,
    #     ZSTD_DStreamOutSize())` and `ZSTD_DStreamOutSize() == ZSTD_BLOCKSIZE_MAX ==
    #     131,072` bytes -- 213x the 616-byte embedded boundary, so the bytes are a malloc
    #     block compaction does not move -- and streaming_decompress.c:42 carries a real
    #     `dcompact` (`sd->buf = rb_gc_location(sd->buf)`). That is the complete *relocate*
    #     idiom, and it is the same regime as its sibling `sc->buf`, which precedents.md
    #     records as CLEARED BY EXECUTION (135,000 operations, ~1,700 compactions per run).
    #
    #     THE ASSERTION STANDS ON A DIFFERENT AND BETTER ARGUMENT, which is why it is not
    #     weakened along with the claim: the `#else` arm never reaches the compiler --
    #     `have_func('rb_gc_mark_movable')` is yes on 4.0.6 and 3.4.10 -- so a rule that let
    #     it answer would be discharging a live row out of DEAD CODE. "Do not read
    #     non-compiled source" needs no defect at :133 to be worth asserting, and the
    #     acceptance criterion is the INDEX (`movable` holds the key, `pinned` does not),
    #     with the row's survival as the visible consequence rather than as the point.
    zstd = _find(pool, "zstd-")
    if zstd is not None:
        zt = Tree(zstd)
        zkey = ("streaming_decompress_t", "buf")
        zrows = [h for h in _hits(zstd) if h[1].endswith("streaming_decompress.c")]
        check(zkey in zt.movable and zkey not in zt.pinned and len(zrows) == 1,
              "movable beats pinning ON THE CORPUS: zstd's streaming_decompress_mark spells "
              "both marks for `sd->buf` across an #ifdef, and the row derived from that "
              "field stands. The pinning spelling does not get to answer for the movable one",
              "movable=%s pinned=%s rows=%s"
              % (zkey in zt.movable, zkey in zt.pinned,
                 ["%s:%d" % (h[1], h[2]) for h in zrows]))

    # 8s. ...AND THE TABLE THAT DECIDES IT IS PREDICATE A's, ASSERTED EQUAL RATHER THAN
    #     ASSUMED EQUAL. A has graded pinning versus movable since round 5b and this file
    #     restates the two regexes rather than importing A's whole-tree index for them.
    #     Two files carrying one semantic table is exactly the shape tu_scope was extracted
    #     out of, so the copy is pinned by an assertion instead of by good intentions.
    #
    #     BOTH DIRECTIONS ARE ASSERTED, because agreement alone is satisfied by two files
    #     being wrong together -- which is the failure that matters here, since `movable`
    #     graded as `pin` is the over-clear the whole fixture above exists to prevent.
    #
    #     WHAT IS SHARED IS THE TABLE, NOT THE ANSWER, and round 11 had to say so out loud
    #     because a parallel audit proposed closing the `#ifdef` hole by having this rule
    #     read predicate A's verdict -- and then found that A cannot grade msgpack's
    #     `c->mapped_string` at all, since `msgpack_buffer_chunk_t` has no wrap site for A's
    #     walk to start from. That is a real gap in A and not a state THIS rule can be in:
    #     `_index_pins` walks REGISTERED MARK FUNCTIONS, not wrap sites, so it grades the
    #     key directly (`rb_gc_mark(c->mapped_string)`, buffer.c:122 -- measured present in
    #     `Tree.pinned` for both msgpack trees). It also joins on nothing: msgpack's two
    #     rows derive from the LOCAL `mapped_string`, which is the store side, so the key is
    #     in the index and clears no row. Two predicates keying off two different walks is
    #     what makes them independent; importing A's answer here would trade this rule's own
    #     coverage for A's, in the direction of clearing MORE rows.
    import sweep_unmarked as _pred_a
    prim_table = ["rb_gc_mark", "rb_gc_mark_movable", "rb_gc_mark_maybe",
                  "rb_gc_mark_and_move", "rb_gc_mark_locations", "RB_GC_MARK",
                  "RB_GC_MARK_MOVABLE", "rb_gc_location", "rb_gc_register_mark_object",
                  "rb_gc_mark_movable_ptr", "memcpy", "mark"]
    disagree = [(n, prim_kind(n), _pred_a.prim_kind(n)) for n in prim_table
                if prim_kind(n) != _pred_a.prim_kind(n)]
    absolute = (prim_kind("rb_gc_mark") == "pin"
                and prim_kind("rb_gc_mark_movable") == "movable"
                and prim_kind("rb_gc_mark_and_move") == "movable"
                and prim_kind("rb_gc_mark_maybe") == "pin"
                and prim_kind("rb_gc_register_mark_object") is None
                and prim_kind("memcpy") is None)
    check(not disagree and absolute,
          "the pinning/movable table agrees with predicate A's over %d spellings, AND "
          "grades the two that matter absolutely -- rb_gc_mark pins, rb_gc_mark_movable "
          "and rb_gc_mark_and_move do not" % len(prim_table),
          disagree or "absolute grades wrong")

    # 8t. GENERATED REDS AND GREENS: THE PIN'S LIFETIME IS THE WRAPPER'S, SO AN ESCAPE IS
    #     DISCHARGED ONLY WHERE THE POINTER CANNOT OUTLIVE IT. #32's P1 review finding, and
    #     the one that matters: round 10 shipped `pinning-mark` clearing EVERY escape on the
    #     strength of "the pin outlasts any window this predicate can classify". It does not.
    #     `rb_gc_mark(w->buf)` runs while the WRAPPER is reachable; a `char *` written into a
    #     file static outlives the wrapper, and the reviewer's own synthetic tree -- `saved =
    #     RSTRING_PTR(w->buf)` -- came back with zero hits under a rule that exists to find
    #     exactly that. A Class B use-after-free cleared by this file.
    #
    #     FIVE arms of one byte-identical tree, four of them red, and each red is a DIFFERENT
    #     way the destination fails to be storage inside the pinned object:
    #
    #       owned    `w->held = p`         same base, one `->`        -> DISCHARGES
    #       static   `saved = p`           file-scope slot            -> STILL A HIT
    #       caller   `o->held = p`         `o` is a pointer PARAMETER -> STILL A HIT
    #       peer     `w->peer->held = p`   same base, TWO `->`        -> STILL A HIT
    #       both     `w->held = p` AND a non-copying library call     -> STILL A HIT
    #
    #     `caller` and `peer` are the two the obvious tightenings would each miss, and they
    #     are why the rule is neither "the escape stores into a struct field" nor "the base
    #     identifier matches". `o->held = p` is the SAME `STORES-INTERIOR` spelling as the
    #     green, writing into an aggregate the caller owns and the wrapper does not; freeing
    #     the wrapper frees nothing of `*o`. `w->peer->held = p` starts at the same
    #     identifier the pin does and ends in a different object, whose lifetime is its own.
    #     `both` is the `all` in `escape_stays_inside`'s caller asserted as a row: a
    #     derivation that stores into the wrapper AND hands the pointer to a non-copying
    #     library has already dangled by the second one, and a rule reading `any` clears it.
    #
    #     THE GREEN IS ASSERTED WITH THE RULE OFF AS WELL. `owned` must be an ESCAPING row
    #     that the rule clears, not a row with no escape on it -- otherwise the fixture is
    #     re-testing round 10's `HELD-ACROSS-WINDOW` case under a new name and the narrowing
    #     is untested in the direction that matters. `--disable-rule pinning-mark` puts the
    #     ESCAPES-INTO-CONTAINER row back on every one of the five.
    esc_c = ("#include <ruby.h>\n\n"
             "struct peer { const char *held; };\n"
             "struct wrap { VALUE buf; const char *held; struct peer *peer; };\n"
             "struct out { const char *held; };\n\n"
             "static const char *saved;\n\n"
             "static void\n"
             "wrap_mark(void *p)\n"
             "{\n"
             "    struct wrap *w = p;\n"
             "    rb_gc_mark(w->buf);\n"
             "}\n\n"
             "static const rb_data_type_t wrap_type = {\n"
             "    \"wrap\",\n"
             "    { wrap_mark, 0, 0, },\n"
             "    0, 0, RUBY_TYPED_FREE_IMMEDIATELY\n"
             "};\n\n"
             "static void\n"
             "feed(VALUE self, struct out *o)\n"
             "{\n"
             "    struct wrap *w;\n"
             "    const char *p;\n"
             "    TypedData_Get_Struct(self, struct wrap, &wrap_type, w);\n"
             "    p = RSTRING_PTR(w->buf);\n"
             "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
             "%s"
             "}\n\n"
             "static VALUE\n"
             "entry(VALUE self)\n"
             "{\n"
             "    struct out o;\n"
             "    feed(self, &o);\n"
             "    return Qnil;\n"
             "}\n\n"
             "void Init_probe(void)\n"
             "{\n"
             "    rb_define_method(rb_cObject, \"f\", entry, 0);\n"
             "}\n")
    esc_arms = {
        "owned":  "    w->held = p;\n",
        "static": "    saved = p;\n",
        "caller": "    o->held = p;\n",
        "peer":   "    w->peer->held = p;\n",
        "both":   "    w->held = p;\n"
                  "    xmlReaderForMemory(p, 4, NULL, NULL, 0);\n",
    }
    def _esc_arm(tag, arm):
        root = _synth("fx-esc-%s" % tag, {"ext/probe.c": esc_c % arm})
        return _sweep(root), _hits(root, disabled=("pinning-mark",))
    er = {t: _esc_arm(t, a) for t, a in esc_arms.items()}
    esc_green, esc_red = ("owned",), ("static", "caller", "peer", "both")
    check(all((er[t][0].funcs, len(er[t][0].derivations), len(er[t][0].with_window))
              == (4, 1, 1) and er[t][1] for t in esc_arms)
          and all(not er[t][0].hits
                  and [d[0] for d in er[t][0].discharges] == ["pinning-mark"]
                  for t in esc_green)
          and all(len(er[t][0].hits) == 1 and not er[t][0].discharges
                  for t in esc_red),
          "pinning-mark escape lifetime red/green: a pin discharges an ESCAPE only where the "
          "destination is storage inside the pinned object -- `w->held = p` clears, while a "
          "file static, a pointer PARAMETER's field, a field of a SECOND object reached from "
          "the same base, and a qualifying store paired with a non-copying library call all "
          "leave the row standing. Every arm is an escaping row with the rule off; the pin's "
          "lifetime is the wrapper's, and round 10 cleared all four",
          [(t, er[t][0].funcs, len(er[t][0].derivations), len(er[t][0].with_window),
            [h[0] for h in er[t][0].hits], sorted(d[0] for d in er[t][0].discharges),
            [h[0] for h in er[t][1]])
           for t in sorted(esc_arms)])

    # ...AND THE DESTINATION TEST ITSELF, asserted directly rather than only through a row,
    # in the shape 8i/8j's minus-split table uses. Five spellings the row fixture cannot
    # reach without five more arms, and the two kinds that can never qualify however the
    # destination is spelled. `w->st.held` is the in-object member hop the corpus needs --
    # zlib's `z->stream.next_in` is that shape and is 44 of the 47 rows round 10 moved --
    # and `w->buf[0]` is refused because `[` cannot be told from indexing a POINTER member
    # without resolving the member's type, which is the recall this rule declines to spend.
    dest = {
        "own-field":   escape_stays_inside("w", "STORES-INTERIOR", "w->held = p"),
        "own-nested":  escape_stays_inside("w", "STORES-INTERIOR", "w->st.held = p"),
        "other-base":  escape_stays_inside("w", "STORES-INTERIOR", "o->held = p"),
        "second-hop":  escape_stays_inside("w", "STORES-INTERIOR", "w->peer->held = p"),
        "indexed":     escape_stays_inside("w", "STORES-INTERIOR", "w->buf[0] = p"),
        "bare-base":   escape_stays_inside("w", "STORES-INTERIOR", "w = p"),
        "by-return":   escape_stays_inside("w", "ESCAPES-BY-RETURN", "w->held = p"),
        "into-static": escape_stays_inside("w", "ESCAPES-INTO-STATIC", "w->held = p"),
    }
    check(dest == {"own-field": True, "own-nested": True, "other-base": False,
                   "second-hop": False, "indexed": False, "bare-base": False,
                   "by-return": False, "into-static": False},
          "owned-destination table: one pointer hop off the derivation's own base and then "
          "in-object member hops stay inside the pinned object; a second `->`, a `[`, a "
          "different base and a bare assignment do not -- and the four escape kinds that "
          "are not stores cannot qualify however the destination is spelled",
          sorted(dest.items()))

    # 8u. GENERATED REDS AND GREEN: A PIN THAT RUNS ONLY SOMETIMES IS NOT A PIN. #32's P2
    #     review finding. The index recorded every syntactic pin call reachable from a
    #     registered `dmark` as unconditional, so `if (w->pins) rb_gc_mark(w->buf);` entered
    #     `pinned` and cleared the row on the `!w->pins` path too -- where the String is
    #     marked by nothing and may be collected or relocated under a live `char *`.
    #
    #     FOUR arms, and the three reds are the three tu_scope rules `unconditional_mark`
    #     composes, one each, because each is a shape the other two pass:
    #
    #       always     `rb_gc_mark(w->buf);`                    -> DISCHARGES
    #       braceless  `if (w->pins) rb_gc_mark(w->buf);`       -> STILL A HIT  conditional_stmt
    #       braced     `if (w->pins) { rb_gc_mark(w->buf); }`   -> STILL A HIT  innermost_block
    #       returned   `if (!w->pins) return;` then the mark    -> STILL A HIT  straight_line
    #
    #     `braceless` is the reviewer's shape and the one a brace test cannot see: the arm has
    #     no block of its own, so the innermost enclosing block is the whole function and the
    #     mark reads as unconditional. `braced` is the mirror -- its head is `{`, so there is
    #     nothing for the arm test to read. `returned` is at depth zero with no conditional
    #     head at all, which is why neither of the other two sees it, and it is not synthetic:
    #     mysql2's `rb_mysql_stmt_mark` is `if (!stmt_wrapper) return;` followed by an
    #     unindented `rb_gc_mark(stmt_wrapper->client);` at statement.c:15.
    #
    #     ALL THREE ARE CORPUS-NEUTRAL, which is the whole reason they are here. The 33 pin
    #     keys the composition drops over the 99 trees are not keys any discharged row joins
    #     on, so a green corpus says nothing about whether the tightening happened -- the
    #     generated red is the only thing that does.
    cond_c = ("#include <ruby.h>\n\n"
              "struct wrap { VALUE buf; int pins; };\n\n"
              "static void\n"
              "wrap_mark(void *p)\n"
              "{\n"
              "    struct wrap *w = p;\n"
              "%s"
              "}\n\n"
              "static const rb_data_type_t wrap_type = {\n"
              "    \"wrap\",\n"
              "    { wrap_mark, 0, 0, },\n"
              "    0, 0, RUBY_TYPED_FREE_IMMEDIATELY\n"
              "};\n\n"
              "static VALUE\n"
              "feed(VALUE self)\n"
              "{\n"
              "    struct wrap *w;\n"
              "    const char *p;\n"
              "    VALUE out;\n"
              "    TypedData_Get_Struct(self, struct wrap, &wrap_type, w);\n"
              "    p = RSTRING_PTR(w->buf);\n"
              "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
              "    out = rb_str_new(p, 4);\n"
              "    return out;\n"
              "}\n\n"
              "void Init_probe(void)\n"
              "{\n"
              "    rb_define_method(rb_cObject, \"f\", feed, 0);\n"
              "}\n")
    cond_arms = {
        "always":    "    rb_gc_mark(w->buf);\n",
        "braceless": "    if (w->pins) rb_gc_mark(w->buf);\n",
        "braced":    "    if (w->pins) {\n        rb_gc_mark(w->buf);\n    }\n",
        "returned":  "    if (!w->pins) return;\n    rb_gc_mark(w->buf);\n",
    }
    cn = {t: _sweep(_synth("fx-cond-%s" % t, {"ext/probe.c": cond_c % a}))
          for t, a in cond_arms.items()}
    cond_red = ("braceless", "braced", "returned")
    check(all((cn[t].funcs, len(cn[t].derivations), len(cn[t].with_window)) == (3, 1, 1)
              for t in cond_arms)
          and not cn["always"].hits
          and [d[0] for d in cn["always"].discharges] == ["pinning-mark"]
          and all(len(cn[t].hits) == 1 and not cn[t].discharges for t in cond_red),
          "conditional-pin red/green: a mark that runs on EVERY execution of the dmark "
          "pins, and the same call behind a braceless `if`, inside a braced `if`, and after "
          "an early `return` does not -- three shapes, three tu_scope rules, each one a "
          "shape the other two read as unconditional. Corpus-neutral, so this fixture is "
          "the only thing that can tell the tightening from no tightening",
          [(t, cn[t].funcs, len(cn[t].derivations), len(cn[t].with_window),
            [h[0] for h in cn[t].hits], sorted(d[0] for d in cn[t].discharges))
           for t in sorted(cond_arms)])

    # 8v. GENERATED REDS AND GREENS: A NAME THAT IS REBOUND STOPS DENOTING WHAT THE
    #     DISCHARGE LOOKED IT UP UNDER. #32's first and second review findings, and they
    #     are ONE mechanism -- a write, after the derivation, to a name the clearance
    #     depends on -- which is why this is one fixture and one function and not two.
    #
    #       stable       nothing rebound                        -> DISCHARGES
    #       field        `w->buf = other;`                      -> STILL A HIT  (path leg)
    #       base         `w = twin;` before `w->held = p`       -> STILL A HIT  (base leg)
    #       other-field  `w->spare = other;`                    -> DISCHARGES
    #       longer-path  `h->w->buf = other;`                   -> DISCHARGES
    #
    #     THE TWO LEGS FAIL DIFFERENT HALVES OF THE ARGUMENT AND BOTH ARE NEEDED. `field`
    #     breaks the PIN: the join is `(type, field)` and the dmark marks whatever the field
    #     HOLDS, so after the rebinding it pins the replacement and the String `p` points
    #     into is referenced by nothing. It needs no escape -- the arm is a hit whether or
    #     not the row escapes. `base` breaks `escape_stays_inside`, whose base comparison is
    #     TEXTUAL: after `w = twin` the same six characters name a different object, so
    #     `w->held = p` stores into an object that can outlive the one holding the String.
    #     The pin claim itself survives there -- every `struct wrap` has `buf` pinned -- and
    #     that is exactly why the base leg is not covered by the path leg.
    #
    #     THE TWO GREENS ARE THE REGEX ASSERTED IN THE DIRECTION THAT COSTS ROWS.
    #     `other-field` is a rebinding of a DIFFERENT member of the same struct, which must
    #     not count, or the rule degenerates into "any write to any member of the base".
    #     `longer-path` is `h->w->buf = other` -- a write to a longer path ENDING in the
    #     same three tokens, which is a different object's `buf`; without the lookbehind
    #     _path_write carries it would read as a write to `w->buf` and clear nothing that
    #     should be cleared. Both are the "narrowed, not deleted" halves this file requires
    #     of any rule that stops discharging.
    #
    #     THE SHAPE IS IN THE CORPUS FOUR TIMES and none of the four moves a verdict, which
    #     is worth stating precisely because it is the reason the corpus is flat: json's
    #     `cResumableParser_feed` (parser.c:2393, `parser->buffer = new_buffer` four lines
    #     on) and `zstream_discard_input` in all three zlib trees (`z->input = Qnil` ten
    #     lines on) both really do rebind the field they derived from. Both are SAFE, for a
    #     reason this rule cannot see and an earlier one can: the pointer is fully consumed
    #     by `memcpy`/`memmove` before the rebinding, so `copies-in-callee` -- which runs
    #     ahead of `pinning-mark` -- has already cleared all four. The ordering is doing the
    #     work, and that is an argument for the order, not for a weaker rebinding test.
    reb_c = ("#include <ruby.h>\n\n"
             "struct wrap { VALUE buf; VALUE spare; const char *held; };\n"
             "struct holder { struct wrap *w; };\n\n"
             "static void\n"
             "wrap_mark(void *p)\n"
             "{\n"
             "    struct wrap *w = p;\n"
             "    rb_gc_mark(w->buf);\n"
             "    rb_gc_mark(w->spare);\n"
             "}\n\n"
             "static const rb_data_type_t wrap_type = {\n"
             "    \"wrap\",\n"
             "    { wrap_mark, 0, 0, },\n"
             "    0, 0, RUBY_TYPED_FREE_IMMEDIATELY\n"
             "};\n\n"
             "static VALUE\n"
             "feed(VALUE self, VALUE peer, VALUE other, struct holder *h)\n"
             "{\n"
             "    struct wrap *w;\n"
             "    struct wrap *twin;\n"
             "    const char *p;\n"
             "    TypedData_Get_Struct(self, struct wrap, &wrap_type, w);\n"
             "    TypedData_Get_Struct(peer, struct wrap, &wrap_type, twin);\n"
             "    p = RSTRING_PTR(w->buf);\n"
             "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
             "%s"
             "    w->held = p;\n"
             "    return Qnil;\n"
             "}\n\n"
             "void Init_probe(void)\n"
             "{\n"
             "    rb_define_method(rb_cObject, \"f\", feed, 0);\n"
             "}\n")
    reb_arms = {
        "stable":      "",
        "field":       "    w->buf = other;\n",
        "base":        "    w = twin;\n",
        "other-field": "    w->spare = other;\n",
        "longer-path": "    h->w->buf = other;\n",
    }

    def _reb_arm(tag, arm):
        root = _synth("fx-reb-%s" % tag, {"ext/probe.c": reb_c % arm})
        return _sweep(root), _hits(root, disabled=("pinning-mark",))
    rb = {t: _reb_arm(t, a) for t, a in reb_arms.items()}
    reb_green, reb_red = ("stable", "other-field", "longer-path"), ("field", "base")
    check(all((rb[t][0].funcs, len(rb[t][0].derivations), len(rb[t][0].with_window))
              == (3, 1, 1) and rb[t][1] for t in reb_arms)
          and all(not rb[t][0].hits
                  and [d[0] for d in rb[t][0].discharges] == ["pinning-mark"]
                  for t in reb_green)
          and all(len(rb[t][0].hits) == 1 and not rb[t][0].discharges for t in reb_red),
          "pin-rebinding red/green: rebinding the SOURCE MEMBER PATH leaves the row standing "
          "-- the dmark then pins the replacement -- and so does rebinding the destination "
          "base local, whose name the escape test compares textually. Rebinding a DIFFERENT "
          "field, and a write to a longer path ending in the same tokens, both still "
          "discharge: the rule is one write to one name, not any write to anything nearby",
          [(t, rb[t][0].funcs, len(rb[t][0].derivations), len(rb[t][0].with_window),
            [h[0] for h in rb[t][0].hits], sorted(d[0] for d in rb[t][0].discharges),
            [h[0] for h in rb[t][1]])
           for t in sorted(reb_arms)])

    # 8w. GENERATED REDS AND GREEN: THE CONDITION CAN LIVE ON THE CALL EDGE. #32's third
    #     review finding, and it is the P2 fix being routed around one level up: the closure
    #     indexed `mark_fields` without keeping the CALL-SITE condition, so
    #     `unconditional_mark` looked inside the helper, found `rb_gc_mark(w->buf)` at depth
    #     zero, and cleared the row on the `!w->pins` path exactly as before the fix.
    #
    #       uncond-call   `mark_fields(w);`                        -> DISCHARGES
    #       cond-call     `if (w->pins) mark_fields(w);`           -> STILL A HIT
    #       braced-call   `if (w->pins) { mark_fields(w); }`       -> STILL A HIT
    #       return-call   `if (!w->pins) return;` then the call    -> STILL A HIT
    #       movable-edge  an unconditional PIN plus a CONDITIONAL
    #                     call to a movable helper                 -> STILL A HIT
    #
    #     The three conditional spellings are 8u's three all over again, one scope out --
    #     the same three tu_scope rules now applied to the EDGE rather than to the call --
    #     which is the evidence that `unconditional_mark` was the right function to reuse
    #     rather than a fourth opinion about what "conditional" means.
    #
    #     `movable-edge` IS THE LOAD-BEARING ARM AND IT GUARDS THE SMALLER EDIT. Narrowing
    #     the whole closure to unconditional edges is one line shorter and it rebuilds the
    #     round-10 over-clear: `move_fields` would leave `mark_fns` entirely, `movable`
    #     would lose `(wrap, buf)`, the subtraction in `_index_pins` would stop firing, and
    #     the unconditional `rb_gc_mark(w->buf)` in the same dmark would answer for it. So
    #     the closure returns BOTH sets and only `pinned` is gated. Measured on this arm:
    #     `pinned` empty, `movable` holds the key, `pin_reachable` holds `wrap_mark` alone.
    edge_c = ("#include <ruby.h>\n\n"
              "struct wrap { VALUE buf; int pins; };\n\n"
              "static void\n"
              "mark_fields(struct wrap *w)\n"
              "{\n"
              "    rb_gc_mark(w->buf);\n"
              "}\n\n"
              "static void\n"
              "move_fields(struct wrap *w)\n"
              "{\n"
              "    rb_gc_mark_movable(w->buf);\n"
              "}\n\n"
              "static void\n"
              "wrap_mark(void *p)\n"
              "{\n"
              "    struct wrap *w = p;\n"
              "%s"
              "}\n\n"
              "static const rb_data_type_t wrap_type = {\n"
              "    \"wrap\",\n"
              "    { wrap_mark, 0, 0, },\n"
              "    0, 0, RUBY_TYPED_FREE_IMMEDIATELY\n"
              "};\n\n"
              "static VALUE\n"
              "feed(VALUE self)\n"
              "{\n"
              "    struct wrap *w;\n"
              "    const char *p;\n"
              "    VALUE out;\n"
              "    TypedData_Get_Struct(self, struct wrap, &wrap_type, w);\n"
              "    p = RSTRING_PTR(w->buf);\n"
              "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
              "    out = rb_str_new(p, 4);\n"
              "    return out;\n"
              "}\n\n"
              "void Init_probe(void)\n"
              "{\n"
              "    rb_define_method(rb_cObject, \"f\", feed, 0);\n"
              "}\n")
    edge_arms = {
        "uncond-call":  "    mark_fields(w);\n",
        "cond-call":    "    if (w->pins) mark_fields(w);\n",
        "braced-call":  "    if (w->pins) {\n        mark_fields(w);\n    }\n",
        "return-call":  "    if (!w->pins) return;\n    mark_fields(w);\n",
        "movable-edge": "    rb_gc_mark(w->buf);\n"
                        "    if (w->pins) move_fields(w);\n",
    }
    eg = {t: _synth("fx-edge-%s" % t, {"ext/probe.c": edge_c % a})
          for t, a in edge_arms.items()}
    es = {t: _sweep(r) for t, r in eg.items()}
    edge_red = ("cond-call", "braced-call", "return-call", "movable-edge")
    mv = Tree(eg["movable-edge"])
    check(all((es[t].funcs, len(es[t].derivations), len(es[t].with_window)) == (5, 1, 1)
              for t in edge_arms)
          and not es["uncond-call"].hits
          and [d[0] for d in es["uncond-call"].discharges] == ["pinning-mark"]
          and all(len(es[t].hits) == 1 and not es[t].discharges for t in edge_red)
          and not mv.pinned and ("wrap", "buf") in mv.movable,
          "conditional-EDGE red/green: a pin reached only through `if (c) helper(w)` does "
          "not clear, in all three spellings of the condition, although the helper's own "
          "body is unconditional -- the closure carries reachability, not just membership. "
          "And a CONDITIONAL edge to a MOVABLE helper still registers the hazard: gating "
          "the whole closure instead of just `pinned` would drop it from `movable` and let "
          "the sibling unconditional pin answer for it",
          [(t, es[t].funcs, len(es[t].derivations), len(es[t].with_window),
            [h[0] for h in es[t].hits], sorted(d[0] for d in es[t].discharges))
           for t in sorted(edge_arms)]
          + [("movable-edge index", sorted(mv.pinned), sorted(mv.movable))])

    # 8x. GENERATED REDS AND GREENS: THE REBINDING CAN GO THROUGH AN ALIAS. #32's third
    #     review round, first finding, and the second time a fix in this rule was routed
    #     around by ONE level of indirection -- `unconditional_mark` by a call to a mark
    #     helper, and now `pin_source_stable` by an ordinary pointer copy. Both first cuts
    #     were exact-path, so the answer here is deliberately NOT a third exact-path clause.
    #
    #       alias-before  `alias = w;` ... `alias->buf = other;`   -> STILL A HIT
    #       alias-after   the copy taken after the derivation      -> STILL A HIT
    #       alias-unused  the alias exists and is only READ        -> DISCHARGES
    #       other-struct  a different wrapper's `twin->buf =`      -> DISCHARGES
    #
    #     THE PATH LEG NOW ASKS tu_scope.alias_map, which is rule 4 -- which locals carry
    #     the same pointer -- and a `struct wrap *` copy is exactly its shape. That buys the
    #     three constraints rule 4 already carries rather than three new ones, and it is why
    #     `other-struct` is a green: `twin` comes from its own TypedData_Get_Struct and is
    #     not in the alias set, so the rule is asking about ALIASES and not about "any
    #     pointer to this struct type", which would decline every wrapper-pair function in
    #     the corpus.
    #
    #     `alias-unused` is the green that keeps the widening honest in the other direction:
    #     an alias that is only ever read must not decline, or the rule degenerates into
    #     "any second name for this pointer exists". A STATED LIMIT, over-reporting and
    #     recorded rather than fixed: a carrier that is killed before the write
    #     (`alias = w; alias = twin; alias->buf = x;`) still declines, because the check asks
    #     the alias SET rather than `alias_reads`. Rule 5's kill would answer it; the safe
    #     direction here is to decline, and no corpus row pays for it.
    ali_c = ("#include <ruby.h>\n\n"
             "struct wrap { VALUE buf; const char *held; };\n\n"
             "static void\n"
             "wrap_mark(void *p)\n"
             "{\n"
             "    struct wrap *w = p;\n"
             "    rb_gc_mark(w->buf);\n"
             "}\n\n"
             "static const rb_data_type_t wrap_type = {\n"
             "    \"wrap\",\n"
             "    { wrap_mark, 0, 0, },\n"
             "    0, 0, RUBY_TYPED_FREE_IMMEDIATELY\n"
             "};\n\n"
             "static VALUE\n"
             "feed(VALUE self, VALUE peer, VALUE other)\n"
             "{\n"
             "    struct wrap *w;\n"
             "    struct wrap *twin;\n"
             "    struct wrap *alias;\n"
             "    const char *p;\n"
             "    VALUE out;\n"
             "    TypedData_Get_Struct(self, struct wrap, &wrap_type, w);\n"
             "    TypedData_Get_Struct(peer, struct wrap, &wrap_type, twin);\n"
             "%s"
             "    p = RSTRING_PTR(w->buf);\n"
             "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
             "%s"
             "    out = rb_str_new(p, 4);\n"
             "    return out;\n"
             "}\n\n"
             "void Init_probe(void)\n"
             "{\n"
             "    rb_define_method(rb_cObject, \"f\", feed, 0);\n"
             "}\n")
    ali_arms = {
        "alias-before": ("    alias = w;\n", "    alias->buf = other;\n"),
        "alias-after":  ("", "    alias = w;\n    alias->buf = other;\n"),
        "alias-unused": ("    alias = w;\n", "    rb_str_new(alias->held, 2);\n"),
        "other-struct": ("", "    twin->buf = other;\n"),
    }

    def _ali_arm(tag, pre, post):
        root = _synth("fx-ali-%s" % tag, {"ext/probe.c": ali_c % (pre, post)})
        return _sweep(root), _hits(root, disabled=("pinning-mark",))
    al = {t: _ali_arm(t, *a) for t, a in ali_arms.items()}
    ali_green, ali_red = ("alias-unused", "other-struct"), ("alias-before", "alias-after")
    check(all((al[t][0].funcs, len(al[t][0].derivations), len(al[t][0].with_window))
              == (3, 1, 1) and al[t][1] for t in ali_arms)
          and all(not al[t][0].hits
                  and [d[0] for d in al[t][0].discharges] == ["pinning-mark"]
                  for t in ali_green)
          and all(len(al[t][0].hits) == 1 and not al[t][0].discharges for t in ali_red),
          "pin-rebinding through an ALIAS: `alias = w; alias->buf = other;` rebinds the "
          "pinned field under a second name and leaves the row standing, whether the copy "
          "is taken before or after the derivation -- the leg asks tu_scope's alias set, "
          "not the literal spelling. An alias that is only READ, and a different wrapper of "
          "the same type, both still discharge",
          [(t, al[t][0].funcs, len(al[t][0].derivations), len(al[t][0].with_window),
            [h[0] for h in al[t][0].hits], sorted(d[0] for d in al[t][0].discharges))
           for t in sorted(ali_arms)])

    # 8y. GENERATED REDS AND GREENS: EVERY RETAINED VARIANT OF A DATA TYPE MUST PIN. #32's
    #     third round, second finding, and the `#ifdef` family one level above 8r's -- that
    #     one is two marks for one FIELD, this is two initialisers for one TYPE:
    #
    #       null-variant     `.dmark = wrap_mark` / `.dmark = NULL`     -> STILL A HIT
    #       movable-variant  an UNTRUSTED type registering the MOVABLE
    #                        mark, beside a trusted type's pin          -> STILL A HIT
    #       both-pin         two variants, both naming the callback     -> DISCHARGES
    #       single           one ordinary registration                  -> DISCHARGES
    #       two-types        a DIFFERENT variable carries the NULL      -> DISCHARGES
    #
    #     `strip_directives` keeps both arms -- deliberately, and predicate A depends on it
    #     in the OPPOSITE direction, since that is what stops a dead `rb_gc_mark` from
    #     downgrading a live `rb_gc_mark_movable` under `stronger()`. So the fix cannot be
    #     to stop retaining variants; it is to grade registration per VARIABLE and let the
    #     unsafe variant win, which is the same disposition `movable` beats `pinned` uses.
    #
    #     `movable-variant` IS THE LOAD-BEARING ARM, and it is 8w's `movable-edge` one scope
    #     out. Untrusted registrations are DEMOTED, not dropped: dropping them takes the
    #     mark function out of the closure and out of `movable` with it, and then a pin
    #     registered by a DIFFERENT type answers for the same `(type, field)` key. Asserted
    #     on the index -- `pinned` empty, `movable` holding `(wrap, buf)` -- so a future
    #     edit that drops instead of demotes fails here and not only on a row.
    #
    #     `two-types` is the green for the grouping itself: trust is per VARIABLE, so one
    #     type's NULL variant must not contaminate another's, and the two are graded apart
    #     even in one file.
    #
    #     CORPUS: 0 INSTANCES, and this was swept for rather than assumed. Over the 99 trees
    #     there are 329 `rb_data_type_t` initialisers in 274 files; 76 of them sit inside a
    #     preprocessor conditional, and ZERO declare the same variable twice. So the shape
    #     is entirely synthetic today, the corpus cannot move, and this fixture is the only
    #     thing that can tell the fix from no fix. The companion sweep at the CALL level
    #     (43 marking primitives inside conditionals, only zstd's three `#else` pins and a
    #     msgpack shim dead) is what 8r pins; this is the same question one level up.
    reg_head = ("#include <ruby.h>\n\n"
                "struct wrap { VALUE buf; };\n"
                "struct other { VALUE buf; };\n\n"
                "static void\n"
                "other_mark(void *p)\n"
                "{\n"
                "    struct other *o = p;\n"
                "    rb_gc_mark(o->buf);\n"
                "}\n\n"
                "static void\n"
                "wrap_mark(void *p)\n"
                "{\n"
                "    struct wrap *w = p;\n"
                "    rb_gc_mark(w->buf);\n"
                "}\n\n"
                "static void\n"
                "move_mark(void *p)\n"
                "{\n"
                "    struct wrap *w = p;\n"
                "    rb_gc_mark_movable(w->buf);\n"
                "}\n\n")
    reg_tail = ("static VALUE\n"
                "feed(VALUE self)\n"
                "{\n"
                "    struct wrap *w;\n"
                "    const char *p;\n"
                "    VALUE out;\n"
                "    TypedData_Get_Struct(self, struct wrap, &wrap_type, w);\n"
                "    p = RSTRING_PTR(w->buf);\n"
                "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
                "    out = rb_str_new(p, 4);\n"
                "    return out;\n"
                "}\n\n"
                "void Init_probe(void)\n"
                "{\n"
                "    rb_define_method(rb_cObject, \"f\", feed, 0);\n"
                "}\n")

    def _dt(name, cb):
        return ("static const rb_data_type_t %s = {\n"
                "    \"%s\", { %s, 0, 0, }, 0, 0, RUBY_TYPED_FREE_IMMEDIATELY\n"
                "};\n" % (name, name, cb))
    reg_arms = {
        "null-variant": ("#ifdef HAVE_PINNING\n" + _dt("wrap_type", "wrap_mark")
                         + "#else\n" + _dt("wrap_type", "NULL") + "#endif\n\n"),
        "both-pin": ("#ifdef HAVE_PINNING\n" + _dt("wrap_type", "wrap_mark")
                     + "#else\n" + _dt("wrap_type", "wrap_mark") + "#endif\n\n"),
        "single": _dt("wrap_type", "wrap_mark") + "\n",
        "two-types": ("#ifdef HAVE_PINNING\n" + _dt("other_type", "other_mark")
                      + "#else\n" + _dt("other_type", "NULL") + "#endif\n\n"
                      + _dt("wrap_type", "wrap_mark") + "\n"),
        "movable-variant": ("#ifdef HAVE_PINNING\n" + _dt("move_type", "move_mark")
                            + "#else\n" + _dt("move_type", "NULL") + "#endif\n\n"
                            + _dt("wrap_type", "wrap_mark") + "\n"),
    }
    rg_root = {t: _synth("fx-reg-%s" % t, {"ext/probe.c": reg_head + m + reg_tail})
               for t, m in reg_arms.items()}
    rg = {t: _sweep(r) for t, r in rg_root.items()}
    reg_green = ("both-pin", "single", "two-types")
    reg_red = ("null-variant", "movable-variant")
    mvt = Tree(rg_root["movable-variant"])
    check(all((rg[t].funcs, len(rg[t].derivations), len(rg[t].with_window)) == (5, 1, 1)
              for t in reg_arms)
          and all(not rg[t].hits and [d[0] for d in rg[t].discharges] == ["pinning-mark"]
                  for t in reg_green)
          and all(len(rg[t].hits) == 1 and not rg[t].discharges for t in reg_red)
          and not mvt.pinned and ("wrap", "buf") in mvt.movable,
          "registration-variant red/green: a type whose OTHER `#ifdef` variant sets `.dmark "
          "= NULL` registers nothing in that build, so its pin cannot clear -- while two "
          "pinning variants, a single registration, and a different variable carrying the "
          "NULL all still discharge. And an untrusted registration is DEMOTED, not dropped: "
          "its movable mark still registers the hazard, or a pin from another type answers "
          "for the same key",
          [(t, rg[t].funcs, len(rg[t].derivations), len(rg[t].with_window),
            [h[0] for h in rg[t].hits], sorted(d[0] for d in rg[t].discharges))
           for t in sorted(reg_arms)]
          + [("movable-variant index", sorted(mvt.pinned), sorted(mvt.movable))])

    # 8l/8m. GENERATED RED: A BARE STORE INTO STATIC STORAGE IS AN ESCAPE.
    #
    #    `static const char *saved; saved = RSTRING_PTR(str);` has no pointer-parameter base
    #    and no `->`/`[`/`.`, so both escape branches declined it and the row discharged
    #    no-window: `derive 1/1 -> windowed 0/0 -> hit 0` on a pointer any later call reads.
    #    THE SINK IS THE FLAG: the control stores the same derivation into a plain local, and
    #    must still discharge -- otherwise the rule is "every assignment is an escape", which
    #    would fire on the commonest local declaration in the corpus.
    static_c = ("#include <ruby.h>\n\n"
                "static const char *saved;\n\n"
                "static VALUE\n"
                "store(VALUE self, VALUE str)\n"
                "{\n"
                "    Check_Type(str, T_STRING);\n"
                "%s\n"
                "    return Qnil;\n"
                "}\n\n"
                "static VALUE\n"
                "later(VALUE self)\n"
                "{\n"
                "    return rb_str_new_cstr(saved);\n"
                "}\n\n"
                "void Init_probe(void)\n"
                "{\n"
                "    rb_define_method(rb_cObject, \"store\", store, 1);\n"
                "    rb_define_method(rb_cObject, \"later\", later, 0);\n"
                "}\n")
    sred = _sweep(_synth("fx-static-red",
                         {"ext/probe.c": static_c % "    saved = RSTRING_PTR(str);"}))
    sctl = _sweep(_synth("fx-static-control",
                         {"ext/probe.c": static_c %
                          "    const char *local = RSTRING_PTR(str);\n    (void)local;"}))
    check(sred.funcs == 3 and len(sred.derivations) == 1 and len(sred.with_window) == 1
          and len(sred.hits) == 1 and sred.hits[0][0] == "ESCAPES-INTO-STATIC",
          "static-sink red: a derivation stored into a file-scope scalar escapes the frame "
          "-- neither existing escape branch had a shape for it",
          "funcs %d, derive %d, win %d, hits %s"
          % (sred.funcs, len(sred.derivations), len(sred.with_window),
             [(h[0], h[2]) for h in sred.hits]))
    check(sctl.funcs == 3 and len(sctl.derivations) == 1 and not sctl.hits
          and any(d[0] == "no-window" for d in sctl.discharges),
          "static-sink control: the same derivation into a plain LOCAL still discharges -- "
          "the sink is recognised by name, not by 'anything assigned to'",
          "funcs %d, derive %d, hits %s, discharges %s"
          % (sctl.funcs, len(sctl.derivations), [(h[0], h[2]) for h in sctl.hits],
             [d[0] for d in sctl.discharges]))

    # 8n/8o. GENERATED RED AND GREEN: A WRITE TO THE SOURCE IS NOT A USE OF IT.
    #
    #    `last-use-after` DISCHARGES and is the most recall-biased rule in the file, so a
    #    trailing `str = other;` counted as the source's last use cleared a row on no
    #    evidence at all. Corpus-neutral -- all 13 of the rule's clears are genuine reads --
    #    which is exactly why the red has to be generated here. The green proves the rule
    #    still fires on a real post-read READ; a non-cfunc helper, because in a cfunc entry
    #    point the argv rule grades the row first and the discharge never runs.
    write_c = ("#include <ruby.h>\n\n"
               "static void\n"
               "helper(VALUE str, VALUE other)\n"
               "{\n"
               "    const char *p = RSTRING_PTR(str);\n"
               "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
               "    consume(p);\n"
               "%s}\n\n"
               "static VALUE\n"
               "entry(VALUE self, VALUE a, VALUE b)\n"
               "{\n"
               "    helper(a, b);\n"
               "    return Qnil;\n"
               "}\n\n"
               "void Init_probe(void)\n"
               "{\n"
               "    rb_define_method(rb_cObject, \"e\", entry, 2);\n"
               "}\n")
    wred = _sweep(_synth("fx-write-red", {"ext/probe.c": write_c % "    str = other;\n"}))
    wgrn = _sweep(_synth("fx-write-green", {"ext/probe.c": write_c % "    keep(str);\n"}))
    check(wred.funcs == 3 and len(wred.derivations) == 1 and len(wred.with_window) == 1
          and len(wred.hits) == 1
          and not any(d[0] == "last-use-after" for d in wred.discharges),
          "write-not-use red: a trailing `str = other;` no longer discharges "
          "last-use-after -- an assignment neither reads the old VALUE nor keeps it",
          "funcs %d, derive %d, win %d, hits %s, discharges %s"
          % (wred.funcs, len(wred.derivations), len(wred.with_window),
             [(h[0], h[2]) for h in wred.hits], [d[0] for d in wred.discharges]))
    check(wgrn.funcs == 3 and len(wgrn.derivations) == 1 and not wgrn.hits
          and any(d[0] == "last-use-after" for d in wgrn.discharges),
          "write-not-use green: a real post-read READ of the source still discharges "
          "last-use-after -- the rule was narrowed to reads, not deleted",
          "funcs %d, derive %d, hits %s, discharges %s"
          % (wgrn.funcs, len(wgrn.derivations), [(h[0], h[2]) for h in wgrn.hits],
             [d[0] for d in wgrn.discharges]))

    # 8p/8q. GENERATED RED AND GREEN: A SHADOWING REDECLARATION IS NOT THE SOURCE.
    #
    #    The third member of the family 8j/8k and 8n/8o fixed, and the reason those three
    #    are now ONE predicate (source_reads) rather than three special cases: every one of
    #    them accepted a bare token occurrence of the name as evidence that the name still
    #    held the object. Here the occurrence is a genuine READ of a genuine variable that
    #    merely SPELLS the same -- `{ VALUE str = other; use(str); }` -- while the original
    #    `str` is dead and the String may move during the rb_funcall.
    #
    #    THREE ARMS, ONE FIXTURE, because the green is what says the rule was narrowed and
    #    not deleted: `same-scope` is an ordinary later read of the real source and MUST
    #    still discharge; `inner-block` is the same read of the same name inside a block
    #    that does not redeclare it, and must also still discharge -- otherwise the fix is
    #    "any read inside a brace stops counting", which would clear nothing and report
    #    everything. Only `shadow` stands.
    shadow_c = ("#include <ruby.h>\n\n"
                "static void\n"
                "helper(VALUE str, VALUE other)\n"
                "{\n"
                "    const char *p = RSTRING_PTR(str);\n"
                "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
                "    consume(p);\n"
                "%s}\n\n"
                "static VALUE\n"
                "entry(VALUE self, VALUE a, VALUE b)\n"
                "{\n"
                "    helper(a, b);\n"
                "    return Qnil;\n"
                "}\n\n"
                "void Init_probe(void)\n"
                "{\n"
                "    rb_define_method(rb_cObject, \"e\", entry, 2);\n"
                "}\n")
    sh_arms = {
        "shadow": "    {\n        VALUE str = other;\n        use(str);\n    }\n",
        "same-scope": "    use(str);\n",
        "inner-block": "    {\n        use(str);\n    }\n",
    }
    sh = {tag: _sweep(_synth("fx-shadow-%s" % tag, {"ext/probe.c": shadow_c % arm}))
          for tag, arm in sh_arms.items()}
    check(sh["shadow"].funcs == 3 and len(sh["shadow"].derivations) == 1
          and len(sh["shadow"].with_window) == 1 and len(sh["shadow"].hits) == 1
          and not any(d[0] == "last-use-after" for d in sh["shadow"].discharges),
          "shadow red: a read of an INNER `VALUE str` no longer discharges last-use-after "
          "-- it is a different variable, and the source is dead at the window",
          "funcs %d, derive %d, win %d, hits %s, discharges %s"
          % (sh["shadow"].funcs, len(sh["shadow"].derivations),
             len(sh["shadow"].with_window), [(h[0], h[2]) for h in sh["shadow"].hits],
             [d[0] for d in sh["shadow"].discharges]))
    check(all(not sh[t].hits and len(sh[t].derivations) == 1
              and any(d[0] == "last-use-after" for d in sh[t].discharges)
              for t in ("same-scope", "inner-block")),
          "shadow green: a genuine later read of the SOURCE still discharges "
          "last-use-after, in the same scope and inside a nested block that does not "
          "redeclare it -- the rule was narrowed to the right variable, not to no variable",
          [(t, len(sh[t].hits), [d[0] for d in sh[t].discharges])
           for t in ("same-scope", "inner-block")])

    # 8r/8s. GENERATED RED AND GREEN: THE IDENTITY OF A STATIC-STORAGE SINK.
    #
    #    Two spellings, one defect, and 8l/8m had neither: the sink is real, and the
    #    identity the store site computes for it does not match the identity the declaration
    #    scan recorded. Both discharged `no-window` on a pointer read by a LATER call.
    #
    #      int-local   `static uintptr_t saved; saved = (uintptr_t)RSTRING_PTR(str);`
    #                  the collector required a `*` in the declaration, so an interior
    #                  pointer laundered through an integer was not a persistent slot at
    #                  all. Class A already carries this evasion by name -- "stored as an
    #                  integer, key, handle or index, not as a `void *`" -- so it is the
    #                  same scent one class down.
    #      qualified   `Cache::saved = RSTRING_PTR(str);`
    #                  the lvalue match extracted `Cache` while the declaration scan
    #                  recorded `saved`, so the membership test could not fire.
    #
    #    THE SINK IS THE FLAG in both arms: `local` stores the same derivation into a plain
    #    automatic local and must still discharge, or the rule has become "every assignment
    #    is an escape" -- the commonest local declaration in the corpus.
    sink_cpp = ("#include <ruby.h>\n\n"
                "class Cache {\n"
                "public:\n"
                "    static const char *saved;\n"
                "};\n\n"
                "const char *Cache::saved;\n\n"
                "static VALUE\n"
                "store(VALUE self, VALUE str)\n"
                "{\n"
                "    Check_Type(str, T_STRING);\n"
                "%s\n"
                "    return Qnil;\n"
                "}\n\n"
                "static VALUE\n"
                "later(VALUE self)\n"
                "{\n"
                "    return rb_str_new_cstr(Cache::saved);\n"
                "}\n\n"
                "extern \"C\" void Init_probe(void)\n"
                "{\n"
                "    rb_define_method(rb_cObject, \"s\", store, 1);\n"
                "    rb_define_method(rb_cObject, \"l\", later, 0);\n"
                "}\n")
    sink_arms = {
        "int-local": "    static uintptr_t held;\n"
                     "    held = (uintptr_t)RSTRING_PTR(str);\n    (void)held;",
        "qualified": "    Cache::saved = RSTRING_PTR(str);",
        "local": "    const char *local = RSTRING_PTR(str);\n    (void)local;",
    }
    sk = {tag: _sweep(_synth("fx-sink-%s" % tag, {"ext/probe.cpp": sink_cpp % arm}))
          for tag, arm in sink_arms.items()}
    check(all(sk[t].funcs == 3 and len(sk[t].derivations) == 1
              and len(sk[t].with_window) == 1 and len(sk[t].hits) == 1
              and sk[t].hits[0][0] == "ESCAPES-INTO-STATIC"
              for t in ("int-local", "qualified")),
          "sink-identity red: an integer-typed function-local static and a C++ qualified "
          "static data member are both persistent sinks -- unfixed, both report "
          "`derive 1/1 -> windowed 0/0 -> hit 0` on an address that outlives the String",
          [(t, sk[t].funcs, len(sk[t].derivations), len(sk[t].with_window),
            [h[0] for h in sk[t].hits], [d[0] for d in sk[t].discharges])
           for t in ("int-local", "qualified")])
    check(sk["local"].funcs == 3 and len(sk["local"].derivations) == 1
          and not sk["local"].hits
          and any(d[0] == "no-window" for d in sk["local"].discharges),
          "sink-identity green: the same derivation into a plain automatic local still "
          "discharges -- a sink is still recognised POSITIVELY, by name",
          "funcs %d, derive %d, hits %s, discharges %s"
          % (sk["local"].funcs, len(sk["local"].derivations),
             [(h[0], h[2]) for h in sk["local"].hits],
             [d[0] for d in sk["local"].discharges]))

    # 8t/8u. GENERATED RED AND GREEN: POINTER ARITHMETIC CARRIES THE POINTER.
    #
    #    `q = p + 1` points into the same String bytes as `p`, but the copy scan required a
    #    bare name on the right, so `q` never joined the alias set, the last use stopped at
    #    the copy and the row discharged `no-window` -- `derive 2/2 -> windowed 0/0 -> hit
    #    0` on a pointer read after a compaction. THE ARITHMETIC IS THE FLAG: the `plain`
    #    arm is the same function with `q = p`, and it must give the same row, or the check
    #    is measuring the alias machinery rather than the arithmetic.
    #
    #    AND THE EXCLUSION IS ASSERTED IN THE SAME FIXTURE. `off = e - p` is a pointer
    #    DIFFERENCE -- an integer, which cannot dangle -- and stringio is the corpus gem
    #    that is safe by exactly this distinction. It must stay out of the alias set, and
    #    its funnel is asserted too: a `derive 0/0` there would clear it for the wrong
    #    reason and read the same as this pass.
    arith_c = ("#include <ruby.h>\n\n"
               "static VALUE\n"
               "arith(VALUE self, VALUE str)\n"
               "{\n"
               "    const char *p = RSTRING_PTR(str);\n"
               "    const char *e = RSTRING_END(str);\n"
               "%s"
               "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
               "%s"
               "}\n\n"
               "void Init_probe(void)\n"
               "{\n"
               "    rb_define_method(rb_cObject, \"a\", arith, 1);\n"
               "}\n")
    ar_arms = {
        "offset": ("    const char *q = p + 1;\n", "    return rb_str_new_cstr(q);\n"),
        "plain": ("    const char *q = p;\n", "    return rb_str_new_cstr(q);\n"),
        "difference": ("    long off = e - p;\n", "    return LONG2NUM(off);\n"),
    }
    ar = {tag: _sweep(_synth("fx-arith-%s" % tag, {"ext/probe.c": arith_c % arm}))
          for tag, arm in ar_arms.items()}
    check(ar["offset"].funcs == 2 and len(ar["offset"].derivations) == 2
          and len(ar["offset"].with_window) == 1 and len(ar["offset"].hits) == 1
          and ar["offset"].hits[0][0] == "ESCAPES-BY-RETURN"
          and (len(ar["offset"].hits), len(ar["offset"].with_window))
          == (len(ar["plain"].hits), len(ar["plain"].with_window)),
          "pointer-arithmetic red: `q = p + 1` carries the buffer and gives the same funnel "
          "and the same row as `q = p` -- unfixed it left the alias set and discharged "
          "no-window",
          "offset %d/%d/%s vs plain %d/%d/%s"
          % (len(ar["offset"].derivations), len(ar["offset"].with_window),
             [h[0] for h in ar["offset"].hits], len(ar["plain"].derivations),
             len(ar["plain"].with_window), [h[0] for h in ar["plain"].hits]))
    check(len(ar["difference"].derivations) == 2 and not ar["difference"].with_window
          and not ar["difference"].hits
          and any(d[0] == "no-window" for d in ar["difference"].discharges),
          "pointer-difference green: `off = e - p` is an integer and stays OUT of the alias "
          "set -- the exclusion stringio depends on, asserted with its funnel so a parse "
          "failure cannot pass as it",
          "derive %d, win %d, hits %s, discharges %s"
          % (len(ar["difference"].derivations), len(ar["difference"].with_window),
             [(h[0], h[2]) for h in ar["difference"].hits],
             [d[0] for d in ar["difference"].discharges]))

    # 8v/8w. GENERATED RED AND GREEN: A TRAILING RETURN TYPE IS NOT THE END OF THE
    #    DECLARATOR EITHER -- and the third variant is why the walk stopped being a list.
    #
    #    8f/8g fixed `__attribute__((...))` and `noexcept` by naming them. `auto bad(VALUE
    #    str) -> VALUE {` broke it again, because `->` is not a word at all: same measured
    #    symptom, `0 fn(s) | derive 0/0 -> hit 0`, the empty index that reads as a clean gem.
    #    So the words are open and the parentheses stay closed (see skip_post_declarator),
    #    and THE HEADER IS THE FLAG: four spellings of one function must give one funnel and
    #    one row.
    #
    #    The REJECTION TABLE is asserted beside it, because an open walk that accepts too
    #    much invents a function body out of the next definition. Each of these must index
    #    exactly the one real definition, `bad`, and nothing else.
    def _hdr_tree(tag, header):
        return _synth("fx-hdr-%s" % tag, {"ext/probe.cpp":
            "#include <ruby.h>\n\n" + header + "\n"
            "{\n"
            "    const char *p = RSTRING_PTR(str);\n"
            "    rb_funcall(rb_mGC, rb_intern(\"compact\"), 0);\n"
            "    return rb_str_new(p, RSTRING_LEN(str));\n"
            "}\n\n"
            "void Init_probe(void)\n"
            "{\n"
            "    rb_define_method(rb_cObject, \"b\", (VALUE(*)(ANYARGS))bad, 1);\n"
            "}\n"})
    hdrs = {
        "plain": "static VALUE bad(VALUE str)",
        "trailing": "static auto bad(VALUE str) -> VALUE",
        "trailing-qual": "static auto bad(VALUE str) -> ns::Value",
        "attr-trailing": "static auto bad(VALUE str) __attribute__((noinline)) -> VALUE",
    }
    hd = {tag: _sweep(_hdr_tree(tag, h)) for tag, h in hdrs.items()}
    base_hd = (hd["plain"].funcs, len(hd["plain"].derivations),
               len(hd["plain"].with_window), [h[0] for h in hd["plain"].hits])
    check(base_hd == (2, 1, 1, ["ESCAPES-BY-RETURN"])
          and all((hd[t].funcs, len(hd[t].derivations), len(hd[t].with_window),
                   [h[0] for h in hd[t].hits]) == base_hd for t in hdrs),
          "trailing-return red: a C++ trailing return type, alone and behind an attribute, "
          "gives the same funnel and the same row as the plain header -- the closed word "
          "list stopped at the `->` and reported 0 fn(s), 0 derivations",
          [(t, hd[t].funcs, len(hd[t].derivations), [h[0] for h in hd[t].hits])
           for t in hdrs])
    rejects = {
        # a macro invocation, then a real definition: the `(` of `bad(` stops the walk
        "macro": "MY_EXPORT(sym)\nstatic VALUE bad(VALUE str)\n{\n    return Qnil;\n}\n",
        # K&R parameter declarations: the `;`
        "knr": "static VALUE bad(str) VALUE str; {\n    return Qnil;\n}\n",
        # a prototype, then a definition: the `;`
        "proto": "static VALUE helper(VALUE);\n"
                 "static VALUE bad(VALUE str)\n{\n    return Qnil;\n}\n",
        # a declarator list with a braced initialiser: the `=`
        "init": "struct S s = mk(1), t = {2};\n"
                "static VALUE bad(VALUE str)\n{\n    return Qnil;\n}\n",
        # THE TWO THE CORPUS FOUND AND THIS TABLE DID NOT. `typedef` stops the walk, and
        # both stop words were added on the strength of a corpus row rather than a fixture
        # -- so emptying POST_DECL_STOP broke nothing here while predicate C's copy of the
        # table caught it. These are the shapes that were actually invented: ffi's
        # `__declspec(align(8))` and trilogy's X-macro list, each followed by a type whose
        # aggregate body was then indexed as a function body under the macro's name.
        "typedef-aggregate": "__declspec(align(8)) typedef struct { int x; } thing_t;\n"
                             "static VALUE bad(VALUE str)\n{\n    return Qnil;\n}\n",
        "x-macro": "XX(A, 1)\ntypedef enum { E_A } phase_t;\n"
                   "static VALUE bad(VALUE str)\n{\n    return Qnil;\n}\n",
    }
    indexed = {}
    for tag, src in rejects.items():
        indexed[tag] = sorted(
            f.name for f in Tree(_synth("fx-rej-%s" % tag,
                                        {"ext/probe.cpp": "#include <ruby.h>\n\n" + src})
                                 ).funcs)
    check(indexed == {"macro": ["bad"], "knr": [], "proto": ["bad"], "init": ["bad"],
                      "typedef-aggregate": ["bad"], "x-macro": ["bad"]},
          "post-declarator green: the open walk still refuses to invent a body -- a macro "
          "call, a prototype, an initialiser list, `__declspec(...) typedef struct` and an "
          "X-macro list each index the ONE real definition, and K&R indexes none (a stated "
          "recall limit, unchanged)", indexed)

    # 8f. ROUND 9: A DISCHARGE RESOLVED THROUGH ANOTHER TRANSLATION UNIT'S BODY (:1360).
    #
    #     `copied_in_callee` looked its callee up in `tree.by_name`, which is keyed by the
    #     BARE NAME, so a call in b.c descended into a.c's same-named `static` helper. Of
    #     the four places this bug lives, this is the worst: the others resolve to the
    #     wrong body and MISREPORT, this one resolves to the wrong body and then CLEARS a
    #     real row on the strength of it. Measured on the fixture below, unfixed:
    #     `derive 2/2 -> windowed 0/0 -> hit 0`, both rows discharged `copies-in-callee`,
    #     and b.c's reason line even names `strncpy() at a.c:9` -- a file b.c does not
    #     include.
    #
    #     ONE fixture carries the red and the green, which is the only way to tell the fix
    #     from switching the rule off: a.c really does copy in its own frame and must stay
    #     discharged, b.c holds the pointer across an rb_funcall and must report. The
    #     funnel is asserted on both sides -- a parser that indexes nothing prints
    #     `derive 0/0 -> hit 0`, which is not the same clean sheet and must not read as one.
    tu_head = ("#include <ruby.h>\n"
               "\n"
               "struct box { const char *p; };\n"
               "\n"
               "static void\n"
               "stash(const char *q)\n"
               "{\n")
    tu_tail = ("}\n"
               "\n"
               "static VALUE\n"
               "%s(VALUE self, VALUE str)\n"
               "{\n"
               "    struct box b;\n"
               "    b.p = RSTRING_PTR(str);\n"
               "    stash(b.p);\n"
               "    return Qnil;\n"
               "}\n"
               "\n"
               "void Init_%s(void) { rb_define_method(rb_cObject, \"%s\", %s, 1); }\n")
    tu = _sweep(_synth("fx-tu-callee", {
        # copies its argument immediately, in its own frame -- a genuine discharge
        "ext/a.c": tu_head + "    static char keep[64];\n"
                             "    strncpy(keep, q, sizeof(keep));\n"
                  + tu_tail % (("a_go",) * 4),
        # holds it across a re-entry into Ruby -- a genuine finding
        "ext/b.c": tu_head + "    rb_funcall(rb_mKernel, rb_intern(\"hook\"), 0);\n"
                             "    rb_str_new(q, 4);\n"
                  + tu_tail % (("b_go",) * 4)}))
    tu_hits = sorted((h[0], h[1]) for h in tu.hits)
    tu_dis = sorted((d[0], d[1]) for d in tu.discharges)
    check(tu.funcs == 6 and len(tu.derivations) == 2 and len(tu.with_window) == 1
          and tu_hits == [("ESCAPES-INTO-CONTAINER", "ext/b.c")],
          "8f RED: b.c's derivation is no longer discharged through a.c's `static stash` "
          "-- the callee a call binds to is its own file's (funnel derive %d/%d, win %d)"
          % (len(tu.derivations), tu.deriv_fns, len(tu.with_window)),
          "hits %s discharges %s" % (tu_hits, tu_dis))
    check(tu_dis == [("copies-in-callee", "ext/a.c")],
          "...and GREEN in the same fixture: a.c's copy is in a.c's OWN callee, so "
          "copies-in-callee still discharges it -- a scoping fix that clears nothing is "
          "the rule turned off", tu_dis)

    # 8g/8h. GENERATED RED AND GREEN: A PERSISTENT SLOT DECLARED IN ANOTHER TRANSLATION
    #    UNIT (:1201).
    #
    #    The sink set was `tree.statics[fn.path]` -- the deriving function's OWN file. A slot
    #    declared `extern const char *saved;` in a header and defined in y.c is not in x.c's
    #    entry, so `saved = RSTRING_PTR(str)` in x.c matched no sink, both escape branches
    #    declined it and the row discharged `no-window`: zero hits on an address every later
    #    call reads through the same name.
    #
    #    THE MIRROR IS THE GREEN, AND IT IS THE SAME RULE. Predicate C scopes a `static` to
    #    its .c and deliberately NOT to a header, because a header's declaration reaches every
    #    includer. Read the other way round that says y.c's own `static const char *hidden;`
    #    reaches nothing outside y.c -- so x.c's plain local of that name is not a sink, and a
    #    tree-wide set that ignored linkage would report it. One fixture carries both: two
    #    stores, one file, one funnel; a fix that scopes too loosely fails the green and one
    #    that does not resolve the header at all fails the red.
    #
    #    x.h declares the slot and y.c defines it, which is what makes this a THREE-file
    #    fixture rather than a two-file one -- the definition has to be somewhere, and putting
    #    it in x.c would make the row visible to the unfixed code.
    xh = ("extern const char *saved;\n")
    yc = ("#include <ruby.h>\n\n"
          "const char *saved;\n"
          "static const char *hidden;\n\n"
          "static VALUE\n"
          "readback(VALUE self)\n"
          "{\n"
          "    return rb_str_new_cstr(hidden ? hidden : saved);\n"
          "}\n\n"
          "void Init_y(void) { rb_define_method(rb_cObject, \"r\", readback, 0); }\n")
    xc = ("#include <ruby.h>\n"
          "#include \"x.h\"\n\n"
          "static VALUE\n"
          "store(VALUE self, VALUE str)\n"
          "{\n"
          "    Check_Type(str, T_STRING);\n"
          "%s\n"
          "    return Qnil;\n"
          "}\n\n"
          "void Init_x(void) { rb_define_method(rb_cObject, \"s\", store, 1); }\n")
    xtu = {
        # the slot really is visible here: x.h declares it, y.c defines it
        "extern": "    saved = RSTRING_PTR(str);",
        # a name that is another translation unit's `static`: this is a plain LOCAL and
        # nothing in this file can even refer to y.c's slot
        "shadowed": "    const char *hidden = RSTRING_PTR(str);\n    (void)hidden;",
    }
    xt = {tag: _sweep(_synth("fx-xtu-%s" % tag,
                             {"ext/x.h": xh, "ext/y.c": yc, "ext/x.c": xc % arm}))
          for tag, arm in xtu.items()}
    check(xt["extern"].funcs == 4 and len(xt["extern"].derivations) == 1
          and len(xt["extern"].with_window) == 1 and len(xt["extern"].hits) == 1
          and xt["extern"].hits[0][0] == "ESCAPES-INTO-STATIC",
          "cross-TU sink red: a slot declared in a header and defined in another .c is a "
          "persistent sink in the file that STORES into it -- unfixed the sink set was that "
          "file's own declarations and the row discharged no-window",
          "funcs %d, derive %d, win %d, hits %s, discharges %s"
          % (xt["extern"].funcs, len(xt["extern"].derivations),
             len(xt["extern"].with_window), [(h[0], h[2]) for h in xt["extern"].hits],
             [d[0] for d in xt["extern"].discharges]))
    check(xt["shadowed"].funcs == 4 and len(xt["shadowed"].derivations) == 1
          and not xt["shadowed"].hits
          and any(d[0] == "no-window" for d in xt["shadowed"].discharges),
          "cross-TU sink green: ANOTHER file's `static` is not visible here, so a local of "
          "the same name still discharges -- the widening is the header carve-out read "
          "backwards, not 'every file-scope name everywhere'",
          "funcs %d, derive %d, hits %s, discharges %s"
          % (xt["shadowed"].funcs, len(xt["shadowed"].derivations),
             [(h[0], h[2]) for h in xt["shadowed"].hits],
             [d[0] for d in xt["shadowed"].discharges]))
    # ...and the parse rule the widening forced, asserted directly rather than through a row:
    # an aggregate body declares MEMBERS and a bare tag declares nothing, so neither may enter
    # the slot set. Confined to one file that was inert; tree-wide it produced four corpus
    # rows immediately (fiddle `struct pinned_data { VALUE ptr; }` against a local `ptr`,
    # rmagick's `char name[1]` against two locals called `name`, date's `struct zone;`).
    slots = file_scope_objects(
        "struct pinned_data { VALUE ptr; };\n"
        "struct zone;\n"
        "typedef struct { int x; } thing_t;\n"
        "static struct holder { const char *p; } g_slot;\n"
        "const char *saved;\n"
        "static const char *hidden;\n")
    check(slots == {"g_slot": True, "saved": False, "hidden": True},
          "slot walk green: an aggregate BODY declares members and a bare tag declares "
          "nothing -- only `g_slot`, `saved` and `hidden` are objects, and only the two "
          "spelled `static` in a .c have internal linkage", sorted(slots.items()))
    # ...and the SECOND spelling of that linkage, which carries no `static` at all (#29
    # item 4). `namespace { const char *saved; }` is internal from the namespace. This is
    # predicate C's item-4 over-clear asked in the REPORTING direction: a slot wrongly
    # called tree-wide makes a store in ANOTHER file look like a store into this one's sink.
    # Corpus-neutral -- no tree in the 99 spells it -- so it is pinned here or nowhere, and
    # the two negative spellings travel with it, since `namespace X {` and `extern "C" {`
    # are transparent to storage scope but do NOT confer internal linkage.
    ns_slots = file_scope_objects(
        "namespace {\n    const char *anon_slot;\n}\n"
        "namespace prof {\n    const char *named_slot;\n}\n"
        "extern \"C\" {\n    const char *c_slot;\n}\n")
    check(ns_slots == {"anon_slot": True, "named_slot": False, "c_slot": False},
          "#29 item 4, this predicate's half: an ANONYMOUS namespace gives its slots "
          "internal linkage with no `static` on the declaration, while a named namespace "
          "and an `extern \"C\"` block do not -- one function, tu_scope.internal_linkage, "
          "answering for both predicates", sorted(ns_slots.items()))

    # 8i/8j. GENERATED RED AND GREEN: A SUBTRACTIVE POINTER EXPRESSION IS STILL A POINTER.
    #
    #    `carries()` rejected every expression containing a `-`, to keep stringio's
    #    `ptr->pos = e - RSTRING_PTR(ptr->string)` out -- a ptrdiff_t, the one gem in the
    #    corpus safe by design. It rejected the adjusted pointer with it: `tail =
    #    RSTRING_END(str) - 1` is a valid pointer into the String's final byte, stored into a
    #    file static that outlives the call, and the row discharged `no-window` with zero
    #    hits. The discriminator is which operand carries the buffer, not whether a `-` is
    #    present.
    #
    #    THREE ARMS, because both directions have to be pinned in one fixture. `plain` is the
    #    same function with no arithmetic at all and fixes what the row should look like;
    #    `adjusted` must give that same row; `difference` must still clear, and its funnel is
    #    asserted so a parse failure cannot pass as the exclusion holding.
    minus_c = ("#include <ruby.h>\n\n"
               "static const char *tail;\n"
               "static long pos;\n\n"
               "static VALUE\n"
               "store(VALUE self, VALUE str)\n"
               "{\n"
               "    const char *p = RSTRING_PTR(str);\n"
               "    const char *e = RSTRING_END(str);\n"
               "%s\n"
               "    return Qnil;\n"
               "}\n\n"
               "static VALUE\n"
               "later(VALUE self)\n"
               "{\n"
               "    return rb_str_new(tail, pos);\n"
               "}\n\n"
               "void Init_probe(void)\n"
               "{\n"
               "    rb_define_method(rb_cObject, \"s\", store, 1);\n"
               "    rb_define_method(rb_cObject, \"l\", later, 0);\n"
               "}\n")
    minus_arms = {
        "adjusted": "    tail = e - 1;\n    (void)p;",
        "plain": "    tail = e;\n    (void)p;",
        "difference": "    pos = e - p;",
    }
    mn = {tag: _sweep(_synth("fx-minus-%s" % tag, {"ext/probe.c": minus_c % arm}))
          for tag, arm in minus_arms.items()}
    base_mn = (len(mn["plain"].derivations), len(mn["plain"].with_window),
               [h[0] for h in mn["plain"].hits])
    check(base_mn == (2, 1, ["ESCAPES-INTO-STATIC"])
          and (len(mn["adjusted"].derivations), len(mn["adjusted"].with_window),
               [h[0] for h in mn["adjusted"].hits]) == base_mn,
          "adjusted-pointer red: `tail = RSTRING_END(str) - 1` stored into a file static "
          "gives the same funnel and the same row as `tail = RSTRING_END(str)` -- unfixed "
          "the `-` alone discharged it no-window",
          "plain %s vs adjusted %s"
          % (base_mn, (len(mn["adjusted"].derivations), len(mn["adjusted"].with_window),
                       [h[0] for h in mn["adjusted"].hits])))
    check(len(mn["difference"].derivations) == 2 and not mn["difference"].with_window
          and not mn["difference"].hits
          and any(d[0] == "no-window" for d in mn["difference"].discharges),
          "pointer-difference green: `pos = e - p` into a file static is an INTEGER and is "
          "still not an escape -- the exclusion stringio depends on, now stated as 'the "
          "right operand carries the buffer' rather than as 'a `-` is present'",
          "derive %d, win %d, hits %s, discharges %s"
          % (len(mn["difference"].derivations), len(mn["difference"].with_window),
             [(h[0], h[2]) for h in mn["difference"].hits],
             [d[0] for d in mn["difference"].discharges]))
    # THE REJECTION TABLE FOR THE SPLIT ITSELF, asserted directly rather than through a row.
    # Three spellings of `-` are not subtraction and a fourth is not top-level; each of them
    # is a constant in _top_level_minus, and a constant added to fix a corpus row is not
    # tested by the corpus staying green -- POST_DECL_STOP was inert in this suite for a
    # whole round on exactly that argument.
    cuts = {t: bool(_top_level_minus(s)) for t, s in {
        "member": "s->ptr",              # `->` is a member access
        "decrement": "p--",              # `--` is a decrement
        "unary": "-RSTRING_LEN(x)",      # a leading `-` has no left operand
        "signed-rhs": "e - -1",          # ...and neither does the second one here
        "nested": "f(a - b)",            # inside a call: the value is f's, not the operand's
        "indexed": "x[-1]",              # inside a subscript
        "binary": "e - 1",               # these two are
        "tight": "e-1",
    }.items()}
    check(cuts == {"member": False, "decrement": False, "unary": False, "nested": False,
                   "indexed": False, "signed-rhs": True, "binary": True, "tight": True},
          "minus-split green: `->`, `--`, a leading sign and a `-` inside brackets are not "
          "top-level subtractions; `e - 1`, `e-1` and the first `-` of `e - -1` are",
          sorted(cuts.items()))

    # 9. PER-RULE MUTATION TABLE. A discharge rule with no generated red is a rule nobody
    #    has tested; round 5 shipped four over-clears in predicate A that a green-only
    #    suite did not catch.
    #
    #    TWO numbers per rule, because one of them lies. `clears` is how many rows the
    #    rule discharges by name. `+hits` is how many rows come BACK when the rule alone
    #    is switched off -- and that one is masked by overlap: the rules run in order, so
    #    turning off `copies-immediately` just lets `no-window` catch the same row and
    #    the delta reads +0 for a rule doing real work. Load-bearing is asserted on
    #    `clears`; `+hits` is printed because a rule that is the ONLY thing holding a row
    #    back is the more dangerous kind and the two numbers together say which is which.
    table, dead = [], []
    trees = [t for t in (_find(pool, p) for p in
                         list(NEGATIVES) + list(TRIAGED) + [q for q, _f, _n in POSITIVES])
             if t is not None]
    swept = {t: _sweep(t) for t in trees}
    for rule in RULES:
        clears = sum(sum(1 for d in s.discharges if d[0] == rule)
                     for s in swept.values())
        moved = 0
        for t in trees:
            on = {(h[0], h[1], h[2]) for h in swept[t].hits}
            off = {(h[0], h[1], h[2]) for h in _hits(t, disabled=(rule,))}
            moved += len(off - on)
        table.append((rule, clears, moved))
        if clears == 0:
            dead.append(rule)
    check(not dead, "every discharge rule is load-bearing: "
          + ", ".join("%s clears %d (+%d hits when off)" % r for r in table), dead)

    # 10. --no-discharge is the recall audit. Whatever it adds is exactly what the rules
    #     suppress, and it must stay small enough to read by hand, because every rule
    #     here is path-INSENSITIVE.
    supp = 0
    for prefix in list(NEGATIVES) + list(TRIAGED):
        d = _find(pool, prefix)
        if d is None:
            continue
        on = {(h[0], h[1], h[2]) for h in _hits(d)}
        off = {(h[0], h[1], h[2]) for h in _hits(d, discharge=False)}
        supp += len(off - on)
    check(supp > 0, "--no-discharge is a live audit: %d suppressed row(s) over the "
                    "negative controls" % supp)

    # 11. The size regime is a COLUMN and must stay one. If a future edit promotes it to
    #     a discharge, this fails until someone finds a tree where it actually fires.
    fires = 0
    for t in trees:
        for fn in Tree(t).funcs:
            for off, macro, expr, _v in derivations(fn):
                fires += size_regime(fn, off, expr)[0] == "HEAP-GUARANTEED"
    check("heap-guaranteed" not in RULES,
          "size regime is a column, not a discharge (%d HEAP-GUARANTEED classification(s) "
          "over %d trees, none of them load-bearing)" % (fires, len(trees)))

    def _index_names(src):
        return {f.name for f in Tree(_synth("fx-conform", {"ext/probe.cpp": src})).funcs}


    # ------------------------------------------------- #29 item 2: the caller-coverage
    #
    # Four of the five follow-ups were a rule generalised once and then not applied at every
    # site that needs it, so the question "which callers need this rule, and do they all
    # call it" is asserted rather than reasoned about. Two assertions, catching different
    # omissions: the BEHAVIOURAL one drives this predicate's own function index through
    # tu_scope's accept table AND its rejection table (opening the crossing up is what once
    # made a sweep invent four functions out of X-macro lists), and the SOURCE one is a lint
    # for the shape every one of the six historical appearances had -- a hand-rolled
    # whitespace skip two lines above a `== "{"`.
    check(not tu_scope.declarator_conformance(_index_names),
          "#29 item 2: predicate D's function index conforms to tu_scope's declarator table "
          "-- every accepted spelling indexed, every rejected one refused, K&R indexing "
          "nothing (the stated recall limit shared by all four predicates)",
          tu_scope.declarator_conformance(_index_names))
    check(tu_scope.unshared_declarator_crossings(
              pathlib.Path(__file__).read_text()) == [],
          "#29 item 2: no hand-rolled `)`-to-`{` crossing left in this file -- the walk is "
          "tu_scope.skip_post_declarator at every site that crosses one",
          tu_scope.unshared_declarator_crossings(pathlib.Path(__file__).read_text()))

    print("\n".join(log))
    print("\nself-test: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every discharged derivation and the rule that cleared it")
    ap.add_argument("--disable-rule", action="append", default=[], choices=RULES,
                    help="turn ONE discharge rule off. The mutation control: whatever "
                         "appears with it off and not with it on is exactly what that "
                         "rule suppresses, and it has to be justified by name.")
    ap.add_argument("--no-discharge", action="store_true",
                    help="turn every discharge rule off (recall audit, not a verdict)")
    ap.add_argument("--self-test", action="store_true",
                    help="run acceptance against the gem trees named in dirs, and exit")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(self_test(a.dirs))
    f, subs = [0] * 7, {}
    for d in a.dirs:
        root = pathlib.Path(d)
        r = sweep(Tree(root), root.name, a.disable_rule, not a.no_discharge)
        report(r, verbose=a.verbose)
        for i, v in enumerate((r.files, r.funcs, len(r.derivations), r.deriv_fns,
                               len(r.with_window), r.window_fns, len(r.hits))):
            f[i] += v
        for h in r.hits:
            subs.setdefault(h[0], []).append("%s %s:%d" % (r.name, h[1], h[2]))
    print("\nFUNNEL over %d tree(s), %d C file(s), %d function(s):\n"
          "  interior derivations ......................... %3d site(s) in %3d fn(s)\n"
          "  with a window before the last read ........... %3d site(s) in %3d fn(s)\n"
          "  surviving every discharge rule (HITS) ........ %3d site(s)"
          % (len(a.dirs), f[0], f[1], f[2], f[3], f[4], f[5], f[6]))
    for sub in ("ESCAPES-BY-RETURN", "STORES-INTERIOR", "ESCAPES-INTO-STATIC",
                "ESCAPES-INTO-CONTAINER", "ESCAPES-INTO-LIBRARY", "ESCAPES-INTO-CALLEE",
                "HELD-ACROSS-WINDOW"):
        for where in subs.get(sub, []):
            print("      %-22s %s" % (sub, where))
    if a.disable_rule:
        print("  NOTE: --disable-rule %s is the mutation control. Rows above that are "
              "absent\n        from a normal run are exactly what that rule suppresses."
              % ",".join(a.disable_rule))
    if a.no_discharge:
        print("  NOTE: --no-discharge is the recall audit, not a verdict. Every hit above "
              "that is\n        absent from a normal run is a suppression to justify by "
              "name, not a finding.")


if __name__ == "__main__":
    main()

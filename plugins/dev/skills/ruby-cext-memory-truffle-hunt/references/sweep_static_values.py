#!/usr/bin/env python3
"""Predicate C: a file-scope VALUE that nothing registers with the GC.

    python3 sweep_static_values.py <gem-dir> [<gem-dir> ...]
    python3 sweep_static_values.py --self-test <gem-dir> [<gem-dir> ...]

THE SHAPE
---------
sweep_unmarked.py walks from a wrap site into the wrapped struct's fields. A VALUE that
lives at FILE SCOPE rather than inside a wrapped struct is invisible to that walk BY
CONSTRUCTION -- there is no wrap site to start from. Such a slot has to be handed to the
GC by hand, and when it is not, ORDINARY GC frees what it points at. No compaction needed.

Three instances by hand across three unrelated gems, which is what makes this a class:

  stackprof   `objtracer`, ext/stackprof/stackprof.c:168 -- a bare `static VALUE` holding
              `rb_tracepoint_new(...)`. Filed upstream as tmm1/stackprof#245. Found by a
              HUMAN three lines from `_stackprof`, which the wrap-site sweep did cover:
              the sweep read the wrapped struct and walked straight past the static beside
              it.
  rbtrace     `rbtracer.list[].self` / `.klass` (ext/rbtrace.c:94-95) inside the file-static
              `rbtracer` object. The gc_hook at :1154 wraps NULL, so `rbtrace_gc_mark`
              (:1083) has no pointer to walk and marks nothing at all.
  kgio        the GREEN of the same shape: `localhost` (ext/kgio/accept.c:10) is registered
              at :501 with rb_gc_register_mark_object.

WHY THE OBJECT'S FIELDS ARE IN SCOPE TOO
----------------------------------------
`static VALUE x;` is only half the shape. stackprof's own registered greens --
`_stackprof.empty_string`, `_stackprof.fake_frame_names[i]` -- are FIELDS of a file-static
struct, and rbtrace's red is a field of a file-static struct two levels down
(`rbtracer.list[MAX_TRACERS]` -> `rbtracer_t.self`). Enumerating only bare scalars makes
acceptance item 2 pass for the wrong reason: the greens would be "not flagged" because they
were never looked at. So stage 1 walks file-scope struct OBJECTS recursively, through
arrays and nested struct types, and the greens are discharged by a named rule rather than
by absence.

THE DISCRIMINATOR, AND THE INVERSE RULE THAT REPLACES A VOCABULARY
------------------------------------------------------------------
"Is the static assigned from something that ALLOCATES?" is the right question, but a list
of allocating functions is a recall hole waiting to happen -- the sweep is only as good as
the day the list was last extended. So the gate is the INVERSE: a slot survives only if
EVERY source is provably one of the safe shapes. Anything else -- an unrecognised call, a
bare identifier, an opaque expression -- is a hit. ALLOC_PRIM exists only to ANNOTATE a hit
with a recognised allocator ("rb_tracepoint_new"), never to decide one.

That inversion is what catches rbtrace. `tracer->self = self;` (:750) is not an allocating
call at all; it stores an arbitrary caller-supplied object. A predicate gated on an
allocator list reports rbtrace clean, and rbtrace is the worse bug of the two.

THE DISCHARGE RULES, EACH FORCED BY UNEDITED CORPUS CODE
--------------------------------------------------------
Six, each named in the output, each with a --disable-rule mutation control in --self-test.
The controls are seven gem trees, not the four obvious ones: with only stackprof/kgio/
rbtrace/vernier the table called `const-published` decorative, because the single corpus
site that needs it is in msgpack. A mutation table is only as strong as its control set,
and "no control broke" is a statement about the controls before it is one about the rule.

  registered-slot   rb_global_variable(&v) / rb_gc_register_address(&v). Roots the SLOT, so
                    later stores are covered too.
                    stackprof.c:1007 `rb_global_variable(&_stackprof.empty_string)`
  registered-value  rb_gc_register_mark_object(v). Marks AND pins the VALUE that was live
                    at that moment -- and NOTHING ELSE. gc.c pushes that object onto
                    vm->mark_object_ary and never reads the C variable again, so a store
                    made after the call puts an object in the slot that nothing roots while
                    the first one stays rooted forever. The rule therefore requires every
                    unsafe store to PRECEDE the call, approximated as same file and lower
                    byte offset; a store of a provably immediate or const-table value is
                    exempt, since it cannot leave a collectable object behind.
                    kgio accept.c:500-501 on `localhost` is the shape that discharges: one
                    assignment, then the registration, and never touched again.
  immediate         every source is Qnil/Qtrue/INT2FIX/ID2SYM(static intern)/another
                    immediate-only static.  stackprof's 28 `S(name)` symbols
  const-table       every source comes from rb_define_class/rb_define_module or is a core
                    `rb_cFoo`/`rb_mFoo`/`rb_eFoo`. NOT because of the constant table: that
                    marks MOVABLE (gc.c mark_const_entry_i -> gc_mark_internal, with a
                    matching update_const_tbl_i on the compaction side), so a class IS
                    relocated out from under a constant. The DEFINING call separately PINS.
                    rb_define_class, rb_define_class_under, rb_define_class_id_under,
                    rb_define_module{,_under,_id_under} and rb_struct_define_under all reach
                    rb_vm_register_global_object (class.c:1495/1502/1552/1604/1608/1633/1639
                    on 4.0.6; spelled rb_vm_add_root_module on 2.7-3.1) -- which is LITERALLY
                    the function rb_gc_register_mark_object delegates to (gc.c:3437), and
                    vm->mark_object_ary is marked by rb_gc_mark_vm_stack_values ->
                    gc_mark_and_pin_internal. So const-table is `registered-value` reached by
                    another spelling: a pin, permanent (RCLASS_IS_ROOT is set once and never
                    cleared), and it OUTLIVES remove_const.
                    That is why the rooted-vs-pinned distinction this skill is built on does
                    not bite here -- and why okra, which caches a RUBY-defined class read via
                    rb_const_get, relocates and SIGSEGVs while iconv's six
                    rb_define_class_under statics do not. Measured 4.0.6/3.4.10/3.1.6
                    arm64-darwin, 3/3, against an internal control (rb_class_new +
                    rb_const_set, same Init frame, adjacent slots, same constant table) that
                    relocated in every run.
                    IF CONST_CALL IS EVER EXTENDED, the test is "does this call reach
                    rb_vm_register_global_object?" -- NOT "does it install a constant?".
                    vernier vernier.cc:1246
  const-published   the slot is the VALUE argument of rb_define_const / rb_const_set. The
                    rooting is at a USE site, so no source rule can see it. It is a VALUE
                    rooting like registered-value and carries the same position rule: the
                    constant table holds the object the argument evaluated to, so publishing
                    one String and then storing a second roots one of the two.
                    msgpack extension_value_class.c:33
  wrapped           the file-static OBJECT is itself handed to TypedData_Wrap_Struct with a
                    dtype whose dmark is non-NULL, AND the wrapper it returns is not
                    provably thrown away. Not a clear -- a HAND-OFF: those fields are
                    exactly what sweep_unmarked.py's walk covers, and reporting them here
                    would double-count predicate A's rows. But a dmark only runs while its
                    wrapper is reachable from a root, so a discarded wrapper marks nothing
                    and the hand-off would route the fields to a walk that finds a
                    correct-looking dmark and clears them -- an over-clear laundered through
                    a second script. Only two shapes count as proof: the call's result is
                    unused, or it is assigned into a file-scope slot that is itself
                    unrooted. Everything else -- returned, assigned to a local, nested in
                    another call -- is the documented floor in wrapper_dest, because the
                    strict reading makes the ordinary allocator cfunc a hit in every gem.
                    stackprof.c:995 `TypedData_Wrap_Struct(..., &stackprof_type, &_stackprof)`
                    is the corpus's only wrapped site (twice, two versions) and it assigns
                    to `gc_hook`, which rb_global_variable roots.

The wrapped rule is the one that makes the stackprof/rbtrace pair discriminating rather
than lucky. Both gems wrap a gc_hook at Init; stackprof passes `&_stackprof` and rbtrace
passes `NULL`. Requiring a resolved dtype with a non-NULL dmark is not decoration either:
msgpack ships `.dmark = NULL` on one of the two dtypes wrapping the same struct (round-5
over-clear (a) in sweep_unmarked.py), and a wrap-implies-safe rule would clear it.

CORRECTION CARRIED FORWARD FROM PREDICATE A
-------------------------------------------
Verified against ruby/internal/symbol.h on 4.0.6: rb_intern, rb_intern2, rb_intern_str and
rb_to_id produce STATIC symbols -- "would never be garbage collected". Only rb_to_symbol
(:226) produces a dynamic, collectable one. So `ID2SYM(rb_intern("wall"))` is immediate and
`ID2SYM(rb_to_symbol(str))` is a hit. The discriminator is the interning function, NOT
rb_intern-vs-rb_intern_str, and an earlier brief had it the other way round.

BE CAREFUL WITH "IT'S PROBABLY A CLASS"
---------------------------------------
Over-clearing a class-shaped static is the failure mode this effort exists to prevent, so
const-table is an ALL-SOURCES rule and it recurses. kgio's `cClientSocket` is the case that
proves the rule earns its keep in both directions: accept.c:504 initialises it from
`cKgio_Socket` (registered), and accept.c:50 reassigns it from `set_accepted`'s parameter --
an arbitrary caller-supplied class. One source safe, one not, so it reports. A first cut
that discharged on ANY const-table source clears it.

READING a constant is a GRADE, not a discharge. `x = rb_const_get(mod, id)` is rooted only
while that constant is not reassigned or removed, so it comes out as CONST-LOOKUP -- the
lowest-severity hit, but still a hit. Two things forced that, and the second is the reason
the mutation table exists: kgio registers three rb_const_get results with
rb_gc_register_mark_object (accept.c:500-505) and leaves two unregistered (kgio_ext.c:78-79),
so the gem's own author does not treat a lookup as a root; and while both were one rule,
EVERY mark_object site in the corpus sat on an rb_const_get result, so const-table shadowed
`registered-value` entirely and the table reported a registration primitive the brief names
as a required counter-shape as DECORATIVE. The rule was not decorative; the precedence was
wrong.

A ZERO MUST BE READABLE
-----------------------
Every run prints the funnel: files, file-scope VALUE slots found, slots with at least one
allocating source, slots discharged (per rule), slots remaining. "0 hits" on a tree with 0
slots is a different fact from "0 hits" on a tree with 40 slots, and only the counters tell
them apart. Round 4's `*: 0 suspects` on an unexpanded shell glob is the precedent.

C++ SCOPES, AND THE STATIC THAT LIVES IN A CLASS BODY
-----------------------------------------------------
`static VALUE` is not a file-scope-only spelling. C++ gives it three more homes, and all
three have the SAME storage duration -- one slot, alive for the whole process, invisible to
the GC unless somebody hands it over:

  namespace prof { static VALUE cache; }     namespace scope IS file scope
  extern "C" { static VALUE cache; }         a linkage block is not a scope at all
  class Registry { static VALUE cache; };    a static DATA MEMBER

top_level_units walked OVER all three. A namespace or a linkage block reads as a function
body, so its entire contents were consumed as one unit and dropped; a class body reads as
neither an aggregate nor a function, so it was yielded whole and then rejected by
_unit_slots. This is the sibling of the hole sweep_unmarked.py closed the same round --
`struct|union` did not match `class`, so vernier measured 0 suspects while holding three
unmarked VALUEs -- and it recurs here because the two scripts lex C++ separately.

The measured before-state on a probe shaped like vernier: an INDENTED `    static VALUE
cache;` inside a class body was found by ACCIDENT, because the function-local-static scan
is `^[ \t]+static\s+VALUE` and a member declaration is also indented -- keyed BARE, so
`Registry::cache` and a file-scope `cache` collapse into one row. An UNINDENTED member, or
one written `public: static VALUE cache;` on a single line, was not found at all. Accidental
recall under the wrong key is the worst of the three outcomes: it looks like coverage.

So class_scopes walks namespaces, linkage blocks and nested class bodies for real, and
_class_member_slots keys what it finds `Class::member`. The qualifier is not decoration --
`rb_global_variable(&Registry::cache)` normalises to `Registry::cache`, and a bare key
cannot match it.

  access labels     `public:` is a LABEL, not a statement, so no `;` separates it from the
                    member after it. Same one-fragment defect sweep_unmarked.py fixed in its
                    field matcher this round; here it hides the FIRST member of every
                    section.
  method bodies     a class body interleaves declarations with method bodies. blank_bodies
                    turns both braces into `;` so `void f() { } static VALUE cache;` is two
                    fragments, not one -- without it every member after the first method is
                    invisible, which is the commonest C++ class layout there is.
  out-of-line       `VALUE Registry::cache = rb_str_new2("x");` at file scope is the
                    DEFINITION the ODR requires, and it is where the interesting assignment
                    usually lives. It is BOTH a declaration and a store, and it is treated
                    as both: declarator() keeps the `::` so the definition raises a slot
                    under the same `Registry::cache` key the class body raised (dedupe
                    merges them), and the ordinary source scan reads its initialiser. The
                    slot is raised from the definition too, deliberately, so a class
                    declared in a header this tree does not ship still leaves a row rather
                    than a silent zero.
  both spellings    a member is `Registry::cache` from outside and bare `cache` from inside
                    its own methods, and both name one object, so the source pattern makes
                    the qualifier OPTIONAL. That inherits the tree-wide name aliasing this
                    script already documents for its dedupe rule -- an unrelated `cache =`
                    elsewhere in the tree is read as a source. Registrations are matched on
                    the QUALIFIED key only: an extra source can only make an all-sources
                    discharge harder, but an extra registration match would CLEAR a row, and
                    that is the direction this sweep is not allowed to be wrong in.

The descent was ported into the SLOT walk and not into the FUNCTION index, and that is the
half-fix a review caught a round later. A file-scope slot inside `namespace prof { ... }`
was found, while `_index_funcs` still counted raw braces -- so every function defined
inside a namespace or an `extern "C"` block sat at nonzero depth, was never indexed, and
its body never contributed a span. A namespaced function holding `static VALUE cache;
cache = rb_str_new_cstr("x");` therefore measured `slots 0/0, HITS 0`: not a row dropped
but an INDEX emptied, which is the shape of zero that reads as a clean gem. The walk now
lives in tu_scope.py -- one implementation, four ports' worth of the same lesson -- and
both the slot walk and the function index read the depth from it.

THE FUNCTION INDEX HAD A SECOND WAY TO COME BACK EMPTY, and the next review found it: the
walk required the body's `{` to sit immediately after the parameter list's `)`, so a
definition carrying an attribute was not indexed either. `static VALUE bad(void)
__attribute__((noinline)) { static VALUE cache; ... }` measured the same `slots 0/0, decls
0, HITS 0`. That is the FOURTH appearance of one walker gap across three scripts --
predicate D hit it twice in one round, for `__attribute__`/`noexcept` and then for a C++
trailing return type -- so tu_scope.skip_post_declarator is now its one home, and its
REJECTION TABLE travels with it. The table matters more here than it looks: the failure
mode of a walk that crosses too much is a body indexed under the wrong name, and in THIS
predicate a wrongly-opened span is then scanned for `static VALUE` declarations. Both
callers assert the table, and each holds down a constant the other does not exercise.

KNOWN, NOT FIXED HERE
---------------------
A METHOD BODY defined inline in a class body is still not scanned for function-local
statics: a class body is a real scope, so it is not transparent, and the members walk reads
declarations rather than statements. `class R { VALUE f() { static VALUE cache; ... } };`
is therefore invisible. Same direction as the defect above and a smaller population --
recorded here rather than left for a third round to rediscover.

A namespace-scope static is keyed BARE (`cache`), because top_level_units walks THROUGH a
namespace rather than qualifying what it finds -- only class bodies qualify. So a
registration spelled from outside the namespace, `rb_global_variable(&prof::cache)`,
normalises to `prof::cache` and does not match. That is an OVER-REPORT, the safe direction,
and closing it by stripping the qualifier is the one thing this file must not do: the same
strip would let `rb_global_variable(&Registry::cache)` discharge an unrelated file-scope
`cache`, which is the over-clear the class-member rule was written to prevent. The honest
fix is to qualify namespace-scope keys the way class members are qualified; it is a corpus
-visible change and it is not one of this pass's threads. Found while pinning the namespace
descent, and pinned as a limit rather than left to be rediscovered.

`_index_structs` still indexes `struct|union` only, so a file-scope OBJECT of C++ class type
(`static ThreadTable table;`) resolves to no struct body and contributes no INSTANCE fields;
and `_struct_slots` splits a struct body on `;` alone, so a field declared after an in-class
method definition shares a fragment with it and is dropped. Both are the instance-field
half of the C++ hole, they are predicate A's subject matter more than this one's, and
neither is touched here. Named so the next reader gets a gap rather than a clean sheet.

LEXING
------
Copied from sweep_unmarked.py. strip_noise blanks comments and string bodies but keeps
newlines; strip_directives keeps line count AND byte length. So byte offsets and line
numbers into the stripped text both match the original file, which every hit here depends
on. --self-test asserts that round-trip on a real corpus file rather than taking it on
trust.

ONE NAME IS NOT ONE SLOT
------------------------
Internal linkage is per translation unit. Two .c files each writing `static VALUE cache;`
declare two objects, and a `rb_global_variable(&cache)` in one of them roots one of them.
Deduping by NAME merged the pair into a single row, discharged it on that registration, and
the other file's `cache = rb_str_new(...)` disappeared behind HITS 0. Slots declared
`static` in a translation unit are therefore keyed per file, and their sources,
registrations and publications are searched in that file alone -- the uniform rule, applied
whether or not the name repeats, because a conditional split is a different sweep on the
trees where it fires. External-linkage names stay tree-wide keyed, and so do statics
declared in HEADERS (see TU_EXT).

The corpus says this is a precision fix and not only a recall one. kgio goes 9 slots to 12
and digest-3.2.0 goes from THREE hits to none: `Init_bubblebabble` declares
`VALUE rb_mDigest, rb_mDigest_Instance, rb_cDigest_Class;` as LOCALS, in a translation unit
that does not contain digest.c's file statics at all, and the tree-wide source scan read
`rb_mDigest = rb_digest_namespace()` and two rb_const_get calls out of that function as
stores to digest.c's slots. Three false rows, all of them from the merge.

A VALUE ROOTING IS NOT A SLOT ROOTING
-------------------------------------
rb_gc_register_mark_object and rb_define_const both root the OBJECT the argument evaluated
to. rb_global_variable and rb_gc_register_address root the ADDRESS. Only the second pair
covers a later store, and the first pair was discharging slots on the name alone. See the
registered-value and const-published entries above for the position rule and its floor.

NAME RESOLUTION IS SHARED, AND LIVES IN tu_scope.py
---------------------------------------------------
Every lookup that turns a NAME at a use site into a DEFINITION goes through
`tu_scope.bind`, which states C's linkage rule once for all four predicates: a use binds
to a definition in its own file first, a `static` definition in another .c/.cc/.cpp/.cxx
is not a candidate at all, and everything else -- non-static definitions, and anything
declared in a HEADER -- stays tree-wide. That module is a sibling file and these scripts
will not run without it; references/ is the unit that ships.

ACCEPTANCE (--self-test): see self_test(). Run it before trusting any result -- silence is
a property of the query until the counters say otherwise. 55 checks; seventeen of them are
the round-9 review threads, and every one of those was measured GREEN on the pre-fix script
-- six of the eight shapes with `slots 0`, a clean sheet produced by the parser finding
nothing. The corpus is nearly neutral on all of them -- zero new HIT rows across 99 trees --
so these fixtures are most of the evidence in the repository that the fixes do anything. The
one exception is worth reading: block-scoping function-local statics takes date 3.5.1 from
19 slots to 65, because it declares 46 `static VALUE pat = Qnil;` in 46 different functions
and the file-scoped key merged them by name. All 65 still discharge, and the one discharge
REASON that moves is the finding: date_parse.c's merged `pat` had been cleared by an
`rb_gc_register_mark_object` inside `regcomp()` -- a call that roots that function's OWN
LOCAL `pat`, in a different function entirely, and never touched any static at all.
"""
import argparse
import pathlib
import re
import shutil
import sys
import tempfile

# The linkage rule, shared with the other three predicates. Sibling module, so
# `python3 .../sweep_static_values.py` finds it wherever it is run from; references/ is the
# unit that ships, and a script copied out of it on its own will not import.
import tu_scope
from tu_scope import TREE, Scope

C_EXT = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp")
# A TRANSLATION UNIT, as opposed to a header. The distinction only matters for internal
# linkage: `static VALUE cache;` in a .c is one object belonging to that file, and scoping it
# there is exact. The same line in a .h is one object PER INCLUDING .c -- the header is not a
# translation unit at all, and the stores live in files this sweep cannot connect to it
# without resolving includes. Scoping a header slot to the header therefore hides every store
# it has and reports UNSOURCED: 10 rows across unicorn x2 and yajl-ruby, all of them noise,
# none of them the defect. Header statics stay tree-wide keyed. THE RESIDUAL, stated rather
# than left implicit: a `static VALUE` in a header included by two .c files is two objects
# this sweep still merges into one row, so a registration in one includer still discharges
# the other's slot -- the very shape scoping fixes for .c files. Closing it needs include
# resolution, which is not in this pass.
TU_EXT = tu_scope.TU_EXT

ALL_RULES = ("registered-slot", "registered-value", "immediate", "const-table",
             "const-published", "wrapped")

# ---------------------------------------------------------------- lexing helpers
#
# Verbatim from sweep_unmarked.py. Comments and string literals are stripped before any
# brace matching: a brace inside either one desynchronises the matcher, and a desynchronised
# matcher yields a bogus struct body -- which here means enumerating the wrong fields, or
# none, and a struct with no fields reads as a clean sheet.


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


def split_args(text):
    """Split an argument or declarator list on top-level commas."""
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


def rhs_after(src, eq_idx):
    """The assigned expression just past `=` at eq_idx, to the next top-level `,`/`;`.

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


NOT_CALLS = {"if", "for", "while", "switch", "return", "sizeof", "defined", "do", "else",
             "case", "typeof", "alignof", "static_assert", "catch", "__attribute__",
             "assert", "RB_LIKELY", "RB_UNLIKELY", "LIKELY", "UNLIKELY"}


def find_calls(body):
    """[(name, args, name_start, past_close)] for every call in `body`."""
    out = []
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?=\()", body):
        if m.group(1) in NOT_CALLS:
            continue
        args, end = call_args(body, m.end())
        if args is not None:
            out.append((m.group(1), args, m.start(), end))
    return out


# C++ SCOPE HEADS AND THE WALK THAT TREATS THEM AS TRANSPARENT -- one implementation, in
# tu_scope.py, beside the linkage rule. Four ports of the same three brace dispositions
# across three scripts before it was written down once; the fourth was this file's own
# function index, which kept counting raw braces after its slot walk had been fixed.
NAMESPACE_HEAD = tu_scope.NAMESPACE_HEAD
LINKAGE_HEAD = tu_scope.LINKAGE_HEAD
top_level_units = tu_scope.top_level_units
# A NAMED class/struct/union body, optionally `final`, optionally with a base clause.
# Anonymous aggregates (`static struct {`) deliberately do not match: they stay on the
# existing _unit_slots path, which reads their declarator list and walks the OBJECT.
# Class bodies are THIS file's business alone -- they are a real scope with a qualified
# key, so tu_scope's walk yields them inert and class_scopes below descends them.
CLASS_HEAD = re.compile(r"\b(?:class|struct|union)\s+(\w+)\s*(?:final\b\s*)?(?::[^{;]*)?$")
ACCESS_LABEL = re.compile(r"\b(?:public|private|protected)\s*:")


def class_scopes(src, base=0, prefix=""):
    """[(qualifier, body offset, body text)] for every C++ class/struct/union BODY.

    Descends through namespaces and linkage blocks (which do not qualify a member name) and
    through nested class bodies (which do), so `class Outer { class Inner { ... }; };` yields
    both, and Inner's members are keyed `Outer::Inner::member`.

    Function bodies are NOT descended: a class defined inside a function is a local class,
    and a local class cannot have a static data member at all.
    """
    n, i, start = len(src), 0, 0
    while i < n:
        c = src[i]
        if c == "{":
            close = match_brace(src, i)
            if close < 0:
                return
            pre = src[start:i].rstrip()
            body, boff = src[i + 1:close], base + i + 1
            m = CLASS_HEAD.search(pre)
            if m:
                qual = prefix + m.group(1) + "::"
                yield qual, boff, body
                for s in class_scopes(body, boff, qual):
                    yield s
            elif NAMESPACE_HEAD.search(pre) or LINKAGE_HEAD.search(pre):
                for s in class_scopes(body, boff, prefix):
                    yield s
            i = start = close + 1
            continue
        if c == ";":
            start = i + 1
        i += 1


def blank_bodies(src):
    """Blank every brace-delimited group, replacing BOTH braces with `;`.

    A class body interleaves member declarations with method bodies, and a method body's
    statements are not members. Length and newlines are preserved so offsets into the result
    still map back to the file, which every hit's file:line depends on.

    Turning the braces into `;` rather than blanking them is what separates
    `void f() { } static VALUE cache;` into two fragments. Left as one, the fragment leads
    with `void f()` and the member declaration behind it never matches -- and "a method,
    then a static" is the commonest class layout there is, so that alone would have made the
    descent look like it works while finding nothing.
    """
    out, depth = [], 0
    for ch in src:
        if ch == "{":
            depth += 1
            out.append(";")
        elif ch == "}":
            depth = max(0, depth - 1)
            out.append(";")
        else:
            out.append(ch if not depth else ("\n" if ch == "\n" else " "))
    return "".join(out)


# ------------------------------------------------------- the vocabulary

REGISTER_SLOT = re.compile(r"^(?:rb_global_variable|rb_gc_register_address)$")
REGISTER_VALUE = re.compile(r"^rb_gc_register_mark_object$")

IMMEDIATE_CONST = {"Qnil", "Qfalse", "Qtrue", "Qundef",
                   "RUBY_Qnil", "RUBY_Qfalse", "RUBY_Qtrue", "RUBY_Qundef"}
# INT2FIX is immediate; INT2NUM IS NOT. rb_int2num_inline returns RB_INT2FIX(v) only when
# RB_FIXABLE(v) and rb_int2big(v) otherwise (ruby/internal/arithmetic/int.h:239), which is
# a heap Bignum; LONG2NUM the same (long.h:308). DBL2NUM is out because flonums are
# conditional on the build.
IMMEDIATE_CALL = re.compile(r"^(?:RB_)?(?:INT2FIX|LONG2FIX|UINT2FIX|ULONG2FIX|CHR2FIX)$")
# ...but INT2NUM OF A LITERAL is. mysql2 result.c:1265-1267 writes `opt_time_year =
# INT2NUM(2000)`, `INT2NUM(1)`, `INT2NUM(0)` into three unregistered statics and registers
# only the two neighbours that really do allocate (rb_str_new2, rb_float_new, :1262/:1264).
# 2000 is FIXABLE on every platform Ruby builds on, so those three are not defects, and
# reporting them buries the two real hits in the same funnel. The literal is the whole
# licence: `INT2NUM(n)` for a runtime n stays a hit.
NUM_CALL = re.compile(r"^(?:RB_)?(?:INT2NUM|UINT2NUM|LONG2NUM|ULONG2NUM|SIZET2NUM)$")
INT_LITERAL = re.compile(r"^[+-]?(?:0[xX][0-9a-fA-F]+|\d+)[uUlL]*$")
FIXNUM_MAX = 2 ** 30 - 1        # the 32-bit floor; anything above is platform-dependent
ID2SYM_CALL = re.compile(r"^(?:RB_)?(?:STATIC_)?ID2SYM$")
# Verified against ruby/internal/symbol.h on 4.0.6: these four say "would become static
# ones; i.e. would never be garbage collected". rb_to_symbol (:226) says "would become
# dynamic ones; i.e. would be garbage collected" and is deliberately absent.
STATIC_INTERN = re.compile(r"^(?:rb_intern|rb_intern2|rb_intern3|rb_intern_str"
                           r"|rb_intern_const|rb_intern_str_const|rb_to_id)$")

# DEFINING a class or module is a discharge: the call itself installs the object in the
# constant table under a permanent name, and that is the shape the brief blesses.
CONST_CALL = re.compile(
    r"^(?:rb_define_class|rb_define_class_under|rb_define_class_id_under"
    r"|rb_define_module|rb_define_module_under|rb_define_module_id_under"
    r"|rb_struct_define_under)$")
# rb_define_error is deliberately NOT here: NO SUCH CRUBY API EXISTS -- `grep -r
# rb_define_error` over the whole 14,766-file 4.0.6 tree returns nothing. So the name could
# only ever bind to a GEM-LOCAL helper, discharged without its body being read; a generated
# red confirms it cleared `eBoom = rb_define_error(...)` whose helper returns rb_class_new,
# a shape measured MOVABLE. Dropping it costs 0 corpus discharges.
# rb_define_class_id_under_no_pin deliberately does NOT match either -- the anchors exclude
# it, and it is the one define-shaped CRuby call that does not pin.
# rb_struct_define_under is here and rb_struct_define is NOT, and the difference is the
# whole msgpack case: `rb_struct_define(NULL, ...)` builds an ANONYMOUS Struct class that no
# constant holds until somebody calls rb_define_const on it.
# READING a constant is NOT. kgio forced the split from inside one gem: accept.c:500-505
# registers THREE rb_const_get results with rb_gc_register_mark_object and kgio_ext.c:78-79
# leaves two unregistered. The gem's own author did not treat a constant lookup as a root,
# and they are right -- `remove_const` or a reassignment strands the C static, and one of
# the three (Kgio::LOCALHOST) is a plain String constant, not a class. Folding both into
# one rule also made `registered-value` unreachable: every mark_object site in the corpus
# is on an rb_const_get result, so the const rule shadowed it and the mutation table
# reported the registration primitive the brief names as a required counter-shape as
# DECORATIVE. Grade, do not discharge.
CONST_LOOKUP = re.compile(
    r"^(?:rb_const_get|rb_const_get_at|rb_const_get_from|rb_path2class|rb_path_to_class"
    r"|rb_singleton_class)$")
# `rb_cObject`, `rb_mKernel`, `rb_eRuntimeError` -- core objects the VM roots itself.
CORE_OBJ = re.compile(r"^rb_[cme][A-Z]\w*$")

# ANNOTATION ONLY. Never gates a verdict -- the gate is the inverse rule (every source must
# be provably safe), because an allocator list is a recall hole that fails silent.
ALLOC_PRIM = re.compile(
    r"^(?:rb_str_new\w*|rb_ary_new\w*|rb_hash_new\w*|rb_funcall\w*|rb_tracepoint_new"
    r"|rb_proc_new|rb_obj_alloc|rb_class_new_instance\w*|rb_struct_new|rb_eval_string\w*"
    r"|rb_sprintf|rb_vsprintf|rb_enc_str_new\w*|rb_utf8_str_new\w*|rb_usascii_str_new\w*"
    r"|rb_external_str_new\w*|rb_str_buf_new\w*|rb_range_new|rb_thread_create|rb_mutex_new"
    r"|rb_fiber_new|rb_block_proc|rb_obj_dup|rb_obj_clone|rb_str_dup|rb_ary_dup"
    r"|rb_to_symbol|rb_str_intern|rb_id2str|rb_sym2str|rb_inspect|rb_obj_as_string"
    r"|rb_String|rb_Array|rb_Hash|rb_Integer|rb_Float|rb_num2\w+|INT2NUM|LONG2NUM"
    r"|ULONG2NUM|UINT2NUM|SIZET2NUM|DBL2NUM|rb_int2inum|rb_uint2inum|rb_ll2inum"
    r"|TypedData_Wrap_Struct|Data_Wrap_Struct|TypedData_Make_Struct|Data_Make_Struct"
    r"|rb_data_object_wrap|rb_data_typed_object_wrap)$")

CAST = re.compile(r"^\(\s*(?:const\s+|unsigned\s+|signed\s+)*"
                  r"(?:VALUE|ID|long|int|unsigned|uintptr_t|intptr_t)\s*\*?\s*\)")

TYPE_KW = {"const", "volatile", "register", "struct", "union", "enum", "unsigned",
           "signed", "long", "short", "int", "char", "void", "float", "double",
           "static", "extern", "inline", "typedef", "_Atomic", "restrict",
           # THREAD-LOCAL STORAGE IS STATIC STORAGE, for this predicate's purposes. A
           # `thread_local VALUE` lives for the whole thread, is read across calls, and sits
           # outside every root set Ruby scans -- worse than a plain file static, because
           # rb_global_variable roots one address and a TLS slot has one per thread. These
           # three spellings (C++11, C11, gcc/clang) were in NEITHER this set nor the
           # ALL-CAPS escape hatch, so the whole declaration failed the leading-token gate
           # and was dropped: 0 slots, which reads as a clean file.
           "thread_local", "_Thread_local", "__thread"}
# Storage/type qualifiers that may sit between `static` and `VALUE` in a function-local
# declaration -- `static volatile VALUE cache;` is one slot, not zero.
LOCAL_QUAL = r"(?:(?:const|volatile|_Atomic|register|thread_local|_Thread_local|__thread)\s+)*"


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


def norm(expr):
    """`&_stackprof.fake_frame_names[i]` -> `_stackprof.fake_frame_names[]`.

    Subscripts collapse because the registration loop indexes with `i` and the slot is
    declared with TOTAL_FAKE_FRAMES; comparing them literally never matches and both greens
    in acceptance item 2 come back red.
    """
    e = re.sub(r"\s+", "", expr)
    e = e.replace("->", ".")
    e = re.sub(r"\[[^\[\]]*\]", "[]", e)
    return e.lstrip("&")


# ------------------------------------------------------- slots


class Slot:
    __slots__ = ("path", "off", "key", "root", "kind", "decl", "opath", "ooff", "scope")

    def __init__(self, path, off, key, root, kind, decl, opath=None, ooff=0, scope=None):
        # path/off point at the VALUE MEMBER, which for rbtrace is rbtracer.c:94-95 inside
        # `rbtracer_t` -- a different declaration, and often a different file, from the
        # file-scope object at :107. Reporting the object's line for both sends the reader
        # to a struct with no VALUE in it.
        self.path = path        # pathlib.Path of the VALUE declaration
        self.off = off          # byte offset of that declaration in the stripped text
        self.key = key          # normalised slot path, e.g. `rbtracer.list[].self`
        self.root = root        # file-scope object name, for the `wrapped` rule
        self.kind = kind        # "scalar" | "field" | "ptr"
        self.decl = decl        # the declaration text, trimmed
        self.opath = opath or path   # the file-scope object's declaration
        self.ooff = ooff or off
        # INTERNAL LINKAGE IS PER TRANSLATION UNIT, and this is the REGION the object
        # belongs to -- a tu_scope.Scope, `TREE` for a slot that is one object tree-wide,
        # `Scope(path)` for a file static, and `Scope(path, span)` for one declared inside a
        # function body, which is one object per FUNCTION. `static VALUE cache;` in two .c files
        # is TWO objects with one name; a `rb_global_variable(&cache)` in the first roots the
        # first and says nothing about the second. Name-only dedupe merged the pair into one
        # row, the registration discharged the merged row, and the unregistered slot in the
        # other file vanished with HITS 0.
        #
        # The rule is UNIFORM: every internal-linkage slot is scoped, whether or not its name
        # is declared `static` in more than one file. A conditional split ("only when the name
        # repeats") is a different sweep on trees where it fires and this one everywhere else,
        # and the merge it would keep is exactly the merge that hid the defect. Scoping also
        # narrows sources, registrations and publications for that slot to its own file, which
        # is what internal linkage means: no other TU can name the object.
        #
        # External-linkage slots stay tree-wide keyed, deliberately. rmagick declares 67
        # `EXTERN VALUE x;` in a header and assigns them across 40 .cpp files; scoping those
        # per file would raise 67 UNSOURCED headers plus 67 unresolvable stores.
        #
        # ROUND 9: THE FILE IS NOT THE INNERMOST SCOPE THERE IS. Two functions in ONE
        # translation unit may each declare `static VALUE cache;`, and those are two objects
        # too -- neither can name the other's. Keyed `(file, "cache")` they merged, one
        # function's `rb_global_variable(&cache)` discharged the merged row, and the other's
        # unrooted `cache = rb_str_new(...)` vanished behind HITS 0: the cross-TU defect
        # above, one scope level down. A Scope carries the function's body span for those,
        # and `contains()` is what every source, registration and publication is filtered by.
        self.scope = scope if scope is not None else TREE

    @property
    def field(self):
        """Last path component, subscript stripped -- the token a store names."""
        return self.key.split(".")[-1].replace("[]", "")

    @property
    def ident(self):
        """The dedupe / memo identity: scope-qualified for an internal-linkage slot."""
        return (self.scope, self.key)


# ------------------------------------------------------- the tree


class Tree:
    """One gem's C sources, indexed for whole-tree slot / source / registration resolution."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.files = {}
        self.macro_defs = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and p.suffix in C_EXT and ".git" not in p.parts:
                try:
                    # Macros are indexed BEFORE directives are blanked: stackprof's
                    # `#define S(name) sym_##name = ID2SYM(rb_intern(#name));` lives
                    # entirely inside a directive line, and without it nothing in the tree
                    # textually assigns to sym_wall -- 28 immediates report as UNSOURCED.
                    decommented = strip_noise(p.read_text(errors="replace"))
                except OSError:
                    continue
                self._index_macros(decommented)
                self.files[p] = strip_directives(decommented)
        self.pasted = self._expand_pastes()
        self.all = "\n".join(self.files.values()) + "\n" + self.pasted
        self.structs = {}        # struct/union name -> body text
        self.aliases = {}        # typedef name -> underlying name
        self.dtypes = {}         # rb_data_type_t name -> initialiser body
        self.funcs = {}          # in-tree function name -> body text
        self.func_spans = {}     # path -> [(body start, body end)] for top-level functions
        # path -> anonymous-namespace body spans. Internal linkage with no `static` on the
        # declaration; see _unit_slots.
        self.anon = {p: tu_scope.anonymous_namespace_spans(t)
                     for p, t in self.files.items()}
        for path, src in self.files.items():
            self._index_structs(path, src)
            self._index_aliases(src)
            self._index_dtypes(src)
            self._index_funcs(path, src)
        self.unresolved_members = 0   # struct members whose type did not resolve
        # C++ descent coverage. A tree with 0 class members is only readable next to the
        # number of class bodies the walk actually entered: "0 members" on 0 bodies is a C
        # gem, "0 members" on 40 bodies is a measured absence, and a parse that entered no
        # bodies at all reports the same 0 as both.
        self.class_bodies = 0
        self.slots = []
        self.objects = {}        # file-scope object name -> (path, offset)
        for path, src in self.files.items():
            self._index_slots(path, src)
        self.class_members = sum(1 for s in self.slots if "::" in s.key)
        # Dedupe by SLOT IDENTITY -- (scope, key) -- not by name. An internal-linkage slot is
        # one object per translation unit, so kgio's `static VALUE sym_wait_writable` in
        # three files is three slots and stays three; an external-linkage name is one object
        # tree-wide however many files declare it, so rmagick's header declarations still
        # collapse. `decls` keeps the pre-dedupe count so the collapse is visible.
        self.decls = len(self.slots)
        seen, uniq = set(), []
        for s in self.slots:
            if s.ident not in seen:
                seen.add(s.ident)
                uniq.append(s)
        self.slots = uniq
        # (scope, key) -> slot, scalars only: the alias-resolution index for is_immediate /
        # is_const_table. Scope-keyed for the same reason the slots are.
        self.scalars = {s.ident: s for s in self.slots if s.kind == "scalar"}
        self.registrations = self._index_registrations()
        self.published = self._index_published()
        self.wraps = self._index_wraps()
        self._src_memo = {}
        self._store_memo = {}

    # -- macros / token pastes ---------------------------------------------

    def _index_macros(self, src):
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
            self.macro_defs.setdefault(m.group(1), []).append(
                ([p for p in (params or []) if p], src[j:nl]))

    def _expand_pastes(self):
        """Expand `##`-pasting macro invocations so the assignment they hide is visible."""
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
                        # Mask the paste operator BEFORE stringify, or `#\s*name` matches
                        # the second `#` of `sym_##name` and the expansion comes out as
                        # `sym_#"wall"` -- no assignment to sym_wall at all.
                        t = re.sub(r"\s*##\s*", "\x00", body)
                        for p, a in zip(params, args):
                            t = re.sub(r"#\s*%s\b" % re.escape(p), '"%s"' % a, t)
                            t = re.sub(r"\b%s\b" % re.escape(p), a, t)
                        out.append(t.replace("\x00", ""))
        return "\n".join(out)

    # -- types --------------------------------------------------------------

    def _index_structs(self, path, src):
        for m in re.finditer(r"\b(?:typedef\s+)?(struct|union)\s+(\w+)?\s*\{", src):
            open_idx = src.index("{", m.end() - 1)
            close = match_brace(src, open_idx)
            if close < 0:
                continue
            body = src[open_idx + 1:close]
            names = []
            if m.group(2):
                names.append(m.group(2))
            semi = src.find(";", close)
            if semi > 0 and src[close + 1:semi].strip():
                for decl in split_args(src[close + 1:semi]):
                    d = decl.strip().lstrip("*").strip()
                    if d.isidentifier():
                        names.append(d)
            for nm in names:
                self.structs.setdefault(nm, (body, path, open_idx + 1))

    def _index_aliases(self, src):
        for m in re.finditer(
                r"\btypedef\s+(?:(?:struct|union)\s+)?(\w+)\s+\*?\s*(\w+)\s*;", src):
            if m.group(1) != m.group(2):
                self.aliases.setdefault(m.group(2), m.group(1))

    def struct_body(self, name, depth=4):
        """(body, path, offset) for a struct/union type name, resolving typedefs."""
        n = name
        for _ in range(depth):
            if n in self.structs:
                return self.structs[n]
            if n in self.aliases:
                n = self.aliases[n]
                continue
            return None
        return None

    def _index_funcs(self, path, src):
        """name -> body, top-level definitions only, for one-level return resolution.

        Also records each body's SPAN. Function-local statics used to be found by requiring
        leading whitespace (`^[ \t]+static\\s+VALUE`), which is indentation standing in for
        storage duration -- and a `static VALUE cache;` written at column zero inside a
        function body has exactly the same storage duration and exactly the same defect,
        while matching nothing at all. The spans replace the proxy with the property.

        DEPTH IS COUNTED OVER STORAGE SCOPES, NOT BRACES, and this is the fourth place in
        the directory that has had to learn it. `namespace X {` and `extern "C" {` nest
        their contents without giving them a new storage duration; counting raw braces put
        every definition inside one at depth 1, so it was never indexed, its body never
        contributed a span, and every function-local `static VALUE` in a C++ gem with the
        ordinary layout was invisible. The measured before-state on a namespaced function
        holding `static VALUE cache; cache = rb_str_new_cstr("x");` is `slots 0/0, HITS 0`
        -- an emptied index, not a dropped row, which is why it reads as a clean gem and
        why the counters rather than the hit count are what the generated red asserts.

        The slot walk above was ported in round 8 and this was not, so the two halves of
        one file disagreed about C++ for a round: file-scope slots inside a namespace were
        found while function-local ones in the same namespace were not. tu_scope.storage_depth
        is the same walk both now read, and predicate D's function index reads it too.

        AND THE DECLARATOR DOES NOT END AT THE `)`. This walk skipped WHITESPACE ONLY and
        then required `{`, so a definition carrying anything between the parameter list and
        the body was never indexed at all:

            static VALUE bad(void) __attribute__((noinline)) { static VALUE cache; ... }

        Measured on that fixture unfixed: `slots 0/0, decls 0, HITS 0` -- the allocating
        `cache` inside it is invisible, and the file reads exactly as clean as a gem with no
        statics in it. The SAME gap, in three scripts, four times: predicate D hit it for
        `__attribute__`/`noexcept` and again for a C++ trailing return type, and this is the
        fourth. tu_scope.skip_post_declarator is that walk's one home, and its rejection
        table travels with it -- opening the crossing up is what once let predicate D invent
        four function bodies out of X-macro lists and `__declspec(...)` before a
        `typedef enum {`. THIS predicate's neighbours are different and the risk runs the
        other way: it is looking for `static VALUE` declarations, so a
        `typedef struct { ... } static_thing;` after a rejection boundary is the shape that
        would cost it a wrong span. `typedef`, `struct` and `static` are all stop words, and
        the self-test asserts the table here as well as in D -- one walk, two neighbourhoods,
        two sets of controls.
        """
        spans = self.func_spans.setdefault(path, [])
        depth_at = tu_scope.storage_depth(src)
        for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?=\()", src):
            if depth_at(m.start()) != 0 or m.group(1) in NOT_CALLS:
                continue
            args, past = call_args(src, m.end())
            if args is None:
                continue
            k = tu_scope.skip_post_declarator(src, past)
            if k >= len(src) or src[k] != "{":
                continue
            close = match_brace(src, k)
            if close > 0:
                self.funcs.setdefault(m.group(1), src[k + 1:close])
                spans.append((k + 1, close))

    def _index_dtypes(self, src):
        for m in re.finditer(r"\brb_data_type_t\s+(\w+)\s*=\s*\{", src):
            open_idx = src.index("{", m.end() - 1)
            close = match_brace(src, open_idx)
            if close > 0:
                self.dtypes[m.group(1)] = src[open_idx + 1:close]

    def dmark_of(self, dtype):
        """The dmark entry of an rb_data_type_t initialiser, or None if unresolved.

        Both spellings are in the corpus and both matter: stackprof and rbtrace write the
        function block positionally (`{ "StackProf", { stackprof_gc_mark, NULL, ... } }`)
        while vernier uses designated initialisers (`.dmark = collector_mark`).
        """
        body = self.dtypes.get(dtype)
        if body is None:
            return None
        m = re.search(r"\.dmark\s*=\s*([^,}]+)", body)
        if m:
            return m.group(1).strip()
        parts = split_args(body)
        if len(parts) >= 2 and parts[1].strip().startswith("{"):
            inner = split_args(parts[1].strip()[1:-1])
            return inner[0].strip() if inner else None
        return None

    # -- stage 1: file-scope VALUE slots -------------------------------------

    def _index_slots(self, path, src):
        for off, unit in top_level_units(src):
            # A unit starts just past the PREVIOUS `;`, so its raw offset is the newline
            # that ends the previous line and every slot reports one line early. objtracer
            # is declared at stackprof.c:168 and printed as :167 -- an off-by-one in the
            # one coordinate a reader uses to find the defect.
            self._unit_slots(path, off, unit)
        # C++ class bodies, keyed `Class::member`. Walked before the function-local scan
        # below, because that scan cannot tell a member from a local and would key the
        # member wrong.
        scopes = list(class_scopes(src))
        self.class_bodies += len(scopes)
        for qual, boff, body in scopes:
            self._class_member_slots(path, qual, boff, body)
        member_spans = [(boff, boff + len(body)) for _q, boff, body in scopes]
        # Function-LOCAL statics have static storage duration and exactly the same problem;
        # top_level_units deliberately skips function bodies, so they are picked up here.
        # vernier's commented-out `static VALUE gc_hook = Data_Wrap_Struct(...)` inside
        # Init_vernier is what this branch exists for.
        #
        # Two things this scan used to key on that are not the property being tested:
        #
        #   INDENTATION. `^[ \t]+static` is a style convention doing duty for storage
        #   duration. A body written flush left -- generated code, a macro-heavy Init, a
        #   file that never saw a formatter -- declares the same slot with the same defect
        #   and matched nothing, and the file then reported 0 slots. Function BODY SPANS say
        #   the same thing without asking the author to indent.
        #
        #   `static` IMMEDIATELY FOLLOWED BY `VALUE`. `static volatile VALUE cache;` is the
        #   idiom a developer reaches for precisely when a slot is written in one call and
        #   read in another -- the shape this predicate is about -- and it was the one
        #   spelling the pattern could not see. Qualifiers are allowed on either side of
        #   `static` now (LOCAL_QUAL), which also picks up `static thread_local VALUE`.
        #
        # AND THE THIRD: THE MATCH USED TO END AT THE FIRST `=`. `[^;=(){}]+[;=]` reads a
        # declarator list only up to its first initialiser, so
        #
        #     static VALUE rooted = Qnil, bad = Qnil;
        #
        # declared ONE slot. Register `rooted` and the file reports `slots 1/1`, one
        # `registered-slot` discharge and HITS 0 while `bad` takes an `rb_str_new_cstr` and
        # is never seen -- a clean-looking sheet produced by half a declaration. The whole
        # STATEMENT is read now, to its top-level `;`, and split_args -- the top-level comma
        # splitter the file-scope and struct walks already use -- separates the declarators.
        # It is genuinely top-level, which is the property that matters here: the commas
        # inside `static VALUE a = f(x, y), b;` are at paren depth 1 and yield `a` and `b`,
        # not four things.
        #
        # The paren gate moves with it, and it is on the DECLARATOR rather than the
        # statement, exactly as _unit_slots case (3) does it: `static VALUE (*fp)(void);` is
        # a function pointer and not a slot, while `static VALUE c = rb_str_new_cstr("x");`
        # is a slot whose initialiser happens to call something. The old character class
        # rejected both by refusing to cross a `(` at all.
        pat = re.compile(r"\b" + LOCAL_QUAL + r"static\s+" + LOCAL_QUAL + r"VALUE\b")
        for a, b in self.func_spans.get(path, ()):
            for m in pat.finditer(src, a, b):
                # An INDENTED class member matches this pattern too -- which is how the
                # sweep had accidental, bare-keyed recall on class statics before
                # class_scopes existed. Keeping both would report one slot twice, once
                # under a key that cannot match `rb_global_variable(&Registry::cache)`.
                if any(x <= m.start() < y for x, y in member_spans):
                    continue
                chunks = split_top_off(src[m.end():b], ";")
                if len(chunks) < 2:
                    continue                    # no terminator in the body: not a statement
                rest = chunks[0][1]
                # A `;` reached only by walking OUT of the enclosing block belongs to some
                # later statement, not to this one.
                if rest.count("}") > rest.count("{") \
                        or "(" in split_top_off(rest, "=")[0][1]:
                    continue
                decl_text = re.sub(r"\s+", " ", m.group(0) + rest).strip() + ";"
                for decl in split_args(rest):
                    nm, arr, ptr = declarator(decl)
                    if nm and not ptr:
                        # A function-local static is internal to its BLOCK, which is one
                        # scope narrower than the file the round-8 split gave it. Two
                        # functions in one TU may each declare `static VALUE cache;` and
                        # they are two objects: keyed `(file, "cache")` they merged, and a
                        # `rb_global_variable(&cache)` in the first discharged the second's
                        # unrooted store.
                        #
                        # AND THE FUNCTION IS STILL ONE SCOPE TOO WIDE. Two DISJOINT nested
                        # blocks in one function may each declare `static VALUE cache`, and
                        # they are two objects for the same reason two functions are: the
                        # name is not visible outside the braces it was declared in.
                        # Registering the first block's and allocating into the second gave
                        # `slots 1/2`, one `registered-slot` discharge and HITS 0 -- an
                        # over-clear reached by a dedupe, which is the shape this file keeps
                        # having to fix. `innermost_block` is tu_scope's, and the same one
                        # `source_reads` asks for its shadowing rule: which braces own this
                        # offset is one question, not two.
                        #
                        # Still gated on TU_EXT, for the header reason in that constant's
                        # comment -- a `static inline` helper in a .h is one object per
                        # INCLUDER, and this pass cannot resolve includes. Those stay
                        # tree-wide keyed, the same residual as a header file static.
                        blk = tu_scope.innermost_block(src[a:b], m.start() - a)
                        span = (a + blk[0], a + blk[1] + 1) if blk else (a, b)
                        self.slots.append(Slot(path, m.start(), nm + arr, nm, "scalar",
                                               decl_text,
                                               scope=Scope(path, span)
                                               if path.suffix in TU_EXT else TREE))

    def _class_member_slots(self, path, qual, boff, body):
        """`static VALUE` DATA MEMBERS of one C++ class body, keyed `Class::member`.

        Non-static members are deliberately not enumerated here: they are per-INSTANCE, and
        an instance of a file-scope object is _struct_slots' subject while an instance
        handed to TypedData_Wrap_Struct is sweep_unmarked.py's. Only `static` has the
        file-scope storage duration this predicate is about.
        """
        flat = ACCESS_LABEL.sub(lambda m: blank(m.group(0)), blank_bodies(body))
        for m in re.finditer(r"[^;]*;", flat):
            frag = m.group(0)[:-1]
            v = re.match(r"^\s*((?:[A-Za-z_]\w*\s+)*?)VALUE\s+(.+)$", frag, re.S)
            if not v:
                continue
            lead = v.group(1).split()
            # `static` must be present -- that is the whole discriminator -- and every other
            # leading token has to be a type keyword or an ALL-CAPS macro, the same gate
            # _unit_slots case (3) uses so that `EXTERN VALUE x;` survives it.
            if "static" not in lead or not all(
                    t in TYPE_KW or t in ("constexpr", "thread_local", "mutable")
                    or t.isupper() for t in lead):
                continue
            # `static VALUE make(VALUE klass);` is a static METHOD, not a slot.
            if "(" in split_top_off(v.group(2), "=")[0][1]:
                continue
            off = boff + m.start() + len(frag) - len(frag.lstrip())
            for decl in split_args(v.group(2)):
                nm, arr, ptr = declarator(decl)
                if nm:
                    self.slots.append(Slot(path, off, qual + nm + arr, qual + nm,
                                           "ptr" if ptr else "scalar",
                                           re.sub(r"\s+", " ", frag).strip()))

    def _unit_slots(self, path, off, unit):
        head_off = off + len(unit) - len(unit.lstrip())
        head = unit.strip()
        if not head or head.startswith("typedef") or re.match(r"^\s*extern\b", head):
            return
        # INTERNAL LINKAGE, and it has TWO SPELLINGS. `static` anywhere ahead of the
        # declarator makes this object private to the file, so it is scoped; `EXTERN VALUE
        # x;` and a bare `VALUE rb_mVernier;` are one object tree-wide and stay unscoped.
        # The declarator itself is cut off first, or an initialiser mentioning `static` in
        # a nested expression would flip the linkage.
        #
        # THE SECOND SPELLING CARRIES NO `static` AT ALL. `namespace { VALUE cache; }` takes
        # internal linkage from the NAMESPACE, so a decision that reads only the declaration
        # text gave two translation units ONE tree-scoped slot and let one file's
        # `rb_global_variable(&cache)` discharge the other file's unregistered allocating
        # one. That is the same over-clear the round-8 `static` split was extracted to end,
        # reached through different syntax -- so it is asked of tu_scope.internal_linkage
        # rather than answered again here, and predicate D asks the same function of the
        # same spans for its own file-scope sinks.
        scope = tu_scope.declared_scope(
            path, tu_scope.internal_linkage(split_top_off(unit, "=")[0][1],
                                            head_off, self.anon.get(path, ())))
        body_open = unit.find("{")
        # (1) `static struct { ... } _stackprof;` / `static struct tag { ... } x;`
        if body_open >= 0:
            pre = unit[:body_open].rstrip()
            if re.search(r"\b(struct|union)\b\s*(\w*)$", pre):
                close = match_brace(unit, body_open)
                if close < 0:
                    return
                for decl in split_args(unit[close + 1:].rstrip().rstrip(";")):
                    nm, arr, ptr = declarator(decl)
                    if nm and not ptr:
                        self.objects[nm] = (path, head_off, scope)
                        self._struct_slots(path, head_off, nm + arr, nm,
                                           unit[body_open + 1:close], path,
                                           off + body_open + 1, 0, scope)
                return
            # An INITIALISER brace, not an aggregate body. `static struct common_field
            # common_http_fields[] = { ... };` (unicorn common_field_optimization.h:17)
            # puts its first `{` after the `=`, so the case (1) test above fails and the
            # bare `return` that used to live here dropped the declaration WHOLE -- the
            # array-of-file-static-struct shape this predicate exists to walk, reported as
            # `field 0`. The discard happened BEFORE any type was inspected, so it was
            # type-blind: it dropped 17 declarations across the corpus and would have
            # dropped a VALUE-bearing one just as silently. Cut the initialiser off and
            # let cases (2)/(3) read the declarator that is left; `head_off` is the unit's
            # own offset and does not move, so the slot still reports its own file:line.
            if not unit[:body_open].rstrip().endswith("="):
                return
            unit = unit[:body_open].rstrip()[:-1].rstrip() + ";"
        # (2) `static rbtracer_t obj;` -- a named struct type at file scope.
        d = re.match(r"^\s*(?:static\s+|const\s+|volatile\s+|_Atomic\s+)*"
                     r"(?:struct\s+|union\s+)?(\w+)\s+([^;]+);$", unit)
        # Falling THROUGH when the type name does not resolve to a struct is the whole
        # point of the `sub is not None` guard. `EXTERN VALUE Module_Magick;` matches this
        # pattern with type=EXTERN, name=`VALUE Module_Magick`; returning here swallowed
        # every rmagick global and the gem measured 1 slot across 15 files.
        sub = self.struct_body(d.group(1)) if d and d.group(1) != "VALUE" else None
        if sub is not None:
            for decl in split_args(d.group(2)):
                nm, arr, ptr = declarator(decl)
                if nm and not ptr:
                    self.objects[nm] = (path, head_off, scope)
                    self._struct_slots(path, head_off, nm + arr, nm, *sub, 0, scope)
            return
        # (3) a bare `static VALUE x, y[N];` / `VALUE rb_mVernier;` / `EXTERN VALUE x;`.
        #
        # The ALL-CAPS leading token is rmagick, and it is a 100-slot recall hole. rmagick
        # declares every one of its file-scope VALUEs as `EXTERN VALUE Module_Magick;`
        # (rmagick.h:333), where EXTERN is `#define EXTERN` in the defining TU and `extern`
        # everywhere else. Requiring a storage keyword found ONE slot in the whole gem --
        # a function-local static -- and the coverage line said `slots 1/1`, which reads
        # like a small gem rather than a parser that dropped the entire header.
        #
        # The paren gate is on the DECLARATOR, not on the whole statement. It is there to
        # reject `VALUE rb_foo(VALUE self);` and `static VALUE (*fp)(VALUE);`, both of which
        # put a `(` before any `=`. Gating on the whole statement also rejected every
        # initialiser that calls something -- illegal for a static in C, ordinary in C++,
        # and exactly the shape an out-of-line static member definition takes:
        # `VALUE Registry::cache = rb_str_new2("x");` produced no slot at all.
        v = re.match(r"^\s*((?:[A-Za-z_]\w*\s+)*?)VALUE\s+([^;]+);$", unit)
        if v and all(t in TYPE_KW or t.isupper() for t in v.group(1).split()) \
                and "(" not in split_top_off(v.group(2), "=")[0][1]:
            v = re.match(r"^.*?VALUE\s+([^;]+);$", unit, re.S)
            for decl in split_args(v.group(1)):
                nm, arr, ptr = declarator(decl)
                if nm:
                    self.slots.append(Slot(path, head_off, nm + arr, nm,
                                           "ptr" if ptr else "scalar", unit.strip(),
                                           scope=scope))

    def _struct_slots(self, opath, ooff, prefix, root, body, bpath, boff, depth,
                      scope=None):
        """Enumerate VALUE members of a file-scope object, recursively.

        rbtrace needs two levels -- `rbtracer` -> `rbtracer_t list[MAX_TRACERS]` ->
        `VALUE self` -- and stops being visible at one. Arrays collapse to `[]` because a
        registration loop indexes with a variable and the declaration with a macro.

        bpath/boff track the STRUCT BODY's own position so a member reports its own
        file:line. rbtracer_t is declared 13 lines above the object that contains it.
        """
        if depth > 4:
            return
        # C++ access specifiers are LABELS, not statements, so no `;` separates one from the
        # member behind it: `struct S { public: VALUE held; }` puts `public:` and
        # `VALUE held` in ONE fragment, the type matcher reads `public` as the type name and
        # the FIRST member of every section disappears. Blanked, not deleted, so the member
        # keeps its offset and reports its own file:line.
        body = ACCESS_LABEL.sub(lambda m: blank(m.group(0)), body)
        for soff, stmt in split_top_off(body, ";"):
            lead = len(stmt) - len(stmt.lstrip())
            s = stmt.strip()
            moff = boff + soff + lead
            if not s or "(" in s:
                continue
            inner_open = s.find("{")
            if inner_open >= 0:                       # anonymous nested struct/union
                close = match_brace(s, inner_open)
                if close < 0:
                    continue
                for decl in split_args(s[close + 1:]):
                    nm, arr, ptr = declarator(decl)
                    if nm and not ptr:
                        self._struct_slots(opath, ooff, "%s.%s%s" % (prefix, nm, arr),
                                           root, s[inner_open + 1:close], bpath,
                                           moff + inner_open + 1, depth + 1, scope)
                continue
            m = re.match(r"^(?:const\s+|volatile\s+|_Atomic\s+|mutable\s+)*"
                         r"(?:(struct|union)\s+)?(\w+)\s+(.+)$", s, re.S)
            if not m:
                continue
            tname, rest = m.group(2), m.group(3)
            if tname == "VALUE":
                for decl in split_args(rest):
                    nm, arr, ptr = declarator(decl)
                    if nm:
                        self.slots.append(
                            Slot(bpath, moff, "%s.%s%s" % (prefix, nm, arr), root,
                                 "ptr" if ptr else "field", s.strip(), opath, ooff,
                                 scope))
                continue
            sub = self.struct_body(tname)
            if sub is not None:
                for decl in split_args(rest):
                    nm, arr, ptr = declarator(decl)
                    if nm and not ptr:
                        self._struct_slots(opath, ooff, "%s.%s%s" % (prefix, nm, arr),
                                           root, *sub, depth + 1, scope)
            elif tname not in TYPE_KW and not re.match(
                    r"^(u?int\w*|size_t|ssize_t|time_t|pid_t|key_t|bool|ID|st_table"
                    r"|pthread\w*|FILE|off_t|socklen_t|uid_t|gid_t|mode_t|dev_t)$", tname):
                # Counted, not silent: an unresolved member type is exactly where a nested
                # VALUE hides, and "0 slots" has to be distinguishable from "0 resolved".
                self.unresolved_members += 1

    # -- stage 2: sources ----------------------------------------------------

    def sources(self, slot):
        """[(rhs, path, offset, owner)] -- every assignment reaching this slot.

        An internal-linkage slot is searched in ITS OWN SCOPE ONLY, plus the macro-paste
        expansion pool, which has no file attribution to give. That is not an optimisation:
        no other translation unit can name the object, so a `cache = rb_str_new2(...)` in
        another file is a store to a DIFFERENT slot of the same name, and reading it here is
        how one file's registration came to discharge another file's store.

        ROUND 9: the same sentence one scope down. A function-local static's scope is its
        BLOCK, so the pool is that function's body span -- a store to `cache` in the
        function next door is a store to a different object, and reading it here is how one
        function's registration came to discharge the other's.
        """
        if slot.ident in self._src_memo:
            return self._src_memo[slot.ident]
        out = []
        # (path, text, lo, hi) -- the byte range of the slot's own scope, so offsets stay
        # absolute and every position rule downstream keeps working unchanged.
        sc = slot.scope
        if sc.path in self.files:
            src = self.files[sc.path]
            lo, hi = sc.span if sc.span else (0, len(src))
            pool = [(sc.path, src, lo, hi)]
        else:
            pool = [(p, t, 0, len(t)) for p, t in self.files.items()]
        pool.append((None, self.pasted, 0, len(self.pasted)))
        if slot.kind == "scalar" or "." not in slot.key:
            # A C++ static data member is spelled `Registry::cache` from outside the class
            # and bare `cache` from inside its own methods, and both name ONE object. So
            # the class qualifier is optional in the pattern: requiring it loses every store
            # made from a method body, and dropping it loses nothing but reads an unrelated
            # `cache =` elsewhere in the tree as a source -- the same tree-wide name
            # aliasing the dedupe rule already documents, and the safe direction, since an
            # extra source can only make an all-sources discharge HARDER to earn.
            name, qual = slot.field, ""
            if "::" in name:
                scope, name = name.rsplit("::", 1)
                qual = r"(?:%s\s*::\s*)?" % r"\s*::\s*".join(
                    re.escape(p) for p in scope.split("::"))
            pat = re.compile(r"(?<![\w.>])%s%s\s*(?:\[[^\[\]]*\])?\s*=(?!=)"
                             % (qual, re.escape(name)))
            for path, src, lo, hi in pool:
                for m in pat.finditer(src, lo, hi):
                    out.append((rhs_after(src, m.end() - 1), path, m.start(), None))
        else:
            # Deliberately not scoped to the owning object: the owner token is all a text
            # scan has, and rbtrace stores through `tracer->self` where `tracer` is a
            # pointer into `rbtracer.list`. Requiring owner == `rbtracer` finds zero stores
            # and the red reports UNSOURCED instead of naming the parameter it swallows.
            pat = re.compile(r"([A-Za-z_]\w*)\s*(?:\.|->)\s*%s\b\s*(?:\[[^\[\]]*\])?\s*=(?!=)"
                             % re.escape(slot.field))
            for path, src, lo, hi in pool:
                for m in pat.finditer(src, lo, hi):
                    out.append((rhs_after(src, m.end() - 1), path, m.start(), m.group(1)))
        self._src_memo[slot.ident] = out
        return out

    # -- stage 3: registrations and wraps ------------------------------------

    def _index_registrations(self):
        """[(kind, normalised target, path, line, offset)] for every GC registration.

        The OFFSET is what separates the two primitives. `rb_global_variable(&v)` and
        `rb_gc_register_address(&v)` root the ADDRESS: the GC re-reads the slot at every
        mark, so a store made an hour later is covered and the call's position is
        irrelevant. `rb_gc_register_mark_object(v)` roots the VALUE the expression evaluated
        to at that instant -- gc.c pushes it onto vm->mark_object_ary and never looks at the
        C variable again. Reassign the slot afterwards and the new object is rooted by
        nothing while the sweep, matching on the name alone, reported the slot discharged.
        """
        out = []
        for path, src in self.files.items():
            for name, args, s, _e in find_calls(src):
                if not args:
                    continue
                if REGISTER_SLOT.match(name) and args[0].strip().startswith("&"):
                    out.append(("registered-slot", norm(args[0]), path,
                                line_at(src, s), s))
                elif REGISTER_VALUE.match(name):
                    out.append(("registered-value", norm(args[0]), path,
                                line_at(src, s), s))
        return out

    def _index_published(self):
        """{slot key -> [(call, path, line, offset)]} for slots handed to rb_define_const.

        The VALUE argument is the last one in both signatures, and it has to be the slot
        itself: `rb_define_const(mod, "X", INT2NUM(BUF_SIZE))` publishes a temporary and
        roots nothing of ours.

        Publication is a VALUE rooting, exactly like rb_gc_register_mark_object: what the
        constant table holds is the object the argument evaluated to, not the C slot. A
        fixture that publishes one String and then stores a second into the same static has
        one rooted object and one unrooted one, and reported HITS 0. So the offset is kept
        and every site is kept -- a slot published twice needs both positions before any of
        them can be called the one that covers a store.
        """
        out = {}
        for path, src in self.files.items():
            for name, args, s, _e in find_calls(src):
                if name not in ("rb_define_const", "rb_const_set", "rb_define_global_const"):
                    continue
                if args:
                    out.setdefault(norm(args[-1]), []).append(
                        (name, path, line_at(src, s), s))
        return out

    def _index_wraps(self):
        """{(scope, object name) -> (dtype, dmark, path, line, dest)} for wrapped objects.

        rbtrace's `TypedData_Wrap_Struct(rb_cObject, &rbtrace_type, NULL)` is the control on
        the other side: it names a dtype with a real dmark but hands it NULL, so `rbtracer`
        never appears here and its fields stay in scope.

        `dest` is where the WRAPPER went, and it is the fourth thing this rule has to know.
        A dmark only runs while the wrapper it belongs to is itself reachable from a GC
        root; a wrapper that is thrown away marks nothing, so handing the object's fields
        off to sweep_unmarked.py routes them to a walk that will find a correct-looking
        dmark and clear them. See wrapper_dest for what the classification can and cannot
        prove.
        """
        out = {}
        for path, src in self.files.items():
            for name, args, s, _e in find_calls(src):
                if name not in ("TypedData_Wrap_Struct", "Data_Wrap_Struct",
                                "rb_data_typed_object_wrap", "rb_data_object_wrap"):
                    continue
                dtype = None
                for a in args:
                    tk = a.strip()
                    if tk.startswith("&") and norm(tk) in self.dtypes:
                        dtype = norm(tk)
                for a in args:
                    tk = norm(a.strip())
                    if not a.strip().startswith("&") or tk not in self.objects:
                        continue
                    _op, _oo, oscope = self.objects[tk]
                    # An object with internal linkage cannot be addressed from another TU,
                    # so a wrap call in a different file is wrapping a different object.
                    if not oscope.contains(path, s):
                        continue
                    out[(oscope, tk)] = (dtype, self.dmark_of(dtype) if dtype else None,
                                         path, line_at(src, s), wrapper_dest(src, s))
        return out


def wrapper_dest(src, call_start):
    """Where did a TypedData_Wrap_Struct result go? ("discarded"|"assign"|"other", target).

    Read backwards from the call name over whitespace and classify the one character that
    decides it:

      `;` `{` `}` `:` or start of file  -> DISCARDED. The statement is the call and nothing
                                           else; the wrapper is unreachable the moment the
                                           statement ends.
      `=` (not `==`, `!=`, `<=`, ...)   -> ASSIGN, and the normalised left-hand side comes
                                           back with it, so the caller can ask whether THAT
                                           slot is rooted.
      anything else                     -> OTHER.

    THE FLOOR, AND IT IS DELIBERATE. "other" is every shape this pass declines to judge:
    `return TypedData_Wrap_Struct(...)`, a wrap nested inside another call, a wrap assigned
    to a LOCAL, and `VALUE obj = TypedData_Wrap_Struct(...)` -- which the backwards scan
    reads as an assignment to `obj` and then fails to resolve to a file-scope slot, so it
    lands in "other" as well. Those are not reported.

    That covers the ordinary allocator cfunc -- wrap, then hand the wrapper to Ruby, which
    roots it wherever the Ruby program keeps it -- and it is the reason the strict reading
    ("discharge only when the wrapper provably reaches a root") was rejected: it makes the
    single commonest correct shape in every C extension a hit, and this predicate's whole
    design principle is that over-clearing is the sin. The cost of the floor is that a
    wrapper stored into a local which is then dropped, or into a slot this pass cannot
    resolve, still discharges the object's fields. Corpus effect of the strict reading vs
    this one: identical, zero rows either way -- both wrapped sites in the corpus assign to
    a file-scope slot that rb_global_variable roots.
    """
    i = call_start - 1
    while i >= 0 and src[i] in " \t\r\n":
        i -= 1
    if i < 0 or src[i] in ";{}:":
        return ("discarded", None)
    if src[i] == "=" and (i == 0 or src[i - 1] not in "=!<>+-*/%&|^"):
        j = i - 1
        while j >= 0 and src[j] in " \t\r\n":
            j -= 1
        end = j + 1
        while j >= 0 and (src[j].isalnum() or src[j] in "_.[]>-:"):
            j -= 1
        lhs = src[j + 1:end].strip()
        return ("assign", norm(lhs)) if lhs else ("other", None)
    return ("other", None)


def split_top_off(text, sep):
    """[(offset, chunk)] split on a top-level separator, ignoring nesting."""
    out, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == sep and depth == 0:
            out.append((start, text[start:i]))
            start = i + 1
    out.append((start, text[start:]))
    return out


def declarator(decl):
    """`* names[TOTAL]` -> ("names", "[]", True). Returns (name, array_suffix, is_ptr).

    Cutting at the first top-level `=` is not cosmetic: rbtrace declares its file-static
    object as `} rbtracer = { .mid_tbl = NULL, ... };` and taking the LAST identifier of
    the whole declarator named the object `NULL`, so both of its red slots came out keyed
    `NULL.list[].self` and the wrapped-object lookup could never match.
    """
    d = split_top_off(decl, "=")[0][1].strip()
    ptr = "*" in d
    arr = "[]" if "[" in d else ""
    d = re.sub(r"\[[^\[\]]*\]", "", d).replace("*", " ")
    # `::` is kept ATTACHED, so the out-of-line definition `VALUE Registry::cache = ...;`
    # yields `Registry::cache` -- the same key the class body raised, which is what lets
    # the dedupe merge the declaration and the definition into one slot instead of two, and
    # what lets `rb_global_variable(&Registry::cache)` match. Taking the last identifier
    # instead named it `cache`, which matches neither.
    d = re.sub(r"\s*::\s*", "::", d)
    ids = [i for i in re.findall(r"[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*", d) if i not in TYPE_KW]
    return (ids[-1] if ids else "", arr, ptr)


# ------------------------------------------------------- the safe shapes


def expand_macro(tree, fn, args):
    """One level of function-like macro substitution, or None.

    vernier writes every one of its three symbol statics as `sym_state = sym("state")`,
    with `#define sym(name) ID2SYM(rb_intern_const(name))` in vernier.hh. strip_directives
    blanks the definition, so without this the RHS is an unrecognised call and all three
    report OPAQUE -- three false positives in the gem the sweep is supposed to clear.
    """
    for params, body in tree.macro_defs.get(fn, ()):
        if len(params) != len(args):
            continue
        t = body.strip()
        for p, a in zip(params, args):
            t = re.sub(r"\b%s\b" % re.escape(p), a, t)
        return t.strip().rstrip(";").strip()
    return None


def returns_all(tree, pred, fn, rules, depth, seen, scope=TREE):
    """Does EVERY `return` in this in-tree function satisfy `pred`?

    rmagick is 44 of the corpus's slots on its own: every `Class_FooType` global is assigned
    `rm_define_enum_type("FooType")`, a one-screen in-tree wrapper (rmenum.cpp:41) whose body
    is `klass = rb_define_class_under(Module_Magick, tag, Class_Enum); ...; return klass;`.
    Without resolving the return the whole set reports CALL, and 44 false positives in one
    gem is a report nobody finishes reading. The local-assignment arm is required, not a
    bonus -- the return is a bare `klass`, never the constructor.

    Every return must satisfy the predicate, so an early `return Qnil` on the error path
    does not weaken the answer and a single unresolved return kills it.
    """
    body = tree.funcs.get(fn)
    if body is None or depth <= 0 or ("fn:" + fn) in seen:
        return False
    seen = seen | {"fn:" + fn}
    rets = []
    for m in re.finditer(r"\breturn\b", body):
        semi = body.find(";", m.end())
        if semi < 0:
            return False
        rets.append(body[m.end():semi].strip())
    if not rets:
        return False
    for ret in rets:
        e = unwrap(ret)
        if pred(tree, e, rules, depth - 1, set(seen), scope):
            continue
        if e.isidentifier():
            locals_ = [rhs_after(body, mm.end() - 1) for mm in
                       re.finditer(r"(?<![\w.>])%s\s*=(?!=)" % re.escape(e), body)]
            if locals_ and all(pred(tree, l, rules, depth - 1, set(seen), scope)
                               for l in locals_):
                continue
        return False
    return True


def is_immediate(tree, expr, rules, depth=3, seen=None, scope=TREE):
    """Provably an immediate VALUE -- one GC never collects. Unknown means False.

    `scope` is the translation unit the expression was read in, so a bare identifier
    resolves to the internal-linkage slot of that file before any tree-wide one.
    """
    seen = seen if seen is not None else set()
    e = unwrap(expr)
    if not e:
        return False
    # `static VALUE default_channels_const = 0;` -- rmagick rmimage.cpp:4690, a
    # zero-initialised static used as its own "not yet fetched" sentinel. 0 IS Qfalse.
    if INT_LITERAL.match(e):
        return True
    if e.isidentifier():
        if e in IMMEDIATE_CONST:
            return True
        sl = _slot_named(tree, e, scope)
        if depth <= 0 or (scope, e) in seen or sl is None:
            return False
        seen.add((scope, e))
        srcs = [r for r, _p, _o, _w in tree.sources(sl)]
        return bool(srcs) and all(is_immediate(tree, s, rules, depth - 1, seen, sl.scope)
                                  for s in srcs)
    fn, args = split_call(e)
    if fn is None:
        return False
    if IMMEDIATE_CALL.match(fn):
        return True
    if NUM_CALL.match(fn) and args and len(args) == 1:
        lit = unwrap(args[0]).rstrip("uUlL")
        if INT_LITERAL.match(unwrap(args[0])):
            try:
                return abs(int(lit, 0)) <= FIXNUM_MAX
            except ValueError:
                return False
    # An ID parked in a VALUE-typed static. kgio accept.c:14 declares `static VALUE
    # iv_kgio_addr` and assigns `rb_intern("kgio_addr")` to it -- a type confusion, not a
    # GC bug: rb_intern yields a STATIC symbol ID that is never collected. Without this it
    # reports OPAQUE and kgio's green is one line short.
    if STATIC_INTERN.match(fn) and args:
        return True
    if ID2SYM_CALL.match(fn) and args and len(args) == 1:
        inner, iargs = split_call(unwrap(args[0]))
        return bool(inner and STATIC_INTERN.match(inner) and iargs)
    if depth > 0:
        exp = expand_macro(tree, fn, args or [])
        if exp is not None and exp != e:
            return is_immediate(tree, exp, rules, depth - 1, seen, scope)
    return False


def is_const_table(tree, expr, rules, depth=3, seen=None, scope=TREE):
    """Provably reachable from the constant table (or a core object the VM roots)."""
    seen = seen if seen is not None else set()
    e = unwrap(expr)
    if not e:
        return False
    if e.isidentifier():
        # A TREE-LOCAL slot wins over CORE_OBJ, always. `rb_[cme][A-Z]\w*` is CRuby's
        # convention for core objects and ALSO the convention gems copy for their own
        # statics -- bootsnap's rb_cBootsnap_CompileCache_UNCOMPILABLE is an rb_const_get
        # result this same sweep reports as CONST-LOOKUP. Testing the pattern first
        # discharged an ALIAS of a slot the sweep was concurrently reporting as a hit.
        sl = _slot_named(tree, e, scope)
        if sl is not None:
            if depth <= 0 or (scope, e) in seen:
                return False
            seen.add((scope, e))
            srcs = [r for r, _p, _o, _w in tree.sources(sl)]
            return bool(srcs) and all(
                is_const_table(tree, s, rules, depth - 1, seen, sl.scope) for s in srcs)
        return bool(CORE_OBJ.match(e))
    fn, args = split_call(e)
    if fn is None:
        return False
    if CONST_CALL.match(fn):
        return True
    if depth > 0:
        exp = expand_macro(tree, fn, args or [])
        if exp is not None and exp != e:
            return is_const_table(tree, exp, rules, depth - 1, seen, scope)
    return returns_all(tree, is_const_table, fn, rules, depth, seen, scope)


def is_const_lookup(tree, expr):
    """Read out of the constant table. A GRADE, never a discharge -- see CONST_LOOKUP."""
    fn, _args = split_call(unwrap(expr))
    return bool(fn and CONST_LOOKUP.match(fn))


def _slot_named(tree, name, scope=TREE):
    """The scalar slot a bare identifier names, seen from `scope`. None if unknown.

    The file's own internal-linkage slot wins over a tree-wide one of the same name, which
    is the resolution C itself performs.
    """
    return tree.scalars.get((scope, name)) or tree.scalars.get((TREE, name))


# ------------------------------------------------------- value-rooting position


def _store_after(tree, sources, path, off, scope=TREE):
    """Describe a store this VALUE rooting cannot cover, or None if it covers them all.

    `rb_gc_register_mark_object(v)` and `rb_define_const(m, "N", v)` both root the OBJECT
    the argument evaluated to. Only the stores that already happened are covered. "Already
    happened" is approximated as: same file, lower byte offset. The approximation is
    one-directional on purpose -- it can call a store LATE that in fact runs earlier (a
    helper defined above the Init that assigns from a Ruby-callable method later, read the
    other way round), and that costs a false positive, which is the direction this predicate
    is allowed to be wrong in. It cannot call a store EARLY that runs later.

    A store that only exists inside a macro-paste expansion has no position at all, so it is
    never covered: an unknown position is not an early one.
    """
    for rhs, spath, soff, _w in sources:
        if spath is not None and spath == path and soff < off:
            continue
        # A store the OTHER rules already prove safe is not a store that outruns the
        # rooting. `pat = Qnil` cannot leave an unrooted collectable object in the slot, and
        # neither can `k = rb_define_class(...)`. date 3.5.1 is the case that forced this
        # and it is a clean measurement of the difference: `regcomp()` compiles a Regexp,
        # freezes it and hands it to rb_gc_register_mark_object, and the thirty
        # `static VALUE pat = Qnil;` it feeds are each written exactly once behind a
        # NIL_P guard. Counting the Qnil initialisers as later stores reported the gem's
        # correct one-shot lazy-registration idiom as a defect.
        if is_immediate(tree, rhs, ALL_RULES, scope=scope) \
                or is_const_table(tree, rhs, ALL_RULES, scope=scope):
            continue
        if spath is None:
            return "<macro-expansion>"
        return "%s:%d" % (spath.relative_to(tree.root), line_at(tree.files[spath], soff))
    return None


def _slot_rooted(tree, slot, rules):
    """Is this slot handed to the GC by ANY registration or publication? Generously.

    Used only to decide whether a TypedData wrapper stored into a file-scope slot is
    provably unrooted, so the generosity is the safe direction: position is ignored and any
    registration kind counts. "Unrooted" here has to mean nothing anywhere claims it.

    `contains` is asked the FILE-level question (no offset) for exactly that reason: a
    block-scoped slot registered elsewhere in its own file still counts as claimed here.
    """
    for kind, target, rpath, _rl, _ro in tree.registrations:
        if kind in rules and target == slot.key and slot.scope.contains(rpath):
            return True
    for cand in tree.published.get(slot.key, ()):
        if slot.scope.contains(cand[1]):
            return True
    return False


# ------------------------------------------------------- the sweep


class Result:
    def __init__(self, name):
        self.name = name
        self.files = 0
        self.slots = 0
        self.decls = 0
        self.scalars = 0
        self.fields = 0
        self.allocating = 0        # slots with >=1 source that can allocate
        self.unresolved_members = 0
        self.class_bodies = 0      # C++ class/struct bodies the descent entered
        self.class_members = 0     # `Class::member` static declarations seen
        self.hits = []             # (grade, path, line, key, headline, detail)
        self.discharges = []       # (rule, path, line, key, why)

    def by_rule(self):
        out = {}
        for rule, _p, _l, _k, _w in self.discharges:
            out[rule] = out.get(rule, 0) + 1
        return out


def sweep(tree, name, rules=ALL_RULES):
    r = Result(name)
    r.files = len(tree.files)
    r.slots = len(tree.slots)
    r.decls = tree.decls
    r.scalars = sum(1 for s in tree.slots if s.kind == "scalar")
    r.fields = sum(1 for s in tree.slots if s.kind != "scalar")
    r.unresolved_members = tree.unresolved_members
    r.class_bodies = tree.class_bodies
    r.class_members = tree.class_members
    for slot in sorted(tree.slots, key=lambda s: (str(s.path), s.off, s.key)):
        rel = str(slot.path.relative_to(tree.root)) if slot.path else "?"
        src = tree.files.get(slot.path, "")
        line = line_at(src, slot.off) if src else 0
        sources = tree.sources(slot)
        owners = {w for _r, _p, _o, w in sources if w}

        allocators = sorted({split_call(unwrap(rhs))[0] for rhs, _p, _o, _w in sources
                             if split_call(unwrap(rhs))[0]
                             and ALLOC_PRIM.match(split_call(unwrap(rhs))[0] or "")})
        if allocators:
            r.allocating += 1

        # -- registration. Checked FIRST, so stackprof's registered greens discharge on the
        #    registration rather than on the `wrapped` hand-off -- acceptance item 2 is a
        #    claim about rb_global_variable, and it has to be tested as one.
        #
        #    Two independent narrowings, and both of them close a way a registration for
        #    one object discharged another:
        #
        #    SCOPE. An internal-linkage slot can only be registered from its own file. A
        #    `rb_global_variable(&cache)` in a.c says nothing about b.c's own `static VALUE
        #    cache`, and matching on the name alone made one call discharge both.
        #
        #    POSITION, for `registered-value` only. rb_gc_register_mark_object roots the
        #    VALUE the argument evaluated to, not the slot; the C variable is never read
        #    again. So the call covers the stores that PRECEDE it and nothing after. A
        #    conservative textual reading of "precedes": same file, lower offset. It is not
        #    a control-flow order and does not claim to be -- a helper defined above Init
        #    and called from a Ruby method later reads as earlier here -- which is why the
        #    stronger half of the rule is the FILE, and why the residual is documented
        #    rather than hidden. rb_global_variable / rb_gc_register_address are exempt by
        #    construction: they root the ADDRESS and the GC re-reads the slot every mark.
        reg = late = None
        for kind, target, rpath, rline, roff in tree.registrations:
            if kind not in rules:
                continue
            if not slot.scope.contains(rpath, roff):
                continue
            tc, sc = target.split("."), slot.key.split(".")
            hit = target == slot.key
            if not hit and len(sc) > 1 and len(tc) == len(sc) and tc[1:] == sc[1:]:
                hit = tc[0] in owners        # a pointer into the object, not the object
            if not hit:
                continue
            after = (kind == "registered-value"
                     and _store_after(tree, sources, rpath, roff, slot.scope))
            if after:
                late = late or (kind, rpath, rline, after)
                continue
            reg = (kind, rpath, rline)
            break
        if reg:
            r.discharges.append((reg[0], rel, line, slot.key,
                                 "%s at %s:%d" % (
                                     "rb_global_variable/rb_gc_register_address"
                                     if reg[0] == "registered-slot"
                                     else "rb_gc_register_mark_object",
                                     reg[1].relative_to(tree.root), reg[2])))
            continue

        # The two source rules are tested TOGETHER, not one after the other. Six gems --
        # ed25519, nio4r x3 files, msgpack, trilogy -- write `static VALUE mFoo = Qnil;`
        # and assign `rb_define_module(...)` in Init. One source is immediate, the other is
        # const-table, so consecutive all-sources rules clear NEITHER and every one of them
        # reported. That idiom is the single commonest way a C extension declares a class
        # static; a predicate that flags it is a predicate nobody reads twice.
        kinds = set()
        for rhs, _p, _o, _w in sources:
            if "immediate" in rules and is_immediate(tree, rhs, rules, scope=slot.scope):
                kinds.add("immediate")
            elif "const-table" in rules and is_const_table(tree, rhs, rules,
                                                           scope=slot.scope):
                kinds.add("const-table")
            else:
                kinds.add(None)
                break
        if sources and None not in kinds:
            rule = "immediate" if kinds == {"immediate"} else "const-table"
            r.discharges.append((rule, rel, line, slot.key,
                                 "every source is %s (%s)"
                                 % (" or ".join(sorted(kinds)),
                                    ", ".join(sorted({re.sub(r"\s+", " ", s[0])[:34]
                                                      for s in sources})[:3]))))
            continue

        # msgpack builds an ANONYMOUS Struct class -- `rb_struct_define(NULL, "type",
        # "payload", NULL)`, extension_value_class.c:32, because rb_struct_define_under was
        # not available on the Rubies it supported -- and installs it on the next line with
        # rb_define_const. The rooting is real but it is at a USE site, not in any
        # assignment, so no source-based rule can see it.
        #
        # It is a VALUE rooting, though, and it carries the same caveat as
        # rb_gc_register_mark_object: the constant table holds the object the argument
        # evaluated to. Publish one String, store a second into the same static, and the
        # second is rooted by nothing -- so a publication only covers the stores that
        # precede it, and the slot needs SOME publication that does.
        pub = pub_late = None
        for cand in (tree.published.get(slot.key, ()) if "const-published" in rules else ()):
            if not slot.scope.contains(cand[1], cand[3]):
                continue
            after = _store_after(tree, sources, cand[1], cand[3], slot.scope)
            if after:
                pub_late = pub_late or (cand, after)
            else:
                pub = cand
                break
        if pub:
            r.discharges.append(("const-published", rel, line, slot.key,
                                 "installed in the constant table by %s at %s:%d"
                                 % (pub[0], pub[1].relative_to(tree.root), pub[2])))
            continue

        # The `wrapped` hand-off is only a hand-off while the WRAPPER is itself rooted. A
        # dmark runs when the GC marks the object that owns it, so a wrapper that is thrown
        # away marks nothing at all, and routing the struct's fields to sweep_unmarked.py
        # sends them to a walk that will find a correct-looking dmark and clear them -- an
        # over-clear laundered through a second script. wrapper_dest carries the floor:
        # only a DISCARDED result, or one assigned into a file-scope slot that is itself
        # unrooted, counts as proof.
        wrap = tree.wraps.get((slot.scope, slot.root))
        wrap_unrooted = None
        if wrap and wrap[4][0] == "discarded":
            wrap_unrooted = "the wrapper is discarded -- the result of the call is not stored"
        elif wrap and wrap[4][0] == "assign":
            dest = _slot_named(tree, wrap[4][1], slot.scope)
            if dest is not None and not _slot_rooted(tree, dest, rules):
                wrap_unrooted = ("the wrapper is stored in `%s`, itself an unrooted "
                                 "file-scope slot" % wrap[4][1])
        if slot.kind != "scalar" and "wrapped" in rules and wrap and wrap[1] \
                and wrap[1] not in ("NULL", "0") and not wrap_unrooted:
            r.discharges.append(("wrapped", rel, line, slot.key,
                                 "object `%s` wrapped with %s (.dmark = %s) at %s:%d "
                                 "-- routed to sweep_unmarked.py"
                                 % (slot.root, wrap[0], wrap[1],
                                    wrap[2].relative_to(tree.root), wrap[3])))
            continue

        # -- surviving: grade it. The grade is a severity column, never a gate, and it is
        #    read off the UNSAFE sources only. Grading off all of them put `Qnil` in
        #    trilogy's reason line -- `static VALUE _global_buffer_pool = Qnil;` reported as
        #    "assigned a caller-supplied VALUE (Qnil)", which names the one source that is
        #    provably fine and hides `create_rb_buffer_pool()`, the one that is not.
        unsafe = [rhs for rhs, _p, _o, _w in sources
                  if not (is_immediate(tree, rhs, ALL_RULES, scope=slot.scope)
                          or is_const_table(tree, rhs, ALL_RULES, scope=slot.scope))]
        lookups = sorted({split_call(unwrap(rhs))[0] for rhs in unsafe
                          if is_const_lookup(tree, rhs)})
        bare = sorted({unwrap(rhs) for rhs in unsafe
                       if re.fullmatch(r"[A-Za-z_]\w*", unwrap(rhs))})
        calls = sorted({split_call(unwrap(rhs))[0] for rhs in unsafe
                        if split_call(unwrap(rhs))[0]})
        if not sources:
            grade = "UNSOURCED"
            why = "no assignment found anywhere in the tree"
        elif allocators:
            grade = "ALLOCATES"
            why = "assigned from %s" % ", ".join(allocators)
        elif lookups and len(lookups) == len(calls) and not bare:
            # rmagick's `static VALUE default_channels_const = 0;` uses the zero as its own
            # not-yet-fetched sentinel, so the slot has an immediate source AND a lookup
            # source. Grading off all sources printed `(?, rb_const_get)` and, before the
            # immediate arm existed, OPAQUE -- the least informative label available for
            # the one shape whose reachability story is precisely known.
            grade = "CONST-LOOKUP"
            why = ("read out of the constant table (%s) and never registered -- rooted only "
                   "while that constant is not reassigned or removed" % ", ".join(lookups))
        elif bare:
            grade = "FOREIGN"
            why = "assigned a caller-supplied VALUE (%s)" % ", ".join(bare[:4])
        elif calls:
            grade = "CALL"
            why = ("assigned from %s -- an in-tree call whose result this pass does not "
                   "resolve" % ", ".join(calls[:4]))
        else:
            grade = "OPAQUE"
            why = "sources are neither immediate nor constant-table reachable"
        detail = ["decl: %s" % re.sub(r"\s+", " ", slot.decl)[:110], "why: " + why]
        if slot.kind != "scalar":
            detail.append("file-scope object `%s` declared at %s:%d"
                          % (slot.root, slot.opath.relative_to(tree.root),
                             line_at(tree.files.get(slot.opath, ""), slot.ooff)))
        if wrap:
            detail.append("object wrapped at %s:%d with dtype %s (.dmark = %s)"
                          % (wrap[2].relative_to(tree.root), wrap[3], wrap[0], wrap[1]))
        if wrap_unrooted:
            detail.append("wrapper NOT rooted: %s, so its dmark never runs" % wrap_unrooted)
        # Name the rooting that was found and rejected. A hit whose reason line says only
        # "assigned from rb_str_new2" on a slot that IS registered somewhere reads as a
        # false positive; saying which call was found, and which store outran it, is the
        # difference between a row a maintainer acts on and one they dismiss.
        if late:
            detail.append("rb_gc_register_mark_object at %s:%d roots the VALUE live at that "
                          "call, not the slot -- the store at %s is not covered by it"
                          % (late[1].relative_to(tree.root), late[2], late[3]))
        if pub_late:
            detail.append("%s at %s:%d publishes the VALUE live at that call, not the slot "
                          "-- the store at %s is not covered by it"
                          % (pub_late[0][0], pub_late[0][1].relative_to(tree.root),
                             pub_late[0][2], pub_late[1]))
        for rhs, spath, soff, _w in sources[:6]:
            detail.append("store: %s:%d  %s = %s" % (
                spath.relative_to(tree.root) if spath else "<macro-expansion>",
                line_at(tree.files[spath], soff) if spath else 0,
                slot.key, re.sub(r"\s+", " ", rhs)[:70]))
        r.hits.append((grade, rel, line, slot.key,
                       "file-scope VALUE `%s` is never registered with the GC" % slot.key,
                       detail))
    return r


def report(r, out=sys.stdout, verbose=False):
    for grade, path, line, _key, headline, detail in sorted(
            r.hits, key=lambda h: (h[1], h[2], h[3])):
        print("%-10s %s:%d  %s" % (grade, path, line, headline), file=out)
        for d in detail:
            print("             %s" % d, file=out)
    if verbose:
        for rule, path, line, key, why in sorted(r.discharges,
                                                 key=lambda d: (d[1], d[2], d[3])):
            print("  discharged %-17s %s:%d  %s -- %s" % (rule, path, line, key, why),
                  file=out)
    rules = r.by_rule()
    # Coverage. "0 hits" means one of four different things and only these counts tell them
    # apart: no C sources, no file-scope VALUE at all, everything discharged by a named
    # rule, or the unit parser resolved nothing.
    print("%-24s %2d file(s) | slots %3d/%-3d (scalar %2d, field %3d, unresolved-member %2d) "
          "| c++ %2d class-body/%-3d static-decl | allocating %2d | discharged %3d [%s] "
          "| HITS %d"
          % (r.name, r.files, r.slots, r.decls, r.scalars, r.fields, r.unresolved_members,
             r.class_bodies, r.class_members, r.allocating, len(r.discharges),
             " ".join("%s=%d" % (k, v) for k, v in sorted(rules.items())) or "-",
             len(r.hits)), file=out)
    return len(r.hits)


# ---------------------------------------------------------------- acceptance


def _find(pool, prefix):
    for d in pool:
        if pathlib.Path(d).name.startswith(prefix):
            return pathlib.Path(d)
        p = pathlib.Path(d)
        if p.is_dir():
            for c in sorted(p.iterdir()):
                if c.is_dir() and c.name.startswith(prefix):
                    return c
    return None


def _sweep(root, rules=ALL_RULES):
    root = pathlib.Path(root)
    return sweep(Tree(root), root.name, rules)


def _mutate(src_tree, edits):
    """Copy a real tree and apply (relpath, old, new) edits. Returns a temp path.

    Generated at test time from the unedited tree, never checked in: a hand-edited control
    is a different program and proves less (round-4 rule).
    """
    tmp = pathlib.Path(tempfile.mkdtemp())
    dst = tmp / pathlib.Path(src_tree).name
    shutil.copytree(src_tree, dst)
    for rel, old, new in edits:
        p = dst / rel
        txt = p.read_text()
        assert old in txt, "mutation anchor absent: %r in %s" % (old[:60], rel)
        p.write_text(txt.replace(old, new, 1))
    return dst


# C++ CONTROLS. Generated at test time from a source string, never checked in, because the
# corpus holds no instance of the shape: vernier is its only real C++ tree and every one of
# its 28 in-class `static VALUE` declarations is a static METHOD. A predicate with no
# control for a shape it claims to cover is a claim, not a result.
#
# Measured on the PRE-FIX script, which is what makes these reds rather than decoration:
#   `public: static VALUE cache;`            -- 0 slots. The label and the declaration share
#                                               one fragment and nothing matched.
#   the member after an inline method body   -- 0 slots, same fragment as the method.
#   `Rooted::cCollector` unindented          -- 0 slots.
#   an INDENTED `    static VALUE x;`        -- 1 slot, keyed BARE `x`, by accident, via the
#                                               function-local-static scan. Accidental recall
#                                               under a key that cannot match
#                                               `rb_global_variable(&Registry::x)` is worse
#                                               than none: it reads as coverage.

RED_CXX_STATIC_MEMBER = """
#include <ruby.h>

/* vernier's shape: a namespace, a class, access labels, inline methods, and the
   out-of-line definitions the ODR requires. Every static below has file-scope storage
   duration and nothing registers any of it. */
namespace prof {

class Registry {
    public: static VALUE cache;

    VALUE build(VALUE k) { cache = rb_str_new2("x"); return cache; }

    /* declared AFTER an inline method body, with no `;` between them */
    static VALUE names[4];
};

VALUE Registry::cache = Qnil;
VALUE Registry::names[4];

void fill(void) { Registry::names[0] = rb_ary_new(); }

}
"""

GREEN_CXX_STATIC_MEMBER = """
#include <ruby.h>

/* The same shape, discharged: rb_define_class installs the object under a permanent
   constant, so the slot is rooted by Ruby and must NOT report. The class body is at
   column 0 -- the indentation the function-local-static scan needs is absent, so nothing
   but the descent can see this member at all. */
namespace prof {
class Rooted {
public:
static VALUE cCollector;
};
VALUE Rooted::cCollector = Qnil;
}

extern "C" void Init_probe(void) {
    prof::Rooted::cCollector = rb_define_class("Collector", rb_cObject);
}
"""


RED_ARRAY_OF_STRUCT = """
#include <ruby.h>

/* unicorn's shape, reduced to two elements: a file-scope ARRAY OF STRUCT whose first
   brace opens an INITIALISER LIST, not the aggregate body. The element type carries a
   VALUE, the init loop stores an allocation into it, and nothing registers it. */
struct common_field {
    const signed long len;
    const char *name;
    VALUE value;
};

static struct common_field common_http_fields[] = {
    { 6, "ACCEPT", Qnil },
    { 6, "COOKIE", Qnil },
};

void Init_probe(void) {
    struct common_field *cf;
    for (cf = common_http_fields;
         cf < common_http_fields + 2; cf++) {
        cf->value = rb_str_new(cf->name, cf->len);
    }
}
"""

GREEN_ARRAY_OF_STRUCT = """
#include <ruby.h>

/* The same shape, registered. This green is the load-bearing half: before the
   initialiser-brace fix it passed for the WRONG REASON -- the declaration was discarded
   whole, so the member was never looked at, and "not flagged" meant "not enumerated". */
struct common_field {
    const signed long len;
    const char *name;
    VALUE value;
};

static struct common_field common_http_fields[] = {
    { 6, "ACCEPT", Qnil },
    { 6, "COOKIE", Qnil },
};

void Init_probe(void) {
    struct common_field *cf;
    for (cf = common_http_fields;
         cf < common_http_fields + 2; cf++) {
        cf->value = rb_str_new(cf->name, cf->len);
        rb_global_variable(&cf->value);
    }
}
"""


RED_CORE_OBJ_SHADOW = """
#include <ruby.h>

/* `rb_[cme][A-Z]\\w*` is CRuby's convention for CORE objects and also the one gems copy for
   their own statics. Here the sweep reports rb_cLocalThing as CONST-LOOKUP and must NOT
   then clear `cached`, which is the same VALUE under another name. Clearing an alias of a
   slot you are concurrently reporting is an over-clear wearing a discharge. */
static VALUE rb_cLocalThing;
static VALUE cached;

void Init_probe(void) {
    rb_cLocalThing = rb_const_get(rb_cObject, rb_intern("LocalThing"));
    cached = rb_cLocalThing;
}
"""

RED_DEFINE_ERROR = """
#include <ruby.h>

/* There is no rb_define_error in CRuby, so this name can only ever be a GEM-LOCAL helper.
   Its body returns rb_class_new, which is MEASURED MOVABLE -- discharging on the name alone
   cleared a class without reading its constructor. */
static VALUE eBoom;

static VALUE rb_define_error(const char *name, VALUE super) {
    VALUE k = rb_class_new(super);
    rb_ivar_set(k, rb_intern("@name"), rb_str_new_cstr(name));
    return k;
}

void Init_probe(void) { eBoom = rb_define_error("Boom", rb_eStandardError); }
"""

GREEN_DEFINE_UNDER = """
#include <ruby.h>

/* The counter-shape, so a future tightening cannot quietly delete the rule: a real
   rb_define_class_under reaches rb_vm_register_global_object and therefore PINS, and must
   stay discharged. */
static VALUE mX;
static VALUE eOk;

void Init_probe(void) {
    mX = rb_define_module("X");
    eOk = rb_define_class_under(mX, "Err", rb_eStandardError);
}
"""


# ---- GENERATED REDS FOR THE ROUND-9 REVIEW THREADS -------------------------------------
#
# Every one of these was measured on the PRE-FIX script and every one of them came back
# GREEN, six of the eight with `slots 0` or a merged row -- a clean sheet produced by the
# parser or the key, which is the exact failure this suite exists to catch. The corpus is
# NEUTRAL on all of them (zero new hit rows across 99 trees), so without these fixtures
# there is no evidence in the repository that any of the fixes does anything.

RED_CROSS_TU_STATIC_A = """
#include <ruby.h>

/* a.c registers ITS OWN `cache`. Nothing here says anything about b.c's. */
static VALUE cache;

void Init_a(void) {
    cache = rb_str_new2("a");
    rb_global_variable(&cache);
}
"""

RED_CROSS_TU_STATIC_B = """
#include <ruby.h>

/* A DIFFERENT OBJECT with the same name -- internal linkage, so b.c's `cache` and a.c's
   are two slots that cannot see each other. Nothing registers this one. Pre-fix: the two
   declarations deduped to one row by NAME, a.c's rb_global_variable discharged the merged
   row, and this store vanished behind `slots 1/2 ... HITS 0`. */
static VALUE cache;

void Init_b(void) {
    cache = rb_str_new2("b");
}
"""

RED_VALUE_REG_REASSIGN = """
#include <ruby.h>

/* rb_gc_register_mark_object(v) roots the OBJECT the argument evaluated to -- gc.c pushes
   it onto vm->mark_object_ary and never reads the C variable again. The second store puts
   an object in the slot that nothing roots, and the first one stays rooted forever. */
static VALUE cache;

void Init_a(void) {
    cache = rb_str_new2("a");
    rb_gc_register_mark_object(cache);
}

void refresh(void) {
    cache = rb_str_new2("b");
}
"""

GREEN_VALUE_REG_ONE_SHOT = """
#include <ruby.h>

/* The counter-shape, and it is the commonest one: assign once, register, never touch it
   again. kgio accept.c:500-501 is the corpus instance. This must stay discharged, or the
   fix above has simply deleted the `registered-value` rule. */
static VALUE localhost;

void Init_a(void) {
    localhost = rb_str_new2("127.0.0.1");
    rb_gc_register_mark_object(localhost);
}
"""

GREEN_VALUE_REG_IMMEDIATE_RESET = """
#include <ruby.h>

/* A later store of a PROVABLY IMMEDIATE value is not a later store: it cannot leave an
   unrooted collectable object in the slot. date 3.5.1 forced this -- thirty
   `static VALUE pat = Qnil;` lazily filled by a regcomp() that freezes and registers each
   Regexp, behind a NIL_P guard. Counting the Qnil initialisers as reassignments reported a
   correct one-shot lazy-registration idiom as a defect. */
static VALUE cache = Qnil;

void Init_a(void) {
    cache = rb_str_new2("a");
    rb_gc_register_mark_object(cache);
}

void reset(void) {
    cache = Qnil;
}
"""

RED_PUBLISHED_REASSIGN = """
#include <ruby.h>

/* rb_define_const publishes the object present AT THE CALL. The constant keeps the first
   String alive; the second is in the C slot with nothing holding it. Pre-fix the
   const-published rule cleared the slot on the name alone. */
static VALUE cache;

void Init_a(void) {
    cache = rb_str_new2("a");
    rb_define_const(rb_cObject, "CACHE", cache);
}

void refresh(void) {
    cache = rb_str_new2("b");
}
"""

RED_WRAPPER_DISCARDED = """
#include <ruby.h>

/* A dmark only runs while the WRAPPER that owns it is reachable from a root. This wrapper
   is thrown away on the line it is created, so state_mark never runs and `state.held` is
   marked by nothing. Pre-fix, any wrapping call with a non-NULL mark callback discharged
   every field of the object -- handing them to sweep_unmarked.py, which finds a
   correct-looking dmark and clears them. An over-clear laundered through a second script. */
typedef struct { VALUE held; } state_t;
static state_t state;

static void state_mark(void *p) { state_t *s = (state_t *)p; rb_gc_mark(s->held); }

static const rb_data_type_t state_type = {
    "State", { state_mark, NULL, NULL, NULL }, 0, 0, 0
};

void Init_a(void) {
    state.held = rb_str_new2("x");
    TypedData_Wrap_Struct(rb_cObject, &state_type, &state);
}
"""

GREEN_WRAPPER_ROOTED = """
#include <ruby.h>

/* stackprof's shape, and the only wrapped site the corpus has: the wrapper goes into a
   file-scope slot that rb_global_variable roots, so the dmark really does run and the
   fields really are predicate A's subject. Must stay discharged as `wrapped`. */
typedef struct { VALUE held; } state_t;
static state_t state;
static VALUE gc_hook;

static void state_mark(void *p) { state_t *s = (state_t *)p; rb_gc_mark(s->held); }

static const rb_data_type_t state_type = {
    "State", { state_mark, NULL, NULL, NULL }, 0, 0, 0
};

void Init_a(void) {
    state.held = rb_str_new2("x");
    gc_hook = TypedData_Wrap_Struct(rb_cObject, &state_type, &state);
    rb_global_variable(&gc_hook);
}
"""

GREEN_WRAPPER_RETURNED = """
#include <ruby.h>

/* THE FLOOR, PINNED AS A TEST rather than left as prose. An allocator cfunc wraps and
   RETURNS the wrapper; where it ends up is decided by the Ruby program, which this sweep
   does not read. Requiring a provably rooted target instead would make the single
   commonest correct shape in every C extension a hit, so "other" is not reported -- and
   that decision has to break loudly if somebody tightens the rule, rather than quietly
   adding rows to every gem in the corpus. */
typedef struct { VALUE held; } state_t;
static state_t state;

static void state_mark(void *p) { state_t *s = (state_t *)p; rb_gc_mark(s->held); }

static const rb_data_type_t state_type = {
    "State", { state_mark, NULL, NULL, NULL }, 0, 0, 0
};

static VALUE alloc(VALUE klass) {
    state.held = rb_str_new2("x");
    return TypedData_Wrap_Struct(klass, &state_type, &state);
}

void Init_a(void) { rb_define_alloc_func(rb_cObject, alloc); }
"""

RED_NAMESPACE_LINKAGE = """
#include <ruby.h>

/* A namespace is file scope and a linkage block is not a scope at all: all three statics
   below have one slot each, alive for the whole process, invisible to the GC. `rooted` is
   the load-bearing third -- a green enumerated INSIDE a namespace and then discharged by
   name, so the descent is proven to be reading the bodies rather than skipping them. */
namespace prof {

static VALUE ns_cache;

namespace inner {
static VALUE deep_cache;
}

extern "C" {
static VALUE rooted;
}

void setup(void) {
    rooted = rb_str_new2("r");
    rb_global_variable(&rooted);
}

}

extern "C" void Init_probe(void) {
    prof::ns_cache = rb_str_new2("n");
    prof::inner::deep_cache = rb_ary_new();
    prof::setup();
}
"""

RED_THREAD_LOCAL = """
#include <ruby.h>

/* Thread-local storage is static storage that Ruby's root set does not reach, and
   rb_global_variable roots ONE address while a TLS slot has one per thread -- so this is
   the worse variant, not a milder one. Neither spelling was in TYPE_KW nor ALL-CAPS, so
   the leading-token gate dropped the whole declaration: 0 slots, which reads as a file
   with no statics in it. */
thread_local VALUE tl_cache;
static thread_local VALUE tl_static_cache;

extern "C" void Init_probe(void) {
    tl_cache = rb_str_new2("a");
    tl_static_cache = rb_ary_new();
}
"""

RED_UNINDENTED_LOCAL_STATIC = """
#include <ruby.h>

/* A function-local static at COLUMN ZERO. Same storage duration, same defect, and the old
   scan required leading whitespace -- style standing in for storage class. 0 slots. */
void Init_probe(void) {
static VALUE cache;
cache = rb_str_new2("a");
}
"""

RED_QUALIFIED_LOCAL_STATIC = """
#include <ruby.h>

/* `static volatile VALUE` is the spelling a developer reaches for precisely when a slot is
   written in one call and read in another -- this predicate's own subject -- and the
   pattern required VALUE to follow `static` immediately, so it was the one shape that
   could not be seen. 0 slots. */
void Init_probe(void) {
    static volatile VALUE cache;
    static VALUE const *ptr;
    cache = rb_str_new2("a");
    (void)ptr;
}
"""

RED_TWO_LOCAL_STATICS = """
#include <ruby.h>

/* ROUND 9: TWO FUNCTIONS, ONE TRANSLATION UNIT, ONE NAME. A function-local static is
   internal to its BLOCK, so these are two objects and nothing outside either function can
   name the other's. Keyed `(file, "cache")` they merged into ONE slot: `slots 1/2`, the
   first function's rb_global_variable discharged the merged row, and the second's
   unrooted String vanished behind HITS 0. The green half is in the same file on purpose --
   `rooted` must STILL discharge, or the split has just deleted the registration rule. */
static VALUE
rooted(VALUE self)
{
    static VALUE cache;
    if (!cache) {
        cache = rb_str_new2("kept");
        rb_global_variable(&cache);
    }
    return cache;
}

static VALUE
unrooted(VALUE self)
{
    static VALUE cache;
    if (!cache) {
        cache = rb_str_new2("lost");
    }
    return cache;
}

void Init_probe(void) { rb_define_method(rb_cObject, "a", rooted, 0); }
"""

def RED_NAMESPACED_FUNCTION(wrapped):
    """A function-local static inside a transparent C++ scope -- WRAPPED is the flag.

    ROUND 9 REVIEW: the namespace descent was ported into the SLOT walk and not into the
    FUNCTION index, so this fixture measured `slots 0/0, decls 0, HITS 0` while the same
    source unwrapped measured 2 slots and one hit. Not a dropped row: an emptied index,
    which is the failure this file's counters exist to tell apart from a clean gem.

    Three things in one file on purpose. `cache` is the RED -- an unregistered
    function-local static in a namespaced function. `keep` is the GREEN in the same scope,
    registered, and it must still discharge by name: a descent that indexes the function
    but cannot read its body would drop the discharge and turn one over-clear into an
    over-report. `Init_probe` sits in the `extern "C"` block, because a linkage block is
    the other transparent scope and a C++ gem puts its entry points in exactly one.
    """
    ns_open, ns_close = ("namespace prof {\n", "}\n") if wrapped else ("", "")
    ln_open, ln_close = ("extern \"C\" {\n", "}\n") if wrapped else ("", "")
    return ("#include <ruby.h>\n\n" + ns_open + """
static VALUE
cached(VALUE self)
{
    static VALUE cache;
    if (!cache) {
        cache = rb_str_new_cstr("lost");
    }
    return cache;
}

static VALUE
rooted(VALUE self)
{
    static VALUE keep;
    if (!keep) {
        keep = rb_str_new_cstr("kept");
        rb_global_variable(&keep);
    }
    return keep;
}
""" + ns_close + "\n" + ln_open + """
void Init_probe(void) {
    rb_define_method(rb_cObject, "c", cached, 0);
    rb_define_method(rb_cObject, "r", rooted, 0);
}
""" + ln_close)


def RED_ATTRIBUTED_FUNCTION(lead, tail):
    """A function-local static inside an ATTRIBUTED definition -- the declarator is the flag.

    ROUND 9 REVIEW (:929): the function index skipped WHITESPACE ONLY between the parameter
    list's `)` and the body's `{`, so a definition carrying an attribute was never indexed,
    contributed no span, and every function-local `static VALUE` inside it was invisible.
    Measured on the attributed arm below, unfixed: `slots 0/0, decls 0, HITS 0` -- against
    2 slots and 1 hit for the identical source without the attribute. An emptied index, not
    a dropped row, which is why the COUNTERS are the assertion and the hit count is the
    corollary.

    THE DECLARATOR IS THE FLAG. Six spellings of one pair of functions must give one funnel
    and one set of rows, because nothing between the `)` and the `{` affects storage
    duration. `("VALUE", "")` is the green the reviewer asked for -- an ordinary
    unattributed function must still index -- and the two `auto ... -> VALUE` arms are what
    hold POST_DECL_PUNCT down in this caller, since a C++ trailing return type is the
    variant that broke predicate D's closed word list and predicate C reads the same C++
    trees.

    Two functions, as elsewhere in this file: `cache` is the RED and `keep` is the GREEN in
    the same scope. A walk that indexes the function but misplaces its body would drop
    `keep`'s registration and turn one over-clear into an over-report.
    """
    return ("#include <ruby.h>\n\n"
            "static " + lead + "\ncached(void)" + tail + """
{
    static VALUE cache;
    if (!cache) {
        cache = rb_str_new_cstr("lost");
    }
    return cache;
}

static """ + lead + """
rooted(void)""" + tail + """
{
    static VALUE keep;
    if (!keep) {
        keep = rb_str_new_cstr("kept");
        rb_global_variable(&keep);
    }
    return keep;
}

void Init_probe(void)
{
    rb_define_method(rb_cObject, "c", cached, 0);
    rb_define_method(rb_cObject, "r", rooted, 0);
}
""")


RED_INITIALISED_DECLARATOR_LIST = """
#include <ruby.h>

/* ROUND 9 REVIEW (:998): THE MATCH USED TO END AT THE FIRST `=`. A declarator list is not
   over when its first declarator takes an initialiser, but `[^;=(){}]+[;=]` stopped there
   and declared ONE slot out of two. Measured unfixed: `slots 1/1`, one `registered-slot`
   discharge for `rooted`, HITS 0 -- while `bad` takes an rb_str_new_cstr and was never a
   slot at all. The green is in the same declaration on purpose: `rooted` must STILL
   discharge, or reading the whole statement has just broken the registration rule. */
static VALUE
probe(VALUE self)
{
    static VALUE rooted = Qnil, bad = Qnil;
    if (NIL_P(rooted)) {
        rooted = rb_str_new_cstr("kept");
        rb_global_variable(&rooted);
    }
    bad = rb_str_new_cstr("lost");
    return bad;
}

void Init_probe(void) { rb_define_method(rb_cObject, "p", probe, 0); }
"""


def GREEN_SINGLE_INITIALISED(registered):
    """ONE declarator, initialised -- the shape the fix must not disturb. REGISTERED is the flag.

    The over-clear was in the declarators AFTER an initialiser, so the single-declarator case
    is where a fix that reads too much would show first: it must still be exactly one slot,
    and it must still discharge when and only when it is registered.
    """
    reg = "\n        rb_global_variable(&only);" if registered else ""
    return ("#include <ruby.h>\n\n"
            "static VALUE\nprobe(VALUE self)\n{\n"
            "    static VALUE only = Qnil;\n"
            "    if (NIL_P(only)) {\n"
            "        only = rb_str_new_cstr(\"x\");" + reg + "\n"
            "    }\n"
            "    return only;\n}\n\n"
            "void Init_probe(void) { rb_define_method(rb_cObject, \"p\", probe, 0); }\n")


GREEN_TOP_LEVEL_COMMAS = """
#include <ruby.h>

/* The commas that separate declarators are the TOP-LEVEL ones. `a = f(x, y), b` is two
   declarators and four commas; splitting on all of them yields four things, two of which
   are `y)` and fragments of a call. `fp` is here because reading the whole statement means
   crossing a `(` the old character class refused outright: a function pointer is still not
   a slot, and the gate that says so is on the declarator, not on the statement. */
static VALUE
probe(VALUE self)
{
    static VALUE a = rb_funcall(self, rb_intern("dup"), 2, x, y), b;
    static VALUE (*fp)(void);
    b = rb_str_new_cstr("lost");
    return a;
}

void Init_probe(void) { rb_define_method(rb_cObject, "p", probe, 0); }
"""


GREEN_HEADER_STATIC_H = """
#ifndef probe_h
#define probe_h
/* `static VALUE` in a HEADER is one object per INCLUDING translation unit, and the header
   is not a translation unit at all. Scoping it to the header hides every store it has --
   10 UNSOURCED rows across unicorn x2 and yajl-ruby, none of them the defect. Header
   statics stay tree-wide keyed; the residual (two includers, one registration, one row) is
   named in the TU_EXT comment. */
static VALUE g_shared;
#endif
"""

GREEN_HEADER_STATIC_C = """
#include <ruby.h>
#include "probe.h"

void Init_probe(void) {
    g_shared = rb_str_new2("x");
    rb_global_variable(&g_shared);
}
"""


def _sweep_source(src, rules=ALL_RULES, suffix=".cc"):
    """Sweep a one-file tree generated from a source string.

    Defaults to C++ because most generated fixtures here are; pass suffix=".c" for the
    ones whose shape is C, so the fixture exercises the same file set a C gem would.
    """
    return _sweep_sources({"probe" + suffix: src}, rules)


def _write_sources(files):
    """Materialise {relative filename: source} as a tree, and return its root.

    Split out from _sweep_sources because the rejection table asserts the FUNCTION INDEX
    rather than the sweep's rows: what a mis-crossed post-declarator walk invents is a
    function, and a fixture that only reads hits cannot see one appear.
    """
    tmp = pathlib.Path(tempfile.mkdtemp()) / "ext"
    tmp.mkdir(parents=True)
    for rel, src in files.items():
        (tmp / rel).write_text(src)
    return tmp


def _sweep_sources(files, rules=ALL_RULES):
    """Sweep a generated tree of {relative filename: source}.

    Several of the shapes below are only expressible across MORE THAN ONE FILE -- internal
    linkage is per translation unit, so a one-file fixture cannot state the question at all.
    """
    return _sweep(_write_sources(files), rules)


def self_test(pool):
    ok, log = True, []

    def check(cond, label, extra=""):
        nonlocal ok
        ok &= bool(cond)
        log.append("%s %s%s" % ("PASS" if cond else "FAIL", label,
                                "" if cond else "   [%s]" % extra))

    sp = _find(pool, "stackprof-0.2.28")
    kgio = _find(pool, "kgio-2.11.4")
    rbt = _find(pool, "rbtrace-0.5.4")
    vern = _find(pool, "vernier-1.10.1")
    msg = _find(pool, "msgpack-1.8.4")
    ed = _find(pool, "ed25519-1.4.0")
    my = _find(pool, "mysql2-0.5.6")
    for label, p in (("stackprof-0.2.28", sp), ("kgio-2.11.4", kgio),
                     ("rbtrace-0.5.4", rbt), ("vernier-1.10.1", vern),
                     ("msgpack-1.8.4", msg), ("ed25519-1.4.0", ed),
                     ("mysql2-0.5.6", my)):
        if p is None:
            print("FAIL fixture missing: %s (pass its directory, or its parent)" % label)
            return 1

    # 0. Line numbers survive the strip pipeline. Every hit prints file:line, so this is a
    #    precondition, not a nicety.
    probe = sp / "ext" / "stackprof" / "stackprof.c"
    raw = probe.read_text(errors="replace")
    stripped = strip_directives(strip_noise(raw))
    anchor = "static VALUE sym_gc_samples, objtracer;"
    off = stripped.index(anchor)
    raw_line = raw[:raw.index(anchor)].count("\n") + 1
    check(len(stripped) == len(raw) and line_at(stripped, off) == raw_line,
          "strip pipeline preserves byte offsets and line numbers (objtracer at :%d)"
          % raw_line,
          "len %d vs %d, line %d vs %d" % (len(stripped), len(raw),
                                           line_at(stripped, off), raw_line))

    spr = _sweep(sp)
    hits = {h[3]: h for h in spr.hits}
    disc = {d[3]: d for d in spr.discharges}

    # 1. THE INSTANCE. tmm1/stackprof#245, found by a human three lines from the struct the
    #    wrap-site sweep already walked.
    check("objtracer" in hits and hits["objtracer"][2] == raw_line
          and hits["objtracer"][0] == "ALLOCATES",
          "stackprof 0.2.28 RED: objtracer at %s:%s, grade %s"
          % (hits["objtracer"][1] if "objtracer" in hits else "-",
             hits["objtracer"][2] if "objtracer" in hits else "-",
             hits["objtracer"][0] if "objtracer" in hits else "-"),
          sorted(hits))
    check("rb_tracepoint_new" in " ".join(hits.get("objtracer", (0, 0, 0, 0, "", []))[5]),
          "...and the reason names rb_tracepoint_new, the allocating source")

    # 2. Same file, the discriminator working rather than luck: the two registered greens
    #    have to be ENUMERATED and then DISCHARGED, not merely absent.
    for key in ("_stackprof.empty_string", "_stackprof.fake_frame_names[]"):
        check(key in disc and disc[key][0] == "registered-slot"
              and "rb_global_variable" in disc[key][4],
              "stackprof %s NOT flagged -- discharged by %s"
              % (key, disc[key][4] if key in disc else "NOTHING (absent, not discharged)"),
              sorted(disc))
    check(not any(k.startswith("sym_") for k in hits),
          "stackprof's 28 `S(name)` paste-macro symbols discharge as immediates",
          sorted(k for k in hits if k.startswith("sym_")))

    # 3. kgio: the natural green of the same shape, on the OTHER registration primitive.
    kg = _sweep(kgio)
    kdisc = {d[3]: d for d in kg.discharges}
    check("localhost" in kdisc and kdisc["localhost"][0] == "registered-value"
          and "rb_gc_register_mark_object" in kdisc["localhost"][4],
          "kgio 2.11.4: localhost EXPLICITLY discharged -- %s"
          % (kdisc["localhost"][4] if "localhost" in kdisc else "not discharged at all"),
          sorted(kdisc))
    # The brief said localhost is kgio's ONLY file-static VALUE. It is not: 12 declarations
    # across 6 files -- and 12 SLOTS, not 9. The old figure of "9 distinct names" was
    # measuring the cross-TU merge itself: `sym_wait_readable` is declared `static` in both
    # poll.c and read.c, and `sym_wait_writable` in poll.c, write.c and writev.c. Those are
    # five separate objects with two names, and internal linkage means no file can see
    # another's. All five discharge as immediates, so the count moves and the verdict does
    # not -- which is why this pin is a pin and not a regression.
    kdup = [k for k in {s.key for s in Tree(kgio).slots}
            if sum(1 for s in Tree(kgio).slots if s.key == k) > 1]
    check(kg.slots == 12 and kg.decls == 12 and sorted(kdup) ==
          ["sym_wait_readable", "sym_wait_writable"],
          "kgio has %d file-scope VALUE declarations and %d slots -- one per translation "
          "unit, so the two names declared static in more than one file (%s) stay separate"
          % (kg.decls, kg.slots, ", ".join(sorted(kdup)) or "none"))
    check("cClientSocket" in {h[3] for h in kg.hits},
          "kgio cClientSocket reports: `Kgio.accept_class=` stores a caller-supplied class "
          "into an unregistered static (accept.c:50)",
          sorted(h[3] for h in kg.hits))

    # 4. rbtrace: the field-of-a-file-static-object shape, two levels down, and the gc_hook
    #    that wraps NULL so nothing marks it.
    rr = _sweep(rbt)
    rhits = {h[3]: h for h in rr.hits}
    for key, want in (("rbtracer.list[].self", 94), ("rbtracer.list[].klass", 95)):
        check(key in rhits and rhits[key][0] == "FOREIGN" and rhits[key][2] == want,
              "rbtrace 0.5.4 RED: %s at %s:%s (the MEMBER's line, not the object's :107)"
              % (key, rhits[key][1] if key in rhits else "-",
                 rhits[key][2] if key in rhits else "-"),
              sorted(rhits))
    check("rbtracer" not in {w for w in Tree(rbt).wraps},
          "rbtrace's gc_hook wraps NULL, so `rbtracer` is not discharged as wrapped")

    # 5. A GREEN generated at test time from the real red tree -- the one-line registration
    #    the upstream fix would add. A checked-in hand edit is a different program.
    green = _mutate(sp, [("ext/stackprof/stackprof.c",
                          "    rb_global_variable(&gc_hook);",
                          "    rb_global_variable(&objtracer);\n"
                          "    rb_global_variable(&gc_hook);")])
    gr = _sweep(green)
    gdisc = {d[3]: d for d in gr.discharges}
    # A green that is really a parse failure wearing a green tick is exactly how round 4
    # passed for the wrong reason. Assert the slot count is unchanged before believing it.
    check("objtracer" not in {h[3] for h in gr.hits}
          and gdisc.get("objtracer", ("",))[0] == "registered-slot"
          and gr.slots == spr.slots,
          "stackprof GREEN once `rb_global_variable(&objtracer)` is added (generated), and "
          "the mutated tree still resolves all %d slots" % spr.slots,
          [h[3] for h in gr.hits] + ["slots %d vs %d" % (gr.slots, spr.slots)])

    # 5b. The commonest class-static idiom in the corpus, and the one a two-pass all-sources
    #     rule flags: `static VALUE mFoo = Qnil;` at file scope, `mFoo = rb_define_module()`
    #     in Init. Six gems write it; ed25519 is the smallest, so it is the control.
    edr = _sweep(ed)
    check(not edr.hits and edr.slots >= 3,
          "ed25519 1.4.0 GREEN: `static VALUE m = Qnil;` + rb_define_module is one slot "
          "with an immediate source AND a const-table source (%d slots, %d discharged)"
          % (edr.slots, len(edr.discharges)),
          [h[3] for h in edr.hits])

    # 5c. Rooting that lives at a USE site. msgpack's anonymous Struct class is created by
    #     `rb_struct_define(NULL, ...)` and installed by rb_define_const on the next line;
    #     nothing about its ASSIGNMENT is safe.
    mg = _sweep(msg)
    mdisc = {d[3]: d for d in mg.discharges}
    check(mdisc.get("cMessagePack_ExtensionValue", ("",))[0] == "const-published",
          "msgpack cMessagePack_ExtensionValue discharged by rb_define_const at a use site "
          "-- %s" % mdisc.get("cMessagePack_ExtensionValue", ("", "", 0, "", "not at all"))[4],
          sorted(h[3] for h in mg.hits))

    # 5d. INT2NUM is not immediate; INT2NUM OF A LITERAL is. mysql2 result.c:1265-1267.
    #     The generated red is the one-token change from the literal to a variable.
    myr = {d[3]: d for d in _sweep(my).discharges}
    check(myr.get("opt_time_year", ("",))[0] == "immediate",
          "mysql2 opt_time_year (INT2NUM(2000)) discharged as immediate")
    myred = _mutate(my, [("ext/mysql2/result.c", "opt_time_year = INT2NUM(2000);",
                          "opt_time_year = INT2NUM(rb_num2int(argc_dummy));")])
    check("opt_time_year" in {h[3] for h in _sweep(myred).hits},
          "...and flips RED when the literal becomes a runtime value (generated)",
          sorted(h[3] for h in _sweep(myred).hits))

    # 5e. RECALL PIN. rmagick declares all 67 of its file-scope VALUEs as `EXTERN VALUE x;`
    #     (rmagick.h:333+), and two separate parser defects each dropped the lot while the
    #     coverage line read `slots 1/1` -- which looks like a small gem, not a broken
    #     parser. Then 44 of the 67 are `Class_FooType = rm_define_enum_type("FooType")`,
    #     an in-tree wrapper around rb_define_class_under, so recall without return
    #     resolution is 44 false positives. Both numbers are pinned.
    rm = _find(pool, "rmagick-6.1.4")
    if rm is None:
        log.append("SKIP rmagick-6.1.4 recall pin (fixture absent) -- not counted as a pass")
    else:
        rmr = _sweep(rm)
        check(rmr.slots >= 60 and len(rmr.hits) <= 5,
              "rmagick 6.1.4 recall pin: %d slots found (EXTERN-declared), %d discharged, "
              "%d hits" % (rmr.slots, len(rmr.discharges), len(rmr.hits)),
              [h[3] for h in rmr.hits])

    # 5f. C++ RED. A `static VALUE` DATA MEMBER of a class is a file-scope slot, and the
    #     descent has to find it under a key the registration vocabulary can match.
    cxx = _sweep_source(RED_CXX_STATIC_MEMBER)
    chits = {h[3]: h for h in cxx.hits}
    check("Registry::cache" in chits and chits["Registry::cache"][0] == "ALLOCATES",
          "C++ RED: `public: static VALUE cache;` is found and reported, keyed "
          "Registry::cache, grade %s"
          % (chits["Registry::cache"][0] if "Registry::cache" in chits else "NOT FOUND"),
          sorted(chits))
    check("Registry::names[]" in chits,
          "C++ RED: a member declared after an inline METHOD BODY is still a member -- "
          "without blank_bodies the two share one fragment and this one disappears",
          sorted(chits))
    check("rb_str_new2" in " ".join(chits.get("Registry::cache", (0,) * 5 + ([],))[5]),
          "...and the store made from inside a method, spelled BARE `cache = ...`, is read "
          "as a source of the qualified slot")
    check(cxx.class_bodies == 1 and cxx.class_members >= 2,
          "C++ RED coverage: %d class bod(ies) entered, %d static VALUE member declaration"
          "(s) seen -- a zero here would be a parse miss wearing a green tick"
          % (cxx.class_bodies, cxx.class_members))

    # 5g. C++ GREEN. The same shape, rooted the way the brief blesses. Over-clearing is the
    #     failure mode this sweep exists to prevent, so the green is asserted as a NAMED
    #     discharge on an ENUMERATED slot, never as an absence.
    cxg = _sweep_source(GREEN_CXX_STATIC_MEMBER)
    gdis = {d[3]: d for d in cxg.discharges}
    check(not cxg.hits and gdis.get("Rooted::cCollector", ("",))[0] == "const-table",
          "C++ GREEN: a class static assigned from rb_define_class is ENUMERATED and then "
          "discharged by const-table -- %s"
          % (gdis["Rooted::cCollector"][4] if "Rooted::cCollector" in gdis
             else "NOT DISCHARGED (absent, not cleared)"),
          [h[3] for h in cxg.hits] + sorted(gdis))
    check(cxg.slots == 1 and cxg.class_bodies == 1,
          "...and the in-class declaration and the out-of-line definition "
          "`VALUE Rooted::cCollector = Qnil;` merge into ONE slot, not two (%d slot(s) from "
          "%d declaration(s))" % (cxg.slots, cxg.decls))

    # 5h. INITIALISER-BRACE RED. `static struct T name[] = { ... };` puts its first `{`
    #     after the `=`, so the aggregate-body test failed and the declaration was dropped
    #     WHOLE -- before any type was inspected, which made the miss type-blind. It cost
    #     17 declarations across the corpus. The predicate walks file-scope struct OBJECTS
    #     by design; this is that walk not starting.
    ini = _sweep_source(RED_ARRAY_OF_STRUCT, suffix=".c")
    ihits = {h[3]: h for h in ini.hits}
    check("common_http_fields[].value" in ihits,
          "initialiser-brace RED: a VALUE member of `static struct T name[] = {...}` is "
          "enumerated and reported -- the shape unicorn reported as `field 0`",
          sorted(ihits))
    check(ini.fields >= 1,
          "...and it is counted as a FIELD slot (%d), not silently absent: `field 0` is "
          "what the miss looked like from the outside" % ini.fields)

    # 5i. INITIALISER-BRACE GREEN. Asserted as a NAMED discharge on an ENUMERATED slot.
    #     Pre-fix this fixture "passed" on slots=0 -- a clean sheet produced by the parser
    #     finding nothing, which is the acceptance-item-2 failure the header warns about.
    ing = _sweep_source(GREEN_ARRAY_OF_STRUCT, suffix=".c")
    idis = {d[3]: d for d in ing.discharges}
    check(not ing.hits
          and idis.get("common_http_fields[].value", ("",))[0] == "registered-slot",
          "initialiser-brace GREEN: `rb_global_variable(&cf->value)` discharges the same "
          "slot by NAME -- %s"
          % (idis["common_http_fields[].value"][4]
             if "common_http_fields[].value" in idis
             else "NOT DISCHARGED (absent, not cleared)"),
          [h[3] for h in ing.hits] + sorted(idis))

    # 5j. const-table's two LATENT over-clears. Neither fires on the corpus -- the CORE_OBJ
    #     arm was instrumented with a recording proxy and never fired on any of the 41 trees
    #     -- so both of these are the generated red doing the job the corpus cannot.
    cos = _sweep_source(RED_CORE_OBJ_SHADOW, suffix=".c")
    cos_dis = {d[3] for d in cos.discharges}
    check("cached" not in cos_dis and len(cos.hits) == 2,
          "CORE_OBJ shadow RED: a gem-local `rb_cLocalThing` is a tree slot, not a core "
          "object, so its alias `cached` must NOT discharge -- the sweep may never clear an "
          "alias of a slot it is concurrently reporting (%d hit(s), discharged %s)"
          % (len(cos.hits), sorted(cos_dis) or "nothing"),
          [h[3] for h in cos.hits])

    dfe = _sweep_source(RED_DEFINE_ERROR, suffix=".c")
    check([h[3] for h in dfe.hits] == ["eBoom"],
          "rb_define_error RED: no such CRuby API exists, so the name binds to a GEM-LOCAL "
          "helper whose body returns rb_class_new -- measured MOVABLE. It must resolve "
          "through the body and report, not discharge on the name",
          [h[3] for h in dfe.hits] + ["discharged:" + d[3] for d in dfe.discharges])

    # 5k. ...and the counter-shape, so tightening the rule cannot quietly delete it.
    gdu = _sweep_source(GREEN_DEFINE_UNDER, suffix=".c")
    gdu_dis = {d[3]: d for d in gdu.discharges}
    check(not gdu.hits and gdu_dis.get("eOk", ("",))[0] == "const-table",
          "const-table GREEN: a real rb_define_class_under reaches "
          "rb_vm_register_global_object and therefore PINS -- it must stay discharged (%s)"
          % (gdu_dis["eOk"][4] if "eOk" in gdu_dis else "NOT DISCHARGED"),
          [h[3] for h in gdu.hits] + sorted(gdu_dis))

    # ---------------------------------------------------------------- round-9 threads
    #
    # Fifteen checks for eight review threads. Read the counter assertion in each one
    # first: every one of these shapes was GREEN before the fix, and six of the eight were
    # green because the parser found NOTHING. A check that only asks "is this key in hits"
    # passes just as well on an empty index, which is the failure mode the whole suite is
    # built around, so each red asserts the slot count it expects to have enumerated.

    # 5l. CROSS-TU RED (thread 1). Two translation units, one name, one registration. The
    #     fixture needs two files because internal linkage cannot be stated in one.
    xtu = _sweep_sources({"a.c": RED_CROSS_TU_STATIC_A, "b.c": RED_CROSS_TU_STATIC_B})
    xhits = {(h[1], h[3]) for h in xtu.hits}
    xdisc = {(d[1], d[3]): d for d in xtu.discharges}
    check(xtu.slots == 2 and xtu.decls == 2 and ("b.c", "cache") in xhits,
          "cross-TU RED: two files each declaring `static VALUE cache` are TWO slots (%d "
          "found from %d declarations), and b.c's unregistered one reports"
          % (xtu.slots, xtu.decls), sorted(xhits))
    check(("a.c", "cache") in xdisc
          and xdisc[("a.c", "cache")][0] == "registered-slot"
          and ("a.c", "cache") not in xhits,
          "...while a.c's IS discharged by its own rb_global_variable -- the registration "
          "has to still work per file, or the split has just deleted the rule",
          sorted(xdisc))

    # 5m. VALUE-REGISTRATION RED (thread 2), and both counter-shapes. rb_gc_register_
    #     mark_object roots the object live at the call, not the slot.
    vrr = _sweep_source(RED_VALUE_REG_REASSIGN, suffix=".c")
    vhits = {h[3]: h for h in vrr.hits}
    check(vrr.slots == 1 and "cache" in vhits
          and "roots the VALUE live at that call" in " ".join(vhits["cache"][5]),
          "registered-value RED: a store after rb_gc_register_mark_object is not covered by "
          "it (%d slot(s), %d hit(s)), and the reason line says which call was rejected"
          % (vrr.slots, len(vrr.hits)),
          [h[3] for h in vrr.hits] + ["discharged:" + d[3] for d in vrr.discharges])
    vrg = {d[3]: d for d in _sweep_source(GREEN_VALUE_REG_ONE_SHOT, suffix=".c").discharges}
    check(vrg.get("localhost", ("",))[0] == "registered-value",
          "registered-value GREEN: assign once, register, never reassign -- kgio's shape "
          "must stay discharged (%s)"
          % (vrg["localhost"][4] if "localhost" in vrg else "NOT DISCHARGED"), sorted(vrg))
    vri = _sweep_source(GREEN_VALUE_REG_IMMEDIATE_RESET, suffix=".c")
    vrid = {d[3]: d for d in vri.discharges}
    check(not vri.hits and vrid.get("cache", ("",))[0] == "registered-value",
          "registered-value GREEN: a later store of an IMMEDIATE cannot leave an unrooted "
          "object, so it does not defeat the registration -- date 3.5.1's thirty "
          "`static VALUE pat = Qnil;` behind a NIL_P guard (%s)"
          % (vrid["cache"][4] if "cache" in vrid else "NOT DISCHARGED"),
          [h[3] for h in vri.hits])

    # 5n. PUBLICATION RED (thread 3). Same mechanism as 5m at a different call; msgpack at
    #     5c is the green that must survive it.
    pbr = _sweep_source(RED_PUBLISHED_REASSIGN, suffix=".c")
    phits = {h[3]: h for h in pbr.hits}
    check(pbr.slots == 1 and "cache" in phits
          and "publishes the VALUE live at that call" in " ".join(phits["cache"][5]),
          "const-published RED: rb_define_const publishes the object present at the call, "
          "so a later store into the same static is rooted by nothing (%d slot(s), %d hit)"
          % (pbr.slots, len(pbr.hits)),
          [h[3] for h in pbr.hits] + ["discharged:" + d[3] for d in pbr.discharges])

    # 5o. WRAPPER-ROOTING RED (thread 4), its green, and its FLOOR.
    wdr = _sweep_source(RED_WRAPPER_DISCARDED, suffix=".c")
    whits = {h[3]: h for h in wdr.hits}
    check(wdr.slots == 1 and wdr.fields == 1 and "state.held" in whits
          and "wrapper NOT rooted" in " ".join(whits["state.held"][5]),
          "wrapper RED: a TypedData wrapper whose result is DISCARDED marks nothing, so the "
          "object's fields may not be handed to sweep_unmarked.py (%d slot(s), %d field(s), "
          "%d hit(s))" % (wdr.slots, wdr.fields, len(wdr.hits)),
          [h[3] for h in wdr.hits] + ["discharged:" + d[3] for d in wdr.discharges])
    wgr = _sweep_source(GREEN_WRAPPER_ROOTED, suffix=".c")
    wgd = {d[3]: d for d in wgr.discharges}
    check(not [h for h in wgr.hits if h[3] == "state.held"]
          and wgd.get("state.held", ("",))[0] == "wrapped",
          "wrapper GREEN: stackprof's shape -- wrapper into a slot rb_global_variable roots "
          "-- stays a hand-off to predicate A (%s)"
          % (wgd["state.held"][4] if "state.held" in wgd else "NOT DISCHARGED"),
          [h[3] for h in wgr.hits] + sorted(wgd))
    wrt = _sweep_source(GREEN_WRAPPER_RETURNED, suffix=".c")
    wrd = {d[3]: d for d in wrt.discharges}
    check(wrt.fields == 1 and wrd.get("state.held", ("",))[0] == "wrapped",
          "wrapper FLOOR: a wrapper RETURNED from an allocator cfunc is not judged and stays "
          "discharged -- the strict reading costs false positives on the commonest correct "
          "shape in every C extension, and that trade is pinned, not remembered (%s)"
          % (wrd["state.held"][4] if "state.held" in wrd else "NOT DISCHARGED"),
          [h[3] for h in wrt.hits] + sorted(wrd))

    # 5p. NAMESPACE / LINKAGE-BLOCK descent (thread 5). Reported as already covered by the
    #     brace-disposition work; verified rather than believed, and pinned so it cannot
    #     regress silently. The third static is a GREEN inside a namespace, so the check
    #     proves the descent READ the body rather than merely not crashing on it.
    nsr = _sweep_source(RED_NAMESPACE_LINKAGE)
    nhits = {h[3] for h in nsr.hits}
    ndisc = {d[3]: d for d in nsr.discharges}
    check(nsr.slots == 3 and nsr.class_bodies == 0 and nhits == {"ns_cache", "deep_cache"},
          "namespace/linkage RED: `namespace X {`, a NESTED namespace and `extern \"C\" {` "
          "are walked THROUGH, not consumed as function bodies (%d slots, hits %s)"
          % (nsr.slots, sorted(nhits) or "none"), sorted(nhits))
    check(ndisc.get("rooted", ("",))[0] == "registered-slot",
          "...and a namespace-scope static that IS registered discharges by name, which is "
          "what says the descent read the body rather than skipped it (%s)"
          % (ndisc["rooted"][4] if "rooted" in ndisc else "NOT DISCHARGED"), sorted(ndisc))

    # 5p2. ROUND-9 REVIEW: THE FUNCTION INDEX IS THE OTHER HALF OF THE SAME DESCENT (:947).
    #      5p above proves file-scope slots survive a namespace. This proves the FUNCTIONS
    #      inside one do -- they did not, and the two halves of one file disagreed about C++
    #      for a round. Unfixed, the wrapped arm measures `slots 0/0, decls 0, HITS 0` on a
    #      file whose unwrapped twin measures 2 slots and 1 hit, so the COUNTERS are the
    #      assertion and the hit count is the corollary: a green-for-the-wrong-reason
    #      regression empties the index and prints the same zero a clean gem prints.
    #
    #      THE WRAPPER IS THE FLAG. The same source with and without `namespace prof {` /
    #      `extern "C" {` must give the same funnel and the same rows, because a namespace
    #      has no storage duration of its own -- comparing the two arms is what tests the
    #      claim rather than restating it, and the flat arm is also the green the reviewer
    #      asked for: an ordinary non-namespaced function must still index.
    nfw = _sweep_source(RED_NAMESPACED_FUNCTION(True))
    nff = _sweep_source(RED_NAMESPACED_FUNCTION(False), suffix=".c")
    nfwd = {d[3]: d for d in nfw.discharges}
    check(nfw.slots == 2 and nfw.decls == 2 and {h[3] for h in nfw.hits} == {"cache"}
          and nfwd.get("keep", ("",))[0] == "registered-slot",
          "namespaced-function RED: a function-local `static VALUE` inside `namespace X {` "
          "is indexed (%d slot(s) from %d declaration(s), hits %s, keep %s) -- the unported "
          "depth count reported slots 0/0 on an EMPTIED INDEX, not a cleared file"
          % (nfw.slots, nfw.decls, sorted(h[3] for h in nfw.hits) or "none",
             nfwd["keep"][0] if "keep" in nfwd else "NOT DISCHARGED"),
          [h[3] for h in nfw.hits] + ["discharged:" + d[3] for d in nfw.discharges])
    check((nfw.slots, nfw.decls, sorted(h[3] for h in nfw.hits),
           sorted((d[0], d[3]) for d in nfw.discharges))
          == (nff.slots, nff.decls, sorted(h[3] for h in nff.hits),
              sorted((d[0], d[3]) for d in nff.discharges)),
          "namespaced-function GREEN: the same two functions unwrapped give the same funnel "
          "and the same rows -- a transparent scope changes nothing, and an ordinary "
          "non-namespaced function still indexes",
          "wrapped %d/%d %s vs flat %d/%d %s"
          % (nfw.slots, nfw.decls, sorted(h[3] for h in nfw.hits),
             nff.slots, nff.decls, sorted(h[3] for h in nff.hits)))

    # 5p3. ROUND-9 REVIEW: THE DECLARATOR DOES NOT END AT THE `)` EITHER (:929).
    #      5p2 above put the FUNCTIONS inside a namespace into the index; this puts the
    #      functions that carry an attribute into it. Fourth appearance of one walker gap --
    #      predicate D fixed it twice in one round, for `__attribute__`/`noexcept` and then
    #      for a C++ trailing return type -- so the walk is tu_scope.skip_post_declarator
    #      and not a third implementation.
    #
    #      THE DECLARATOR IS THE FLAG, and the bare one is the green: six spellings of one
    #      pair of functions must give one funnel and one set of rows. Unfixed, every arm
    #      but the bare one measures `slots 0/0, decls 0, HITS 0` against its 2 slots and
    #      one hit -- so the counters are asserted first and the hit set second.
    heads = (("VALUE", ""), ("VALUE", " __attribute__((noinline))"), ("VALUE", " noexcept"),
             ("VALUE", " __attribute__((noinline)) noexcept"), ("auto", " -> VALUE"),
             ("auto", " __attribute__((noinline)) -> VALUE"))
    at = {h: _sweep_source(RED_ATTRIBUTED_FUNCTION(*h)) for h in heads}
    bare = at[heads[0]]
    bared = {d[3]: d[0] for d in bare.discharges}
    check(bare.slots == 2 and bare.decls == 2 and {h[3] for h in bare.hits} == {"cache"}
          and bared.get("keep") == "registered-slot",
          "attributed-function GREEN (the flag off): an ordinary unattributed definition "
          "indexes both its function-local statics (%d slot(s) from %d declaration(s), "
          "hits %s, keep %s)"
          % (bare.slots, bare.decls, sorted(h[3] for h in bare.hits) or "none",
             bared.get("keep", "NOT DISCHARGED")),
          [h[3] for h in bare.hits] + ["discharged:" + d[3] for d in bare.discharges])
    shape = {h: (r.slots, r.decls, sorted(x[3] for x in r.hits),
                 sorted((d[0], d[3]) for d in r.discharges)) for h, r in at.items()}
    check(all(shape[h] == shape[heads[0]] for h in heads),
          "attributed-function RED: `__attribute__((noinline))`, `noexcept`, both together "
          "and a C++ trailing return type between the `)` and the `{` each give the same "
          "funnel and the same rows as the bare definition -- the whitespace-only walk never "
          "reached the brace, indexed no function, and reported slots 0/0 on an EMPTIED INDEX",
          [((l + t) or "<bare>", shape[(l, t)][0], shape[(l, t)][1], shape[(l, t)][2])
           for l, t in heads])

    # 5p4. THE REJECTION TABLE, ASSERTED IN THIS PREDICATE'S OWN NEIGHBOURHOOD.
    #      Opening the crossing up is what let predicate D invent four function bodies out
    #      of X-macro lists and `__declspec(...)` before a `typedef enum {`, so the table
    #      travels with the walk -- but the shapes that sit next to a rejection boundary
    #      differ per caller, and THIS predicate is looking for `static VALUE`
    #      declarations. A span wrongly opened over `typedef struct { ... } static_thing;`
    #      is scanned for them, which is why the aggregate case is here and not only in D.
    #
    #      Each fixture must index exactly the one real definition and still find its
    #      `cache`. K&R indexes none: a stated recall limit, unchanged by opening the words.
    rejects = {
        # a macro invocation, then a real definition: the `(` of `probe(` stops the walk
        "macro": "MY_EXPORT(sym)\n",
        # K&R parameter declarations: the `;`
        "knr": None,
        # a prototype, then a definition: the `;`
        "proto": "static VALUE helper(VALUE);\n",
        # a declarator list with a braced initialiser: the `=`
        "init": "struct S s = mk(1), t = {2};\n",
        # an attribute, then a typedef'd aggregate: `typedef` stops it. A span opened over
        # the aggregate body would be handed to the function-local `static VALUE` scan.
        "typedef-aggregate": "__declspec(align(8)) typedef struct { int x; } static_thing;\n",
        # an X-macro list, then a type: trilogy's shape, the one D actually invented from
        "x-macro": "XX(A, 1)\ntypedef enum { E_A } phase_t;\n",
    }
    body = ("{\n    static VALUE cache;\n    if (!cache) {\n"
            "        cache = rb_str_new_cstr(\"lost\");\n    }\n    return cache;\n}\n")
    rj, rjh = {}, {}
    for tag, lead in rejects.items():
        head = ("static VALUE probe(v) VALUE v;\n" if lead is None
                else lead + "static VALUE probe(void)\n")
        files = {"probe.c": "#include <ruby.h>\n\n" + head + body}
        rj[tag] = sorted(Tree(_write_sources(files)).funcs)
        rjh[tag] = sorted(h[3] for h in _sweep_sources(files).hits)
    check(rj == {"macro": ["probe"], "knr": [], "proto": ["probe"], "init": ["probe"],
                 "typedef-aggregate": ["probe"], "x-macro": ["probe"]},
          "post-declarator rejection table: a macro call, a prototype, an initialiser list, "
          "`__declspec(...) typedef struct` and an X-macro list each index the ONE real "
          "definition and invent nothing -- the open walk's failure mode is a body attributed "
          "to the wrong name, and that body is then scanned for `static VALUE`", rj)
    check(all(rjh[t] == (["cache"] if t != "knr" else []) for t in rejects),
          "...and the real definition's own function-local static is still found in every "
          "one of them (K&R indexes no function at all: a stated recall limit, unchanged)",
          rjh)

    # 5q. THREAD-LOCAL (thread 6).
    tlr = _sweep_source(RED_THREAD_LOCAL)
    tlh = {h[3] for h in tlr.hits}
    check(tlr.slots == 2 and tlh == {"tl_cache", "tl_static_cache"},
          "thread_local RED: both spellings are static-duration slots outside every root "
          "set Ruby scans (%d slots, hits %s) -- pre-fix the leading-token gate dropped the "
          "declaration and the file measured 0 slots"
          % (tlr.slots, sorted(tlh) or "none"), sorted(tlh))

    # 5r. UNINDENTED function-local static (thread 7).
    ulr = _sweep_source(RED_UNINDENTED_LOCAL_STATIC, suffix=".c")
    check(ulr.slots == 1 and {h[3] for h in ulr.hits} == {"cache"},
          "unindented function-local RED: storage duration comes from the FUNCTION SPAN, "
          "not from leading whitespace (%d slot(s), hits %s)"
          % (ulr.slots, sorted(h[3] for h in ulr.hits) or "none"),
          [h[3] for h in ulr.hits])

    # 5s. QUALIFIED function-local static (thread 8).
    qlr = _sweep_source(RED_QUALIFIED_LOCAL_STATIC, suffix=".c")
    check(qlr.slots == 1 and {h[3] for h in qlr.hits} == {"cache"},
          "qualified function-local RED: `static volatile VALUE` is one slot, and the "
          "`static VALUE const *ptr` beside it is still correctly rejected as a pointer "
          "(%d slot(s), hits %s)"
          % (qlr.slots, sorted(h[3] for h in qlr.hits) or "none"),
          [h[3] for h in qlr.hits])

    # 5u. ROUND-9 THREAD: TWO FUNCTION-LOCAL STATICS OF ONE NAME IN ONE FILE (:1011).
    #     The round-8 split scoped an internal-linkage slot to its FILE, which is one level
    #     too coarse for a function-local static: the file is not the innermost scope there
    #     is. Measured on the fixture below, unfixed: `slots 1/2`, one `registered-slot`
    #     discharge, HITS 0 -- a live over-clear, one function's rb_global_variable
    #     answering for another function's object.
    #
    #     The DECLARATION count is asserted beside the slot count, because the two failure
    #     modes print the same HITS: a parser that stops seeing function-local statics
    #     reports `slots 0/0` and reads exactly as clean as the merge did.
    tls = _sweep_source(RED_TWO_LOCAL_STATICS, suffix=".c")
    tld = {d[3]: d for d in tls.discharges}
    check(tls.slots == 2 and tls.decls == 2 and {h[3] for h in tls.hits} == {"cache"}
          and len(tls.hits) == 1,
          "two-local-statics RED: one name in two function bodies is TWO slots (%d found "
          "from %d declarations) and the unregistered one reports (%d hit(s))"
          % (tls.slots, tls.decls, len(tls.hits)),
          [h[3] for h in tls.hits] + ["discharged:" + d[3] for d in tls.discharges])
    check(len(tls.discharges) == 1 and tld.get("cache", ("",))[0] == "registered-slot"
          and tls.hits and tls.hits[0][2] > tls.discharges[0][2],
          "...and GREEN in the same file: `rooted`'s own rb_global_variable still "
          "discharges ITS slot, and the row that stands is the LATER declaration -- a "
          "block-scoping fix that clears nothing is the registration rule turned off",
          [(d[0], d[2]) for d in tls.discharges] + [(h[0], h[2]) for h in tls.hits])

    # 5v. ROUND-9 REVIEW: DECLARATORS AFTER AN INITIALISER (:998).
    #     `static VALUE rooted = Qnil, bad = Qnil;` ended the match at the first `=`, so the
    #     statement declared one slot instead of two. Measured on the fixture unfixed:
    #     `slots 1/1`, one `registered-slot` discharge, HITS 0 -- a live over-clear in which
    #     the registration of the FIRST declarator is the whole reason the second's
    #     rb_str_new_cstr never appears.
    #
    #     The SLOT and DECLARATION counts are asserted beside the hit set, because the two
    #     failure modes print the same HITS: a fix that stopped seeing the declaration
    #     altogether reports `slots 0/0` and reads exactly as clean as reading half of it did.
    idl = _sweep_source(RED_INITIALISED_DECLARATOR_LIST, suffix=".c")
    idd = {d[3]: d[0] for d in idl.discharges}
    check(idl.slots == 2 and idl.decls == 2 and {h[3] for h in idl.hits} == {"bad"}
          and idd.get("rooted") == "registered-slot" and len(idl.discharges) == 1,
          "initialised-declarator-list RED: one statement declares TWO slots (%d found from "
          "%d declaration(s)), the unregistered one reports (hits %s), and GREEN in the same "
          "declaration -- `rooted`'s rb_global_variable still discharges ITS slot (%s)"
          % (idl.slots, idl.decls, sorted(h[3] for h in idl.hits) or "none",
             idd.get("rooted", "NOT DISCHARGED")),
          [h[3] for h in idl.hits] + ["discharged:" + d[3] for d in idl.discharges])

    # 5w. GREEN: the single-declarator case the fix must not disturb, registration as the
    #     flag. Reading the whole statement is only correct if it changes nothing here.
    sir = _sweep_source(GREEN_SINGLE_INITIALISED(True), suffix=".c")
    siu = _sweep_source(GREEN_SINGLE_INITIALISED(False), suffix=".c")
    check(sir.slots == 1 and not sir.hits
          and {d[3]: d[0] for d in sir.discharges}.get("only") == "registered-slot"
          and siu.slots == 1 and {h[3] for h in siu.hits} == {"only"},
          "single-initialised-declarator GREEN: `static VALUE only = Qnil;` is still exactly "
          "one slot, and still discharges when and only when it is registered "
          "(registered %d slot/%d hit, unregistered %d slot/%d hit)"
          % (sir.slots, len(sir.hits), siu.slots, len(siu.hits)),
          [h[3] for h in sir.hits] + ["unreg:" + h[3] for h in siu.hits])

    # 5x. ...and the commas that separate declarators are the TOP-LEVEL ones. A splitter that
    #     is not would turn `a = f(x, y), b` into four fragments, two of them call arguments.
    #     Both declarators are unregistered statics taking an allocating source -- `a` from
    #     rb_funcall in its own initialiser, `b` from rb_str_new_cstr -- so the shape of the
    #     fixture says two slots and two rows, named `a` and `b`. A splitter that is not
    #     top-level reports four slots under names like `y)`; one that still stops at the
    #     first `=` reports one.
    tlc = _sweep_source(GREEN_TOP_LEVEL_COMMAS, suffix=".c")
    check(tlc.slots == 2 and {h[3] for h in tlc.hits} == {"a", "b"},
          "top-level-comma GREEN: `static VALUE a = f(x, y), b;` is exactly `a` and `b` "
          "(%d slot(s), hits %s), and the `static VALUE (*fp)(void);` beside it is still "
          "rejected as a function pointer -- reading the whole statement crosses a `(` the "
          "old character class refused outright"
          % (tlc.slots, sorted(h[3] for h in tlc.hits) or "none"),
          sorted(h[3] for h in tlc.hits))

    # 5t. HEADER carve-out. The scope split applies to translation units; a `static VALUE`
    #     in a HEADER keeps its tree-wide key, or its stores -- which live in the .c that
    #     includes it -- become invisible and the row reports UNSOURCED.
    hdr = _sweep_sources({"probe.h": GREEN_HEADER_STATIC_H,
                          "probe.c": GREEN_HEADER_STATIC_C})
    hdd = {d[3]: d for d in hdr.discharges}
    check(hdr.slots == 1 and not hdr.hits
          and hdd.get("g_shared", ("",))[0] == "registered-slot",
          "header carve-out GREEN: a header-declared static resolves its stores in the "
          "including .c and discharges there (%d slot(s), %d hit(s)) -- scoping it to the "
          "header raised 10 UNSOURCED rows across unicorn x2 and yajl-ruby, all noise"
          % (hdr.slots, len(hdr.hits)), [h[3] for h in hdr.hits] + sorted(hdd))

    # 6. MUTATION TABLE. Disable each discharge rule in turn; a rule that can be removed
    #    without breaking a control is decorative and should be deleted. msgpack is in the
    #    control set because `const-published` fires nowhere else in the corpus -- with the
    #    four obvious controls only, the table called a load-bearing rule decorative.
    controls = {"stackprof": sp, "kgio": kgio, "rbtrace": rbt, "vernier": vern,
                "msgpack": msg, "ed25519": ed, "mysql2": my}
    base = {n: {h[3] for h in _sweep(p).hits} for n, p in controls.items()}
    table = []
    for rule in ALL_RULES:
        reduced = tuple(x for x in ALL_RULES if x != rule)
        broken = []
        for n, p in controls.items():
            extra = {h[3] for h in _sweep(p, reduced).hits} - base[n]
            if extra:
                broken.append("%s +%d (%s)" % (n, len(extra), sorted(extra)[0]))
        table.append((rule, broken))
        check(bool(broken), "mutation: disabling `%s` breaks a control -- %s"
              % (rule, "; ".join(broken) or "NOTHING; the rule is decorative, delete it"))

    # 7. A zero must be readable. vernier is the natural green: every static is either an
    #    ID2SYM(rb_intern_const) or an rb_define_class_under, and the counters have to show
    #    that the query resolved something before its zero means anything.
    vr = _sweep(vern)
    check(not vr.hits and vr.files > 0 and vr.slots > 0 and len(vr.discharges) > 0,
          "vernier 1.10.1 GREEN and readable (%d files, %d slots, %d discharged)"
          % (vr.files, vr.slots, len(vr.discharges)),
          [h[3] for h in vr.hits])

    # 8. Recall audit. --no-discharge is what the rules suppress; it must stay small enough
    #    to read by hand, because all five rules are path-INSENSITIVE.
    sup = 0
    for p in controls.values():
        sup += len({h[3] for h in _sweep(p, ()).hits} - {h[3] for h in _sweep(p).hits})
    check(sup > 0, "--no-discharge exposes %d suppressed slot(s) across the %d controls; "
                   "each is named by rule in -v output" % (sup, len(controls)))

    # ------------------------------------ #29 items 3, 4 and 5: three storage-scope holes
    #
    # All three are OVER-CLEARS, all three read as a clean sheet, and each ships its green
    # as well as its red -- a rule that stops clearing needs a fixture proving it still
    # clears what it should, or the fix is a deletion.

    # 3. LOCAL STATICS ARE SCOPED TO THE BLOCK, NOT THE FUNCTION. Two DISJOINT nested
    #    blocks in one function may each declare `static VALUE cache`; keyed by the
    #    enclosing function's span they dedupe to one slot, and the first block's
    #    `rb_global_variable(&cache)` then discharges the second block's unrooted
    #    allocation. Measured unfixed: `slots 1/2`, one registered-slot discharge, HITS 0.
    #    THE COUNTER IS THE FLAG: the fix is visible as 2 decls surviving the dedupe, which
    #    is not something the hit list alone can show.
    two_block = """#include <ruby.h>

static VALUE
entry(VALUE self, VALUE arg)
{
    if (RTEST(arg)) {
        static VALUE cache;
        if (!cache) {
            cache = rb_str_new_cstr("a");
            rb_global_variable(&cache);
        }
        return cache;
    } else {
        static VALUE cache;
        if (!cache) {
            cache = rb_str_new_cstr("b");
        }
        return cache;
    }
}

void Init_probe(void) { rb_define_method(rb_cObject, "e", entry, 1); }
"""
    tb = _sweep_sources({"probe.c": two_block})
    check((tb.slots, tb.decls, sorted(d[0] for d in tb.discharges),
           sorted(h[0] for h in tb.hits)) == (2, 2, ["registered-slot"], ["ALLOCATES"]),
          "#29 item 3 RED: two disjoint blocks in one function are two slots -- one "
          "registered, one allocating and unrooted. Unfixed they merged to `slots 1/2` "
          "and the registration discharged both",
          "slots %d/%d disch %s hits %s" % (tb.slots, tb.decls,
                                            [(d[0], d[3]) for d in tb.discharges],
                                            [(h[0], h[3]) for h in tb.hits]))
    ob = _sweep_sources({"probe.c": two_block[:two_block.index("    } else {")]
                         + "    }\n    return Qnil;\n}\n\n"
                         + "void Init_probe(void) { rb_define_method(rb_cObject, \"e\", "
                           "entry, 1); }\n"})
    check((ob.slots, ob.decls, sorted(d[0] for d in ob.discharges), ob.hits)
          == (1, 1, ["registered-slot"], []),
          "#29 item 3 GREEN: a genuine SINGLE-block static registered in its own block "
          "still discharges -- the scope was narrowed, not broken",
          "slots %d/%d disch %s hits %s" % (ob.slots, ob.decls,
                                            [(d[0], d[3]) for d in ob.discharges],
                                            [(h[0], h[3]) for h in ob.hits]))

    # 4. AN ANONYMOUS NAMESPACE IS INTERNAL LINKAGE WITH NO `static` ON THE DECLARATION.
    #    Two TUs each spelling `namespace { VALUE cache; }` are two objects. Scoped on the
    #    declaration text alone they merged into one tree-wide slot and a.cc's registration
    #    discharged b.cc's allocating one: `slots 1/2`, HITS 0.
    anon_head = "#include <ruby.h>\n\nnamespace {\n    VALUE cache;\n}\n"
    named_head = "#include <ruby.h>\n\nnamespace prof {\n    VALUE cache;\n}\n"
    reg_tail = "\nvoid reg_a(void) { rb_global_variable(&%scache); }\n"
    use_tail = ("\nextern \"C\" VALUE mk_b(VALUE self) { %(q)scache = "
                "rb_str_new_cstr(\"b\"); return %(q)scache; }\n"
                "void Init_probe(void) { rb_define_method(rb_cObject, \"b\", mk_b, 0); }\n")
    an = _sweep_sources({"a.cc": anon_head + reg_tail % "",
                         "b.cc": anon_head + use_tail % {"q": ""}})
    check((an.slots, an.decls, sorted(d[0] for d in an.discharges),
           sorted(h[0] for h in an.hits)) == (2, 2, ["registered-slot"], ["ALLOCATES"]),
          "#29 item 4 RED: `namespace { VALUE cache; }` in two translation units is two "
          "slots -- one registered, one not. Unfixed the scope decision read only the "
          "declaration text, merged them and discharged the unregistered one",
          "slots %d/%d disch %s hits %s" % (an.slots, an.decls,
                                            [(d[0], d[3]) for d in an.discharges],
                                            [(h[0], h[3]) for h in an.hits]))
    nm = _sweep_sources({"a.cc": named_head + reg_tail % "prof::",
                         "b.cc": named_head + use_tail % {"q": "prof::"}})
    st = _sweep_sources(
        {"a.cc": "#include <ruby.h>\n\nstatic VALUE cache;\n" + reg_tail % "",
         "b.cc": "#include <ruby.h>\n\nstatic VALUE cache;\n" + use_tail % {"q": ""}})
    check((nm.slots, nm.decls) == (1, 2)
          and (st.slots, st.decls, sorted(d[0] for d in st.discharges),
               sorted(h[0] for h in st.hits)) == (2, 2, ["registered-slot"],
                                                  ["ALLOCATES"]),
          "#29 item 4 GREEN, both directions: a NAMED namespace is external linkage and "
          "still merges tree-wide (slots 1/2), while a plain `static` in one TU still "
          "does not reach another (slots 2/2, one discharged one hit)",
          "named %d/%d, static %d/%d %s" % (nm.slots, nm.decls, st.slots, st.decls,
                                            [(h[0], h[3]) for h in st.hits]))

    def _index_names(src):
        return set(Tree(_write_sources({"probe.cpp": src})).funcs)


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
          "#29 item 2: predicate C's function index conforms to tu_scope's declarator table "
          "-- every accepted spelling indexed, every rejected one refused, K&R indexing "
          "nothing (the stated recall limit shared by all four predicates)",
          tu_scope.declarator_conformance(_index_names))
    check(tu_scope.unshared_declarator_crossings(
              pathlib.Path(__file__).read_text()) == [],
          "#29 item 2: no hand-rolled `)`-to-`{` crossing left in this file -- the walk is "
          "tu_scope.skip_post_declarator at every site that crosses one",
          tu_scope.unshared_declarator_crossings(pathlib.Path(__file__).read_text()))

    print("\n".join(log))
    print("\nmutation table (rule -> controls that break when it is disabled):")
    for rule, broken in table:
        print("  %-17s %s" % (rule, "; ".join(broken) or "NONE -- decorative"))
    print("\nself-test: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every discharged slot and which rule cleared it")
    ap.add_argument("--disable-rule", action="append", default=[], choices=ALL_RULES,
                    help="turn one discharge rule off (mutation control)")
    ap.add_argument("--no-discharge", action="store_true",
                    help="turn every discharge rule off. Recall audit: whatever appears "
                         "here and not in a normal run is exactly what the rules suppress, "
                         "and each one has to be justified by name.")
    ap.add_argument("--self-test", action="store_true",
                    help="run acceptance against the gem trees named in dirs, and exit")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(self_test(a.dirs))
    rules = () if a.no_discharge else tuple(x for x in ALL_RULES
                                            if x not in a.disable_rule)
    f, grades = [0] * 9, {}
    for d in a.dirs:
        root = pathlib.Path(d)
        r = sweep(Tree(root), root.name, rules)
        report(r, verbose=a.verbose)
        for i, v in enumerate((r.files, r.slots, r.scalars, r.fields, r.allocating,
                               len(r.discharges), len(r.hits), r.class_bodies,
                               r.class_members)):
            f[i] += v
        for h in r.hits:
            grades.setdefault(h[0], []).append("%s %s:%d  %s" % (r.name, h[1], h[2], h[3]))
    print("\nFUNNEL over %d tree(s), %d C file(s):\n"
          "  file-scope VALUE slots ........................ %4d  (scalar %d, field %d)\n"
          "  C++ class bodies descended into ............... %4d\n"
          "  ...`Class::member` static declarations seen ... %4d  (in-class and "
          "out-of-line, before the by-key dedupe)\n"
          "  ...with at least one allocating source ........ %4d\n"
          "  discharged by a named rule .................... %4d\n"
          "  REMAINING (hits) .............................. %4d"
          % (len(a.dirs), f[0], f[1], f[2], f[3], f[7], f[8], f[4], f[5], f[6]))
    for g in ("ALLOCATES", "FOREIGN", "CALL", "OPAQUE", "CONST-LOOKUP", "UNSOURCED"):
        for where in grades.get(g, []):
            print("      %-10s %s" % (g, where))
    if a.no_discharge or a.disable_rule:
        print("  NOTE: discharge rules are off (%s). This is the recall audit, not a "
              "verdict." % ("all" if a.no_discharge else ",".join(a.disable_rule)))


if __name__ == "__main__":
    main()

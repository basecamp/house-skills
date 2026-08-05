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
                    at that moment.  kgio accept.c:501 on `localhost`
  immediate         every source is Qnil/Qtrue/INT2FIX/ID2SYM(static intern)/another
                    immediate-only static.  stackprof's 28 `S(name)` symbols
  const-table       every source comes from rb_define_class/rb_define_module or is a core
                    `rb_cFoo`/`rb_mFoo`/`rb_eFoo`. The DEFINING call installs the object
                    under a permanent constant.  vernier vernier.cc:1246
  const-published   the slot is the VALUE argument of rb_define_const / rb_const_set. The
                    rooting is at a USE site, so no source rule can see it.
                    msgpack extension_value_class.c:33
  wrapped           the file-static OBJECT is itself handed to TypedData_Wrap_Struct with a
                    dtype whose dmark is non-NULL. Not a clear -- a HAND-OFF: those fields
                    are exactly what sweep_unmarked.py's walk covers, and reporting them
                    here would double-count predicate A's rows.
                    stackprof.c:995 `TypedData_Wrap_Struct(..., &stackprof_type, &_stackprof)`

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

LEXING
------
Copied from sweep_unmarked.py. strip_noise blanks comments and string bodies but keeps
newlines; strip_directives keeps line count AND byte length. So byte offsets and line
numbers into the stripped text both match the original file, which every hit here depends
on. --self-test asserts that round-trip on a real corpus file rather than taking it on
trust.

ACCEPTANCE (--self-test): see self_test(). Run it before trusting any result -- silence is
a property of the query until the counters say otherwise.
"""
import argparse
import pathlib
import re
import shutil
import sys
import tempfile

C_EXT = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp")

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


def top_level_units(src):
    """[(offset, text)] -- one entry per depth-0 declaration or definition.

    The `{` disposition is the whole trick. A depth-0 brace is part of the CURRENT
    statement when it opens an aggregate (`static struct {`) or an initialiser (`... = {`),
    and ends it when it opens a function body. Getting that backwards merges
    `Init_stackprof`'s body into the next declaration and the `static VALUE sym_object,
    ...;` line after it becomes a declarator list of the function -- silently dropping 28
    slots, which is a recall hole that reports as a clean sheet.
    """
    n, i, start = len(src), 0, 0
    while i < n:
        c = src[i]
        if c == "{":
            close = match_brace(src, i)
            if close < 0:
                return
            pre = src[start:i].rstrip()
            if re.search(r"\b(struct|union|enum)\b\s*\w*$", pre) or pre.endswith("="):
                i = close + 1
                continue
            yield start, src[start:close + 1]
            i = start = close + 1
            continue
        if c == ";":
            yield start, src[start:i + 1]
            i = start = i + 1
            continue
        i += 1


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
    r"|rb_define_error|rb_struct_define_under)$")
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
           "static", "extern", "inline", "typedef", "_Atomic", "restrict"}


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
    __slots__ = ("path", "off", "key", "root", "kind", "decl", "opath", "ooff")

    def __init__(self, path, off, key, root, kind, decl, opath=None, ooff=0):
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

    @property
    def field(self):
        """Last path component, subscript stripped -- the token a store names."""
        return self.key.split(".")[-1].replace("[]", "")


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
        for path, src in self.files.items():
            self._index_structs(path, src)
            self._index_aliases(src)
            self._index_dtypes(src)
            self._index_funcs(src)
        self.unresolved_members = 0   # struct members whose type did not resolve
        self.slots = []
        self.objects = {}        # file-scope object name -> (path, offset)
        for path, src in self.files.items():
            self._index_slots(path, src)
        # Dedupe by KEY, not by (file, key). kgio declares `static VALUE sym_wait_writable`
        # in three separate files -- three genuinely distinct slots that a name-keyed source
        # scan cannot tell apart anyway. Merging them makes the all-sources discharge rules
        # strictly HARDER to earn, which is the safe direction; `decls` keeps the pre-dedupe
        # count so the collapse is visible rather than silent.
        self.decls = len(self.slots)
        seen, uniq = set(), []
        for s in self.slots:
            if s.key not in seen:
                seen.add(s.key)
                uniq.append(s)
        self.slots = uniq
        self.scalars = {s.key for s in self.slots if s.kind == "scalar"}
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

    def _index_funcs(self, src):
        """name -> body, top-level definitions only, for one-level return resolution."""
        depth, cursor = 0, 0
        for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?=\()", src):
            depth += src.count("{", cursor, m.start()) - src.count("}", cursor, m.start())
            cursor = m.start()
            if depth != 0 or m.group(1) in NOT_CALLS:
                continue
            args, past = call_args(src, m.end())
            if args is None:
                continue
            k = past
            while k < len(src) and src[k] in " \t\r\n":
                k += 1
            if k >= len(src) or src[k] != "{":
                continue
            close = match_brace(src, k)
            if close > 0:
                self.funcs.setdefault(m.group(1), src[k + 1:close])

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
        # Function-LOCAL statics have static storage duration and exactly the same problem;
        # top_level_units deliberately skips function bodies, so they are picked up here.
        # vernier's commented-out `static VALUE gc_hook = Data_Wrap_Struct(...)` inside
        # Init_vernier is what this branch exists for.
        for m in re.finditer(r"^[ \t]+static\s+VALUE\s+([^;=(){}]+)[;=]", src, re.M):
            for decl in split_args(m.group(1)):
                nm, arr, ptr = declarator(decl)
                if nm and not ptr:
                    self.slots.append(Slot(path, m.start(), nm + arr, nm, "scalar",
                                           "static VALUE " + decl.strip()))

    def _unit_slots(self, path, off, unit):
        head_off = off + len(unit) - len(unit.lstrip())
        head = unit.strip()
        if not head or head.startswith("typedef") or re.match(r"^\s*extern\b", head):
            return
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
                        self.objects[nm] = (path, head_off)
                        self._struct_slots(path, head_off, nm + arr, nm,
                                           unit[body_open + 1:close], path,
                                           off + body_open + 1, 0)
            return
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
                    self.objects[nm] = (path, head_off)
                    self._struct_slots(path, head_off, nm + arr, nm, *sub, 0)
            return
        # (3) a bare `static VALUE x, y[N];` / `VALUE rb_mVernier;` / `EXTERN VALUE x;`.
        #
        # The ALL-CAPS leading token is rmagick, and it is a 100-slot recall hole. rmagick
        # declares every one of its file-scope VALUEs as `EXTERN VALUE Module_Magick;`
        # (rmagick.h:333), where EXTERN is `#define EXTERN` in the defining TU and `extern`
        # everywhere else. Requiring a storage keyword found ONE slot in the whole gem --
        # a function-local static -- and the coverage line said `slots 1/1`, which reads
        # like a small gem rather than a parser that dropped the entire header.
        v = re.match(r"^\s*((?:[A-Za-z_]\w*\s+)*?)VALUE\s+([^;]+);$", unit)
        if v and all(t in TYPE_KW or t.isupper() for t in v.group(1).split()) \
                and "(" not in v.group(2):
            v = re.match(r"^.*?VALUE\s+([^;]+);$", unit, re.S)
            for decl in split_args(v.group(1)):
                nm, arr, ptr = declarator(decl)
                if nm:
                    self.slots.append(Slot(path, head_off, nm + arr, nm,
                                           "ptr" if ptr else "scalar", unit.strip()))

    def _struct_slots(self, opath, ooff, prefix, root, body, bpath, boff, depth):
        """Enumerate VALUE members of a file-scope object, recursively.

        rbtrace needs two levels -- `rbtracer` -> `rbtracer_t list[MAX_TRACERS]` ->
        `VALUE self` -- and stops being visible at one. Arrays collapse to `[]` because a
        registration loop indexes with a variable and the declaration with a macro.

        bpath/boff track the STRUCT BODY's own position so a member reports its own
        file:line. rbtracer_t is declared 13 lines above the object that contains it.
        """
        if depth > 4:
            return
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
                                           moff + inner_open + 1, depth + 1)
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
                                 "ptr" if ptr else "field", s.strip(), opath, ooff))
                continue
            sub = self.struct_body(tname)
            if sub is not None:
                for decl in split_args(rest):
                    nm, arr, ptr = declarator(decl)
                    if nm and not ptr:
                        self._struct_slots(opath, ooff, "%s.%s%s" % (prefix, nm, arr),
                                           root, *sub, depth + 1)
            elif tname not in TYPE_KW and not re.match(
                    r"^(u?int\w*|size_t|ssize_t|time_t|pid_t|key_t|bool|ID|st_table"
                    r"|pthread\w*|FILE|off_t|socklen_t|uid_t|gid_t|mode_t|dev_t)$", tname):
                # Counted, not silent: an unresolved member type is exactly where a nested
                # VALUE hides, and "0 slots" has to be distinguishable from "0 resolved".
                self.unresolved_members += 1

    # -- stage 2: sources ----------------------------------------------------

    def sources(self, slot):
        """[(rhs, path, offset, owner)] -- every assignment reaching this slot."""
        if slot.key in self._src_memo:
            return self._src_memo[slot.key]
        out = []
        if slot.kind == "scalar" or "." not in slot.key:
            pat = re.compile(r"(?<![\w.>])%s\s*(?:\[[^\[\]]*\])?\s*=(?!=)"
                             % re.escape(slot.field))
            for path, src in list(self.files.items()) + [(None, self.pasted)]:
                for m in pat.finditer(src):
                    out.append((rhs_after(src, m.end() - 1), path, m.start(), None))
        else:
            # Deliberately not scoped to the owning object: the owner token is all a text
            # scan has, and rbtrace stores through `tracer->self` where `tracer` is a
            # pointer into `rbtracer.list`. Requiring owner == `rbtracer` finds zero stores
            # and the red reports UNSOURCED instead of naming the parameter it swallows.
            pat = re.compile(r"([A-Za-z_]\w*)\s*(?:\.|->)\s*%s\b\s*(?:\[[^\[\]]*\])?\s*=(?!=)"
                             % re.escape(slot.field))
            for path, src in list(self.files.items()) + [(None, self.pasted)]:
                for m in pat.finditer(src):
                    out.append((rhs_after(src, m.end() - 1), path, m.start(), m.group(1)))
        self._src_memo[slot.key] = out
        return out

    # -- stage 3: registrations and wraps ------------------------------------

    def _index_registrations(self):
        """[(kind, normalised target, path, line)] for every GC registration in the tree."""
        out = []
        for path, src in self.files.items():
            for name, args, s, _e in find_calls(src):
                if not args:
                    continue
                if REGISTER_SLOT.match(name) and args[0].strip().startswith("&"):
                    out.append(("registered-slot", norm(args[0]), path, line_at(src, s)))
                elif REGISTER_VALUE.match(name):
                    out.append(("registered-value", norm(args[0]), path, line_at(src, s)))
        return out

    def _index_published(self):
        """{slot key -> (call, path, line)} for slots handed to rb_define_const/rb_const_set.

        The VALUE argument is the last one in both signatures, and it has to be the slot
        itself: `rb_define_const(mod, "X", INT2NUM(BUF_SIZE))` publishes a temporary and
        roots nothing of ours.
        """
        out = {}
        for path, src in self.files.items():
            for name, args, s, _e in find_calls(src):
                if name not in ("rb_define_const", "rb_const_set", "rb_define_global_const"):
                    continue
                if args:
                    out.setdefault(norm(args[-1]), (name, path, line_at(src, s)))
        return out

    def _index_wraps(self):
        """{object name -> (dtype, dmark, path, line)} for wrapped file-scope objects.

        rbtrace's `TypedData_Wrap_Struct(rb_cObject, &rbtrace_type, NULL)` is the control on
        the other side: it names a dtype with a real dmark but hands it NULL, so `rbtracer`
        never appears here and its fields stay in scope.
        """
        out = {}
        for path, src in self.files.items():
            for name, args, s, _e in find_calls(src):
                if name not in ("TypedData_Wrap_Struct", "Data_Wrap_Struct",
                                "rb_data_typed_object_wrap", "rb_data_object_wrap"):
                    continue
                dtype = None
                for a in args:
                    t = a.strip()
                    if t.startswith("&") and norm(t) in self.dtypes:
                        dtype = norm(t)
                for a in args:
                    t = norm(a.strip())
                    if a.strip().startswith("&") and t in self.objects:
                        out[t] = (dtype, self.dmark_of(dtype) if dtype else None,
                                  path, line_at(src, s))
        return out


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
    ids = [i for i in re.findall(r"[A-Za-z_]\w*", d) if i not in TYPE_KW]
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


def returns_all(tree, pred, fn, rules, depth, seen):
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
        if pred(tree, e, rules, depth - 1, set(seen)):
            continue
        if e.isidentifier():
            locals_ = [rhs_after(body, mm.end() - 1) for mm in
                       re.finditer(r"(?<![\w.>])%s\s*=(?!=)" % re.escape(e), body)]
            if locals_ and all(pred(tree, l, rules, depth - 1, set(seen))
                               for l in locals_):
                continue
        return False
    return True


def is_immediate(tree, expr, rules, depth=3, seen=None):
    """Provably an immediate VALUE -- one GC never collects. Unknown means False."""
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
        if depth <= 0 or e in seen or e not in tree.scalars:
            return False
        seen.add(e)
        srcs = [r for r, _p, _o, _w in tree.sources(_slot_named(tree, e))]
        return bool(srcs) and all(is_immediate(tree, s, rules, depth - 1, seen)
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
            return is_immediate(tree, exp, rules, depth - 1, seen)
    return False


def is_const_table(tree, expr, rules, depth=3, seen=None):
    """Provably reachable from the constant table (or a core object the VM roots)."""
    seen = seen if seen is not None else set()
    e = unwrap(expr)
    if not e:
        return False
    if e.isidentifier():
        if CORE_OBJ.match(e):
            return True
        if depth <= 0 or e in seen or e not in tree.scalars:
            return False
        seen.add(e)
        srcs = [r for r, _p, _o, _w in tree.sources(_slot_named(tree, e))]
        return bool(srcs) and all(is_const_table(tree, s, rules, depth - 1, seen)
                                  for s in srcs)
    fn, args = split_call(e)
    if fn is None:
        return False
    if CONST_CALL.match(fn):
        return True
    if depth > 0:
        exp = expand_macro(tree, fn, args or [])
        if exp is not None and exp != e:
            return is_const_table(tree, exp, rules, depth - 1, seen)
    return returns_all(tree, is_const_table, fn, rules, depth, seen)


def is_const_lookup(tree, expr):
    """Read out of the constant table. A GRADE, never a discharge -- see CONST_LOOKUP."""
    fn, _args = split_call(unwrap(expr))
    return bool(fn and CONST_LOOKUP.match(fn))


def _slot_named(tree, name):
    for s in tree.slots:
        if s.key == name:
            return s
    return Slot(None, 0, name, name, "scalar", name)


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
        reg = None
        for kind, target, rpath, rline in tree.registrations:
            if kind not in rules:
                continue
            tc, sc = target.split("."), slot.key.split(".")
            hit = target == slot.key
            if not hit and len(sc) > 1 and len(tc) == len(sc) and tc[1:] == sc[1:]:
                hit = tc[0] in owners        # a pointer into the object, not the object
            if hit:
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
            if "immediate" in rules and is_immediate(tree, rhs, rules):
                kinds.add("immediate")
            elif "const-table" in rules and is_const_table(tree, rhs, rules):
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
        pub = tree.published.get(slot.key) if "const-published" in rules else None
        if pub:
            r.discharges.append(("const-published", rel, line, slot.key,
                                 "installed in the constant table by %s at %s:%d"
                                 % (pub[0], pub[1].relative_to(tree.root), pub[2])))
            continue

        wrap = tree.wraps.get(slot.root)
        if slot.kind != "scalar" and "wrapped" in rules and wrap and wrap[1] \
                and wrap[1] not in ("NULL", "0"):
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
                  if not (is_immediate(tree, rhs, ALL_RULES)
                          or is_const_table(tree, rhs, ALL_RULES))]
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
          "| allocating %2d | discharged %3d [%s] | HITS %d"
          % (r.name, r.files, r.slots, r.decls, r.scalars, r.fields, r.unresolved_members,
             r.allocating, len(r.discharges),
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
    # across 6 files, 9 distinct names, and one of the nine does NOT discharge. Pinned here
    # so the next reader gets the measured count rather than the claim.
    check(kg.slots == 9 and kg.decls == 12,
          "kgio has %d file-scope VALUE declarations / %d distinct names, not 1"
          % (kg.decls, kg.slots))
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
    f, grades = [0] * 7, {}
    for d in a.dirs:
        root = pathlib.Path(d)
        r = sweep(Tree(root), root.name, rules)
        report(r, verbose=a.verbose)
        for i, v in enumerate((r.files, r.slots, r.scalars, r.fields, r.allocating,
                               len(r.discharges), len(r.hits))):
            f[i] += v
        for h in r.hits:
            grades.setdefault(h[0], []).append("%s %s:%d  %s" % (r.name, h[1], h[2], h[3]))
    print("\nFUNNEL over %d tree(s), %d C file(s):\n"
          "  file-scope VALUE slots ........................ %4d  (scalar %d, field %d)\n"
          "  ...with at least one allocating source ........ %4d\n"
          "  discharged by a named rule .................... %4d\n"
          "  REMAINING (hits) .............................. %4d"
          % (len(a.dirs), f[0], f[1], f[2], f[3], f[4], f[5], f[6]))
    for g in ("ALLOCATES", "FOREIGN", "CALL", "OPAQUE", "CONST-LOOKUP", "UNSOURCED"):
        for where in grades.get(g, []):
            print("      %-10s %s" % (g, where))
    if a.no_discharge or a.disable_rule:
        print("  NOTE: discharge rules are off (%s). This is the recall audit, not a "
              "verdict." % ("all" if a.no_discharge else ",".join(a.disable_rule)))


if __name__ == "__main__":
    main()

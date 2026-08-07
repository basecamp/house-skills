#!/usr/bin/env python3
"""Predicate B: an in-place String conversion whose result escapes the frame that owns it.

    python3 sweep_escaped_conversion.py <gem-dir> [<gem-dir> ...]
    python3 sweep_escaped_conversion.py --self-test <gem-dir> [<gem-dir> ...]

THE SHAPE
---------
A helper takes a `VALUE` **by value**, converts it in place -- `StringValue`,
`StringValuePtr`, `FilePathValue`, `rb_string_value(&x)` -- and something derived from
that conversion outlives the callee's frame. The parameter is a copy, so the conversion
writes the new String into the *callee's own local*; the caller's `VALUE` still holds the
unconverted original. Nothing roots the converted object once the callee returns.

Two sub-shapes, both confirmed, in two unrelated gems:

  RETURNS-INTERIOR  the helper returns `RSTRING_PTR` of its own converted local.
                    rmagick `rm_str2cstr` -- rmagick/rmagick#1846
  STORES-INTERIOR   it writes that interior somewhere the frame does not own: through an
                    out-parameter, or into a file-scope slot. Same defect, later read.
  CALLER-DEREFS     the *callers* dereference their own unconverted copy after the call.
                    bootsnap `bs_cache_path` (1.24.x, still at HEAD). Worse than a UAF:
                    with a `Pathname` argument the caller `RSTRING_PTR`s a `T_OBJECT`, and
                    the callee's `Check_Type(path_v, T_STRING)` -- which passes, because it
                    checks the converted local -- gave the reader false assurance.

THE SAFE COUNTER-SHAPE
----------------------
A helper taking `VALUE *`, so the conversion writes back into the caller's variable. Its
caller-side equivalent -- the caller assigning its own variable from the coercion,
`file = rb_rescue(RESCUE_FUNC(rb_String), file, ...)` (rmagick rmimage.cpp:11564, :15853)
or `str = convert_encoding(str);` (json parser.c:2369) -- is the same fix written at the
other end, and is the discharge that keeps those three sites green.

Correction worth keeping: rmagick 6.1.4 was described to us as already shipping the
`VALUE *` idiom at rmimage.cpp:11565/:15853. It does not. There is **no `VALUE *`
parameter anywhere** in rmagick's .cpp sources -- every `VALUE *` in the tree is the
`int argc, VALUE *argv` cfunc signature. What those two lines ship is the caller-side
write-back above, which is semantically the same fix and is why they are green, but a
reader looking for a `VALUE *` helper to copy would find nothing. The third safe shape is
a third thing again -- bootsnap <=1.23.0 did the `FilePathValue` **in the cfunc entry
point**, so the frame that converts is the frame that reads. Moving that one call down
into `bs_cache_path` in 1.24.0 is what created the defect.

START FROM THE ESCAPE, NOT THE CONVERSION
-----------------------------------------
A conversion writing into a frame is a bug only if something derived from it outlives that
frame. Measured funnel over the 23-gem corpus (bootsnap 1.24.6 adds 1 / 1 / 2):

    by-value VALUE params converted in place ..... 101 params, in 81 functions
    after excluding cfunc entry points ............ 13 params, in  12 functions
    RETURNS-INTERIOR ..............................  1   (rmagick rm_str2cstr)
    CALLER-DEREFS ................................   2   (bootsnap 1.24.5, 2 call sites)

Watch the unit: 101 is (function, parameter) pairs and 12 is distinct functions -- mysql2's
`opt_connect_attr_add_i` converts two parameters, so the two counts are one apart. Both are
printed on every run rather than picked, because a funnel that silently switches unit is a
funnel you cannot check.

Excluding cfunc entry points is sound for both sub-shapes and is not a heuristic: a cfunc
has no C-level caller (so CALLER-DEREFS cannot exist) and returns `VALUE` (so
RETURNS-INTERIOR cannot exist). Its converted local lives in its own conservatively
scanned C frame for the whole body.

THE ARGV-SEEDED LOCAL IS PREDICATE D'S, NOT THIS ONE'S
-------------------------------------------------------
This predicate keys on by-value PARAMETERS. cgi's `cgiesc_unescape` does
`VALUE str = argv[0]; StringValue(str);` -- a **local**, so the conversion is outside this
walk by construction, and no amount of tuning here reaches it. That gap was round 6's
residual and it is now covered, deliberately and in one place only:
`sweep_interior_escape.py` (predicate D) starts from the DERIVATION at any storage class
and treats the argv-seeded local as its charter case, with cgi as a named acceptance item.
Do not widen this predicate to chase it -- two predicates half-covering one shape is how a
gem ends up cleared by both.

THREE THINGS THAT ARE NOT TUNING KNOBS
--------------------------------------
* **`argv[i]` is the canonical RED shape, not a discharge.** rmagick#1846 is filed on
  exactly `rm_str2cstr(argv[0], &format_l)`. The VM stack pins what `argv[i]` holds, which
  is the *original* object -- never the String the callee coerced. An early cut of this had
  it backwards and discharged the filed bug.
  Round 7 measured the other half of that sentence and it is worth stating precisely,
  because predicate D depends on it: conservatively scanned argv memory pins against
  MOVEMENT as well as collection (0/20 corrupt vs 20/20 for the same subject reachable only
  from a global, on 4.0.6 / 3.4.10 / 3.4.7). So `argv` really is a discharge -- for the
  object it holds, which on this shape is never the converted one. Both statements are true
  at once and predicate D's docstring reconciles them.
* **Bound the caller scan by the caller's own function body.** `--window 1500` reinstates
  the fixed-window scan and mysql2 comes straight back: four one-line callers that
  `return _mysql_client_options(self, OPT, value);` and whose windows run past their own
  closing brace into `set_charset_name`'s `RSTRING_PTR(value)` -- a different function's
  parameter that happens to share a name. All four spurious hits print the same line, 1415,
  which is the tell. zlib and json were the other two gems that window burned; here they
  are cleared one stage earlier, by the two named discharge rules below, so the window flag
  no longer reaches them. json is load-bearing either way: it was already a burned false
  positive in round 4, so re-burning it is a real cost.
* **A zero must be readable.** Every run prints per-stage funnel counts, so "nothing found"
  is distinguishable from "the query failed to resolve anything". Round 4's `*: 0 suspects`
  on an unexpanded shell glob is the precedent.

THE DISCHARGE RULES, AND WHAT THEY CANNOT SEE
---------------------------------------------
Four rules, each forced by unedited corpus code, each named in the output:

  caller assigns the return value back    json parser.c:2369 `str = convert_encoding(str);`
  caller converted its own copy first     rmagick rmimage.cpp:15851 `FilePathStringValue(file)`
                                          zlib.c:2152 `StringValue(src)`
  caller assigned it from a coercion      rmagick rmimage.cpp:11564
                                          `file = rb_rescue(RESCUE_FUNC(rb_String), file, ...)`
  argument is a temporary                 rmagick rmilist.cpp:1206 `add_format_prefix(rm_io_path(file))`

All of them are **path-insensitive** -- there is no CFG here. rminfo.cpp's
`StringValueCStr(argv[0])` is in a different `switch` case from its `rm_str2cstr(argv[0],
...)`, and zlib's `StringValue(src)` is in the other arm of the `if` from its
`do_inflate(z, src)`; both verdicts are right, but for a reason the rule does not know. A
defect sitting on the else-branch of a conversion WOULD be wrongly cleared. `--no-discharge`
turns every rule off and prints what they suppress; over the whole corpus that set is three
lines, which is short enough to read by hand, and --self-test pins its size and shape.

LEXING
------
Copied from sweep_unmarked.py. `strip_noise` blanks comments and string bodies but keeps
newlines, and `strip_directives` keeps line count and byte length, so byte offsets AND line
numbers into the stripped text both match the original file. Predicate B prints file:line
for every hit, so `--self-test` asserts that round-trip on a real corpus file rather than
taking it on trust.

SIX RECALL GAPS, AND WHY THE CORPUS CANNOT VOUCH FOR THE FIXES
--------------------------------------------------------------
Review of #28 found six ways a real instance stayed invisible. Sweeping all 99 corpus
trees before and after fixing them adds **no row and removes none**; the only corpus-
visible change at all is that two rmagick discharges now cite the rule that actually
reaches the call. So the corpus is neutral, which is exactly the condition under which a
green suite proves nothing -- this predicate's own history has a green fixture that passed
on an empty index. Each fix therefore ships with a GENERATED RED synthetic in self_test()
(items 10a-10f), red before and green after, and each asserts the FUNNEL COUNTERS rather
than only the hit count, because the failure being guarded against prints `0 fn(s),
0 conversions` and reads as clean:

  10a  an ALIAS of the interior stored through an out-param   `p = RSTRING_PTR(s); *out = p`
  10b  definitions inside `namespace` / `extern "C"`          indexed 0 fn(s) before
  10c  `RSTRING_GETMEM(s, p, len)` as an alias SOURCE         the macro's output argument
  10d  cfunc exclusion scoped to the registered definition    a `static` namesake in another TU
  10e  the interior stored into a file-scope slot             `g_saved = RSTRING_PTR(s)`
  10f  a caller conversion an assignment overwrote            `StringValue(x); x = y; f(x)`

A SEVENTH, ONE COPY FURTHER ALONG, from the round-9 review (:716). 10a seeds the alias set
from the conversion itself, so `*out = p` is found and `q = p; *out = q` is not: pointer
identity did not survive a local-to-local copy, and `p = RSTRING_PTR(str); q = p; return q;`
-- the filed rmagick defect written one line longer -- reported one converted non-cfunc and
ZERO hits. Predicate D carried the identical defect on its own alias set, so the propagation
is now `tu_scope.alias_set` and this file supplies only the seeds. Item 13, generated red and
green: `q = p; return q;` must give the SAME row as `return p;`, `q = p; *out = q;` must be a
STORES-INTERIOR rather than be swallowed as an alias, and `r = q;` running BEFORE `q = p;`
must make `r` nothing at all. Corpus-neutral, like all six above.

All of them keep this predicate keyed on a BY-VALUE PARAMETER (`converted_params` ->
`value_params` -> a bare `VALUE` in the parameter list) and change only what counts as an
*escape*. None of them introduces a window, a GC-triggering call, or a pointer held across
one -- that is predicate D's charter and widening B into it is how a gem gets cleared by
both. 10c in particular is the escape-analysis half of `RSTRING_GETMEM`: D covers the same
macro's argument taking part in WINDOW analysis, separately.

FOUR SHARED RULES, ALL OF THEM IN tu_scope.py
---------------------------------------------
Every lookup that turns a NAME at a use site into a DEFINITION goes through
`tu_scope.bind`, which states C's linkage rule once for all four predicates: a use binds
to a definition in its own file first, a `static` definition in another .c/.cc/.cpp/.cxx
is not a candidate at all, and everything else -- non-static definitions, and anything
declared in a HEADER -- stays tree-wide.

This file was the last one carrying its own PRE-EXTRACTION copies of the other three, and
what that cost was measured rather than argued: 23,120 indexed definitions over the 99-tree
corpus against the 23,318 predicates C and D agreed on. Collapsing it onto tu_scope moved
the count to 23,318 and moved NO row -- the whole visible difference is nio4r 341 -> 416 and
sassc 983 -> 1106, definitions carrying `EV_NOEXCEPT` and a C++ `const` qualifier, none of
which converts a by-value parameter.

  rule 2  which braces open a storage scope   -- `namespace X {`, `extern "C" {`
  rule 3  where a declarator ends and a body begins, with its rejection table, which this
          file's --self-test now asserts alongside C's and D's
  rule 4  which locals carry the same pointer -- the transitive closure over local-to-local
          copies that `escapes_by_return` seeds from its conversion and from RSTRING_GETMEM

That module is a sibling file and these scripts will not run without it; references/ is the
unit that ships.

ACCEPTANCE (--self-test): see self_test(). Run it before trusting any result from this
script -- silence is a property of the query until the counts say otherwise.
"""
import argparse
import pathlib
import re
import shutil
import sys
import tempfile

# The linkage rule, shared with the other three predicates. It lives in its own module
# because every one of the four had been patched for the same defect -- an
# internal-linkage name resolved tree-wide -- once per lookup table. Sibling file, so
# `python3 .../sweep_escaped_conversion.py` finds it wherever it is run from; the
# references/ directory is the unit that ships, and a script copied out of it alone will
# not import.
import tu_scope

C_EXT = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp")

# ---------------------------------------------------------------- lexing helpers
#
# Verbatim from sweep_unmarked.py. Comments and string literals are stripped before any
# brace matching: a brace inside either one silently desynchronises the matcher, and a
# desynchronised matcher yields a bogus function body -- which here means attributing a
# caller's dereference to the wrong frame, in both directions.


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


# THE BRACE WALK AND THE DECLARATOR WALK ARE tu_scope's, NOT THIS FILE'S ANY MORE.
#
# Both were ported into this file verbatim in round 8 and left here when they were extracted
# in round 9, so this was the last remaining pre-extraction copy in the directory -- measured
# at 23,120 indexed functions against the 23,318 predicates C and D agree on over the same 99
# trees. The 198 definitions in the gap are the ones carrying `__attribute__((...))`,
# `noexcept`, `EV_NOEXCEPT` or a C++ `const` qualifier between the `)` and the `{`, which
# this file's own five-line index skipped whitespace only across.
#
# Rule 2 (which braces open a storage scope) and rule 3 (where a declarator ends and a body
# begins) are one rule each now, in one file, with one rejection table that this file's
# self-test asserts alongside C's and D's.
match_brace = tu_scope.match_brace
top_level_units = tu_scope.top_level_units
scope_zero_braces = tu_scope.scope_zero_braces
skip_post_declarator = tu_scope.skip_post_declarator


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


def line_of(text, off):
    """1-based line number of a byte offset. Exact only because blank() keeps newlines."""
    return text.count("\n", 0, off) + 1


# ------------------------------------------------------- the vocabulary
#
# In-place conversions. The first group takes the lvalue directly and rewrites it; the
# second takes its ADDRESS, so `rb_string_value(&str)` on a by-value parameter is the same
# defect written with an ampersand, while `rb_string_value(strp)` on a `VALUE *strp`
# parameter is the safe counter-shape.

LVALUE_CONV = {"StringValue", "StringValuePtr", "StringValueCStr", "SafeStringValue",
               "ExportStringValue", "FilePathValue", "FilePathStringValue"}
ADDR_CONV = {"rb_string_value", "rb_string_value_ptr", "rb_string_value_cstr",
             "rb_file_path_value", "rb_check_string_type_ptr"}

# Interior derivers: a `char *` into the String's bytes. These are what must not outlive
# the frame that owns the conversion.
INTERIOR = {"RSTRING_PTR", "RSTRING_END", "RSTRING_GETMEM", "StringValuePtr",
            "StringValueCStr", "rb_string_value_ptr", "rb_string_value_cstr",
            "RSTRING_PTRZ"}
# Length reads. Not a dangling pointer, but on the CALLER-DEREFS shape they read the
# header of whatever unconverted object the caller still holds -- bcrypt_pbkdf's
# `RSTRING_LEN` on an unvalidated VALUE is the precedent for treating that as a finding.
LENGTH = {"RSTRING_LEN", "RSTRING_LENINT", "RSTRING_EMBED_LEN"}
DEREF = INTERIOR | LENGTH

# Registration macros whose named function is a cfunc entry point: no C-level caller, and
# it returns VALUE, so neither sub-shape can exist there.
DEFINE_RE = re.compile(r"^rb_define_(method|singleton_method|module_function|"
                       r"global_function|private_method|protected_method|method_id|"
                       r"protected_method_id|private_method_id|alloc_func)$")

# Callees whose return value is provably a String. Used only as a caller-side DISCHARGE.
STRING_PRODUCER = re.compile(
    r"^(rb_str_\w+|rb_String|rb_obj_as_string|rb_enc_str_\w+|rb_external_str_\w+"
    r"|rb_utf8_str_\w+|rb_usascii_str_\w+|rb_locale_str_\w+|rb_filesystem_str_\w+"
    r"|rb_sprintf|rb_vsprintf|rb_id2str|rb_inspect|rb_obj_class_name|rb_get_path"
    r"|rb_check_string_type|rb_any_to_s|rb_class_name|rb_file_absolute_path"
    r"|rb_file_expand_path|rb_string_value)$")

PARAM_VALUE_RE = re.compile(r"^(?:const\s+|volatile\s+|register\s+)*VALUE\s+(\w+)$")
PARAM_VALUE_PTR_RE = re.compile(r"^(?:const\s+|volatile\s+|register\s+)*VALUE\s*\*\s*(\w+)$")


def param_name(decl):
    """Best-effort declarator name: `char (* cache_path)[N]` -> cache_path."""
    d = decl.split("[")[0]
    ids = re.findall(r"[A-Za-z_]\w*", d)
    kw = {"const", "volatile", "register", "struct", "union", "enum", "unsigned",
          "signed", "long", "short", "int", "char", "void", "float", "double", "static"}
    ids = [i for i in ids if i not in kw]
    return ids[-1] if ids else ""


# `typedef` and `using` name types, `template` heads a definition with no object of its
# own. `extern VALUE x;` is NOT skipped: it names a persistent slot defined elsewhere,
# which is the sink we are looking for.
DECL_NOT_OBJECT = re.compile(r"^(?:typedef|using|template|namespace)\b")


def file_scope_objects(src):
    """Names declared at file or namespace scope -- file statics and globals.

    A slot at this scope outlives every frame in the file, so an interior pointer stored
    into one escapes the converting frame exactly as a return value does. Recognising the
    sink POSITIVELY, by name, is what keeps `char *p = RSTRING_PTR(str);` -- a plain local,
    and the alias case escapes_by_return already handles -- from reading as an escape. The
    inverted form ("any store to something that is not provably frame-local") is the
    recall-biased one and is the wrong trade here: this predicate's whole value is a funnel
    narrow enough to read, and every unparsed local declaration would widen it.
    """
    names = set()
    for _off, unit in top_level_units(src):
        u = unit.strip()
        if not u.endswith(";"):
            continue                    # a function body or a class body, not a slot
        u = u[:-1].strip()
        if not u or DECL_NOT_OBJECT.match(u):
            continue
        for d in split_args(u):
            d = d.split("=")[0]
            # a prototype declares no object; a function POINTER does, and is spelled
            # `(*fp)(...)`, so only the un-parenthesised declarator is dropped
            if "(" in d and not re.search(r"\(\s*\*", d):
                continue
            nm = param_name(d)
            if nm:
                names.add(nm)
    return names


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
        self.is_static = is_static  # internal linkage: this name is this file's alone
        # Where a CALL can bind to this definition. tu_scope states the rule once for all
        # four predicates: a `static` in a .c/.cc/.cpp/.cxx is this file's alone, and
        # anything else -- including a `static` in a HEADER -- stays tree-wide.
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
        self.cfuncs = set()             # bare names registered anywhere in the tree
        self.cfunc_regs = set()         # (path, name) -- registered IN that file
        self.statics = {}               # path -> names at file/namespace scope
        for path, src in self.files.items():
            self._index_funcs(path, src)
            self._index_cfuncs(path, src)
            self.statics[path] = file_scope_objects(src)
        for f in self.funcs:
            self.by_name.setdefault(f.name, []).append(f)
        # per-file (bstart, bend, Func), innermost-last, for offset -> enclosing frame
        self.ranges = {}
        for f in self.funcs:
            self.ranges.setdefault(f.path, []).append(f)
        for v in self.ranges.values():
            v.sort(key=lambda f: (f.bstart, -f.bend))

    def _index_funcs(self, path, src):
        """Top-level definitions only.

        The depth check is not cosmetic: a macro invocation followed by a block --
        `RB_VM_LOCK_ENTER() { ... }` -- parses as a definition nested inside a real
        function, and the innermost-frame lookup would then bound a caller scan by the
        macro's block instead of the function's body. That is a false NEGATIVE generator,
        which is the failure mode that matters.

        Depth is counted over STORAGE scopes, not braces, and the crossing from the `)` to
        the `{` is a walk rather than a whitespace skip. Both are tu_scope's -- the same
        five lines predicate C and predicate D index with, after this file spent a round
        carrying its own copy of them. What that cost was measurable and was measured: this
        index found 23,120 definitions over the corpus where C and D found 23,318, and the
        198 missing ones are every definition whose declarator carries `__attribute__((...))`,
        `noexcept`, `EV_NOEXCEPT` or a C++ `const` qualifier. A definition this walk drops
        takes its conversions, its escapes and its call sites with it.
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
            # the declaration specifiers, back to the previous statement boundary
            head = src[max(0, m.start() - 300):m.start()]
            head = head[max(head.rfind(";"), head.rfind("}"), head.rfind("{")) + 1:]
            self.funcs.append(Func(m.group(1), path, src, params, m.start(), k + 1, close,
                                   bool(re.search(r"\bstatic\b", head))))

    def _index_cfuncs(self, path, src):
        for name, args, _s, _e in find_calls(src):
            if DEFINE_RE.match(name):
                names = re.findall(r"[A-Za-z_]\w*", " ".join(args))
                self.cfuncs.update(names)
                self.cfunc_regs.update((path, n) for n in names)

    def is_cfunc(self, fn):
        """Is `fn` THE definition registered as a Ruby cfunc -- not merely a namesake?

        Keying the exclusion on the bare name is a whole-tree over-clear: two translation
        units may each define a `static` function called `collide`, and registering one of
        them deleted the other from the funnel. `static` means the name belongs to its own
        file, so the registration has to be in that file to reach it. A name with external
        linkage can be registered from anywhere, so for those the tree-wide set still
        applies.

        Not covered, and in the over-REPORTING direction: a `static` or `inline` definition
        in a header, registered from the .c that includes it. The registration is in a
        different file, so the exclusion misses and the helper stays in the funnel.
        """
        if (fn.path, fn.name) in self.cfunc_regs:
            return True
        return not fn.is_static and fn.name in self.cfuncs

    def enclosing(self, path, off):
        """The innermost top-level function whose body contains `off`, or None."""
        best = None
        for f in self.ranges.get(path, ()):
            if f.bstart > off:
                break
            if f.bend > off and (best is None or f.bstart > best.bstart):
                best = f
        return best


# ------------------------------------------------------- stage 1: the conversion


def converted_params(fn):
    """[(index, param, conv_macro, offset)] -- by-value VALUE params converted IN PLACE.

    `StringValue(x)` where x is the parameter name, or `rb_string_value(&x)`. A conversion
    applied to `*p` or to a local is not this predicate.
    """
    hits, body = [], fn.body
    names = {nm: idx for idx, nm in fn.value_params()}
    if not names:
        return hits
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
        if target in names:
            hits.append((names[target], target, name, fn.bstart + s))
    # de-dupe on (param, macro), keeping the first conversion site
    seen, out = set(), []
    for idx, nm, macro, off in hits:
        if (idx, nm) in seen:
            continue
        seen.add((idx, nm))
        out.append((idx, nm, macro, off))
    return out


# ------------------------------------------------------- stage 3a: escape by return/store


def escapes_by_return(fn, param, statics=()):
    """[(kind, offset, text, sink)] -- the converted local's interior leaving the frame.

    `statics` is the set of names declared at file or namespace scope in `fn`'s own file
    (Tree.statics). A store into one of those is an escape for the same reason a store
    through an out-parameter is: the slot outlives every frame in the translation unit,
    while nothing roots the String the conversion produced.
    """
    body = fn.body
    found = []
    ptr_params = {nm for decl, nm in fn.params
                  if nm and ("*" in decl or "[" in decl) and nm != param}
    statics = set(statics)

    # Locals that ALIAS the interior before it escapes:
    #
    #     const char *p = RSTRING_PTR(str);
    #     return p;                          /* or:  *out = p;  or:  g_saved = p; */
    #
    # Matching only `return RSTRING_PTR(str);` misses this, and it is a completely
    # ordinary way to spell the very defect this predicate targets -- rm_str2cstr with one
    # more line. Recall gap, so it fails silent: the funnel reports the conversion, finds
    # no escape, and prints a clean sheet.
    #
    # Two alias SOURCES, not one. The assignment form is the obvious one; the other is
    # RSTRING_GETMEM(str, p, len), which hands the interior back through an OUTPUT
    # ARGUMENT and so never passes under an `=` at all. It is in INTERIOR already, so
    # `return RSTRING_PTR(str)` and `return p` after a GETMEM are the same defect -- but
    # only the first was ever found. (Predicate D has a sibling gap on the same macro; its
    # half is about the argument taking part in WINDOW analysis. This half is escape
    # analysis only and does not reach into D.)
    #
    # AND THE ALIAS OF AN ALIAS IS STILL THE INTERIOR. `p = RSTRING_PTR(str); q = p;
    # return q;` seeded only `p`, so `derives("q")` was false, the return read as clean and
    # the tree reported one converted non-cfunc and ZERO hits -- the same silent recall loss
    # one copy further along, in the same function, and the same defect predicate D carried
    # on its own alias set. The propagation is tu_scope's fourth rule, stated once for both;
    # what stays here is the SEEDS, which are this predicate's own: an in-place conversion
    # of its by-value parameter, and RSTRING_GETMEM's output argument.
    seeds = {}

    def seed(nm, off):
        if nm not in seeds or off < seeds[nm]:
            seeds[nm] = off

    for name, args, _s, _e in find_calls(body):
        if name in ("RSTRING_GETMEM", "rb_str_getmem") and len(args) >= 2 \
                and args[0].strip() == param:
            nm = re.fullmatch(r"\*?\s*([A-Za-z_]\w*)", args[1].strip())
            if nm:
                seed(nm.group(1), _s)
    for m in re.finditer(r"(?<![=!<>])=(?!=)", body):
        semi = body.find(";", m.end())
        if semi < 0:
            continue
        if not any(name in INTERIOR
                   and param in re.findall(r"[A-Za-z_]\w*", " ".join(args))
                   for name, args, _s, _e in find_calls(body[m.end():semi])):
            continue
        lhs = body[max(0, m.start() - 200):m.start()]
        stmt = lhs[lhs.rfind(";") + 1:]
        stmt = stmt[stmt.rfind("{") + 1:].strip()
        # `p`, `char *p`, `const char *p` -- a plain local, not `*out`, `o->f` or a
        # file-scope slot, which are the STORES-INTERIOR case handled below.
        nm = re.match(r"^(?:[A-Za-z_]\w*\s+)*\*?\s*([A-Za-z_]\w*)$", stmt)
        if nm and nm.group(1) not in ptr_params and nm.group(1) not in statics:
            seed(nm.group(1), m.start())
    # The exclusion is passed in rather than applied afterwards: an out-parameter and a
    # file-scope slot are the two SINKS this function reports, and a sink that is also
    # pointer-typed reads as a copy. Swallowing `*out = p` into the alias set would delete
    # the STORES-INTERIOR row it exists to produce.
    #
    # AND A NAME THAT WAS AN ALIAS IS NOT ONE FOR EVER. `alias_set` names the carriers;
    # matching the NAME anywhere after that reported `p = RSTRING_PTR(str); p = "safe";
    # return p;` as RETURNS-INTERIOR on a string literal -- a false positive in the
    # propagation this file gained in the same review. The kill is tu_scope's fifth rule
    # applied to the alias set, so what comes back here is OFFSETS of occurrences that still
    # evaluate to the interior, not a name soup that can never be unlearned.
    alias_reads = tu_scope.alias_reads(body, seeds, exclude=ptr_params | statics)

    def derives(expr, base):
        """Does `expr`, taken at offset `base` in the body, evaluate to the interior?"""
        if any(name in INTERIOR
               and param in re.findall(r"[A-Za-z_]\w*", " ".join(args))
               for name, args, _s, _e in find_calls(expr)):
            return True
        return any(base <= o < base + len(expr) for o in alias_reads)

    for m in re.finditer(r"\breturn\b", body):
        semi = body.find(";", m.end())
        if semi < 0:
            continue
        if derives(body[m.end():semi], m.end()):
            found.append(("RETURNS-INTERIOR", fn.bstart + m.start(),
                          body[m.start():semi + 1].strip(), "the return value"))
    # Stores into memory the frame does not own. Two sinks, one shape:
    #
    #   an OUT-PARAMETER      `*out = RSTRING_PTR(str)`, `out->f = ...`, `out[i] = ...`
    #                         -- the caller owns that memory, so the pointer outlives this
    #                         frame exactly as a return value would. A bare `out = ...`
    #                         is NOT one: it rebinds the callee's private copy.
    #   a PERSISTENT SLOT     `g_saved = RSTRING_PTR(str)`, `g_state.p = ...` -- a name at
    #                         file or namespace scope. Here the bare form IS the escape,
    #                         which is why the two sinks need different lvalue rules.
    for m in re.finditer(r"(?<![=!<>])=(?!=)", body):
        lhs = body[max(0, m.start() - 200):m.start()]
        stmt = lhs[lhs.rfind(";") + 1:].strip()
        stmt = stmt[stmt.rfind("{") + 1:].strip()
        base = re.match(r"^\*?\s*([A-Za-z_]\w*)\s*(->|\[|\.|$)", stmt)
        if not base:
            continue
        if base.group(1) in ptr_params:
            if not (stmt.startswith("*") or base.group(2) in ("->", "[")):
                continue
            sink = "out-param %s" % base.group(1)
        elif base.group(1) in statics:
            sink = "file-scope slot %s" % base.group(1)
        else:
            continue
        semi = body.find(";", m.end())
        if semi < 0:
            continue
        rhs = body[m.end():semi]
        if derives(rhs, m.end()):
            found.append(("STORES-INTERIOR", fn.bstart + m.start(),
                          (stmt + " =" + rhs).strip(), sink))
    return found


# ------------------------------------------------------- stage 3b: escape by caller deref


def call_sites(tree, fn):
    """[(caller, arg_list, offset_of_call, offset_past_call)] for in-tree calls of `fn`.

    ROUND 9: A CALL BINDS TO THE DEFINITION ITS OWN TRANSLATION UNIT CAN SEE.

    The scan is textual and tree-wide, so `helper(x)` in b.c matched `fn` regardless of
    which file `fn` was defined in. Two translation units may each define a `static
    helper`: a.c's converts in place and returns an interior, b.c's is unrelated and its
    caller then reads `RSTRING_LEN(x)`. Attributing b.c's call to a.c's body reports
    CALLER-DEREFS against b.c on a helper that never ran there -- and a call site is also
    what makes an escape REACHABLE, so the same misattribution decorates a real finding
    with call sites that cannot reach it.

    tu_scope.bind is the same rule the other three predicates resolve names by; here it
    runs in the caller-to-callee direction rather than callee-to-caller, which is the only
    thing that made this look like a different bug.
    """
    out = []
    for path, src in tree.files.items():
        peers = tree.by_name.get(fn.name, ())
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


ASSIGN_BACK = re.compile(r"([A-Za-z_]\w*(?:\s*\[[^\]]*\])?)\s*=\s*$")


def caller_holds_string(caller, arg, upto):
    """Is `arg` provably already a String in the CALLER's frame before the call?

    Three ways, every one of them taken from unedited corpus code:

      1. the caller converted its own copy      -- `StringValue(x); helper(x);`
      2. the caller assigned it from a coercion -- rmagick rmimage.cpp:11564/:15853,
         `file = rb_rescue(RESCUE_FUNC(rb_String), file, ...)`, and the plain
         `x = rb_str_new(...)` form
      3. the caller type-checked it             -- `Check_Type(x, T_STRING)`

    NOT a discharge: `argv[i]`. The VM stack pins what argv[i] holds, which is the
    ORIGINAL object; rmagick#1846 is filed on exactly that call shape.

    Every one of the three is gated on REACHING the call. An assignment to `arg` kills
    whatever was established about `arg` before it: `StringValue(x); x = y; helper(x);`
    discharged on a conversion that no longer describes the value being passed, and `y`
    may be any object at all. So the scan below starts at the LAST assignment to `arg`,
    and that same assignment is then judged on its own RHS by rule 2 -- which is the only
    rule that was already reaching-correct, because it always took the last one.
    """
    if not re.fullmatch(r"[A-Za-z_]\w*", arg):
        return None
    before = caller.body[:upto]
    last, reassign = None, -1
    for m in re.finditer(r"\b%s\s*=(?!=)" % re.escape(arg), before):
        last, reassign = m, m.start()
    for name, args, s, _e in find_calls(before):
        if s < reassign or not args:
            continue
        a0 = args[0].strip()
        if (name in LVALUE_CONV and a0 == arg) or \
           (name in ADDR_CONV and a0 == "&" + arg):
            return "caller converted its own copy (%s)" % name
        if name in ("Check_Type", "rb_check_type") and a0 == arg and \
                len(args) > 1 and "T_STRING" in args[1]:
            return "caller Check_Type'd its own copy"
    if last is not None:
        semi = before.find(";", last.end())
        rhs = before[last.end():semi if semi > 0 else len(before)]
        for name, _args, _s, _e in find_calls(rhs):
            if STRING_PRODUCER.match(name):
                return "caller assigned it from %s()" % name
        if re.search(r"\brb_String\b", rhs):     # rb_rescue(RESCUE_FUNC(rb_String), ...)
            return "caller assigned it from a rb_String coercion"
    return None


def caller_derefs(tree, fn, param_idx, param, window=None, discharge=True):
    """[(caller, arg, deref_macro, offset)] -- callers dereferencing their own copy.

    Bounded by the CALLER'S OWN BODY. A fixed window instead of the body bound reported
    mysql2, zlib and json, all three of them the window running past the closing brace
    into the next function. `window` reinstates that, for the self-test only.
    """
    hits, discharged, unmapped = [], [], 0
    for caller, args, off, past in call_sites(tree, fn):
        if len(args) != len(fn.params):
            unmapped += 1
            continue
        arg = args[param_idx].strip()
        # Member and indirect lvalues are RE-READABLE, so they belong in the scan, not in
        # the discharge pile. `helper(w->path)` followed by `RSTRING_PTR(w->path)` is the
        # CALLER-DEREFS class with a struct field standing in for the local: the helper
        # converted its private VALUE copy and the caller's field still holds the original.
        # Requiring a bare identifier discharged every one of those as "a temporary".
        #
        # Still discharged: genuine temporaries -- a call result, a cast, a literal,
        # anything the caller has no name for and therefore cannot read a second time.
        if not re.fullmatch(
                r"[A-Za-z_]\w*(\s*(->|\.)\s*[A-Za-z_]\w*|\s*\[[^\]]*\])*", arg):
            discharged.append((caller, arg, off, "argument is a temporary, "
                                                 "not an lvalue the caller can re-read"))
            continue
        rel_call = off - caller.bstart
        # The caller-side write-back: `str = convert_encoding(str);` (json parser.c:2369).
        # This is the `VALUE *` fix written at the other end -- the caller's own variable
        # now holds the converted String, so its later RSTRING_LEN is reading the object
        # the conversion produced. Deleting just the `str = ` is the one-token mutation
        # that flips json red in --self-test.
        back = ASSIGN_BACK.search(caller.body[:rel_call])
        if discharge and back and \
                re.sub(r"\s+", "", back.group(1)) == re.sub(r"\s+", "", arg):
            discharged.append((caller, arg, off,
                               "caller assigns the return value back to %s" % arg))
            continue
        why = caller_holds_string(caller, arg, rel_call) if discharge else None
        if why:
            discharged.append((caller, arg, off, why))
            continue
        lo = past - caller.bstart
        hi = len(caller.body) if window is None else min(len(caller.body),
                                                         lo + window)
        if window is not None:
            # the broken scan: keep reading past the caller's closing brace
            tail = caller.src[caller.bstart + lo:caller.bstart + lo + window]
        else:
            tail = caller.body[lo:hi]
        norm = re.sub(r"\s+", "", arg)
        reconverted = None
        for name, cargs, s, _e in find_calls(tail):
            if not cargs:
                continue
            a0 = cargs[0].strip()
            if discharge and ((name in LVALUE_CONV and a0 == arg) or
                              (name in ADDR_CONV and a0 == "&" + arg)):
                reconverted = name
                break
            if name in DEREF and re.sub(r"\s+", "", a0) == norm:
                hits.append((caller, arg, name,
                             caller.bstart + lo + s, tail[max(0, s - 40):s + 60]))
                break
        if reconverted:
            discharged.append((caller, arg, off,
                               "caller re-converts it before reading (%s)" % reconverted))
    return hits, discharged, unmapped


# ------------------------------------------------------- the sweep


class Result:
    def __init__(self, name):
        self.name = name
        self.files = 0
        self.funcs = 0
        self.conversions = []      # (fn, idx, param, macro, off)
        self.non_cfunc = []        # same, minus cfunc entry points
        self.hits = []             # (subshape, path, line, headline, detail lines)
        self.discharges = []       # (subshape, path, line, text)
        self.unmapped = 0

    # Counts are printed in BOTH units -- (function, parameter) pairs and distinct
    # functions -- because the two differ (mysql2's opt_connect_attr_add_i converts two
    # parameters) and a funnel that silently switches unit cannot be checked.
    @staticmethod
    def _fns(rows):
        return len({(f.path, f.hdr) for f, _i, _p, _m, _o in rows})

    @property
    def conv_fns(self):
        return self._fns(self.conversions)

    @property
    def non_cfunc_fns(self):
        return self._fns(self.non_cfunc)


def sweep(tree, name, window=None, discharge=True):
    r = Result(name)
    r.files = len(tree.files)
    r.funcs = len(tree.funcs)
    for fn in tree.funcs:
        for idx, param, macro, off in converted_params(fn):
            r.conversions.append((fn, idx, param, macro, off))
            # Scoped to the registered DEFINITION, not to the bare name: two translation
            # units may each define a `static` helper called `collide`, and a name-keyed
            # exclusion deletes both from the funnel when only one is a cfunc.
            if tree.is_cfunc(fn):
                continue
            r.non_cfunc.append((fn, idx, param, macro, off))

    for fn, idx, param, macro, off in r.non_cfunc:
        rel = str(fn.path.relative_to(tree.root))
        # -- 3a: the interior leaves the frame by return, out-param or file-scope slot
        for kind, eoff, text, sink in escapes_by_return(fn, param,
                                                        tree.statics.get(fn.path, ())):
            sites = call_sites(tree, fn)
            reaching, cleared = [], []
            for caller, args, coff, _past in sites:
                if len(args) != len(fn.params):
                    continue
                arg = args[idx].strip()
                why = caller_holds_string(caller, arg, coff - caller.bstart) \
                    if discharge else None
                where = "%s:%d" % (caller.path.relative_to(tree.root),
                                   line_of(caller.src, coff))
                (cleared if why else reaching).append(
                    "%s  %s(%s)%s" % (where, fn.name, arg, "  -- " + why if why else ""))
            r.hits.append((
                kind, rel, line_of(fn.src, eoff),
                "%s(%s %s) converts in place with %s, then %s"
                % (fn.name, "VALUE", param, macro,
                   "returns its interior" if kind == "RETURNS-INTERIOR"
                   else "stores its interior into %s" % sink),
                ["def %s:%d" % (rel, fn.line()),
                 "escape: %s" % re.sub(r"\s+", " ", text)[:120]]
                + ["reaching call site: " + s for s in reaching]
                + ["discharged call site: " + s for s in cleared]))
        # -- 3b: the caller reads its own unconverted copy
        hits, discharged, unmapped = caller_derefs(tree, fn, idx, param, window,
                                                   discharge)
        r.unmapped += unmapped
        for caller, arg, deref, doff, ctx in hits:
            crel = str(caller.path.relative_to(tree.root))
            r.hits.append((
                "CALLER-DEREFS", crel, line_of(caller.src, doff),
                "%s() reads %s(%s) after %s(%s ...) converted only its own copy"
                % (caller.name, deref, arg, fn.name, arg),
                ["helper: %s:%d %s(VALUE %s) via %s"
                 % (rel, fn.line(), fn.name, param, macro),
                 "caller: %s:%d %s" % (crel, caller.line(), caller.name),
                 "site: " + re.sub(r"\s+", " ", ctx).strip()[:110]]))
        for caller, arg, coff, why in discharged:
            r.discharges.append(("CALLER-DEREFS",
                                 str(caller.path.relative_to(tree.root)),
                                 line_of(caller.src, coff),
                                 "%s(%s) -- %s" % (fn.name, arg, why)))
    return r


def report(r, out=sys.stdout, verbose=False):
    for sub, path, line, headline, detail in sorted(r.hits, key=lambda h: (h[1], h[2])):
        print("%-16s %s:%d  %s" % (sub, path, line, headline), file=out)
        for d in detail:
            print("                   %s" % d, file=out)
    if verbose:
        for sub, path, line, why in sorted(r.discharges, key=lambda d: (d[1], d[2])):
            print("  discharged %s %s:%d  %s" % (sub, path, line, why), file=out)
    # Coverage. "0 hits" means one of three different things and only these counts tell
    # them apart: the tree has no C sources at all, it has no by-value conversion, or the
    # query failed to resolve any function body.
    print("%-26s %3d file(s) %5d fn(s) | conv %3d/%-3d -> non-cfunc %2d/%-2d -> hit %d "
          "(discharged %d, unmapped-arity %d)"
          % (r.name, r.files, r.funcs,
             len(r.conversions), r.conv_fns, len(r.non_cfunc), r.non_cfunc_fns,
             len(r.hits), len(r.discharges), r.unmapped), file=out)
    return len(r.hits)


# ---------------------------------------------------------------- acceptance


def _find(pool, prefix):
    for d in pool:
        if pathlib.Path(d).name.startswith(prefix):
            return pathlib.Path(d)
    return None


def _hits(root, window=None, discharge=True):
    root = pathlib.Path(root)
    return sweep(Tree(root), root.name, window, discharge).hits


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
        p.write_text(txt.replace(old, new))
    return dst


def _synth(name, files):
    """Write a synthetic tree from the test itself; return its path.

    Generated at test time and never checked in, for the same reason _mutate is: a fixture
    that lives on disk drifts away from the assertion that reads it, and the pair is the
    artifact. These exist because the corpus is NEUTRAL on the recall fixes below -- zero
    added rows, zero removed -- so a corpus run cannot tell a working fix from an absent
    one, and a green suite would prove nothing.
    """
    root = pathlib.Path(tempfile.mkdtemp()) / name
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def self_test(pool):
    ok = True
    log = []

    def check(cond, label, extra=""):
        nonlocal ok
        ok &= bool(cond)
        log.append("%s %s%s" % ("PASS" if cond else "FAIL", label,
                                "" if cond else "   [%s]" % extra))

    rmagick = _find(pool, "rmagick-")
    bootsnap = _find(pool, "bootsnap-1.24.6")
    jsn = _find(pool, "json-")
    for label, p in (("rmagick-6.1.4", rmagick), ("bootsnap-1.24.6", bootsnap),
                     ("json-2.20.0", jsn)):
        if p is None:
            print("FAIL fixture missing: %s (pass its directory as an argument)" % label)
            return 1

    # 0. line numbers survive the strip pipeline. Everything below prints file:line, so
    #    this is a precondition, not a nicety.
    probe = rmagick / "ext" / "RMagick" / "rmutil.cpp"
    raw = probe.read_text(errors="replace")
    stripped = strip_directives(strip_noise(raw))
    off = stripped.index("rm_str2cstr(VALUE str")
    raw_line = raw[:raw.index("rm_str2cstr(VALUE str")].count("\n") + 1
    check(len(stripped) == len(raw) and line_of(stripped, off) == raw_line,
          "strip pipeline preserves byte offsets and line numbers",
          "len %d vs %d, line %d vs %d"
          % (len(stripped), len(raw), line_of(stripped, off), raw_line))

    # 1. rmagick: RETURNS-INTERIOR on rm_str2cstr, with the right file:line.
    rh = _hits(rmagick)
    r_ret = [h for h in rh if h[0] == "RETURNS-INTERIOR" and "rm_str2cstr" in h[3]]
    check(len(r_ret) == 1 and r_ret[0][1].endswith("rmutil.cpp"),
          "rmagick 6.1.4 RED: RETURNS-INTERIOR rm_str2cstr at %s"
          % (("%s:%d" % (r_ret[0][1], r_ret[0][2])) if r_ret else "-"),
          [(h[0], h[1], h[2]) for h in rh])
    check(any("argv[0]" in d for d in (r_ret[0][4] if r_ret else [])
              if d.startswith("reaching call site")),
          "rmagick: argv[i] call sites counted as REACHING, not discharged")

    # 2. bootsnap 1.24.6: CALLER-DEREFS on bs_cache_path. Two independent reds, two
    #    unrelated gems -- that is the signal that this is a class, not a bug.
    bh = _hits(bootsnap)
    b_cd = [h for h in bh if h[0] == "CALLER-DEREFS" and "bs_cache_path" in h[3]]
    check(len(b_cd) >= 1 and all(h[1].endswith("bootsnap.c") for h in b_cd),
          "bootsnap 1.24.6 RED: CALLER-DEREFS bs_cache_path at %s"
          % ", ".join("%s:%d" % (h[1], h[2]) for h in b_cd),
          [(h[0], h[1], h[2]) for h in bh])

    # 3. GREEN generated by rewriting the helper to take `VALUE *` -- the fix, applied
    #    mechanically to the real tree at test time.
    g = _mutate(bootsnap, [(
        "ext/bootsnap/bootsnap.c",
        "bs_cache_path(VALUE cachedir_v, VALUE namespace_v, VALUE path_v, "
        "char (* cache_path)[MAX_CACHEPATH_SIZE])\n{\n  FilePathValue(path_v);",
        "bs_cache_path(VALUE cachedir_v, VALUE namespace_v, VALUE *path_vp, "
        "char (* cache_path)[MAX_CACHEPATH_SIZE])\n{\n  VALUE path_v;\n"
        "  rb_string_value(path_vp);\n  path_v = *path_vp;"),
        ("ext/bootsnap/bootsnap.c",
         "bs_cache_path(cachedir_v, namespace_v, path_v, &cache_path);",
         "bs_cache_path(cachedir_v, namespace_v, &path_v, &cache_path);")])
    gr, br = sweep(Tree(g), "g"), sweep(Tree(bootsnap), "b")
    # A green that is really a parse failure wearing a green tick is exactly how the
    # round-4 sweep passed item 4 for the wrong reason. Assert the mutated tree still
    # resolves the same functions before believing its silence.
    check(not [h for h in gr.hits if "bs_cache_path" in h[3]] and gr.funcs == br.funcs,
          "bootsnap GREEN once bs_cache_path takes VALUE * (generated), and the "
          "mutated tree still resolves all %d functions" % br.funcs,
          [(h[0], h[1], h[2]) for h in gr.hits] + ["funcs %d vs %d" % (gr.funcs, br.funcs)])

    g2 = _mutate(rmagick, [
        ("ext/RMagick/rmutil.cpp",
         "rm_str2cstr(VALUE str, size_t *len)\n{\n    StringValue(str);",
         "rm_str2cstr(VALUE *strp, size_t *len)\n{\n    rb_string_value(strp);\n"
         "    VALUE str = *strp;")])
    g2r, r2r = sweep(Tree(g2), "g2"), sweep(Tree(rmagick), "r")
    check(not [h for h in g2r.hits if "rm_str2cstr" in h[3]]
          and g2r.funcs == r2r.funcs,
          "rmagick GREEN once rm_str2cstr takes VALUE * (generated), and the mutated "
          "tree still resolves all %d functions" % r2r.funcs,
          [(h[0], h[1], h[2]) for h in g2r.hits]
          + ["funcs %d vs %d" % (g2r.funcs, r2r.funcs)])

    # 4. The safe caller-side write-back rmagick already ships is not flagged. Those
    #    lines are `file = rb_rescue(RESCUE_FUNC(rb_String), file, ...)` immediately
    #    before `rm_str2cstr(file, ...)` -- the same fix written at the caller.
    #    (Reported to us as "rmagick's VALUE * idiom"; rmagick has no `VALUE *` parameter
    #    anywhere in its .cpp -- see the report.)
    bad = [h for h in rh if h[1].endswith("rmimage.cpp")
           and h[2] in (11563, 11564, 11565, 11566, 15852, 15853, 15854, 15855)]
    check(not bad, "rmagick rmimage.cpp:11565/:15853 caller write-back NOT flagged", bad)
    rdis = [d for d in sweep(Tree(rmagick), "rmagick").discharges
            if d[1].endswith("rmimage.cpp") and d[2] in (11566, 15855)]
    check(len(rdis) == 2, "...and both are explicitly DISCHARGED, not merely unmatched",
          rdis)

    # 5a. Natural green: json. `str = convert_encoding(str)` re-roots the caller's own
    #     variable, and `VALUE Vsource = convert_encoding(src)` reads Vsource, never src.
    jh = _hits(jsn)
    check(not jh, "json 2.20.0 GREEN (natural)", [(h[0], h[1], h[2]) for h in jh])

    # 5b. ONE-TOKEN mutation of that green: drop the assignment back, keep everything
    #     else. `RSTRING_LEN(str)` on the next line then reads the caller's unconverted
    #     copy. Cheaper than a synthesised shape and it tests the real code path.
    jred = _mutate(jsn, [("ext/json/ext/parser/parser.c",
                          "str = convert_encoding(str);", "convert_encoding(str);")])
    jrh = [h for h in _hits(jred) if h[0] == "CALLER-DEREFS"]
    check(len(jrh) == 1 and "convert_encoding" in jrh[0][3],
          "json flips RED on the one-token deletion of `str = `",
          [(h[0], h[1], h[2], h[3]) for h in jrh])

    # 5c. The body bound is the single largest false-positive source. Reinstating the
    #     fixed ~1500-char window brings mysql2 back: four callers that all `return
    #     _mysql_client_options(...)` immediately, whose windows run past their own
    #     closing brace into set_charset_name's `RSTRING_PTR(value)` -- a DIFFERENT
    #     function's parameter that happens to share the name. Every one of the four
    #     spurious hits is reported at the same line, 1415, which is the tell.
    probes = ("mysql2-", "zlib-", "json-", "trilogy-2.12", "msgpack-")
    windowed, bounded = set(), set()
    for prefix in probes:
        d = _find(pool, prefix)
        if d is None:
            continue
        gem = pathlib.Path(d).name.split("-")[0]
        if _hits(d, window=1500):
            windowed.add(gem)
        if _hits(d):
            bounded.add(gem)
    check("mysql2" in windowed and not bounded,
          "fixed 1500-char window re-burns %s; body-bounding clears them"
          % (sorted(windowed) or "nothing"), "windowed=%s bounded=%s"
          % (sorted(windowed), sorted(bounded)))

    # 5d. Recall audit. Every discharge rule must be load-bearing AND small enough to read
    #     by hand, because all three are path-INSENSITIVE: they do not know that rminfo's
    #     `StringValueCStr(argv[0])` is in a different `switch` case from its
    #     `rm_str2cstr(argv[0], ...)`, or that zlib's `StringValue(src)` is in the other
    #     arm of the `if` from its `do_inflate(z, src)`. Both verdicts happen to be right;
    #     a defect sitting on the else-branch of a conversion would be wrongly cleared.
    #     --no-discharge prints exactly that set, and it has to stay this short.
    suppressed = []
    for prefix in ("json-", "rmagick-", "zlib-", "mysql2-"):
        d = _find(pool, prefix)
        if d is None:
            continue
        on = {(h[0], h[1], h[2]) for h in _hits(d)}
        off = {(h[0], h[1], h[2]) for h in _hits(d, discharge=False)}
        suppressed += sorted(off - on)
    check(len(suppressed) == 3
          and all(s[0] == "CALLER-DEREFS" for s in suppressed)
          and any("parser.c" in s[1] for s in suppressed)
          and sum("rminfo.cpp" in s[1] for s in suppressed) == 2,
          "discharge rules suppress exactly 3 auditable sites (json write-back, "
          "rminfo x2 re-convert)", suppressed)

    # 6. A zero is readable: the coverage line must carry non-zero denominators even on
    #    a gem with no hits, or "0 hits" is indistinguishable from a failed query.
    rj = sweep(Tree(jsn), "json")
    check(rj.files > 0 and rj.funcs > 0 and len(rj.conversions) > 0,
          "coverage counters make json's zero readable "
          "(%d files, %d fns, %d conversions)" % (rj.files, rj.funcs,
                                                  len(rj.conversions)))

    # 9. The two recall gaps Codex found on #28, each as a mutation of a KNOWN RED. A
    #    defect spelled a slightly different way has to stay found; both of these failed
    #    silent, which is the worst direction for a sweep -- the funnel reported the
    #    conversion, found no escape, and printed a clean sheet.

    # 9a. The interior aliased into a local before the return. rm_str2cstr with one more
    #     line, which is a completely ordinary way to write it.
    alias_tree = _mutate(rmagick, [(
        "ext/RMagick/rmutil.cpp",
        "    return RSTRING_PTR(str);\n}",
        "    {\n        char *p_alias_ = RSTRING_PTR(str);\n"
        "        return p_alias_;\n    }\n}")])
    ah = [h for h in _hits(alias_tree) if h[0] == "RETURNS-INTERIOR"
          and "rm_str2cstr" in h[3]]
    check(len(ah) == 1,
          "RED stays red when the interior is aliased into a local before the return",
          [(h[0], h[1], h[2]) for h in _hits(alias_tree)])

    # 9b. The caller re-reads a struct MEMBER rather than a bare local. Requiring a plain
    #     identifier discharged this as "a temporary", but `w->path` is re-readable and is
    #     exactly the CALLER-DEREFS class with a field standing in for the local.
    member_tree = _mutate(bootsnap, [(
        "ext/bootsnap/bootsnap.c",
        "  bs_cache_path(cachedir_v, namespace_v, path_v, &cache_path);\n"
        "\n  return bs_fetch(RSTRING_PTR(path_v), path_v, cache_path, handler, args);",
        "  struct { VALUE p; } holder_;\n  holder_.p = path_v;\n"
        "  bs_cache_path(cachedir_v, namespace_v, holder_.p, &cache_path);\n"
        "\n  return bs_fetch(RSTRING_PTR(holder_.p), path_v, cache_path, handler, args);")])
    mh = [h for h in _hits(member_tree) if h[0] == "CALLER-DEREFS"
          and "bs_cache_path" in h[3]]
    check(any("holder_.p" in str(h[4]) for h in mh) or len(mh) >= 2,
          "a re-readable struct MEMBER argument is scanned, not discharged as a temporary",
          [(h[0], h[1], h[2]) for h in _hits(member_tree)])

    # 10. GENERATED REDS for the six recall gaps found reviewing #28. Every one of them
    #     is corpus-NEUTRAL -- sweeping all 99 trees before and after adds no row and
    #     removes none -- so these synthetics are the only thing standing between a
    #     working fix and a silently reverted one.
    #
    #     Each check asserts the FUNNEL, not just the hit count. The failure mode being
    #     guarded against is green-for-the-wrong-reason: 10b's tree indexed `0 fn(s),
    #     0 conversions` before the fix and read as clean, which is the same clean sheet a
    #     parser regression would print. A row count alone cannot tell those apart.

    def synth(label, files, funcs, conv, noncf):
        """(hits, ok_funnel) after sweeping a synthetic tree with the expected counters."""
        r = sweep(Tree(_synth(label, files)), label)
        return r, (r.funcs == funcs and len(r.conversions) == conv
                   and len(r.non_cfunc) == noncf)

    # 10a. The interior aliased into a local, then stored through an OUT-PARAM. The alias
    #      set was consulted for `return p` and not for `*out = p`, so the out-param
    #      branch looked for an INTERIOR call in the RHS, found `p`, and reported nothing.
    r, fok = synth("t_alias_outparam", {"ext/t.c": """#include <ruby.h>
static VALUE grab(VALUE str, const char **out)
{
    StringValue(str);
    const char *p = RSTRING_PTR(str);
    *out = p;
    return Qnil;
}
static VALUE go(VALUE self, VALUE arg)
{
    const char *sink = 0;
    grab(arg, &sink);
    return rb_str_new2(sink);
}
void Init_t(void) { rb_define_method(rb_cObject, "go", go, 1); }
"""}, funcs=3, conv=1, noncf=1)
    check(fok and [h for h in r.hits if h[0] == "STORES-INTERIOR"],
          "10a RED: an ALIAS of the interior stored through an out-param is an escape",
          "funnel fn=%d conv=%d non-cfunc=%d hits=%s"
          % (r.funcs, len(r.conversions), len(r.non_cfunc),
             [(h[0], h[2]) for h in r.hits]))

    # 10b. C++ scope heads. `namespace` and `extern "C"` put every definition at nonzero
    #      brace depth, so _index_funcs skipped the lot: `0 fn(s)` on a tree whose whole
    #      point is one defect. The funnel assertion is the check here -- the hit count
    #      going from 0 to 1 is the smaller half of it.
    r, fok = synth("t_namespace", {"ext/t.cpp": """#include <ruby.h>
namespace tt {
    static const char *grab(VALUE str)
    {
        StringValue(str);
        return RSTRING_PTR(str);
    }
}
extern "C" {
static VALUE go(VALUE self, VALUE arg)
{
    return rb_str_new2(tt::grab(arg));
}
void Init_t(void) { rb_define_method(rb_cObject, "go", go, 1); }
}
"""}, funcs=3, conv=1, noncf=1)
    check(fok and [h for h in r.hits if h[0] == "RETURNS-INTERIOR"],
          "10b RED: definitions inside `namespace` / `extern \"C\"` are INDEXED "
          "(3 fn, not 0) and their escape found",
          "funnel fn=%d conv=%d non-cfunc=%d hits=%s"
          % (r.funcs, len(r.conversions), len(r.non_cfunc),
             [(h[0], h[2]) for h in r.hits]))

    # 10c. RSTRING_GETMEM hands the interior back through an OUTPUT ARGUMENT, so the
    #      assignment-keyed alias pass never saw `p` at all and `return p` read as clean.
    r, fok = synth("t_getmem", {"ext/t.c": """#include <ruby.h>
static const char *grab(VALUE str)
{
    const char *p;
    long len;
    StringValue(str);
    RSTRING_GETMEM(str, p, len);
    return p;
}
static VALUE go(VALUE self, VALUE arg) { return rb_str_new2(grab(arg)); }
void Init_t(void) { rb_define_method(rb_cObject, "go", go, 1); }
"""}, funcs=3, conv=1, noncf=1)
    check(fok and [h for h in r.hits if h[0] == "RETURNS-INTERIOR"],
          "10c RED: RSTRING_GETMEM's output argument is an alias of the interior",
          "funnel fn=%d conv=%d non-cfunc=%d hits=%s"
          % (r.funcs, len(r.conversions), len(r.non_cfunc),
             [(h[0], h[2]) for h in r.hits]))

    # 10d. Two TUs, two unrelated `static` functions called `collide`. Registering one as
    #      a cfunc added the BARE NAME to a tree-wide set and deleted the other from the
    #      funnel: 1 conversion, 0 non-cfuncs, 0 hits. The green half matters as much --
    #      registering the helper itself must still exclude it, or the fix is just the
    #      exclusion turned off.
    collide_a = """#include <ruby.h>
static VALUE collide(VALUE self, VALUE arg) { return arg; }
void Init_a(void) { rb_define_method(rb_cObject, "collide", collide, 1); }
"""
    collide_b = """#include <ruby.h>
static const char *collide(VALUE str)
{
    StringValue(str);
    return RSTRING_PTR(str);
}
static VALUE go(VALUE self, VALUE arg) { return rb_str_new2(collide(arg)); }
void Init_b(void) { rb_define_method(rb_cObject, "go", go, 1); }
"""
    r, fok = synth("t_collide", {"ext/a.c": collide_a, "ext/b.c": collide_b},
                   funcs=5, conv=1, noncf=1)
    check(fok and [h for h in r.hits if h[0] == "RETURNS-INTERIOR"],
          "10d RED: a same-named `static` helper in another TU is not excluded by the "
          "cfunc registration of its namesake",
          "funnel fn=%d conv=%d non-cfunc=%d hits=%s"
          % (r.funcs, len(r.conversions), len(r.non_cfunc),
             [(h[0], h[2]) for h in r.hits]))
    rg, _ = synth("t_collide_green",
                  {"ext/a.c": collide_a,
                   "ext/b.c": collide_b.replace(
                       'rb_define_method(rb_cObject, "go", go, 1);',
                       'rb_define_method(rb_cObject, "go", go, 1);\n'
                       '  rb_define_method(rb_cObject, "c", collide, 1); ')},
                  funcs=5, conv=1, noncf=0)
    check(not rg.hits and len(rg.conversions) == 1 and not rg.non_cfunc,
          "...and GREEN when THAT definition's own file registers it (exclusion intact)",
          "funnel conv=%d non-cfunc=%d hits=%s"
          % (len(rg.conversions), len(rg.non_cfunc), [(h[0], h[2]) for h in rg.hits]))

    # 10e. The interior stored into PERSISTENT storage -- a file-scope slot and a field of
    #      a file-scope object. Neither lvalue is in ptr_params, so the out-param branch
    #      skipped both and the frame looked like it kept its pointer to itself.
    r, fok = synth("t_static_sink", {"ext/t.c": """#include <ruby.h>
static const char *saved_path;
static struct { const char *p; } g_slot;
static VALUE stash(VALUE str)
{
    StringValue(str);
    saved_path = RSTRING_PTR(str);
    g_slot.p = RSTRING_PTR(str);
    return Qnil;
}
static VALUE go(VALUE self, VALUE arg) { stash(arg); return rb_str_new2(saved_path); }
void Init_t(void) { rb_define_method(rb_cObject, "go", go, 1); }
"""}, funcs=3, conv=1, noncf=1)
    sinks = {h[3].split("into ")[-1] for h in r.hits if h[0] == "STORES-INTERIOR"}
    check(fok and sinks == {"file-scope slot saved_path", "file-scope slot g_slot"},
          "10e RED: a store into a file-scope slot, or a field of one, is an escape",
          "funnel fn=%d conv=%d non-cfunc=%d sinks=%s"
          % (r.funcs, len(r.conversions), len(r.non_cfunc), sorted(sinks)))

    # 10f. A caller conversion that an assignment overwrote before the call. `x = y`
    #      makes everything established about `x` stale, and `y` may be any object; the
    #      discharge fired anyway. The green half pins that rule 1 still works when
    #      nothing intervenes -- otherwise the fix is indistinguishable from deleting it.
    stale = """#include <ruby.h>
static VALUE helper(VALUE x) { StringValue(x); return Qnil; }
static VALUE go(VALUE self, VALUE x, VALUE y)
{
    StringValue(x);
%s    helper(x);
    return rb_str_new2(RSTRING_PTR(x));
}
void Init_t(void) { rb_define_method(rb_cObject, "go", go, 2); }
"""
    r, fok = synth("t_stale_conv", {"ext/t.c": stale % "    x = y;\n"},
                   funcs=3, conv=2, noncf=1)
    check(fok and [h for h in r.hits if h[0] == "CALLER-DEREFS"] and not r.discharges,
          "10f RED: a caller conversion that a later assignment overwrote no longer "
          "discharges",
          "funnel fn=%d conv=%d non-cfunc=%d hits=%s discharges=%s"
          % (r.funcs, len(r.conversions), len(r.non_cfunc),
             [(h[0], h[2]) for h in r.hits], [d[3] for d in r.discharges]))
    rg, _ = synth("t_stale_conv_green", {"ext/t.c": stale % ""}, 3, 2, 1)
    check(not rg.hits and len(rg.discharges) == 1
          and "converted its own copy" in rg.discharges[0][3]
          and len(rg.conversions) == 2,
          "...and GREEN with the assignment removed: the conversion REACHES the call",
          "hits=%s discharges=%s" % ([(h[0], h[2]) for h in rg.hits],
                                     [d[3] for d in rg.discharges]))

    # ---------------------------------------------------------------- round-9 thread
    #
    # 11. CALL RESOLUTION PER TRANSLATION UNIT (:744). `call_sites` located callers by a
    #     tree-wide TEXT SEARCH for the name, so a call in b.c to b.c's OWN `static
    #     helper` was attributed to a.c's same-named converting one and CALLER-DEREFS was
    #     reported against b.c -- a caller that never runs the converting body.
    #
    #     The RED and the GREEN are in ONE fixture on purpose: a.c's caller is a genuine
    #     finding and b.c's is not, so a "fix" that scopes too hard loses the row in a.c
    #     and fails the same check. Funnel asserted, because the shape where the parser
    #     indexes nothing prints the same single hit as a correct run of the wrong half.
    tu_conv = """#include <ruby.h>
static VALUE helper(VALUE x) { StringValue(x); return Qnil; }
static VALUE a_go(VALUE self, VALUE arg)
{
    helper(arg);
    return LONG2NUM(RSTRING_LEN(arg));
}
void Init_a(void) { rb_define_method(rb_cObject, "a_go", a_go, 1); }
"""
    tu_plain = """#include <ruby.h>
static VALUE helper(VALUE x) { return rb_obj_class(x); }
static VALUE b_go(VALUE self, VALUE arg)
{
    helper(arg);
    return LONG2NUM(RSTRING_LEN(arg));
}
void Init_b(void) { rb_define_method(rb_cObject, "b_go", b_go, 1); }
"""
    r, fok = synth("t_tu_callsite", {"ext/a.c": tu_conv, "ext/b.c": tu_plain},
                   funcs=6, conv=1, noncf=1)
    tu_hits = sorted((h[0], h[1]) for h in r.hits)
    check(fok and tu_hits == [("CALLER-DEREFS", "ext/a.c")],
          "11 RED: a call in b.c binds to b.c's own `static helper`, so only a.c's caller "
          "-- which really does run the converting body -- is a CALLER-DEREFS (%d fn, "
          "%d conv)" % (r.funcs, len(r.conversions)), tu_hits)
    # ...and the GREEN half of the same rule: make the converting helper TREE-WIDE and
    # b.c's call is genuinely a call to it, so the second row comes back. Without this a
    # `return []` in call_sites would pass the check above.
    rg, gok = synth("t_tu_callsite_extern",
                    {"ext/a.c": tu_conv.replace("static VALUE helper", "VALUE helper"),
                     "ext/b.c": tu_plain.replace(
                         "static VALUE helper(VALUE x) { return rb_obj_class(x); }\n", "")},
                    funcs=5, conv=1, noncf=1)
    check(gok and sorted((h[0], h[1]) for h in rg.hits)
          == [("CALLER-DEREFS", "ext/a.c"), ("CALLER-DEREFS", "ext/b.c")],
          "...and GREEN tree-wide: with no namesake in b.c and external linkage on the "
          "helper, BOTH callers reach it and both report",
          sorted((h[0], h[1]) for h in rg.hits))

    # ---------------------------------------------------------------- round-9 thread
    #
    # 12. THE FUNCTION INDEX IS tu_scope's NOW (:716's neighbour). This file carried the
    #     last pre-extraction copy of the brace walk and the declarator walk, measured at
    #     23,120 indexed definitions against the 23,318 predicates C and D agreed on over the
    #     same 99 trees. The collapse is corpus-visible in exactly one place -- nio4r 341 ->
    #     416 and sassc 983 -> 1106, and no row moves -- so the red has to be generated, and
    #     it is the same shape both other callers assert.
    def hdr_tree(tag, header):
        return _synth("t_hdr_%s" % tag, {"ext/t.cpp": """#include <ruby.h>
%s
{
    StringValue(str);
    return RSTRING_PTR(str);
}
static VALUE go(VALUE self, VALUE arg) { return rb_str_new2(grab(arg)); }
void Init_t(void) { rb_define_method(rb_cObject, "go", go, 1); }
""" % header})

    hdrs = {
        "plain": "static const char *grab(VALUE str)",
        "attr": "static const char *grab(VALUE str) __attribute__((noinline))",
        "noexcept": "static const char *grab(VALUE str) noexcept",
        "trailing": "static auto grab(VALUE str) -> const char *",
    }
    hd = {t: sweep(Tree(hdr_tree(t, h)), t) for t, h in hdrs.items()}
    base_hd = (hd["plain"].funcs, len(hd["plain"].conversions), len(hd["plain"].non_cfunc),
               sorted(h[0] for h in hd["plain"].hits))
    check(base_hd == (3, 1, 1, ["RETURNS-INTERIOR"])
          and all((hd[t].funcs, len(hd[t].conversions), len(hd[t].non_cfunc),
                   sorted(h[0] for h in hd[t].hits)) == base_hd for t in hdrs),
          "12 RED: `__attribute__((...))`, `noexcept` and a C++ trailing return type between "
          "the `)` and the `{` give the same funnel and the same row as the bare declarator "
          "-- the pre-extraction index skipped whitespace only and reported 0 fn(s), "
          "0 conversions",
          [(t, hd[t].funcs, len(hd[t].conversions), [h[0] for h in hd[t].hits])
           for t in hdrs])
    # ...and the REJECTION TABLE, because opening the crossing up is what once made a sweep
    # invent four functions out of X-macro lists and `__declspec(...)`. tu_scope carries the
    # table; every caller asserts it, and this is now the third.
    rejects = {
        "macro": "MY_EXPORT(sym)\nstatic VALUE bad(VALUE str)\n{\n    return Qnil;\n}\n",
        "knr": "static VALUE bad(str) VALUE str; {\n    return Qnil;\n}\n",
        "proto": "static VALUE helper(VALUE);\n"
                 "static VALUE bad(VALUE str)\n{\n    return Qnil;\n}\n",
        "init": "struct S s = mk(1), t = {2};\n"
                "static VALUE bad(VALUE str)\n{\n    return Qnil;\n}\n",
        "typedef-aggregate": "__declspec(align(8)) typedef struct { int x; } thing_t;\n"
                             "static VALUE bad(VALUE str)\n{\n    return Qnil;\n}\n",
        "x-macro": "XX(A, 1)\ntypedef enum { E_A } phase_t;\n"
                   "static VALUE bad(VALUE str)\n{\n    return Qnil;\n}\n",
    }
    indexed = {t: sorted(f.name for f in Tree(
        _synth("t_rej_%s" % t, {"ext/t.cpp": "#include <ruby.h>\n\n" + src})).funcs)
        for t, src in rejects.items()}
    check(indexed == {"macro": ["bad"], "knr": [], "proto": ["bad"], "init": ["bad"],
                      "typedef-aggregate": ["bad"], "x-macro": ["bad"]},
          "12 GREEN: the open walk still refuses to invent a body -- a macro call, a "
          "prototype, an initialiser list, `__declspec(...) typedef struct` and an X-macro "
          "list each index the ONE real definition, and K&R indexes none (a stated recall "
          "limit, shared with predicates C and D)", indexed)

    # 13. POINTER IDENTITY SURVIVES A LOCAL-TO-LOCAL COPY (:716).
    #
    #     `p = RSTRING_PTR(str); q = p; return q;` seeded only `p`, so `derives("q")` was
    #     false and the escape scan found nothing: one converted non-cfunc, ZERO hits, on the
    #     filed defect written one line longer. Corpus-neutral, so this synthetic is the only
    #     thing between a working fix and a silently reverted one, and the funnel is asserted
    #     because the shape being guarded against prints `0 fn(s), 0 conversions`.
    #
    #     THE COPY IS THE FLAG. The `direct` arm returns `p` and must give the same row; if
    #     the two disagree the check is measuring the conversion walk rather than the copy.
    chain = """#include <ruby.h>
static const char *grab(VALUE str, const char **out)
{
    StringValue(str);
    const char *p = RSTRING_PTR(str);
    const char *q;
    const char *r = 0;
%s    return %s;
}
static VALUE go(VALUE self, VALUE arg)
{
    const char *sink = 0;
    const char *got = grab(arg, &sink);
    return rb_str_new2(got ? got : sink);
}
void Init_t(void) { rb_define_method(rb_cObject, "go", go, 1); }
"""
    ch_arms = {
        # the copy, then the return through it
        "copy": ("    q = p;\n", "q"),
        # the same defect written directly -- the row this one must match
        "direct": ("    q = p;\n", "p"),
        # the copy, then the store through it into the caller's out-parameter
        "store": ("    q = p;\n    *out = q;\n", "0"),
        # ORDERING: `r = q` runs BEFORE `q = p`, so `r` never held the buffer and the
        # return through it is not an escape. Without this the fix is "any assignment
        # anywhere makes an alias".
        "before": ("    r = q;\n    q = p;\n", "r"),
    }
    ch = {}
    for tag, (mid, ret) in ch_arms.items():
        r = sweep(Tree(_synth("t_chain_%s" % tag, {"ext/t.c": chain % (mid, ret)})), tag)
        ch[tag] = (r.funcs, len(r.conversions), len(r.non_cfunc),
                   sorted(h[0] for h in r.hits))
    check(ch["direct"] == (3, 1, 1, ["RETURNS-INTERIOR"])
          and ch["copy"] == ch["direct"],
          "13 RED: `q = p; return q;` is the same row as `return p;` -- unfixed the alias "
          "set held only `p` and the tree reported 1 conversion, 1 non-cfunc and 0 hits",
          "copy %s vs direct %s" % (ch["copy"], ch["direct"]))
    check(ch["store"] == (3, 1, 1, ["STORES-INTERIOR"]),
          "13 RED, second sink: the copy stored through an out-parameter is a "
          "STORES-INTERIOR -- and the out-param is NOT swallowed into the alias set, which "
          "would have deleted the row instead of adding one", "%s" % (ch["store"],))
    check(ch["before"] == (3, 1, 1, []),
          "13 GREEN: `r = q;` running BEFORE `q = p;` does not make `r` an alias -- the "
          "propagation is in offset order, not a name soup", "%s" % (ch["before"],))
    #     ...and the `exclude` argument, which was inert in this suite AND over the whole
    #     corpus until this fixture existed. `*out = p` is a copy by every test the
    #     propagation applies -- pointer-typed left-hand side, one bare name on the right --
    #     so without the exclusion the SINK joins the alias set and every later mention of
    #     `out` reads as the buffer, including `out[1]`, which is a different element of the
    #     caller's array. One row becomes two.
    r = sweep(Tree(_synth("t_sink_not_alias", {"ext/t.c": """#include <ruby.h>
static const char *grab(VALUE str, const char **out)
{
    StringValue(str);
    const char *p = RSTRING_PTR(str);
    *out = p;
    return out[1];
}
static VALUE go(VALUE self, VALUE arg)
{
    const char *sink[2] = {0, 0};
    return rb_str_new2(grab(arg, sink));
}
void Init_t(void) { rb_define_method(rb_cObject, "go", go, 1); }
"""})), "t_sink_not_alias")
    check((r.funcs, len(r.conversions), len(r.non_cfunc),
           sorted(h[0] for h in r.hits)) == (3, 1, 1, ["STORES-INTERIOR"]),
          "13 GREEN, the exclusion: an out-parameter is a SINK and does not become an alias "
          "of what was stored through it -- `return out[1]` is not the buffer",
          "funnel fn=%d conv=%d non-cfunc=%d hits=%s"
          % (r.funcs, len(r.conversions), len(r.non_cfunc), sorted(h[0] for h in r.hits)))

    # ---------------------------------------------------------- round-9 follow-up thread
    #
    # 14. AN ALIAS IS NOT AN ALIAS FOR EVER (#29 item 1). The propagation added as item 13
    #     tracked copies and never KILLS, so a local stayed a carrier after it had been
    #     overwritten and this predicate reported RETURNS-INTERIOR on a string literal --
    #     a FALSE POSITIVE in the code item 13 had just landed. Four arms, because the
    #     fix has to kill without unlearning:
    #
    #       kill        the defect: `p = "safe"; return p;`  -- must NOT report
    #       live        the same function without the kill   -- must still report
    #       kill-copy   `p = "safe"; q = p; return q;`       -- the copy carries the
    #                                                           literal, not the interior
    #       cond-kill   `if (out) { p = "safe"; } return p;` -- a write that need not run
    #                                                           must NOT discharge; this is
    #                                                           the whole reason the kill
    #                                                           mode is DOMINATING_WRITE and
    #                                                           not source_reads' default
    #       late-kill   `q = p; p = "safe"; return q;`       -- `q` was already a carrier
    #
    #     AND THE THIRD HOLE, FOUND BY CODEX ON THE #29 PR ITSELF: a write whose right-hand
    #     side still READS the name stores the pointer back, so it must not kill. The other
    #     two holes (`conditional_stmt`, `straight_line`) ask whether the write RUNS; this
    #     one asks what it STORES, and a pointer walk is the commonest thing C does to an
    #     interior pointer:
    #
    #       walk        `p = p + 1; return p;`                -- must STILL report
    #       walk-call   `p = strchr(p, 47); return p;`        -- ditto, spelled as a call
    #
    #     Measured against `54fc3f2`: rmagick's `rm_str2cstr` with one walk inserted is RED
    #     before the alias kill and CLEAN after it, which is a real defect going silent. The
    #     `kill` arm is what keeps this from being a way to switch the kill off -- it shares
    #     every line with `walk` except the right-hand side.
    #
    #     AND THE FOURTH, ALSO FROM THE #30 REVIEW: a write guarded by a conditional
    #     OPERATOR rather than a conditional STATEMENT. There is no `if` here at all, so the
    #     block test, `straight_line` and the braceless-arm test all agreed it dominates:
    #
    #       and-write   `out && (p = "safe"); return p;`      -- must STILL report
    #       or-write    `out || (p = "safe"); return p;`      -- ditto
    #       tern-write  `out ? (p = "safe") : 0; return p;`   -- ditto
    #
    #     On the path where the guard is false `p` still carries the interior, and both
    #     spellings silently dropped rmagick's row when measured. `kill` remains the arm
    #     that proves the kill is narrowed rather than switched off.
    #
    #     AND TWO MORE FROM THE SAME REVIEW, both measured on rmagick before being fixed:
    #
    #       restore     `q = p; p = "safe"; p = q; return p;`  -- a carrier can be RESTORED
    #                   after it is killed. One `since` per name cannot say so, so
    #                   alias_reads() unions a second read set from the restoring copy
    #                   rather than moving the seed; see its docstring for why additive.
    #       restore-copy `... p = q; r = p; return r;`          -- and the restoration has
    #                   to PROPAGATE. The first cut of that pass only unioned the restored
    #                   name's own reads, so a fresh local copied from the restored carrier
    #                   never entered the alias set at all and the return was invisible.
    #                   This arm returns `r`, not `p`, which is the whole point of it.
    #       qual-write  `Holder::p = "safe"; return p;`        -- a QUALIFIED member is not
    #                   this local. The third spelling of the `->`/`.` rule writes() already
    #                   carries, and the one character that was missing from it.
    #
    #     AND ONE MORE, WHOSE CONTROL NAMED ITS OWN CAUSE:
    #
    #       for-body    `for (n = 0; n < 3; n++) p = "safe"; return p;`  -- must STILL report
    #       while-body  `while (out) p = "safe"; return p;`              -- ditto, and this
    #                   one was ALREADY right
    #
    #     The `for` header holds two semicolons that are not statement separators, so the
    #     statement-boundary scan took the last of them and measured the head as `n++)`
    #     rather than `for (n = 0; n < 3; n++)`. `while`, whose head has no semicolon at
    #     all, was correct the whole time -- the pair is what identified the cause rather
    #     than merely the symptom, and it is why both spellings are kept here.
    #
    #     THE FUNNEL IS ASSERTED IN EVERY ARM. A regression that empties the index prints
    #     `0 fn(s), 0 conversions` and would otherwise read as four passing greens.
    kill_src = """#include <ruby.h>
static const char *grab(VALUE str, const char **out)
{
    StringValue(str);
    const char *p = RSTRING_PTR(str);
    const char *q;
    const char *r;
%s    return %s;
}
static VALUE go(VALUE self, VALUE arg)
{
    const char *sink = 0;
    return rb_str_new2(grab(arg, &sink));
}
void Init_t(void) { rb_define_method(rb_cObject, "go", go, 1); }
"""
    kill_arms = {
        "live":      ("", "p", ["RETURNS-INTERIOR"]),
        "kill":      ('    p = "safe";\n', "p", []),
        "kill-copy": ('    p = "safe";\n    q = p;\n', "q", []),
        "cond-kill": ('    if (out) { p = "safe"; }\n', "p", ["RETURNS-INTERIOR"]),
        "late-kill": ('    q = p;\n    p = "safe";\n', "q", ["RETURNS-INTERIOR"]),
        "walk":      ('    p = p + 1;\n', "p", ["RETURNS-INTERIOR"]),
        "walk-call": ('    p = strchr(p, 47);\n', "p", ["RETURNS-INTERIOR"]),
        "and-write": ('    out && (p = "safe");\n', "p", ["RETURNS-INTERIOR"]),
        "or-write":  ('    out || (p = "safe");\n', "p", ["RETURNS-INTERIOR"]),
        "tern-write":('    out ? (p = "safe") : 0;\n', "p", ["RETURNS-INTERIOR"]),
        "restore":   ('    q = p;\n    p = "safe";\n    p = q;\n', "p",
                      ["RETURNS-INTERIOR"]),
        "qual-write":('    Holder::p = "safe";\n', "p", ["RETURNS-INTERIOR"]),
        "for-body":  ('    for (n = 0; n < 3; n++) p = "safe";\n', "p",
                      ["RETURNS-INTERIOR"]),
        "while-body":('    while (out) p = "safe";\n', "p", ["RETURNS-INTERIOR"]),
        "constexpr": ('    if constexpr (0) p = "safe";\n', "p", ["RETURNS-INTERIOR"]),
        "restore-copy": ('    q = p;\n    p = "safe";\n    p = q;\n    r = p;\n', "r",
                         ["RETURNS-INTERIOR"]),
    }
    kr = {}
    for tag, (mid, ret, _want) in kill_arms.items():
        r = sweep(Tree(_synth("t_kill_%s" % tag, {"ext/t.c": kill_src % (mid, ret)})), tag)
        kr[tag] = (r.funcs, len(r.conversions), len(r.non_cfunc),
                   sorted(h[0] for h in r.hits))
    check(all(kr[t] == (3, 1, 1, want) for t, (_m, _r, want) in kill_arms.items()),
          "14 RED (#29 item 1): a reassigned alias stops carrying the interior -- "
          "`p = \"safe\"; return p;` is not RETURNS-INTERIOR, while the same function "
          "without the reassignment still is, a conditional reassignment still is, and a "
          "write that re-derives from the name (`p = p + 1`) still is",
          kr)

    def _index_names(src):
        return {f.name for f in Tree(_synth("t_conform", {"ext/t.cpp": src})).funcs}


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
          "#29 item 2: predicate B's function index conforms to tu_scope's declarator table "
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
                    help="print every discharged call site and why (audit the recall)")
    ap.add_argument("--window", type=int, default=None,
                    help="DELIBERATELY BROKEN: bound the caller scan by N chars instead "
                         "of by the caller's body. Kept only so --self-test can show the "
                         "false positives it produces.")
    ap.add_argument("--no-discharge", action="store_true",
                    help="turn every discharge rule off. Recall audit: whatever appears "
                         "here and not in a normal run is exactly what the rules suppress, "
                         "and each one has to be justified by name.")
    ap.add_argument("--self-test", action="store_true",
                    help="run acceptance against the gem trees named in dirs, and exit")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(self_test(a.dirs))
    f, subs = [0] * 7, {}
    for d in a.dirs:
        root = pathlib.Path(d)
        r = sweep(Tree(root), root.name, a.window, not a.no_discharge)
        report(r, verbose=a.verbose)
        for i, v in enumerate((r.files, r.funcs, len(r.conversions), r.conv_fns,
                               len(r.non_cfunc), r.non_cfunc_fns, len(r.hits))):
            f[i] += v
        for h in r.hits:
            subs.setdefault(h[0], []).append("%s %s:%d" % (r.name, h[1], h[2]))
    print("\nFUNNEL over %d tree(s), %d C file(s), %d function(s):\n"
          "  by-value VALUE params converted in place ..... %3d param(s) in %3d fn(s)\n"
          "  after excluding cfunc entry points ............ %3d param(s) in %3d fn(s)\n"
          "  escaping the frame (HITS) ..................... %3d site(s)"
          % (len(a.dirs), f[0], f[1], f[2], f[3], f[4], f[5], f[6]))
    for sub in ("RETURNS-INTERIOR", "STORES-INTERIOR", "CALLER-DEREFS"):
        for where in subs.get(sub, []):
            print("      %-17s %s" % (sub, where))
    if a.window:
        print("  NOTE: --window %d is the DELIBERATELY BROKEN caller scan. Any hit above "
              "may be\n        a fixed-window artifact; re-run without it." % a.window)
    if a.no_discharge:
        print("  NOTE: --no-discharge is the recall audit, not a verdict. Every hit above "
              "that is\n        absent from a normal run is a suppression to justify by "
              "name, not a finding.")


if __name__ == "__main__":
    main()

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

  RETURNS-INTERIOR  the helper returns (or stores through an out-param) `RSTRING_PTR` of
                    its own converted local.  rmagick `rm_str2cstr` -- rmagick/rmagick#1846
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

THREE THINGS THAT ARE NOT TUNING KNOBS
--------------------------------------
* **`argv[i]` is the canonical RED shape, not a discharge.** rmagick#1846 is filed on
  exactly `rm_str2cstr(argv[0], &format_l)`. The VM stack pins what `argv[i]` holds, which
  is the *original* object -- never the String the callee coerced. An early cut of this had
  it backwards and discharged the filed bug.
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

ACCEPTANCE (--self-test): see self_test(). Run it before trusting any result from this
script -- silence is a property of the query until the counts say otherwise.
"""
import argparse
import pathlib
import re
import shutil
import sys
import tempfile

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


class Func:
    __slots__ = ("name", "path", "src", "params", "hdr", "bstart", "bend")

    def __init__(self, name, path, src, params, hdr, bstart, bend):
        self.name = name
        self.path = path
        self.src = src            # the whole stripped file text
        self.params = params      # [(decl, name)] in order
        self.hdr = hdr            # offset of the function name
        self.bstart = bstart      # offset just past `{`
        self.bend = bend          # offset of the matching `}`

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
        self.cfuncs = set()
        for path, src in self.files.items():
            self._index_funcs(path, src)
            self._index_cfuncs(src)
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
        """
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
            if close < 0:
                continue
            params = [(a, param_name(a)) for a in args
                      if a.strip() and a.strip() not in ("void", "...")]
            self.funcs.append(Func(m.group(1), path, src, params, m.start(), k + 1, close))

    def _index_cfuncs(self, src):
        for name, args, _s, _e in find_calls(src):
            if DEFINE_RE.match(name):
                self.cfuncs.update(re.findall(r"[A-Za-z_]\w*", " ".join(args)))

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


def escapes_by_return(fn, param):
    """[(kind, offset, text)] -- the converted local's interior leaving the frame."""
    out, body = fn.body, fn.body
    found = []
    ptr_params = {nm for decl, nm in fn.params
                  if nm and ("*" in decl or "[" in decl) and nm != param}
    for m in re.finditer(r"\breturn\b", body):
        semi = body.find(";", m.end())
        if semi < 0:
            continue
        expr = body[m.end():semi]
        for name, args, s, _e in find_calls(expr):
            if name in INTERIOR and param in re.findall(r"[A-Za-z_]\w*", " ".join(args)):
                found.append(("RETURNS-INTERIOR", fn.bstart + m.start(),
                              body[m.start():semi + 1].strip()))
                break
    # store through an out-parameter: `*out = RSTRING_PTR(str)`, `out->f = ...`,
    # `out[i] = ...`. The caller owns that memory, so the pointer outlives this frame
    # exactly as a return value would.
    for m in re.finditer(r"(?<![=!<>])=(?!=)", body):
        lhs = body[max(0, m.start() - 200):m.start()]
        stmt = lhs[lhs.rfind(";") + 1:].strip()
        stmt = stmt[stmt.rfind("{") + 1:].strip()
        base = re.match(r"^\*?\s*([A-Za-z_]\w*)\s*(->|\[|\.|$)", stmt)
        if not base or base.group(1) not in ptr_params:
            continue
        if not (stmt.startswith("*") or base.group(2) in ("->", "[")):
            continue
        semi = body.find(";", m.end())
        if semi < 0:
            continue
        rhs = body[m.end():semi]
        for name, args, _s, _e in find_calls(rhs):
            if name in INTERIOR and param in re.findall(r"[A-Za-z_]\w*", " ".join(args)):
                found.append(("STORES-INTERIOR", fn.bstart + m.start(),
                              (stmt + " =" + rhs).strip()))
                break
    return found


# ------------------------------------------------------- stage 3b: escape by caller deref


def call_sites(tree, fn):
    """[(caller, arg_list, offset_of_call, offset_past_call)] for in-tree calls of `fn`."""
    out = []
    for path, src in tree.files.items():
        for m in re.finditer(r"\b%s\s*(?=\()" % re.escape(fn.name), src):
            caller = tree.enclosing(path, m.start())
            if caller is None or caller is fn:
                continue            # a prototype, the definition header, or self-recursion
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
    """
    if not re.fullmatch(r"[A-Za-z_]\w*", arg):
        return None
    before = caller.body[:upto]
    for name, args, _s, _e in find_calls(before):
        if not args:
            continue
        a0 = args[0].strip()
        if (name in LVALUE_CONV and a0 == arg) or \
           (name in ADDR_CONV and a0 == "&" + arg):
            return "caller converted its own copy (%s)" % name
        if name in ("Check_Type", "rb_check_type") and a0 == arg and \
                len(args) > 1 and "T_STRING" in args[1]:
            return "caller Check_Type'd its own copy"
    last = None
    for m in re.finditer(r"\b%s\s*=(?!=)" % re.escape(arg), before):
        last = m
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
        if not re.fullmatch(r"[A-Za-z_]\w*(\s*\[[^\]]*\])?", arg):
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
            if fn.name in tree.cfuncs:
                continue
            r.non_cfunc.append((fn, idx, param, macro, off))

    for fn, idx, param, macro, off in r.non_cfunc:
        rel = str(fn.path.relative_to(tree.root))
        # -- 3a: the interior leaves the frame by return or out-param
        for kind, eoff, text in escapes_by_return(fn, param):
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
                   else "stores its interior through an out-param"),
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

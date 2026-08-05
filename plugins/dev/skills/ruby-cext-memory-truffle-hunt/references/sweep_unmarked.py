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

ROUND 5: THREE OVER-CLEARS THIS SCRIPT SHIPPED WITH
---------------------------------------------------
All three made a broken struct read as safe, which is the one failure mode the effort
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

ACCEPTANCE (--self-test): flags fieldTypes on mysql2 m2-red and not on m2-green; clears
all six VALUE fields of sqlite3 pr-723's struct and flags whichever one a mutated tree
stops marking.

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

Then: python3 sweep_unmarked.py --self-test acceptance   (expects 5/5 PASS)

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

C_EXT = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".rs")

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
    """[(name, [args])] for every call in a body, nested calls included."""
    out = []
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*(?=\()", body):
        if m.group(1) in NOT_CALLS:
            continue
        args, _ = call_args(body, m.end())
        if args:
            out.append((m.group(1), args))
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
        self.funcs = {}          # function name -> body text
        self.type_of_dtype = {}  # rb_data_type_t name -> wrapped struct type name
        self.wrap_sites = []     # (path, dtype, struct_type, macro)
        self._helper_memo = {}   # ("dmark"|"dcompact") -> {fn -> kind}, with cycle guard
        for path, src in self.files.items():
            self._index_structs(path, src)
            self._index_aliases(src)
            self._index_funcs(src)
            self._index_dtypes(src)
        for path, src in self.files.items():
            self._index_wraps(path, src)

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
            _, j = call_args(src, m.end() - 1)
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

    def body_of(self, name):
        """The body of an in-tree callee, function or function-like macro."""
        return self.funcs.get(name, self.macros.get(name))

    # -- structs ------------------------------------------------------------

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

    def _index_funcs(self, src):
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
        for name, _args in find_calls(self.body_of(fn) or ""):
            k = loc_kind(name) if key == "dcompact" else prim_kind(name)
            if k is None and depth > 0 and name != fn and self.body_of(name) is not None:
                k = self.helper_kind(name, key, depth - 1)
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
        for name, args in find_calls(body):
            kind = loc_kind(name) if key == "dcompact" else prim_kind(name)
            if kind:
                for tok in arg_tokens(args):
                    direct[tok] = stronger(direct.get(tok), kind)
            elif depth > 0 and name != fn and self.body_of(name) is not None:
                hk = self.helper_kind(name, key)
                if hk:
                    for tok in arg_tokens(args):
                        helper[tok] = stronger(helper.get(tok), hk)
                self._collect_marks(name, key, direct, helper, mentioned, depth - 1)

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
    """VALUE fields of a struct body, as (name, is_pointer)."""
    fields = []
    # Nested anonymous struct/union bodies are part of the same allocation, so scan whole.
    for m in FIELD.finditer(body):
        for decl in split_args(m.group(1)):
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


def report(name, tree, suspects, clears, verbose):
    for cat, path, st, field, dtype, dt in suspects:
        print("%-10s %s: struct %s: VALUE %s  (%s: dmark=%s dcompact=%s)"
              % (cat, path, st, field, dtype,
                 dt.get("dmark", "-"), dt.get("dcompact", "-")))
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
    print("%s: %d suspect(s), %d field(s) cleared "
          "[%d wrap site(s), %d dtype(s), %d unresolved]"
          % (name, len(suspects), len([c for c in clears if c[2] != "-"]),
             len(tree.wrap_sites), len(tree.dtypes), unresolved),
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


def self_test(base):
    """Fail loudly rather than let a broken query clear the corpus by accident."""
    base = pathlib.Path(base)
    ok = True

    def fields_flagged(tree_dir):
        s, _ = sweep(Tree(tree_dir))
        return {(st, f) for _, _, st, f, _, _ in s}

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
        ok &= (want == got)
        print("%s %s" % ("PASS" if want == got else "FAIL", label))
    if red - green != {("mysql2_result_wrapper", "fieldTypes")}:
        ok = False
        print("FAIL red/green differ by more than fieldTypes: %s" % (red - green))
    else:
        print("PASS red/green differ by exactly fieldTypes")

    # sqlite3 PR #723: all six VALUE fields marked, three of them via helpers.
    s3 = base / "sqlite3-pr723" / "ext"
    got = fields_flagged(s3)
    six = {"busy_handler", "functions", "collations", "aggregators",
           "trace_handler", "authorizer"}
    leaked = {f for st, f in got if f in six}
    ok &= not leaked
    print("%s sqlite3 pr-723 clears all six (%s)"
          % ("PASS" if not leaked else "FAIL", sorted(leaked) or "clean"))

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
        hit = "authorizer" in mgot
        ok &= hit
        print("%s de-marked sqlite3 tree flags authorizer" % ("PASS" if hit else "FAIL"))

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
    ):
        cats, fields = flagged_from_source(src)
        hit = want_cat in cats and want_field in fields
        ok &= hit
        print("%s red %s%s" % ("PASS" if hit else "FAIL", label,
                              "" if hit else "  [got %s %s]" % (sorted(cats), sorted(fields))))

    cats, fields = flagged_from_source(GREEN_MACRO)
    clean = not fields
    ok &= clean
    print("%s green (macro) a gem's own gc_location #define counts as an update%s"
          % ("PASS" if clean else "FAIL", "" if clean else "  [got %s]" % sorted(fields)))

    print("\nself-test: %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="*")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every cleared field and why (audit the recall)")
    ap.add_argument("--self-test", metavar="ACCEPTANCE_DIR",
                    help="run the acceptance test and exit")
    a = ap.parse_args()
    if a.self_test:
        sys.exit(self_test(a.self_test))
    total = 0
    for d in a.dirs:
        tree = Tree(d)
        s, c = sweep(tree)
        total += report(pathlib.Path(d).name, tree, s, c, a.verbose)
    print("\n%d suspect field(s) across %d tree(s)" % (total, len(a.dirs)),
          file=sys.stderr)


if __name__ == "__main__":
    main()

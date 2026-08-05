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
  NO-COMPACT field marked movable, absent from dcompact -- stale after compaction only
             (openssl#1088 shape)
  VALUE*     a VALUE array/pointer field; needs a marking loop, check the bound by hand

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


def strip_noise(src):
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        if two == "/*":
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(" " * (j - i))
            i = j
        elif two == "//":
            j = src.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            # Keep the quotes so `"name"` in an rb_data_type_t still parses as a token.
            out.append(c + " " * (j - i - 2) + c if j - i >= 2 else " " * (j - i))
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


# ---------------------------------------------------------------- tree model


class Tree:
    """One gem's C sources, indexed for cross-file resolution."""

    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.files = {}
        for p in sorted(self.root.rglob("*")):
            if p.is_file() and p.suffix in C_EXT and ".git" not in p.parts:
                try:
                    self.files[p] = strip_directives(
                        strip_noise(p.read_text(errors="replace")))
                except OSError:
                    pass
        self.all = "\n".join(self.files.values())
        self.structs = {}        # name -> body text
        self.struct_file = {}    # name -> path (for reporting)
        self.aliases = {}        # typedef name -> underlying name
        self.dtypes = {}         # rb_data_type_t name -> {"dmark":fn, "dcompact":fn, ...}
        self.funcs = {}          # function name -> body text
        self.type_of_dtype = {}  # rb_data_type_t name -> wrapped struct type name
        self.wrap_sites = []     # (path, dtype, struct_type, macro)
        for path, src in self.files.items():
            self._index_structs(path, src)
            self._index_aliases(src)
            self._index_funcs(src)
            self._index_dtypes(src)
        for path, src in self.files.items():
            self._index_wraps(path, src)

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
                dtype = base_type(args[di])
                st = type_name(args[ti]) if ti is not None else None
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

    def mark_text(self, dtype):
        """Bodies of dmark+dcompact, plus one level of in-tree callees.

        One level, not zero: sqlite3 PR #723 marks three of its six fields through
        helpers (`rb_sqlite3_pin_array_and_contents(c->functions)`). Those name the field
        in the dmark body itself, but a helper taking the whole struct would not, so the
        callee bodies are folded in. Transitivity only ever CLEARS, which is why each
        clear is printed with the reason rather than swallowed.
        """
        out = {}
        for key in ("dmark", "dcompact"):
            fn = self.dtypes.get(dtype, {}).get(key)
            if not fn or fn in ("NULL", "0", "RUBY_DEFAULT_FREE"):
                out[key] = ""
                continue
            body = self.funcs.get(fn, "")
            extra = []
            for cm in re.finditer(r"\b([A-Za-z_]\w*)\s*\(", body):
                callee = cm.group(1)
                if callee != fn and callee in self.funcs:
                    extra.append(self.funcs[callee])
            out[key] = body + "\n" + "\n".join(extra)
        return out


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
    seen, reported = set(), set()
    # Typed dtypes first: a gem carrying both `TypedData_Make_Struct` and a legacy
    # `Data_Make_Struct` under an #ifdef wraps the same struct twice, and the legacy
    # pseudo-dtype has no dcompact by construction -- reporting it too would double every
    # line and invent a NO-COMPACT on a gem that has one.
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
        marks = tree.mark_text(dtype)
        decl_in = tree.struct_file.get(st, path)
        for field, is_ptr in value_fields(tree.structs[st]):
            if (st, field) in reported:
                continue
            word = re.compile(r"\b%s\b" % re.escape(field))
            in_mark = bool(word.search(marks["dmark"]))
            in_compact = bool(word.search(marks["dcompact"]))
            cat = None
            if not in_mark:
                cat = "UNMARKED"
            elif is_ptr:
                cat = "VALUE*"
            elif re.search(r"rb_gc_mark_movable\s*\([^;]*\b%s\b" % re.escape(field),
                           marks["dmark"]) and not in_compact:
                cat = "NO-COMPACT"
            if cat:
                reported.add((st, field))
                suspects.append((cat, decl_in, st, field, dtype,
                                 tree.dtypes.get(dtype, {})))
            else:
                reported.add((st, field))
                clears.append((dtype, st, field,
                               "named in dmark" + ("+dcompact" if in_compact else "")))
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


def self_test(base):
    """Fail loudly rather than let a broken query clear the corpus by accident."""
    base = pathlib.Path(base)
    ok = True

    def fields_flagged(tree_dir):
        s, _ = sweep(Tree(tree_dir))
        return {(st, f) for _, _, st, f, _, _ in s}

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

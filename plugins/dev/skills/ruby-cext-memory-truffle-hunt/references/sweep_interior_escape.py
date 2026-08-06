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

    guarded            RB_GC_GUARD(src) at or after the last deref
    no-window          nothing between derive and last deref that can trigger GC,
                       and the pointer does not leave the frame
    last-use-after     the source VALUE is used again at or after the last deref.
                       RECALL-BIASED BY CONSTRUCTION: the compiler may drop the VALUE
                       *before* its last syntactic use -- that is RB_GC_GUARD's own
                       documented rationale (include/ruby/internal/memory.h). This rule
                       therefore over-clears, and it is kept only because without it
                       every correct in-call use in the corpus reports. Round 6's okra
                       finding is the proof it can be wrong: `x19` held the VALUE up to
                       the pointer load and was then overwritten by the GumboOutput*.
    copies-immediately the derive feeds a copying call with no window in between

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

ACCEPTANCE (--self-test): see self_test(). Twelve positive controls, thirteen negative
controls, a per-rule mutation table, and generated reds rather than a green-only suite.
Run it before trusting any result -- silence is a property of the query until the counts
say otherwise.
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
COPIES = re.compile(r"^(memcpy|memmove|strn?cpy|strn?cat|strn?dup|snprintf|vsnprintf"
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
        for path, src in self.files.items():
            self._index_funcs(path, src)
            self._index_cfuncs(src)
        for f in self.funcs:
            self.by_name.setdefault(f.name, []).append(f)
        self.ranges = {}
        for f in self.funcs:
            self.ranges.setdefault(f.path, []).append(f)
        for v in self.ranges.values():
            v.sort(key=lambda f: (f.bstart, -f.bend))

    def _index_funcs(self, path, src):
        """Top-level definitions only.

        The depth check is not cosmetic: a macro invocation followed by a block --
        `RB_VM_LOCK_ENTER() { ... }` -- parses as a definition nested inside a real
        function, and the innermost-frame lookup would then bound a scan by the macro's
        block instead of the function's body. That is a false NEGATIVE generator.
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


def statement_before(body, rel):
    """The text of the partial statement ending at `rel`. Used to find an assignment."""
    lhs = body[max(0, rel - 400):rel]
    for cut in (";", "{", "}", ","):
        lhs = lhs[lhs.rfind(cut) + 1:]
    return lhs


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
    """
    if is_indexed(fn, deriv_off, macro):
        return None
    stmt = statement_before(fn.body, deriv_off - fn.bstart).replace("\n", " ")
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


def escapes(fn, deriv_off, expr, alias, cons, tree=None):
    """[(kind, off, text, extra)] -- ways the pointer outlives or leaves this frame."""
    body, found = fn.body, []
    rel = deriv_off - fn.bstart
    ptrp = fn.ptr_params()

    def carries(text):
        # Pointer DIFFERENCE is an offset, not a pointer. stringio's
        # `ptr->pos = e - RSTRING_PTR(ptr->string)` stores a long; reading it as a stored
        # interior pointer flagged the one gem in the corpus that is safe by design.
        stripped = text.replace("->", "")
        if "-" in stripped:
            return False
        if alias and re.search(r"\b%s\b" % re.escape(alias), text):
            return True
        return any(nm in INTERIOR for nm, _a, _s, _e in find_calls(text))

    # 1. returned -- either directly, or via the local it was aliased into.
    for m in re.finditer(r"\breturn\b", body):
        if m.start() < rel - 200:
            continue
        semi = body.find(";", m.end())
        if semi < 0:
            continue
        if carries(body[m.end():semi]):
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
        b = re.match(r"^\*?\s*([A-Za-z_]\w*)\s*(->|\[|\.|$)", stmt)
        if not b:
            continue
        semi = body.find(";", m.end())
        if semi < 0:
            continue
        rhs = body[m.end():semi]
        if not carries(rhs):
            continue
        if b.group(1) == alias:
            continue                      # the aliasing assignment itself
        if b.group(1) in ptrp and (stmt.startswith("*") or b.group(2) in ("->", "[")):
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
        if (alias and re.search(r"\b%s\b" % re.escape(alias), joined)) or \
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
            idx = next((i for i, a in enumerate(cons[1])
                        if any(d + "(" in re.sub(r"\s+", "", a) for d in INTERIOR)
                        or (alias and re.search(r"\b%s\b" % re.escape(alias), a))), None)
            for callee in tree.by_name[cons[0]]:
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
    if alias:
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


def window_between(fn, lo, hi):
    """[(kind, name, off)] for every window call strictly between two body offsets.

    ORDERED, unlike predicate B, which is explicitly path-insensitive. The blind spot is
    inherited and stated in the docstring: there is no CFG, so a window on a branch the
    deref cannot reach still counts, and a defect on the else-branch of a conversion is
    still wrongly cleared.
    """
    body = fn.body
    a, b = lo - fn.bstart, hi - fn.bstart
    if b <= a:
        return []
    out = []
    for name, args, s, _e in find_calls(body[a:b]):
        kind = classify_window(name, args)
        if kind:
            out.append((kind, name, fn.bstart + a + s))
    return out


def last_use(fn, deriv_off, alias):
    """Offset of the last use of the derived pointer in this frame, or None.

    Only the ALIAS is tracked. A bare `foo(RSTRING_PTR(s))` has no name for the pointer,
    so it cannot be used a second time: its only use is the consumer, in the same
    statement, with no room for a window. That is the difference between the two shapes
    and it is why this returns None rather than scanning forward.
    """
    if not alias:
        return None
    body = fn.body
    rel = deriv_off - fn.bstart
    last = None
    for m in re.finditer(r"\b%s\b" % re.escape(alias), body):
        if m.start() > rel:
            last = fn.bstart + m.start()
    return last


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
    guardable = {var}
    for m in re.finditer(r"\b([A-Za-z_]\w*)\s*=(?!=)\s*%s\s*;" % re.escape(var), body):
        guardable.add(m.group(1))
    for m in re.finditer(r"\bRB_GC_GUARD\s*\(\s*([A-Za-z_]\w*)\s*\)", body):
        if m.group(1) in guardable and m.start() >= rel:
            return "GUARDED", ("RB_GC_GUARD(%s) after the derive" % m.group(1)
                               + ("" if m.group(1) == var
                                  else " (a copy of %s)" % var))

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

    # last syntactic use of the VALUE at or after the last deref
    ld = last_use_off
    if ld is not None:
        uses = [m.start() for m in re.finditer(r"\b%s\b" % re.escape(var), body)]
        if uses and fn.bstart + max(uses) >= ld:
            return "LAST-USE-AFTER", "%s is used again at or after the last deref" % var
    return "UNROOTED", "nothing roots %s across the window" % var


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
RULES = ("guarded", "no-window", "last-use-after", "copies-immediately")


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
            cons = consumer(fn, off, macro)
            esc = escapes(fn, off, expr, alias, cons, tree)
            lu = last_use(fn, off, alias)

            # The pointer copied straight into memory the caller owns, in the same
            # statement as the derive. There is no window because there is no gap.
            if not esc and cons and COPIES.match(cons[0]) \
                    and "copies-immediately" not in off_rule:
                r.discharges.append(("copies-immediately", rel, line_of(fn.src, off),
                                     "%s(%s) in %s -- consumed by %s() in the same "
                                     "statement" % (macro, expr, fn.name, cons[0])))
                continue

            # An escape has no upper bound: the pointer outlives the frame, so the window
            # is everything that runs afterwards, in this frame and every caller's.
            if esc:
                win = window_between(fn, off, max(e[1] for e in esc)) or \
                    [("ESCAPE", esc[0][0], esc[0][1])]
            elif lu is not None:
                win = window_between(fn, off, lu)
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
NEGATIVES = ["json-", "erb-", "bcrypt-", "ed25519-", "racc-"]

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
TRIAGED = {"mysql2-0.5.6": 11, "zlib-": 16, "iconv-": 5, "zstd-": 4, "sqlite3-": 3,
           "websocket-driver-": 2, "stringio-": 1, "msgpack-": 1}


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
    check(not flagged, "the 5 clean negative controls are unflagged or cleared by a "
                       "named rule", flagged)

    # 5b. the triaged residue. Pinned, so noise cannot grow unnoticed and a genuine new
    #     row cannot hide inside an existing pile. Growth is a FAIL even though the rows
    #     themselves are accepted -- see TRIAGED for what each one is.
    grew = []
    for prefix, expected in sorted(TRIAGED.items()):
        d = _find(pool, prefix)
        if d is None:
            continue
        n = len(_hits(d))
        if n != expected:
            grew.append("%s %d != %d" % (prefix, n, expected))
    check(not grew, "the triaged residue is unchanged: "
          + ", ".join("%s %d" % (k, v) for k, v in sorted(TRIAGED.items())), grew)

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
    tb, tg = _find(pool, "trilogy-bc"), _find(pool, "trilogy-green")
    if tb and tg:
        hb = [h for h in _hits(tb) if "cext.c" in h[1]]
        hg = [h for h in _hits(tg) if "cext.c" in h[1]]
        check(len(hb) > len(hg),
              "trilogy red/green pair: bc flags %d, green flags %d" % (len(hb), len(hg)))

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
    for sub in ("ESCAPES-BY-RETURN", "STORES-INTERIOR", "ESCAPES-INTO-CONTAINER",
                "ESCAPES-INTO-LIBRARY", "ESCAPES-INTO-CALLEE", "HELD-ACROSS-WINDOW"):
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

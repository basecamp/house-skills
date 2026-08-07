# Precedents

Real findings from six hunt rounds against the native gems pinned by the production
`Gemfile.lock`s of five Rails applications.

**Sanitised on purpose.** A scent library is a skill, and skills ship — this file is
distributed both inside the `dev` plugin and as a standalone skill. So it names the gem, the
version and the mechanism, and never which application pins what or how far along the fix is.
That pairing is the operational half, it belongs in an internal ticket, and it is worthless
here anyway: the durable content is the bug shape, not who was exposed to it in August 2026.
Re-apply the §7 disclosure test whenever you add a row.

Read the status honestly, because the skill it illustrates demands it:

- Most rows were reproduced by execution with a control, 3/3 — **but not all.**
  pg#734 and puma#3984 are **latent**: a code-level defect with no default-config
  repro. The openssl NPN sites and protobuf's HEAD verdict are **code reading only**.
- **Outcome means "what we filed", not "what upstream accepted."** All of these were
  filed by us and are unconfirmed. The one exception is trilogy#312, where byroot
  agreed at code level ("I'm unable to repro on my machine, but looking at the code,
  it indeed does look like a `GC_GUARD` is missing") and opened PR #313 — **merged
  2026-08-05, but in no released version**, so there is still no upgrade path. See
  "Neither trilogy fix is in a released version" below.
- The sqlite3 fix referenced throughout is PR #723, **open and unmerged**.

These serve the same purpose as a vulnerability pattern library: they are what the
bug classes actually look like in the wild, and they calibrate what is worth chasing.

## Round 1 — Class A: raw `VALUE` handed to a C library

| Gem | Site | Mechanism | Outcome |
|---|---|---|---|
| psych | `psych_emitter.c:97` — `yaml_emitter_set_output(emitter, writer, (void *)self)` | Emitter's own `VALUE` stored in libyaml, read on every write | [ruby/psych#811](https://github.com/ruby/psych/issues/811) — reproduced on 5.2.6–5.4.0 (oldest tested; range likely wider). **Exposure is narrow**: streaming `Psych::Emitter` only, `Psych.dump` unaffected |
| nokogiri | `SAX::PushParser` — raw `VALUE` in libxml2 `_private`, no `dmark`/`dcompact` | Stored at registration, read later | [sparklemotion/nokogiri#3665](https://github.com/sparklemotion/nokogiri/issues/3665) |
| fiddle | `Fiddle::Closure` | SEGV after compaction | [ruby/fiddle#211](https://github.com/ruby/fiddle/issues/211) — reproduced on 1.1.8 |
| pg | — | — | [ged/ruby-pg#734](https://github.com/ged/ruby-pg/issues/734) |
| libxml-ruby | `ruby_xml_registry.c:8` + reader | Three findings. The worst is not a raw `VALUE` at all: a file-static `st_table` of `xmlNodePtr → VALUE` held as raw `st_data_t` and read back **inside mark functions**, feeding dead VALUEs to `rb_gc_mark` | [xml4r/libxml-ruby#231](https://github.com/xml4r/libxml-ruby/issues/231) — corrupts the collector (`[BUG] try to mark T_NONE object`), not just a user call |
| sqlite3 | `database.c:525`, `aggregator.c:248` — `(void *)block` to `sqlite3_create_function` | Same shape PR #466 fixed for `busy_handler` and never extended | reported to the project |

## Round 2 — Class B: `char *` into a String's bytes

| Gem | Site | Mechanism | Outcome |
|---|---|---|---|
| google-protobuf | `map.c` `Map_index_set`, `message.c` `Map_initialize_kwarg` — key built with a NULL arena aliases a *temporary*, then the value conversion allocates before `upb_Map_Set` copies the key | Liveness. **Fires under ordinary GC** — one corruption in 150k iterations vs 100/100 under `GC.stress`. Silent key corruption, not a crash | [protocolbuffers/protobuf#29023](https://github.com/protocolbuffers/protobuf/issues/29023) — 4.29.3→4.35.1 + HEAD |
| openssl | `ossl_ssl.c:810/814/830` — ALPN/NPN callbacks stash the same `VALUE` the `dcompact` doesn't update | Class A regression from converting SSLContext to `rb_gc_mark_movable` | [ruby/openssl#1088](https://github.com/ruby/openssl/issues/1088) — **`master` only**; all releases pin and are safe |
| trilogy | `cext.c` — `connopt.hostname`/`path` re-read after `try_connect` to format the error | Mobility; `try_connect` releases the GVL | [trilogy-libraries/trilogy#312](https://github.com/trilogy-libraries/trilogy/issues/312) — 2.9.0 and 2.10.0 both fail |
| nokogiri | `xml_reader.c` — `xmlReaderForMemory(StringValuePtr(rb_buffer), …)` | Mobility, **only when linked against libxml2 ≤ 2.10** | [sparklemotion/nokogiri#3666](https://github.com/sparklemotion/nokogiri/issues/3666) — packaged builds measured safe (2.12.9 and 2.13.9), `--use-system-libraries` against a distro 2.9.x not |
| puma | `mini_ssl.c:294-296` — key passphrase as `SSL_CTX` passwd_cb userdata | Mobility; **latent** — no public path dereferences it after the frame | [puma/puma#3984](https://github.com/puma/puma/issues/3984), filed as latent |

Adjacent, found in passing and out of both classes:
[bryanp/llhttp#41](https://github.com/bryanp/llhttp/issues/41) — `data.length` (characters)
where `bytesize` is required, silently truncating multibyte bodies.

### Round 2 corrections, made in round 3

**trilogy `change_db`/`query`/`escape` — the "safe by construction" verdict was right,
both stated reasons were wrong.** Round 2 said they were safe because the `RSTRING_PTR` is
memcpy'd before `begin_write`. Round 3's brief said that was incomplete because
`trilogy_buffer_expand` calls Ruby's `xrealloc` while the pointer is live. Measured on the
built artifacts (`nm -D`), *both* are wrong:

- `trilogy_xallocator.h` **exists only in 2.12.x.** 2.9.0/2.10.0 `src/buffer.c:36` is plain
  libc `realloc` — `nm` shows `realloc@GLIBC_2.17`; 2.12.6 shows `ruby_xrealloc` and no libc
  allocator at all. So the GC window the brief described exists in the **2.12.x** pin and not
  the 2.9.0 one — the opposite of the way round the plan assumed, and a reminder that "newer"
  and "safer" are independent when the change is which allocator got compiled in.
- All three sites are nevertheless **clean by execution**, for a structural reason neither
  round found: `trilogy_buffer_expand` only fires once the packet buffer passes
  `TRILOGY_DEFAULT_BUF_SIZE` = 32768, so the source String must be tens of KB to reach an
  expand — far above the 616-byte embedded boundary, hence always a malloc block compaction
  never moves. The mobility half of Class B is unreachable here by construction.

**Neither trilogy fix is in a released version.** `fe2293f` and PR #313 (`63392f00`) both
merged 2026-08-05; `v2.12.6` is tagged 2026-06-23 and `git compare` puts it 21 and 20
commits behind them. Round 2's precedent row implied an upgrade path for #312; there is none
yet. **Merged is not released**, and a precedent table that records only the merge quietly
tells the next reader to close the finding.

## Round 3 — a `VALUE` handed to a C library that nothing pins

The generalisation of openssl#1088: not "a missing `dcompact`" but *a `VALUE` reaching a
non-Ruby library where the owning object's `dmark` does not call the **pinning**
`rb_gc_mark` on it*. Released openssl is the canonical safe form and the patch template —
`ossl_sslctx_mark` pins the context with itself.

| Gem | Site | Mechanism | Outcome |
|---|---|---|---|
| sqlite3 | `database.c:283` trace, `:671` authorizer, `:525` create_function, `:766` collation, `aggregator.c:248`, `aggregator.c:79` | Six live sites. `database_mark` pinned only `busy_handler`. `(void *)self` is the worst shape: `SQLite3::Database` is a T_DATA that **relocates**, and its slot is reused immediately | our own repo — [PR #723](https://github.com/sparklemotion/sqlite3-ruby/pull/723) closes all six; red/green verified 0/3 → 3/3 |
| openssl | `ossl_ssl.c:810` (NPN advertise), `:814` (NPN select) | Round 2 could not reach these because both ends negotiated TLS 1.3, where NPN is never sent. A TLS 1.2 ceiling made both fail immediately | extends [ruby/openssl#1088](https://github.com/ruby/openssl/issues/1088) — master only |
| nokogiri | `xslt_stylesheet.c:63` `_private`, `xml_reader.c:682` and `xml_sax_parser_context.c:93` `(void *)rb_io` | Stylesheet/IO `VALUE` in a persistent libxml2/libxslt object with no `dmark`/`dcompact`, read on every later use | new upstream issues; affected at HEAD |
| mysql2 | `result.h:9` `fieldTypes` | **Not this class at all** — a `VALUE` field of an xmalloc'd struct that no `dmark` marks. Freed by *ordinary* GC inside the very call that allocates it; `rb_ary_store` then writes through a freed slot | upstream (affected at HEAD, 0.5.4–0.5.7). A private fork branched off 0.5.4 carries it too — **auditing upstream proves nothing about a fork**, whatever its version string says |
| *(held — a CRuby default gem)* | a decoder stores its **own wrapper's `VALUE`** into the C library's stream struct at construction | the type declares no `dcompact`, so compaction leaves the copy stale; one branch reads it back and dereferences it | **routed privately, unfixed at the time of writing** — site withheld until it ships. Shape: `obj = TypedData_Make_Struct(...); <lib_struct>-><opaque_field> = (void *)obj;` |
| pg | `pg_connection.c:2994/:3055` | `PQsetNotice*` userdata; `pgconn_gc_compact` has no `self` back-reference | already covered by [ged/ruby-pg#734](https://github.com/ged/ruby-pg/issues/734); pg was outside the audited corpus |
| prometheus-client-mmap | `mmap.rs:535-541` `track_rstring` | **A third shape.** `let key = str.as_raw()` — a `VALUE` laundered through Ruby as an Integer and used as a WeakMap key. Compaction leaves a stale key; a later string in the recycled slot collides and **evicts a live entry** | upstream (gitlab-org); affected at HEAD |

### Out of class, found by the same sweep

- **A private zlib fork re-exposed CVE-2026-27820, and shadowed the fix.** `zstream_buffer_ungets`
  (`zlib.c:825-844`) overflows `z->buf`. Upstream fixed it Nov 2025 and released 2026-03-05 —
  but a fork frozen at ruby/zlib `785d747` (2021-03-07) never received that or anything else in
  between, and its `lib/zlib.so` **shadows the patched default gem** on `require "zlib"`. So the
  patched stdlib is installed and is not the code that runs. Reproduced 3/3 via
  `GzipReader#ungetc`; the patched stdlib is clean 3/3 through the same harness.

  Three generic lessons, which is why this is here at all. A fork carried for one small patch
  silently inherits **every** fix it has missed since — diff it against upstream and check when
  it last merged, not what its version string says. A fork of a **default gem** is invisible to
  a lockfile-level audit, because the vulnerable copy is not the one the lock names. And the
  remedy is usually smaller than the fork: this one was a version-string bump plus upstream's
  own 6-line `Bug #10961` fix, so the diff *was* the deliverable.
- **zstd-ruby `skippable_frame.c:25-26`** — `rb_str_new(input_data, input_size + 8 + skip_size)`
  reads past the end of the argument's buffer. Deterministic SEGV at 1 MiB.
- **trilogy `cext.c:1190` (HEAD)** — `rb_enc_get(str)` before `StringValue(str)`, so any
  `to_str` object (including `SimpleDelegator`) NULL-derefs. `fe2293f` did not touch it.
- **websocket-driver `websocket_mask.c:9`** — no `Check_Type`; a non-String argument is
  dereferenced as an `RString`. **Latent**: both in-gem call sites are String by construction.


## Round 4 — the checkable invariant, and the first untrusted-input track

Every finding below was **independently re-reproduced** by a second agent that was forbidden from
reading the first one's harness, before anything was filed. That caught a mis-diagnosis carried
for a whole round (rmagick), a false positive in our own sweep (json), and a wrong correction
to an application-side encoding claim — see the calibration notes.

| Gem | Site | Mechanism | Outcome |
|---|---|---|---|
| stackprof | `stackprof.c:122` `_stackprof.interval` | A `VALUE` field no mark function marks. `NUM2INT` falls through to `rb_to_int`, which converts a *temporary* and stores the **original**, so any heap numeric (`Rational`, `Complex`, any `#to_int`) is freed by **ordinary GC** while the field points at it. Read back at `:400` (handed *back to Ruby* in the results hash), `:704`, `:711`, `:846` (allocation tracepoint), `:913` | [tmm1/stackprof#244](https://github.com/tmm1/stackprof/issues/244) — 0.2.26, 0.2.28, HEAD. Latent: `Integer`/`Float` are immediates |
| stackprof | `stackprof.c` `objtracer` | A **file-static** `VALUE` holding an `rb_tracepoint_new`, never `rb_global_variable`-registered. Worst outcome is silent: when the recycled slot is *another* `TracePoint`, `stop` returns `true` and disables somebody else's instrumentation | [tmm1/stackprof#245](https://github.com/tmm1/stackprof/issues/245) — latent; needs an external `#disable` |
| rmagick | `rmutil.cpp:329-339` `rm_str2cstr` | **Liveness, not mobility.** For a `to_str` duck type, `StringValue` writes the converted String into the callee's *own local* while `argv[i]` keeps the original — so nothing references it once the function returns. 435/500 at 10k allocations in the window, under ordinary GC | [rmagick/rmagick#1846](https://github.com/rmagick/rmagick/issues/1846) — 6.1.1, 6.1.4, HEAD |
| bcrypt_pbkdf | `bcrypt_pbkdf_ext.c:28`,`:30` | `RSTRING_LEN` on a **completely unvalidated** `VALUE` gates a fixed 64-byte read. Wrong as written *and* as built: `-O3` emits a raw `ldr x1,[x1,#16]` before any type check. 7/7 SIGSEGV on immediates; ASan heap over-read on a gate-passing object | [net-ssh/bcrypt_pbkdf-ruby#41](https://github.com/net-ssh/bcrypt_pbkdf-ruby/issues/41) — latent; `__bc_crypt_hash` has zero callers outside the gem's own tests |
| mittens | `ext.c:38-56` | `RSTRING_PTR(language)` held across `sb_stemmer_delete`/`sb_stemmer_new`/`rb_raise` with **no register or stack slot holding the `VALUE`** — proven by disassembling the shipped `.so` | [ankane/mittens#6](https://github.com/ankane/mittens/issues/6) — latent; the success path imports no allocating symbol, so `rb_raise` is the only window |

### Not filed, and why — routing is half the work

- **gvltools 0.4.0** — a real use-after-free (thread-scoped pointer cache anchored by *fiber-scoped*
  `Thread#[]=`; 3/3 per vector, one needing no app misuse). **Fixed upstream in 0.5.0** before the
  hunt started, by the same change we derived. Routes to a version bump, not a filing — and
  checking that first would have saved the reproducer.
- **bootsnap 1.24.5 namespace overflow** — confirmed 3/3 under ASan, but **fixed in 1.24.6** and
  independently reported by someone else. A version bump, not a filing. Worth recording: the 1.24.6 changelog
  does not mention the fix, `bundler-audit` has no entry, and a Dependabot PR proposing the bump sat
  unmerged for two months. Neither the changelog nor the tooling would have surfaced it.
- **zlib `zstream_run_func`** — the same frozen fork violates the GVL invariant 4236/4236 and
  1498/1498; **upstream 3.2.3 measures 0/4236 and 0/1498.** Fork-only, so there is nothing to
  file. The number worth keeping is the correction: round 3's "~1/700" was the
  concurrency-*overlap* rate, not the violation rate, which is 100%. Report the rate of the
  thing you are claiming, not the rate of the window you observed it through.

### Cleared by execution this round

nio4r 2.7.5 (**safe by design** — Pin + Indirect, 210,000/210,000 witnesses, 2,813 compactions
inside a blocked `select()`, positive control = the real gem with two lines changed), bcrypt 3.1.22,
ed25519 1.4.0 (both *stack-liveness-by-accident*, not by design), json `rvalue_stack`, msgpack
`mapped_strings`, stackprof `mode`/`fake_frame_names`/`empty_string`/`frames_buffer`, rmagick
`rmdraw.cpp:479` (600 in-window compactions, 0 relocations — safe because
`rb_vm_save_machine_context` pins the C stack frame for the whole nogvl window), all 13 ruby-core
bundled gems.

## Rounds 5–6 — the file-scope `VALUE`, and a defect with nowhere to file it

Predicate C's class: a `VALUE` that lives at file scope rather than inside a wrapped struct, so
the wrap-site walk cannot see it by construction. Three instances in three unrelated gems is what
made it a class rather than a curiosity.

| Gem | Site | Mechanism | Outcome |
|---|---|---|---|
| rbtrace | `rbtrace.c:94-95` `rbtracer.list[].self` / `.klass` | Caller-supplied objects stored in fields of a **file-static struct**, two levels down. The gc_hook at `:1154` wraps `NULL`, so `rbtrace_gc_mark` at `:1083` has no pointer to walk and marks **nothing at all** — the mark function exists and is dead | [tmm1/rbtrace#112](https://github.com/tmm1/rbtrace/issues/112) — 0.5.4 |
| kgio | `accept.c:10` `cClientSocket` | A file-scope `VALUE` nothing registers, while its three siblings *are* registered with `rb_gc_register_mark_object` at `:500-506`. `Kgio.accept_class=` (`:50`) stores an **arbitrary caller-supplied class** into it; read back at `:120`/`:124`/`:128` and consumed by `sock_for_fd` at `:213` | **Not filed — no tracker exists.** 2.11.4 is the latest release; the project is mailing-list based and effectively unmaintained. One-line fix: `rb_gc_register_address(&cClientSocket)` after `:504` |
| trilogy | `cext.c:27` `_global_buffer_pool` | **Not a defect, and the reason is stronger than the one first given.** Not merely self-healing — the whole static is guarded on `#ifndef HAVE_RB_RACTOR_LOCAL_STORAGE_VALUE_NEWKEY`, and the extconf probe succeeds on every Ruby the gemspec allows (`>= 3.0`) | Cleared. Preprocessing with the generated flags yields **0** occurrences; without the flag, 4 — so the check discriminates |

### kgio is the round's best-evidenced finding and it still should not be filed

Reproduced 3/3 with a clean control on ruby 4.0.6 arm64-darwin23 and ruby 3.4.7, including through
the **real `UNIXServer#kgio_accept` path**; fault addresses included `0x2f62696c2f362e48`, ASCII
`H.6/lib/`, i.e. the slot recycled into a String. Adding `rb_gc_register_address` takes it green
3/3, and the precondition flips to `moved: false` — the expected signature of the correct fix.

**The correction that matters: the anonymous-class case is not the only exposure.** The default is
incidentally *pinned*, because `cClientSocket = cKgio_Socket` and that sibling is
`rb_gc_register_mark_object`'d. But the **relocation** path fires with *any* class, including a
permanently-rooted named constant — `class MySocket < Kgio::Socket; end` plus one `GC.compact` is
sufficient, and a named subclass is what the gem's own `test/test_accept_class.rb` does. A first
analysis that stops at "only an anonymous class can be collected" understates it.

Routing, per §7: untrusted input cannot reach this. Triggering needs the *application author* to
call `Kgio.accept_class=` **and** enable compaction, so it is a latent defect wanting a
maintainer's judgement rather than a private disclosure — and there is no maintainer to want it.
Filing to a dead mailing list buys nothing; the real remediation is dropping kgio with the server.
Recorded here so the next round does not re-derive it.

Two harness false negatives paid for on the way, both of which reported a **broken** gem clean:
`ObjectSpace.define_finalizer(k, proc { ... })` with the proc created in the same method as `k`
captures `k` in its binding and **roots the subject**; and `object_id` is a monotonic counter in
modern Ruby, not an address, so a "did it move?" check built on it always answers false — use
`ObjectSpace.dump(o)["address"]`.

### One finding is held out of this file

A memory-safety defect found this round was routed through a **private** disclosure channel and is
still untriaged. Per §8, anything routed privately either goes in sanitised or stays internal with
a pointer from here — and for an unfixed defect in an untrusted-input parser, naming the gem and
the mechanism in a file that ships *is* the exploit path. It stays internal. The transferable part
is already above: the predicate that found it is predicate C, and the shape is in the table.

## The pass-1 sweep

The generalisation of mysql2's `fieldTypes`, and the first predicate in four rounds that is a
**checkable invariant** rather than a scent: enumerate a GC-managed struct's `VALUE` fields,
diff against its mark function. Sweepable with auditable recall, which none of the previous
three predicates were.

Sweep script: [references/sweep_unmarked.py](sweep_unmarked.py). Run it with `--self-test`
before trusting any result from it.

### The pass-1 query failed its own validation — this is the round's most reusable lesson

§3 says to validate the query against a known instance before trusting its silence. Doing so
caught **four separate defects**, each of which independently produced a false clean sheet.
Recording them because they are all *generic* C-parsing traps, not mysql2 trivia:

| Defect | Symptom | Why it clears a broken gem |
|---|---|---|
| Globbed only `*.c`/`*.cpp` | mysql2's `mysql2_result_wrapper` is declared in **`result.h`** | The known instance was never scanned. **Output was byte-identical for the patched and unpatched trees** |
| Preprocessor directives left in initialisers | `#ifdef HAVE_RB_GC_MARK_MOVABLE` sits *between* the positional entries of an `rb_data_type_t` | `.dcompact` parses as absent, so **every movable field in the gem reports NO-COMPACT**. Also made a `#define TypedData_Get_Struct(obj, type, ignore, sval)` compat shim register a wrap site on a type named `ignore` |
| Standalone typedefs unresolved | sqlite3 declares `struct _sqlite3Ruby {…};` then `typedef struct _sqlite3Ruby sqlite3Ruby;` **separately** | `sqlite3Ruby` — the name `TypedData_Make_Struct` actually passes — never registers, so the entire gem resolves to "struct type unresolved" and reports **0 suspects: a clean sheet produced by the query failing** |
| Hybrid initialiser form | json's `JSON_ResumableParser_type` uses a designated `.function =` holding a **positional** list | Matched neither the designated nor the positional branch, so `dmark=-` — "no mark function at all" — on a field that is in fact **pinned** with `rb_gc_mark` *and* updated in the `dcompact`. A false positive on the safest struct in the gem |

Measured, v1 vs v2 on the mysql2 red/green pair:

| | v1 | v2 |
|---|---|---|
| true positives on `m2-red` | **0** | 1 (`fieldTypes`) |
| false positives | **3** | 0 |
| `m2-red` vs `m2-green` | **identical output** | differs by exactly `fieldTypes` |

### Start from the wrapper, not the struct

v1 enumerated every struct in the file and reported three stack-local argument structs —
`nogvl_send_query_args.sql`, `async_query_args.self`, `nogvl_prepare_statement_args.sql` — the
discriminator's textbook safe case, and pure false positives. **A struct that is never wrapped
is not GC-managed and is out of scope by construction**, so walk:

```
TypedData_Make_Struct / _Wrap_Struct / _Get_Struct / Data_Make_Struct / rb_data_typed_object_*
  -> the rb_data_type_t it names
    -> that type's .dmark / .dcompact, resolved whole-tree, one in-tree callee deep
      -> only then the wrapped struct's VALUE fields
```

The three false positives drop out for the right reason. One callee deep is needed because
sqlite3 PR #723 marks three of its six fields through helpers.

### Make silence self-documenting

`0 suspects, 0 cleared` means one of **three** completely different things, and only a coverage
count distinguishes them. Print wrap-site, dtype and unresolved counts per tree:

- `0 wrap sites` → structurally out of scope; the gem wraps nothing (rinku, bcrypt,
  bcrypt_pbkdf, ed25519, all four bootsnap versions, websocket-driver, erb)
- `0 cleared, N wrap sites` → the wrapped struct holds no `VALUE` (mittens' `stemmer_t`,
  nio4r's `NIO_ByteBuffer`, bigdecimal)
- **the query failed to resolve** — the case the counter exists to expose

This earned its place within an hour: a bundled-gem run printed `*: 0 suspect(s), 0 cleared
[0 wrap sites]` with a **literal asterisk**, because the shell glob had not expanded and the
directory was empty. Without the counter that reads as thirteen clean gems.

### Acceptance test — and a fixture that cannot work

`--self-test` asserts, and all five pass:

1. flags `fieldTypes` on `m2-red`
2. clears it on `m2-green`
3. red and green differ by **exactly** that field
4. clears all six `VALUE` fields of sqlite3 PR #723's struct
5. a **generated** de-marked copy of that tree flags the field again

Item 5 is the positive control and is not optional: without it, item 4 was passing for the
wrong reason — the typedef defect above made the whole tree resolve to nothing, so "clears all
six" was the unresolved-struct artifact wearing a green tick.

**A fixture worth recording as impossible.** The round-4 plan asked the sweep to "flag the six
known sites on sqlite3 `main`". It cannot, and that is not a defect: `main`'s struct has exactly
**one** `VALUE` field (`busy_handler`) and `database_mark` **does** mark it. Measured — `main`
= 0 suspects, 1 cleared. Main's six sites are raw `VALUE`s handed to SQLite, i.e. round 3's
predicate. **Two predicates that both fire on the same gem do not fire on the same code**, and
picking a fixture from the wrong one wastes a round.

### Generate the control; never hand-edit it

§4 says build the control in as a flag rather than as a second file. The sqlite3 red fixture
follows that by `cp -r`-ing the green tree and deleting one `rb_gc_mark` line *inside the test*,
so red and green cannot drift apart. A checked-in hand-edited red tree is a different program
and proves less.

### Known limitation

`TypedData_Get_Struct(obj, conmode, &conmode_type, …)` in io-console resolves nothing, because
`conmode` is a **`#define`**, not a typedef, and directives are blanked before parsing. On POSIX
it is `struct termios`, which holds no `VALUE`s, so the verdict is unaffected — but a gem whose
payload type is reached through a macro will read as unresolved rather than as a hit.

---

## Burned false NEGATIVES — two detectors that inverted their own verdict

Both found in round 4, both generic, both independently reproduced by the orchestrator. These
are worse than a false positive: a false positive costs you an afternoon, these clear a gem that
is broken.

### `set -o pipefail` + `grep -q` reports a found defect as CLEAN

```bash
set -uo pipefail
if printf '%s\n' "$out" | grep -q "ERROR: AddressSanitizer"; then  # never taken
```

`grep -q` exits 0 on the **first** match and closes the pipe → the producer dies of **SIGPIPE
(141)** → `pipefail` promotes 141 to the pipeline status → `if` takes the *else* branch. **The
pipeline reports "clean" because the pattern matched.**

It fires only when the searched text exceeds one pipe buffer (64 KiB) with the match near the
top — which is to say, on verbose sanitizer reports specifically. **A small fixture passes, so
it survives unit-testing the harness.** Measured on a real 154,052-byte ASan report: pipeline
status 141, verdict `CLEAN`, pattern present. It reported a reproduced ASan stack-buffer-overflow
as `VERDICT=CLEAN`.

Fix: match with bash `case`/`[[ == ]]` (no pipeline), or `grep -c` (consumes all input). `head -1`
is the other common early-exit consumer with the same trap. Reproducer:
[references/pipefail_false_negative.sh](pipefail_false_negative.sh).

### ASan's `abort_on_error=1` discards buffered stdout

SIGABRT drops the stdio buffer, so the harness's provenance lines — loaded path, built-vs-loaded
checksum — **vanish in exactly the run that found the bug**, and a checksum check that treats
missing output as a mismatch then reports `FAILED-RUN` on a real finding. Fix: `$stdout.sync =
true` before the run. General form: any provenance you print is unreliable in a crashing run
unless it is unbuffered, and a crashing run is the one you most need it for.

## New burned false positives (round 4)

- **A stack-local argument struct holding a `VALUE`** is safe — the discriminator's textbook
  case. v1 of the round-4 sweep reported three; none is a finding.
- **`JSON_ResumableParser.buffer` is marked**, despite a sweep saying `dmark=-`. Round 3's
  clearance of json stands. Read the mark function before believing a tool that says there
  isn't one.

## New scents (round 4)

- **A file-static `VALUE` is not covered by "`rb_global_variable` statics are safe."** That
  exemption applies to statics that were *registered*. stackprof's `objtracer` is a file-static
  holding an `rb_tracepoint_new`, written at registration and read in a later call, and named in
  no mark function — the safe-idiom exemption reads as if it covers it, and does not. Its
  siblings `fake_frame_names`/`empty_string` in the *same struct* **are** registered and were
  measured pinned (4/4 registered slots interior to a file-static struct stayed put across 3
  compactions while 200/200 witnesses relocated). Registration is per-slot; do not infer it for
  a neighbour.

- **`NUM2INT`/`NUM2LONG` stores the *original* `VALUE`, not the converted one.** They fall
  through to `rb_to_int`, which converts a **temporary** — so writing `x->field = NUM2INT(arg)`
  into an unmarked field parks a heap object there whenever the caller passed anything that is
  not an immediate. `Rational`, `Complex` and any `#to_int` duck type are all accepted; only
  `Integer` and `Float` are safe, because they are immediates. This is how stackprof's `interval`
  becomes a live use-after-free through an ordinary-looking numeric keyword argument.

- **`StringValue(str)` assigns to the *callee's local*, leaving the converted String unrooted.**
  When the argument is a `to_str` duck type rather than a String, `argv[i]` still holds the
  **original** object, so after the conversion helper returns, nothing references the new String
  — while its `char *` is still in flight. rmagick's `rm_str2cstr` is the instance, and it fires
  at 435/500 under **ordinary GC** with a large enough coercion window. Note the shape: the
  pinning that protects the String case (`rb_gc_mark_locations` over the VM stack) does **not**
  protect the coerced case, so a single site is safe for one argument type and unsafe for another.

- **An ActiveRecord `encrypts` attribute is an encoding-laundering channel.**
  `Cipher#encrypt` writes `clean_text.encoding.name` into the message header and `#decrypt`
  `force_encoding`s it back, so a non-UTF-8 tag round-trips **verbatim** through a column that
  holds ciphertext and is therefore never validated by the database. That is what carries a
  `BINARY`-tagged attacker string through a `utf8mb4` schema and into a C parser.

- **`valid_encoding?` is the wrong guard for "safe to hand to a UTF-8 parser."** It is
  unconditionally **true** for every single-byte encoding, so a check written against broken
  UTF-8 silently passes arbitrary bytes tagged `ASCII-8BIT`/`ISO-8859-1`/`Windows-1252`. Round 4
  found this mistake made *independently at two layers* — an application's own transcode guard,
  and the `ENC_CODERANGE_BROKEN` check inside the C extension it fed. Guard on the **encoding**,
  not on validity. (Gem unnamed on purpose: the extension is unfixed and its upstream is dead.
  Re-apply the §7 disclosure test at close-out — a scent library is a skill, and skills ship.)

## A fix template that is right for one bug class and wrong for the other

bcrypt's `bcrypt_ext.c:35-36` — `prefix = rb_str_new_frozen(prefix); input = rb_str_new_frozen(input);`
— gets cited as *the* frozen-copy idiom. Round 4 propagated it as a use-after-free template and
that was **wrong**, in a way worth writing down because the two classes look alike:

- **`rb_str_new_frozen` does not always copy the bytes.** Measured on ruby 4.0.6: an independent
  buffer at n=24/100/600, but at **n=5000 the frozen result has an identical `RSTRING_PTR`** — it
  shares the parent's malloc'd buffer. So it is not a "hand the library its own bytes" idiom.
- **It is the *mutation*-safety leg, not the GC-lifetime leg.** Frozen copies stop an in-block
  `replace`/`<<`/`clear` from reallocating under a live pointer — which is exactly right for a
  mutation-triggered UAF, and says nothing about collection.
- **bcrypt's `RB_GC_GUARD`s are not even the active mechanism** in that build: with *both* deleted
  it survived 3/3 at equal sensitivity (63,694–72,840 relocations). What carries bcrypt is the same
  conservative stack-liveness that carries yajl and websocket-driver — **Pin by
  stack-liveness-by-accident**, not by construction.

So: for a **mutation** UAF, cite 35–36. For a **GC-lifetime** UAF, cite what actually holds the
object — a local the GC can see for the pointer's whole lifetime plus `RB_GC_GUARD` past its last
use (`:53-54`), or an explicit copy. Citing 35–36 for the latter produces a fix that appears to
work and does not.

## New safe idioms (round 4)

- **A C stack parameter is pinned even while parked in nogvl.**
  `rb_vm_save_machine_context` saves the thread's stack bounds *and registers* at blocking-region
  entry, so the frame is conservatively scanned for the whole GVL-released window. Measured on
  rmagick's `str_to_image`: **600 in-window compactions, 0 relocations**, with a positive control
  on the same binary relocating 28/28. This is why "holds `RSTRING_PTR` across a nogvl call" is
  not by itself a finding.

- **A deliberate `dmark = NULL` on a non-owning view.** msgpack's `buffer_view_data_type` marks
  nothing *by design*: it holds `@owner`, and the owner's own dmark pins every chunk's
  `mapped_string` head..tail inclusive. A `NULL` dmark is a question, not a verdict — find out
  who owns the payload.

## Positive controls that cannot fail (round 4)

Three cases where the *control*, not the test, was the broken instrument. Each would have
produced a confident clean sheet.

- **An end-of-array off-by-one is masked by a stack copy.** msgpack's `packer.c:120` keeps
  `parent_buffer` as a C-stack struct copy whose embedded `.tail.mapped_string` is
  conservatively pinned — so mutating the *last* index yields a control that cannot fail.
  Retarget to the **first** entry.

- **`RSTRING_PTR(RARRAY_AREF(ary, 1))` is pinned by the saved register set.** An attempted
  unpinned in-call pointer measured 0/5 because the compiler materialises that `VALUE` in a
  register, and the blocking-region context save covers registers too. Constructing a genuinely
  unpinned pointer required eliminating every `VALUE` and passing the raw address as an Integer.

- **`String#+@` on an unfrozen constant returns `self`**, and `+@`/`dup` of a *frozen heap*
  String returns a **copy-on-write share with an identical `RSTRING_PTR`** (measured). The scent
  library already documents the CoW trap via `String#[]`; it arrives just as easily through
  `+@`. Build the subject with `String.new(capacity:) << src` and **assert the pointers differ**.

## Two more harness rules earned this round

- **Measure the precondition in a separate pass, not from the object under test.** json's
  `parser.partial_value` materialises an Array holding every pending element, keeping them live
  across the GC — a build with the mark loop **entirely dead** still reported clean.

- **No verdict line ⇒ FAILED RUN.** A `NameError` from constant ordering made a runner print
  *nothing*, which read as a pass. Same family as the `pipefail` inversion: absence of a failure
  signal is not a negative result. Assert that the run produced its verdict.

- **An amplifier set too high is a harness artifact that reads like a finding.**
  `interval: 1` in stackprof is a raw SEGV with no bug report — an interrupt storm, not the
  defect under test.

## Calibration notes from round 4

- **Independent re-verification earned its keep three times.** A second agent, forbidden from
  reading the first one's harness, refuted a round-3 diagnosis (rmagick was called *mobility*; it
  is *liveness*, and plain Strings are provably safe at 200/200 witnesses relocated), found a third
  affected call site nobody had, and showed the defect fires in a threaded shape needing no caller
  cooperation at all. Reviewing a report is not verification.
- **A correction can be wrong too, and the orchestrator's own measurement can be wrong.** An agent
  "corrected" a reachability claim; the correction was refuted by an end-to-end parse. Then the
  agent found the *real* mechanism — `Encoding.default_internal`, which Rails sets and a bare
  `ruby` does not — and it was the orchestrator's re-verification that had been run in the wrong
  environment. Same parse, one variable: `nil` → attacker bytes intact; `UTF-8` → `ArgumentError`.
  **State the environment a memory-safety measurement was taken in; "I reproduced it" is not
  portable between a bench script and a booted app.**
- **`GC.stress` can be the *weaker* amplifier.** rmagick's liveness defect: `GC.stress` gave 1/200,
  while 10,000 ordinary allocations in the window gave 199/200. Allocation *volume* drove it, not
  GC frequency. Report both and let the bigger number be the one that isn't the amplifier.
- **Two predicates that both fire on one gem do not fire on the same code.** sqlite3 `main` has six
  confirmed Class-A sites and measures **0 suspects** under the round-4 invariant, because its
  struct's one `VALUE` field is properly marked. Picking a fixture from the wrong predicate wastes
  a round.
- **Check who maintains the project before choosing a disclosure channel.** One target turned out
  to be a repo we have admin on, which is why its private-reporting endpoint returned 403. Another
  was already fixed upstream, and a third had a two-month-old Dependabot PR proposing the exact fix.
  Three of six findings needed no external filing at all — routing is half the work, and doing it
  first would have saved the drafting.

## Cleared by execution

Worth recording: a gem cleared *by execution* is a durable result, and the reason it is
safe teaches the idiom.

| Gem | Why safe |
|---|---|
| mysql2 (and the private 0.5.4 fork) | Query path: `args` is a stack local so the converted String is pinned even mid-nogvl, **and** there are explicit `RB_GC_GUARD`s — belt and braces, not bare pinning. Survived ~1700 concurrent compactions with the strings demonstrably relocating. `mysql_ssl_set` copies (verified against the real libmysqlclient in C). |
| ffi | Best-behaved of the set: `rb_gc_mark_movable` **plus** a real `dcompact` using `rb_gc_location`, everywhere. |
| msgpack | Pins with `rb_gc_mark`; keeps its own copy of any non-frozen/non-binary feed String. |
| yajl-ruby | Correct *by accident* — no `RB_GC_GUARD` on the parse paths; safe only because the argument `VALUE` is stack-live and therefore pinned. Fragile to refactoring. |
| sassc | `FFI::MemoryPointer.from_string` copies; libsass gets an FFI-malloc'd buffer it owns and frees — a deliberate ownership hand-off. |
| llhttp-ffi, puma parser, protobuf decode/descriptor paths, openssl releases | Consumed in-call, or copied. |
| json 2.20.0 | `RUBY_TYPED_EMBEDDABLE` makes every `TypedData_Get_Struct(self, …)` an **interior pointer into a movable slot** — `JSON::State` and both parsers relocate 200/200. Safe only because `self` is conservatively pinned for the cfunc's duration (measured: 0/28 in-call relocations vs 1/1 immediately outside). The `ResumableParser` is the best-behaved cross-call design in three rounds: it re-derives its interior pointers at every entry point. ~116,500 compaction-bearing operations, ~217,000 compactions, clean. |
| zstd-ruby 2.0.6 | `sc->buf` really is `rb_gc_mark_movable` in a malloc'd struct, and its **slot** relocates — but it is `rb_str_new(NULL, ZSTD_CStreamOutSize())` = 131,591 bytes, 214× the embedded boundary, so its bytes are a malloc block that never moves. The `dcompact` is present and updates both stored VALUEs. 135,000 operations, ~1,700 compactions per run, clean. |
| websocket-driver 0.8.2 | The yajl case sharpened. Safety comes from Ruby's **VM stack** being marked with `rb_gc_mark_locations` (hence pinned), not from the C frame — the compiler does not keep the argument alive. 36,000 forced windows with a full compaction inside the exact `rb_str_new` gap, clean. Zero `RB_GC_GUARD`; fragile to refactoring. |
| rmagick 6.1.4/6.1.1 | `Draw::primitives` is the complete *relocate* idiom: `rb_gc_mark_movable` **plus** a `dcompact` calling `rb_gc_location` on the one VALUE the struct holds. 2,000 operations with size-matched churn between store and read, clean. |
| zlib `zstream_run`'s `next_in`/`next_out` | Cached across `rb_thread_call_without_gvl`, but `zstream_mark` pins both Strings with the non-movable `rb_gc_mark`. ~350 compactions landing inside an individual zlib call's window, clean. |
| sqlite3 `exec_batch` `database.c:896/:900` | `callback_ary` is a live C stack local for the whole synchronous `sqlite3_exec`, so conservative scanning pins it even though the callback re-enters Ruby. The discriminator's textbook safe case, verified by forcing a full compaction *mid-batch*: 18 runs, 200/200 witnesses relocated each time, correct rows every time. |
| sqlite3 `busy_handler`/`progress_handler` | The **Indirect** idiom and the in-repo template: both pass `(void *)ctx`, the xmalloc'd struct, never a `VALUE`. This is the shape PR #466 introduced and never extended — which is exactly why the other six sites existed. |

## Calibration notes

- The gem that looked most suspicious on paper (nokogiri `Reader`) was **safe as shipped**
  and unsafe only in a supported build configuration. The gem nobody flagged
  (google-protobuf) had the worst bug. Read the code; don't rank by reputation.
- Three of five round-2 findings were **not** reachable by the obvious "call it and compact"
  test — they needed a warm-up call, a concurrent compacting thread, or `GC.stress` to open
  the window.
- Two of the round-2 gems were safe only because of conservative stack scanning, not by
  design. That is worth saying in a report even when there is no bug to file.
- **Source reading is not a verdict either.** libxml2 2.12.x stores the caller's pointer
  (`ctxt->mem = mem`) and reads from it through a callback — which reads as vulnerable, and
  a source-only review called it so. Measurement refuted it: libxml2 drains the buffer up
  front, so packaged 2.12.9 is safe at 8.5 KB. Reading the library is how you form the
  hypothesis; only execution settles it.
- **Settle allocator questions on the built artifact, not the header.** `nm -D` on the
  `.so` is what proved trilogy 2.9.0 uses libc `realloc` while 2.12.6 uses `ruby_xrealloc`.
  A conditionally-compiled header (`-DTRILOGY_XALLOCATOR` from `extconf.rb`) reads as
  present in the tree and absent from the binary.
- **A negative that measured zero relocations has sensitivity zero, not high sensitivity.**
  Round 3 nearly cleared nokogiri's `Reader` on a `Tempfile` subject: `FL_FINALIZE` pins
  anything with an ObjectSpace finalizer, so it *cannot* move and the run proves nothing.
  Always report the witness count alongside the operation count.
- **"My Ruby callback didn't run" can be the failure, not a false negative.** At openssl
  `:814` the stale `rb_attr_get` returns `Qnil`, so the block never runs — a naive
  "did the block fire?" precondition reports the *bug* as a missed precondition. Round 2
  drew exactly that wrong conclusion. Distinguish "never registered" from "registered,
  read a stale slot, got nil".
- **The layout of `RTypedData` changed.** On ruby 4.0 it is +0 flags, +8 klass, +16
  fields_obj, +24 `const rb_data_type_t *type`, +32 `void *data`. The classic +16/+24
  layout silently yields a garbage pointer.

## New scents from round 3

- **A `VALUE` stored as an integer, key, handle or index is a stale address too** — not
  only one cast to `void *`. prometheus-client-mmap's `str.as_raw()` WeakMap key survives
  every grep in the round-3 sweep (no `(void*)self`, no `ex_data`, no `_private`) and is
  invisible to mark/compact review, because the *value* side is a perfectly ordinary weak
  reference. It fails silently: compaction does not corrupt the map, it just makes one key
  unreachable, and the damage appears only when a later insert collides on the recycled
  address.
- **A `VALUE` field of an xmalloc'd TypedData struct that `dmark` never marks.** This is the
  generalisation that catches mysql2's `fieldTypes`, and the round-3 predicate misses it
  entirely — nothing is handed to a C library at all. mysql2's round-2 clearance ("the only
  thing it hands libmysqlclient is its xmalloc'd wrapper struct, not a VALUE") remains true
  *and* misses the bug. Enumerate the struct's `VALUE` fields against the mark function;
  don't start from the library call.
- **Sweep-query false negatives, measured.** The round-3 query missed two of sqlite3's six
  sites: `sqlite3_create_collation` (no term for it) and `aggregator.c:79`, where the
  `VALUE` is written into a *library-allocated* buffer (`sqlite3_aggregate_context`) rather
  than passed as an argument. Add `create_collation`, `_aggregate_context`, `as_raw`,
  `opaque =` and `\.dmark` to pass 1.
- **A movable mark on a String moves the slot, never a large malloc'd byte buffer.** zstd's
  `sc->buf` has all three premises of the scent — movable mark, captured raw pointer, an
  allocation in between — and is still safe, because the buffer is fixed at 131,591 bytes
  and can never be embedded. "Run both size regimes" does not apply when the extension, not
  you, chooses the size.

---

## Round 9 — routing, and four zeros that were not clearances

Two findings were routed **privately** this round — one under an embargoed advisory, one not yet
reported. Per truffle-hunt §7, re-applied at check-in time, **both appear here as shapes rather
than as targets**: no gem, no file, no line, no trigger construction. This file ships publicly and
twice, so a worked row naming an unfixed defect is a disclosure regardless of intent. The scents
below lose nothing by it.

### The routing scent, which is worth more than either finding

- **An API that appears in the reproducer may be on the wrong side of the trigger.** A decoder read
  a stale pointer on one branch, and that branch also *calls* a configuration API — so the obvious
  reading was "the developer chose to use that feature," and it routed public on that basis. Wrong:
  the API is what the C code calls **in response to** the condition, and the condition is set by a
  flag in the input's own header. The original reproducer had used that API only on the *encoding*
  side, to manufacture test data, which is exactly how the misreading survived three rounds.
  **Discriminator:** build the input in one process, consume it in another that *provably cannot
  name* the suspected API, and grep the consumer to prove it. If it still fires, the API was
  scenery.
- **"Developer-chosen" is a claim about every caller in the corpus.** Having decided the trigger
  was input-driven, the second question is who feeds it — and a widely-bundled HTTP library turned
  out to retain one of these decoders across a response body and feed it chunk by chunk, so
  ordinary remote input reaches it and nobody chose anything. The decoder is reachable only through
  an ivar there, so it is movable. **A retained decoder plus a per-chunk feed is the shape to grep
  for**; the one-shot class-method form is pinned for the duration of its own call and measured
  clean at 2000 iterations under `GC.stress` + `auto_compact`.

### Burned false positives

- **A short String is not necessarily an embedded String.** `Encoding#name` returns a 5-byte
  String whose bytes live in `.rodata`, so `RSTRING_PTR` moved **0/3** while the object slot moved
  3/3 against 200/200 witnesses. This breaks the standing heuristic "under the boundary ⇒ embedded
  ⇒ the bytes relocate", and it cleared nokogiri `xml_sax_parser_context.c:52`. Check
  `Hunt.embedded?` on the actual object; never infer mobility from `bytesize`.
- **A different extension's function-locals read as this file's statics.** digest's
  `Init_bubblebabble` (`bubblebabble.c:129`) declares `VALUE rb_mDigest, rb_mDigest_Instance,
  rb_cDigest_Class;` as **locals**, and a tree-wide source scan read that function's assignments as
  stores to `digest.c`'s file statics — three manufactured hits, removed by scoping slots per
  translation unit.
- **First-wins callback resolution picks the wrong body.** `ext/nokogiri` has two static functions
  named `mark` (`xml_document.c:56`, `xslt_stylesheet.c:6`); the document's won on glob order, so
  `nokogiriXsltStylesheetTuple.func_instances` reported as unmarked when its own file marks it
  three lines down. Proven by mutation: rename that one symbol and 1 suspect → 0.
  **But the type is still defective for a different reason** — `xslt_stylesheet.c:63` hands libxslt
  `ss->_private = (void *)self` with no `dcompact`. A row can be a false positive on the *field*
  and a true positive on the *type*; clearing it for the wrong reason hides the right one.

### Four zeros that were not clearances

- **A predicate family can be structurally blind to a whole library.** `gumbo-parser/` is about
  half of nokogiri's C, and all four predicates report zero on it — because
  `rg -l '\bVALUE\b|rb_[a-z_]+\('` over `gumbo-parser/src/` returns **0 files**. There is no Ruby
  API there at all, while B and D still resolved 35 files / 333 functions, so the parser worked.
  Zero rows with a healthy funnel and no Ruby API means **no verdict**, not a clean sheet. The
  class lives in the *binding* (`ext/nokogiri/gumbo.c`), which is where the rows actually were.
- **A `--self-test` that silently runs a *smaller* suite.** The first report of this said B and D
  "print `fixture missing` and exit 0" given the corpus parent instead of `$CORPUS/*/`. **That is
  wrong and was checked: both exit 1.** The real defect is worse because it is quiet — fixtures were
  looked up with `if d is None: continue`, so a **partial** pool skipped whole checks and still
  printed `PASS`. Only the *count* moves. An empty pool announces itself; a pool missing three trees
  does not. Read the count, never the word. (Recorded with its correction because the wrong version
  reached a shipping skill before anyone re-ran it — an agent reporting a methodology defect can be
  wrong about it, which is the rule this library already states and this round still had to relearn.)
- **An instrument copied out of a moving worktree.** A mid-edit snapshot of `sweep_unmarked.py`,
  taken while three agents were editing, **failed its own self-test 6/26**. Snapshot with
  `git show HEAD:<path>` and record each `shasum`, or the sweep measures an artefact of the clock.
- **A compaction run that recorded zero compactions.** A 20,000-iteration `auto_compact` run on
  nokogiri logged **0 compactions**; its sensitivity is zero and it clears nothing. It was
  excluded, and the row was cleared on a 101,607-compaction run instead. Report compactions next
  to iterations, always.

### The pinning-mark discharge, deferred twice and built in round 10 — and the corpus had the trap

zlib's seven `z->buf` rows had been hand-cleared every round on one line: a true predicate-D shape
cleared by a **pinning `rb_gc_mark`**. Round 9 found more of the same disposition — json
`parser.c:2410`, msgpack `buffer.c:317`/`:340`, unicorn's registered `httpdate` buffer. It was not
built in rounds 8 or 9, deliberately: confusing `rb_gc_mark` with `rb_gc_mark_movable` would
over-clear the corpus in one step, so it needed a generated red for **both** marks plus a third for
"named in neither".

Round 10 built it, with those three reds plus two more — the same pinning call under a `.dmark` of
`NULL` (registration is what makes the claim true), and a green for a dmark that delegates to a
helper. **47 rows off the 99-tree corpus, 459 → 412, 0 added, 0 columns changed, and no other
discharge's count moved.** Predicate A, B and C output is byte-identical.

**The thing worth carrying is that the deferral was right, and the corpus proved it rather than the
fixture.** zstd-ruby 2.0.6's `streaming_decompress_mark` spells both marks for the *same field*:

```c
#ifdef HAVE_RB_GC_MARK_MOVABLE
    rb_gc_mark_movable(sd->buf);      /* the branch that compiles today */
#else
    rb_gc_mark(sd->buf);
#endif
```

A sweep keeps the code inside conditionals, so a rule asking "does a pinning mark name this field
anywhere" answers **yes** — and clears `streaming_decompress.c:133`, `RSTRING_PTR(sd->buf)` with an
`rb_str_new` before the read. **Movable beats pinning, per field, and the subtraction is asserted on
that tree and not only on a fixture.**

**Round 11 withdraws the reason round 10 gave for that assertion, and keeps the assertion.** :133
was described as "one of zstd's confirmed real rows". It is not — it is `TRIAGED["zstd-"]` residue.
`sd->buf = rb_str_new(NULL, ZSTD_DStreamOutSize())` is 131,072 bytes, 213× the 616-byte embedded
boundary, so its bytes are a malloc block compaction does not move, and `streaming_decompress.c:42`
carries a real `dcompact`. That is the complete *relocate* idiom, same regime as `sc->buf`, which
this file already records as cleared by execution. The assertion survives on the stronger argument:
`have_func('rb_gc_mark_movable')` is **yes** on 4.0.6 and 3.4.10, so the `#else` arm never reaches
the compiler, and a rule that let it answer would be clearing a live row out of **dead code**. The
acceptance criterion is the index, not the row. The same shim also appears **cross-file** —
`#define rb_gc_mark_movable(x) rb_gc_mark(x)` in an `#else` arm of ffi's `compat.h:65` and mysql2's
`mysql2_ext.h:43` — which a per-file textual resolver would miss; grading the *spelling* at each
call site gets it right, and in the safe direction on old Rubies.

Two pin shapes are still hand-cleared and are scoped out by name: the **store side** (msgpack
writes the source String into a pinned field *after* deriving from a local — a true clear, but it
needs the store to dominate every window, which is a second rule with its own reds), and
**`rb_gc_register_mark_object`** (unicorn — pins harder than any dmark, but there is no type, no
field and no wrapper, and registration is per-*slot*: a later assignment to the same slot leaves the
new String unregistered while a (type, field) key still matches).

### …and round 10 shipped it too wide in two places. Both are review findings on #32

The rule cleared **every** escape, on the argument that a pin outlasts any window a sweep can
classify. **It outlasts every window bounded by the frame, and nothing else.** The pin is
`rb_gc_mark(w->buf)` in the wrapper's own `dmark`; it runs while the wrapper is reachable. So
`static const char *saved; saved = RSTRING_PTR(w->buf);` was cleared — wrapper collected, mark stops
running, String freed, `saved` dangles, with a correct pinning mark present the whole time. A Class
B use-after-free cleared by the instrument that exists to find it, and the reviewer reproduced it
before the maintainer did.

The fix is not "the destination is a struct field" and not "the base identifier matches": `o->held =
p` for a pointer **parameter** is the same `STORES-INTERIOR` spelling writing into an aggregate the
caller owns, and `w->peer->held = p` starts at the same identifier and ends in a different object.
It is **one pointer hop off the derivation's own base, then in-object member hops** — which is every
real escape in the corpus, because the shape is a struct caching an interior pointer into a String
the same struct owns and marks (`z->stream.next_in = RSTRING_PTR(z->input)`, 44 rows). And **all**
escapes on a row must qualify, not any.

Separately, every syntactic pin call reachable from a registered `dmark` was recorded as
unconditional. **A pin that runs only sometimes is not one** — `if (w->pins) rb_gc_mark(w->buf);`
marks nothing on the other path. Three shapes, three rules, and each is one the other two read as
unconditional: the **braceless** arm (no block of its own, so a brace test sees the whole function),
the **braced** arm (its head is `{`, so there is nothing for the arm test to read), and the **early
return** (depth zero, no conditional head — mysql2's `rb_mysql_stmt_mark`). The polarity is inverted
from every other caller of these rules: elsewhere an unrecognised guard kills a live row, so narrow
is safe; here it *clears* one, so the composition deliberately over-reports.

**Cost: 412 → 414 over the 99 trees.** The escape tightening returns 2 rows, both over-reports this
pass accepts (json's `return rb_utf8_str_new(ptr + offset, …)` copies the bytes, but saying so is a
different rule). The conditional tightening returns **none** — it drops 33 of 148 pin keys and not
one is a key a discharged row joins on. **It drops 0 of 69 movable keys**, which is what keeps the
dead-`#else`-arm subtraction above working; gating `movable` on the same test would rebuild the
round-10 over-clear out of the round-11 fix. A corpus-neutral tightening is exactly the one a green
suite cannot tell from no tightening, so both ship with generated reds and five mutation modes.

### A tightened rule gets reviewed against its new surface, and three more shapes came back

The second review pass on the same PR found three, all of them ways the *fixed* rule still clears
what it should not. None moves a corpus row — the fixtures are the entire evidence — and two of the
three are the previous fix being routed around rather than new territory.

**The condition can live on the call edge.** `if (w->pins) mark_fields(w);` where `mark_fields`
itself does an unconditional `rb_gc_mark(w->buf)`. The per-body test looks inside the helper, sees
depth zero, and says unconditional — so the conditional-pin fix is bypassed one scope out. A closure
that answers "can a mark callback reach this function" is the wrong closure; it has to answer "is
this function reached on **every** execution", which is reachability propagated along edges. The
same three tu_scope rules apply, now to the edge instead of to the call, which is the evidence that
reusing them was right rather than inventing a fourth opinion about "conditional".

**Gate `pinned`, never the closure.** The one-line-shorter version drops conditionally-reached
helpers from the closure entirely — and then a *movable* mark behind a conditional edge is gone from
`movable` too, the per-key subtraction stops firing, and a sibling unconditional `rb_gc_mark` on the
same field answers for it. That is the round-10 over-clear rebuilt out of the round-11 fix, so the
closure returns two sets.

**A rebound name stops denoting what the rule looked it up under.** Two spellings, one mechanism,
both a write *after* the derivation:

| | | breaks |
|---|---|---|
| `p = RSTRING_PTR(w->buf); w->buf = other;` | the **member path** | the pin — the dmark marks what the field *holds*, so it now pins the replacement while `p` points at the original |
| `p = RSTRING_PTR(w->buf); w = other; w->held = p;` | the **base** | the destination test, whose base comparison is textual — the same characters now name a different object, which can outlive the one holding the String |

The pin claim survives the second (every `struct wrap` has `buf` pinned), which is exactly why one
leg does not cover the other. **The shape is in the corpus four times** — json's
`cResumableParser_feed` rebinds `parser->buffer` four lines after deriving from it, and
`zstream_discard_input` sets `z->input = Qnil` ten lines on, in all three zlib trees. All four are
*safe*, and safe for a reason this rule cannot see: the pointer is fully consumed by
`memcpy`/`memmove` first, so `copies-in-callee` — which runs **ahead** of `pinning-mark` — has
already cleared them. The discharge order is doing real work, and that is an argument for the order
rather than for a weaker rebinding test.

### Coverage is not row count

The C++ `namespace` / `extern "C"` brace dispositions, ported from predicate C into B and D, moved
**no rows at all** and took sassc from **95 to 983 indexed functions** — about 90% of that tree had
been invisible to every later stage, reading as a clean gem. When a fix is row-neutral, report the
funnel delta or the fix looks like a no-op.

### Iteration count is not sensitivity — instrument the window, not the loop

psych's `start_document_try` was reported in round 8 as needing a *coercible object* among the tag
directives, on the strength of "0/30 with plain Strings". **Round 9 refuted that**: the plain
round-trip (`Encoding.default_internal = ISO-8859-1`, then `Psych.parse_stream(src).to_yaml` with
8 `%TAG` directives) corrupts **20/20 with zero compactions**, on all nine ruby × psych cells. No
coercion needed; it is a plain use-after-free.

The more transferable half is why the *non*-adversarial number was worthless. 380,000 clean
round-trips sounds decisive. But **300,000 of them had zero GCs inside the emit call** — the export
dups were embedded-size, so they generated no `malloc_increase` and no malloc-limit GC ever landed
in the window. Pushing the handles past the 616-byte boundary bought real sensitivity: the
remaining 80,000 put **5,485 ordinary GCs and 3 compactions inside the window**, still clean. So
the honest bound is "one per 5,485 in-call GCs", not "one per 380,000 iterations".

This is the **same mechanism as one of the round's privately-routed findings**, arrived at from a
different direction — under the embedded boundary the conversions allocate nothing, so the GC rate
that drives the defect never rises. Two independent findings, one lesson: **when the subject is a
converted String, iteration count measures nothing until you have measured GCs inside the window.**
Count them with a probe; do not infer them.

### Two burned false negatives in the reachability query itself

- **`xargs -d '\n'` is GNU-only.** BSD `xargs` rejects it, and the sweep reported **0 hits across
  989 gem paths** with the error swallowed. A reachability query that returns zero because it never
  ran is indistinguishable from a gem nothing calls. Use `tr '\n' '\0' | xargs -0`, and validate
  the query against a known instance before trusting its silence — psych's own tree, in that case.
- **A name-directory lookup misses git and path gems.** `bundle list --paths` resolved **989** gem
  paths for the five apps; **66 of 446 locked names are git- or path-sourced**, and any query that
  builds paths from `<name>-<version>` skips every one of them. Our own forks are in that 66.

### Where a Ruby object is parked decides whether it pins — and `<main>` is the trap

"A live local is conservatively pinned" is **correct**, and round 9 tried to retract it on the
strength of a reproducer that appeared to fire through a plain local. The retraction was wrong; what
the matrix actually shows is narrower and more useful. Measured one variable per row, `Object.new`
and `StringIO`, **identical on ruby 3.4.10 / 4.0.0 / 4.0.6** under
`verify_compaction_references(expand_heap: true, toward: :empty)`:

| object reachable from | result |
|---|---|
| a **method** local | **pinned** |
| a **block** local | **pinned** |
| a method local closed over by a lambda | **pinned** |
| a method local in a frame that captured `binding` | **pinned** |
| a method local, compaction opened from a deeper C frame via `#to_str` | **pinned** |
| a **toplevel `<main>`** local | **MOVED** |
| a global array element | **MOVED** |
| an **ivar**, once the constructing frame has popped | **MOVED** |
| an **element of an Array** — even a live method-local Array | **MOVED** |

A local in a live Ruby frame pins, full stop; routing the window through a C frame changes nothing.
**The exception is the toplevel `<main>` scope**, and that is the hazard: a script written at top
level can look like it reproduces *through a local* when the real mechanism is an ivar or an array
element. Every reproducer in that round ran at top level. Write the subject into a **method**, and
assert relocation **through the path the C code actually reads** — the gem's own ivar — rather than
through whatever the script happens to hold.

Corollary for parking: an Array element is never pinned by its Array being local, which is why
`argv` pins the array and not its elements — the same fact this library already records for psych's
tag directives, arrived at from the other end.

### A "confirmed" that only fires under `verify_compaction_references` must say so

`GC.verify_compaction_references` is a debug API no production application calls. For nokogiri's two
IO sites the subject is a `T_DATA`/`T_OBJECT`, and with fragmentation built **in the subject's own
size pool** (800k retained `Object`s in the slot-40 pool, freed, subjects in late pages):

| trigger | IO relocated | same-run String witnesses |
|---|---|---|
| `GC.compact` ×5 | **0/200** | 200/200 |
| `GC.auto_compact = true` + `GC.start` ×5 | **0/200** | 200/200 |
| `verify_compaction_references(expand_heap:, toward: :empty)` | **200/200** | 200/200 |

The String column proves the compactor ran and had holes; plain compaction simply does not select
those pages. They are movable in principle, so this is a page-selection property, not immovability
— but a report that leans on the debug API without saying so is claiming a severity it has not
measured. The first attempt at this measurement was **invalid** and worth recording: it fragmented
with 100-byte Strings (`slot_size` 160) while `StringIO` is `slot_size` 40, so there were no holes
in the subject's pool at all. **Fragment in the subject's own size pool or the negative is void.**

Contrast, from the same gem in the same round: nokogiri's XSLT params are **Strings**, which do
relocate under ordinary compaction — that site reproduces under `GC.compact`, under `GC.start` with
`auto_compact`, and under plain allocation with **no `GC` call anywhere in the process**. Same gem,
same round, opposite answer, because of the subject's type. Do not generalise a compaction
sensitivity across subject types.

### One more silent-corruption false negative

A cell reported `fail 0/3` because the wrapper counted only non-zero exits. The run was corrupting
the whole time — `<out/>` where `<out>Employee List</out>` was expected, **exit 0, no exception**.
Same quiet face as `rb_rescue` swallowing a `NoMethodError` on the IO sites and yielding an empty
parse. **Assert the expected output, never the exit code**: a "didn't crash" regression test ships
green over a broken build.

### A detector calibrated against the defective build clears the fix and convicts the patch

The worst false positive of round 9, because it was loud, confident and **inverted**. A lifetime
probe for the mysql2 `prepare` transcoding bug reported **3,500 anomalies out of 3,500 prepares on
the *patched* build** — which reads as the fix introducing a memory fault.

The tell was the **100% rate**. A real memory fault is sporadic; *every single row* failing is the
signature of a wrong constant, not a wrong pointer. The probe asserted
`v.bytesize == padding.bytesize + 3`, and `padding + 3` is the **buggy** build's output: vanilla
ships raw UTF-8, so a latin1 server reads `C3 A9` as two characters. The correctly patched build
ships `E9` — one character, `padding + 2`.

```
variant=vanilla  returned bytes=[101, 195, 169]  bytesize=3   -> PASS
variant=patched  returned bytes=[101, 233]       bytesize=2   -> FLAGGED as anomaly
```

So the "failure" was a 3,500/3,500 **re-confirmation of the defect**, showing up in an assertion
that had baked the buggy behaviour in as correct.

This is a nastier failure mode than a detector that cannot fail. A green-only harness is merely
uninformative; this one actively **clears the defect and convicts the fix**, and it does so with a
number that looks like overwhelming evidence. Two rules follow:

- **Make the expected value variant-aware, or derive it from the specification rather than from a
  run.** Any constant you obtained by observing the current build encodes the current build's bugs.
- **Read a 100% rate as a bug in the harness until proven otherwise.** Round 4 recorded the mirror
  of this — a green fixture that passed on `slots=0`. Both are the same disease: the assertion was
  calibrated on the wrong side.

### Hit-set identity proves the pin's IN-CLASS posture, and nothing else

Rounds 6–8 upheld labels across a version boundary by showing the four predicates produced an
identical hit set on both versions. The method's own warning was that for **msgpack and cbor the
predicate output is byte-identical across the boundary while a real memory-safety fix sits in the
diff** — which reads as the method refuting itself. Round 9 chased it, and it does not:

**All four predicates key on invariants about objects the *Ruby GC owns*** — a `VALUE` field of a
managed struct, a file-scope `VALUE`, a by-value `VALUE` coerced in place, a `char *` derived from a
String. msgpack 1.8.4's fix is a use-after-free of a page in msgpack's **own `rmem` allocator**,
whose dangling pointers are `char *` / `void **` fields that never touch a Ruby object. cbor
0.5.10.3's is an integer constant and a control-flow assignment. **Both sit outside all four domains
by construction**, so the silence measures the instruments, not the gems.

The correction is to the *claim*, not the technique:

> Hit-set identity soundly proves **"the pin's in-class posture equals the audited version's."**
> It proves **nothing about what else the pin carries.**

Round 8 wrote both claims into one table cell, and that is the actual defect. State the narrow one,
and note that anything out of class is unmeasured — a version bump can carry a real fix your
predicate cannot see, and identity across the boundary is exactly what you would expect in that case.

Two rows in that section were also simply mislabelled, and re-running settled them in the other
direction: **rbtrace 0.5.2** and **rmagick 6.1.1** had been carried by identity and turned out to be
**confirmable by execution at the pin** — rmagick 3/3 corrupt under **ordinary GC**, with a positive
identification rather than a crash (the lookup returned the value registered under the churn
content, proving the dangling read hit the recycled slot), firing at ≥1000 same-pool allocations in
the window and clean at ≤100. Four of the six rows never belonged in the section at all: they had
been executed at their pinned versions in an earlier round, and one gem's hit sets were never
identical across the boundary to begin with.

### The failure mode to look for: a defect that empties an INDEX, not one that drops a row

Round 9 extracted two shared rules into `tu_scope.py`, each after it had been independently
re-derived several times across the four sweeps:

1. **Translation-unit scoping** — an internal-linkage name resolved tree-wide instead of in the
   using file. Patched **six times** across four scripts before it was named.
2. **Which braces open a storage scope** — `namespace X {` and `extern "C" {` are transparent, and
   a walk that counts raw braces indexes *nothing* inside one. Ported **four times**.

A third is queued and has been fixed three times already: the **post-declarator walker**, i.e. the
tokens between `)` and `{` — `__attribute__((...))`, `noexcept`, and C++ trailing return types
(`auto f(VALUE) -> VALUE {`).

**What they share is why they survived, and it generalises past this toolchain.** A defect that
drops one row looks like a defect that dropped one row. A defect that **empties an index** looks
like a **clean gem** — the funnel prints small honest-looking numbers, every later stage agrees,
and the output is indistinguishable from a well-audited extension. sassc read as clean at **95**
indexed functions; the real number is **983**. The fix moved +1,140 spans across nine trees and
changed **zero** rows.

So: **when a fix is row-neutral, report the funnel delta**, or it looks like a no-op and gets
reverted by the next person optimising. And when a gem reports zero, the first question is not
"is it clean" but **"did the parser see anything at all"** — which is what the coverage counters
are for, and why they are printed rather than logged.

**Corollary, learned the expensive way:** opening a walker up is not free. Relaxing the
post-declarator step to skip `__attribute__` made it **invent four functions** out of `XX(...)`
X-macro lists and a `__declspec(...)` before a `typedef enum {`. Every widening needs a **rejection
table asserted in the self-test**, not just an acceptance fixture.

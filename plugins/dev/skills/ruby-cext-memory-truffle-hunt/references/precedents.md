# Precedents

Real findings from three hunt rounds against the gems in the Basecamp app locks
(bc3, haystack, fizzy, launchpad, queenbee).

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
| fiddle | `Fiddle::Closure` | SEGV after compaction | [ruby/fiddle#211](https://github.com/ruby/fiddle/issues/211) — **queenbee ships fiddle 1.1.8** |
| pg | — | — | [ged/ruby-pg#734](https://github.com/ged/ruby-pg/issues/734) |
| libxml-ruby | `ruby_xml_registry.c:8` + reader | Three findings. The worst is not a raw `VALUE` at all: a file-static `st_table` of `xmlNodePtr → VALUE` held as raw `st_data_t` and read back **inside mark functions**, feeding dead VALUEs to `rb_gc_mark` | [xml4r/libxml-ruby#231](https://github.com/xml4r/libxml-ruby/issues/231) — corrupts the collector (`[BUG] try to mark T_NONE object`), not just a user call |
| sqlite3 | `database.c:525`, `aggregator.c:248` — `(void *)block` to `sqlite3_create_function` | Same shape PR #466 fixed for `busy_handler` and never extended | reported to the project |

## Round 2 — Class B: `char *` into a String's bytes

| Gem | Site | Mechanism | Outcome |
|---|---|---|---|
| google-protobuf | `map.c` `Map_index_set`, `message.c` `Map_initialize_kwarg` — key built with a NULL arena aliases a *temporary*, then the value conversion allocates before `upb_Map_Set` copies the key | Liveness. **Fires under ordinary GC** — one corruption in 150k iterations vs 100/100 under `GC.stress`. Silent key corruption, not a crash | [protocolbuffers/protobuf#29023](https://github.com/protocolbuffers/protobuf/issues/29023) — 4.29.3→4.35.1 + HEAD |
| openssl | `ossl_ssl.c:810/814/830` — ALPN/NPN callbacks stash the same `VALUE` the `dcompact` doesn't update | Class A regression from converting SSLContext to `rb_gc_mark_movable` | [ruby/openssl#1088](https://github.com/ruby/openssl/issues/1088) — **`master` only**; all releases pin and are safe |
| trilogy | `cext.c` — `connopt.hostname`/`path` re-read after `try_connect` to format the error | Mobility; `try_connect` releases the GVL | [trilogy-libraries/trilogy#312](https://github.com/trilogy-libraries/trilogy/issues/312) — 2.9.0/2.10.0 fail, **haystack pins 2.9.0** |
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
  allocator at all. So the GC window the brief described exists in **fizzy's** pin, not
  haystack's — the opposite of what the plan assumed.
- All three sites are nevertheless **clean by execution**, for a structural reason neither
  round found: `trilogy_buffer_expand` only fires once the packet buffer passes
  `TRILOGY_DEFAULT_BUF_SIZE` = 32768, so the source String must be tens of KB to reach an
  expand — far above the 616-byte embedded boundary, hence always a malloc block compaction
  never moves. The mobility half of Class B is unreachable here by construction.

**Neither trilogy fix is in a released version.** `fe2293f` and PR #313 (`63392f00`) both
merged 2026-08-05; `v2.12.6` is tagged 2026-06-23 and `git compare` puts it 21 and 20
commits behind them. Round 2's precedent row implied an upgrade path for #312. There is
none yet — this is internal remediation *blocked on an upstream release*.

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
| mysql2 | `result.h:9` `fieldTypes` | **Not this class at all** — a `VALUE` field of an xmalloc'd struct that no `dmark` marks. Freed by *ordinary* GC inside the very call that allocates it; `rb_ary_store` then writes through a freed slot | upstream (affected at HEAD, 0.5.4–0.5.7) **and** internal: the `0.5.4.latin1utf8` fork carries it |
| zlib | `zlib.c:1230` store / `:1116` read — `z->stream.opaque = (voidpf)obj` | `zstream_mark` marks `buf` and `input`, never `obj`; the `Z_NEED_DICT` branch reads it back | ruby/zlib, **all versions incl. HEAD** — private channel: it is a CRuby default gem |
| pg | `pg_connection.c:2994/:3055` | `PQsetNotice*` userdata; `pgconn_gc_compact` has no `self` back-reference | already covered by [ged/ruby-pg#734](https://github.com/ged/ruby-pg/issues/734); pg is in no app lock |
| prometheus-client-mmap | `mmap.rs:535-541` `track_rstring` | **A third shape.** `let key = str.as_raw()` — a `VALUE` laundered through Ruby as an Integer and used as a WeakMap key. Compaction leaves a stale key; a later string in the recycled slot collides and **evicts a live entry** | upstream (gitlab-org); affected at HEAD |

### Out of class, found by the same sweep

- **zlib-basecamp-patch 1.1.1 ships CVE-2026-27820.** `zstream_buffer_ungets`
  (`zlib.c:825-844`) overflows `z->buf`. Fixed upstream Nov 2025 and released 2026-03-05;
  the fork is frozen at ruby/zlib `785d747` (2021-03-07) and its `lib/zlib.so` **shadows the
  patched default gem** on `require "zlib"`. bc3 and haystack both ship it. Reproduced 3/3
  via `GzipReader#ungetc`, patched stdlib clean 3/3. The diff is the deliverable here: the
  entire fork is a version-string bump plus upstream's own 6-line `Bug #10961` fix.
- **zstd-ruby `skippable_frame.c:25-26`** — `rb_str_new(input_data, input_size + 8 + skip_size)`
  reads past the end of the argument's buffer. Deterministic SEGV at 1 MiB.
- **trilogy `cext.c:1190` (HEAD)** — `rb_enc_get(str)` before `StringValue(str)`, so any
  `to_str` object (including `SimpleDelegator`) NULL-derefs. `fe2293f` did not touch it.
- **websocket-driver `websocket_mask.c:9`** — no `Check_Type`; a non-String argument is
  dereferenced as an `RString`. **Latent**: both in-gem call sites are String by construction.

## Cleared by execution

Worth recording: a gem cleared *by execution* is a durable result, and the reason it is
safe teaches the idiom.

| Gem | Why safe |
|---|---|
| mysql2 (incl. the `0.5.4.latin1utf8` fork) | Query path: `args` is a stack local so the converted String is pinned even mid-nogvl, **and** there are explicit `RB_GC_GUARD`s — belt and braces, not bare pinning. Survived ~1700 concurrent compactions with the strings demonstrably relocating. `mysql_ssl_set` copies (verified against the real libmysqlclient in C). |
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

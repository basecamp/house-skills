# Precedents

Real findings from two hunt rounds against the gems in the Basecamp app locks
(bc3, haystack, fizzy, launchpad, queenbee).

Read the status honestly, because the skill it illustrates demands it:

- Most rows were reproduced by execution with a control, 3/3 — **but not all.**
  pg#734 and puma#3984 are **latent**: a code-level defect with no default-config
  repro. The openssl NPN sites and protobuf's HEAD verdict are **code reading only**.
- **Outcome means "what we filed", not "what upstream accepted."** All of these were
  filed by us and are unconfirmed. The one exception is trilogy#312, where byroot
  agreed at code level ("I'm unable to repro on my machine, but looking at the code,
  it indeed does look like a `GC_GUARD` is missing") and opened PR #313, still open.
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

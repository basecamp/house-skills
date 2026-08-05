---
name: ruby-cext-memory-truffle-hunt
description: >
  Use when a Ruby native extension segfaults intermittently, corrupts data under
  load, or breaks after GC.compact — and when auditing native gems for dangling
  pointers. The scent library for use-after-free and GC-invalidated pointers in
  C extensions: raw VALUEs handed to libraries, and char* into String bytes.
---

# Memory Safety in Ruby Native Extensions

## Overview

A C extension that hands a library a raw `VALUE` or a raw `char *` and lets it outlive the
call has a dangling pointer. Two things invalidate it:

- **Collection** — the object is freed and its memory reused (a plain use-after-free)
- **Relocation** — GC compaction moves the object; the library keeps the old address

Compaction gets the attention, but collection is the more dangerous of the two: it needs no
`GC.compact` and fires under ordinary GC. The worst bug found with this scent library — a
google-protobuf map-key use-after-free — corrupts data silently under ordinary GC with no
compaction involved at all: one corrupted key observed across 150,000 operations, against
100/100 under `GC.stress`.

This is the **scent library**. For the hunt machinery — corpus scoping, proof protocol,
delegation, filing — use [truffle-hunt](../truffle-hunt/SKILL.md).

Precedents: [references/precedents.md](references/precedents.md).
Harness: [references/harness.rb](references/harness.rb).

---

## The Two Classes

### Class A — a raw `VALUE` stored by a C library

```c
sqlite3_create_function(db, name, argc, flags,
                        (void *)block,        /* ✗ raw VALUE, stored by SQLite */
                        rb_sqlite3_func, NULL, NULL);
...
VALUE callable = (VALUE)sqlite3_user_data(ctx);   /* ✗ read back much later */
```

Keeping the object **alive** (an ivar array, a global) does not keep it **in place**.

### Class B — a `char *` into a Ruby String's bytes

Two sub-mechanisms needing *different* fixes:

| Sub-mechanism | Cause | Fix |
|---|---|---|
| **Liveness** | String never retained; GC collects it, buffer freed | retain it |
| **Mobility** | String *is* retained but **embedded**, so its bytes live in the object slot and compaction moves them | pin, copy, or force a heap buffer |

Mobility is the one everybody misses. **Measure the embedded boundary on your Ruby** — the
folklore 23 was the old max embedded *length* (so 24 was the first heap length); variable-width
allocation moved it ~26×:

```
ruby 4.0.6 arm64-darwin: embedded boundary at 616 (first NON-embedded length)
short (100B)  embedded=true   bytes 0x11ff99b30 -> 0x120384448  MOVED
long  (5000B) embedded=false  bytes 0x74c2f21000 -> 0x74c2f21000  STABLE
```

Most SQL statements, paths, hostnames, passphrases and XML fragments sit under that. (Not most
PEM: an RSA-2048 private key is ~1675 bytes and a cert ~977 — heap. EC keys and passphrases
are short.) A heap buffer is *stable* under compaction, **so a test using only a large string
exercises liveness alone and will wrongly clear a gem that has the mobility bug.**

**Length is a proxy, not the property.** A String that grew by `<<`, was built with
`String.new(capacity:)`, or came from `File.read` / `IO#read` / `StringIO#read` is
heap-allocated **even at 100 bytes** — and therefore stable under compaction. That is the same
false negative running in the opposite direction, and it hits the realistic case: input read
off a socket or file. Assert it, never infer it:

```ruby
raise "subject is not embedded" unless Hunt.embedded?(subject)
```

---

## The Discriminator

What keeps the hunt from drowning in sweep hits. The two classes are **not** symmetric here —
conflating them is the easiest way to burn a real lead:

> A **`VALUE`** created, stored and consumed inside one synchronous call is safe:
> conservative machine-stack *and register* scanning both marks **and pins** it
> (`rb_gc_impl_mark_maybe` → `gc_mark_and_pin`).
>
> A **`char *` is not covered.** `is_pointer_to_heap` rejects any pointer that isn't
> slot-aligned (`p % BASE_SLOT_SIZE != 0`), and `RSTRING_PTR` of an embedded String sits 24
> bytes into the slot. A `char *` is only as safe as the originating `VALUE` — and the
> compiler may drop that `VALUE` after its last *syntactic* use while the pointer is still
> live. That is the documented rationale for `RB_GC_GUARD`
> (`include/ruby/internal/memory.h`).
>
> So an in-call `char *` is safe **only** while a `VALUE` for the same String provably stays
> on the stack: an unmodified argument, a struct field on the stack, or an explicit
> `RB_GC_GUARD` after the pointer's last use. Anything stored at **registration** and read in
> a **later** call is never safe.

`yajl` is the honest example of surviving on stack-liveness alone: `yajl_parse` is
non-copying and re-enters Ruby from its callbacks, with no `RB_GC_GUARD` anywhere — correct
by accident. `mysql2`'s query path holds `RSTRING_PTR` across a nogvl call *and* carries
explicit `RB_GC_GUARD`s, so it is belt-and-braces rather than an example of bare pinning.

### The three safe idioms

| Idiom | How | Example |
|---|---|---|
| **Pin** | `rb_gc_mark` (not `rb_gc_mark_movable`) | openssl's ex_data, msgpack's buffer |
| **Relocate** | `rb_gc_mark_movable` **+** a `dcompact` calling `rb_gc_location` on *every* stored copy | ffi's `Function.c` |
| **Indirect** | Hand the library the address of a malloc'd struct, never a `VALUE` | sqlite3 PR #466's `busy_handler` |

Relocate is where it goes wrong: openssl `master` made SSLContext movable and added a
`dcompact` updating **one** of the **four** places it stashes the same `VALUE`. If a gem is
movable, enumerate every stored copy.

---

## The Scent

Suspect when all three hold:

1. A `char *` derives from a Ruby String (`RSTRING_PTR`, `StringValuePtr`, `StringValueCStr`,
   `RSTRING_GETMEM`), or a `VALUE` is cast to `void *`.
2. It reaches a library entry point that **does not copy**, or is held across a call that can
   trigger GC — anything re-entering Ruby, or releasing the GVL.
3. The String/object is neither pinned nor stack-live for the pointer's whole lifetime.

**High-signal non-copying APIs** — this list found every lead across two rounds:

```
BIO_new_mem_buf          xmlReaderForMemory       SQLITE_STATIC
CURLOPT_POSTFIELDS       MDB_val                  leveldb::Slice
sass_make_data_context   yajl_parse               upb_StringView
*_set_input_buffer       SSL_CTX_set_default_passwd_cb_userdata
SSL_CTX_set_alpn_select_cb                SSL_CTX_set_next_proto_select_cb
```

**Negative signals:** `RB_GC_GUARD` present; openssl's `volatile VALUE *` write-back idiom
(`ossl_obj2bio`); the String is a stack argument live across the whole call.

**A NULL/absent arena or allocator argument is a red flag.** protobuf's `Convert_StringData`
aliases the caller's bytes when passed a NULL arena and copies otherwise — the comment even
said "only needed temporarily", which was true for three of its five callers and false for the
two that mattered.

### Never conclude "copies" from the API name — or from reading the library's source

`xmlReaderForMemory` has had four different buffer regimes:

| libxml2 | implementation | effect |
|---|---|---|
| ≤ 2.10 | `xmlParserInputBufferCreateStatic` | **aliases** the caller's buffer — vulnerable |
| 2.11.x | `...CreateMem` + `xmlBufAdd` | eager copy |
| 2.12.x | `...CreateMem`, `ctxt->mem = mem` + `xmlMemRead` callback | **retains** the caller's pointer and reads from it lazily |
| 2.13+ | `...CreateMem` eager copy | copy |

Same call, four answers. **Check the linked library version** — `Nokogiri::VERSION_INFO`,
not the gem version — and then settle it by measurement, because reading the source is not
enough either: 2.12.x *looks* vulnerable (it stores the caller's pointer) but measures **safe**,
since libxml2 drains the whole buffer up front. Confirmed on packaged 2.12.9 with an 8.5 KB
document mutated in its late region: the reader still returned the pre-mutation bytes.

```ruby
reader = Lib::Reader.new(xml)
Hunt.mutate_in_place!(xml, "<r><item>BBBBBBBB</item></r>")   # asserts the buffer didn't move
# sees "BBBB" => NON-COPYING (reads the live Ruby buffer) => vulnerable
# sees "AAAA" => copied or drained up front => safe
```

No GC required. Two traps: **copy-on-write** — `String#[]`, `slice`, `split` share the
parent's buffer, and the first write unshares and *moves* the bytes, so an aliasing library
reads the OLD content and you conclude "safe" on the exact bug you're hunting (build the
subject at full length yourself; `mutate_in_place!` asserts this). And a document small
enough to be drained in one read can't show streaming — size the input past the library's
read chunk before trusting a "copies" verdict.

---

## Test Shape

A local variable is conservatively pinned and will mask the bug, so **park the subject in a
global**:

```ruby
$holder = [Subject.new(short_string)]   # < boundary => embedded => relocatable
GC.verify_compaction_references(expand_heap: true, toward: :empty)
# ...exercise the lazy/later read...
```

Run **both size regimes** — short embedded (mobility) and large heap with the reference
dropped plus churn (liveness). Clear a gem only when both pass. Controls, positive control and
3/3 per [truffle-hunt](../truffle-hunt/SKILL.md).

---

## Class-Specific False Negatives

Each of these made a genuinely broken gem report "survived".

**Churn must land in the subject's size pool.** For an *embedded* subject the freed slot is
only reused by fillers from the same GC size pool — check `ObjectSpace.dump(s)["slot_size"]`,
not `bytesize`: 100 B and 135 B share the 160-byte pool and both bite; 100 B and 10 B do not.
Matching the bytesize is the easy way to guarantee it, and is sufficient but not necessary.
Nokogiri reported clean 3/3 until this was fixed. Always dump the old address to confirm:

```ruby
Hunt.peek(old_addr, len)   # "ZZZZ..." good;  "<r><item>..." churn missed
```

*Two limits.* This doesn't apply to a **heap** subject — its bytes are a malloc block and any
filler size reclaims it; fillers need not be retained. And if churn seems not to bite a heap
subject, the subject is usually still **retained** — nothing was freed, which is correct, not
a defect.

**Lazy, one-shot registration.** If the C-side registration happens on first use and is then
frozen or guarded, compacting *before* that first use registers the callback with the
post-move address and the bug cannot appear. openssl looked clean until a **warm-up handshake**
was added before the compaction. Generally: exercise the object once, compact, then exercise
again.

**Verify which binary loaded.** RubyGems prefers a precompiled platform gem over a source build
of the same version. Two nokogiri results were silently produced by the wrong `.bundle`. Print
the loaded path; prefer `ruby -I<path>/lib` over `GEM_HOME`.

**The witness must not be a live local**, or conservative scanning pins it and it reports "did
not move" while compaction ran fine.

**`GC.stress` is an amplifier, not a prover.** It makes a narrow window reproducible, but a bug
that only appears under it may still fire in production — confirm by running long without it.
protobuf's went from 100/100 under stress to a single corruption across 150,000 ordinary-GC
iterations (0/50k, 0/50k, 1/50k). That second result proved it fires without stress; treat it
as an existence proof, not a rate.

## Class-Specific False Positives

- `ALLOCV_N` is **safe** — `imemo_tmpbuf` is conservatively marked.
- `rb_global_variable` statics are **safe**.
- `rb_protect` struct-smuggling is a different thing, not this bug.
- Absence of an `rb_gc_location` grep hit proves nothing — it's often macro-wrapped. Read the
  mark/compact functions.

---

## Validation Checklist

- [ ] Embedded boundary measured on this Ruby, not assumed
- [ ] `Hunt.embedded?(subject)` asserted — not inferred from bytesize
- [ ] Discriminator applied — in-call uses discarded before testing
- [ ] Copying semantics settled by mutation, and the *linked* library version recorded
- [ ] Both size regimes run
- [ ] Churn lands in the subject's `slot_size` pool; old address dumped to prove it bit
- [ ] Lazy registration exercised before compaction
- [ ] Loaded binary path printed and correct
- [ ] Witness parked in a global, relocation confirmed
- [ ] New scents and burned false positives fed back into this file

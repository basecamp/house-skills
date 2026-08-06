# Harness for hunting GC-compaction bugs in Ruby native extensions.
#
# Usage: put this on the load path and `require "harness"`.
#   COMPACT=0 in the environment turns compaction into a no-op => that run is the CONTROL.
#   Every finding requires: control passes, test fails, 3/3 runs.
#
# The comments below are load-bearing. Each one records a false negative that made a
# genuinely broken gem report "survived".

require "objspace"
require "fiddle"

module Hunt
  module_function

  # ---------------------------------------------------------------- compaction

  # SINGLE-THREADED harnesses only. `verify_compaction_references` installs a read
  # barrier by mprotect-ing heap page bodies, which is what makes it a detector -- and
  # what makes it WRONG for a concurrent harness. A second thread reading an *embedded*
  # String's bytes inside a GVL-released region faults on a page the compactor has
  # protected, and the fault handler needs the GVL to service it, so the process dies on
  # a pointer that was **valid and pinned**. Measured: 3/3 gdb backtraces with thread 1
  # in the nogvl C call and thread 3 in lock_page_body / gc_unprotect_pages /
  # gc_ref_update under gc_verify_compaction_references.
  #
  # That is a false positive for exactly the mobility regime this hunt targets, and it
  # bites only embedded subjects -- a >616B malloc'd buffer lives outside GC pages, which
  # is why earlier rounds using large strings never hit it.
  #
  # Use `compact_concurrent!` from any harness with a compacting thread.
  def compact!
    if ENV["COMPACT"] == "0"
      warn "[harness] CONTROL: compaction skipped"
      false
    else
      GC.verify_compaction_references(expand_heap: true, toward: :empty)
      warn "[harness] compaction done"
      true
    end
  end

  # Concurrent-safe: plain GC.compact installs no read barrier.
  #
  # It relocates FAR less, and sensitivity has to be earned over a whole run rather than
  # assumed per call. Measured on ruby 4.0.6 aarch64-linux:
  #
  #   verify_compaction_references, one call, fresh witnesses  -> 200/200 moved
  #   GC.compact,                   one call, fresh witnesses  ->   0/200 moved
  #   GC.compact,   witnesses planted once, 2,142 calls        ->   0/200 moved
  #   GC.compact,   fresh batch per round, ~1,100-1,600 calls  -> ~92% of 66k-98k moved
  #
  # So a single GC.compact routinely relocates NOTHING: re-planting per round is necessary
  # but not sufficient, and only the accumulated per-run witness count is evidence. Report
  # it. A concurrent run that never observed a relocation has sensitivity zero no matter
  # how many compactions it counted.
  #
  # And plain GC.compact does not expand the heap, so a defect whose trigger is heap
  # *expansion* does not reproduce under it at all -- measured on prometheus-client-mmap:
  # 20/20 evictions with expand_heap:, 0/20 with plain GC.compact, none within 20,000
  # allocations. If a concurrent harness needs expansion, you need a different instrument,
  # not a different flag.
  def compact_concurrent!
    if ENV["COMPACT"] == "0"
      warn "[harness] CONTROL: compaction skipped"
      false
    else
      GC.compact
      true
    end
  end

  # ------------------------------------------------------------------ measuring

  # Measure the embedded/heap boundary on THIS ruby. Do not assume a value --
  # variable-width allocation moved it from 23 bytes to 616 between releases.
  # Under the boundary a String's bytes live in the object slot and relocate with it;
  # at or above they are a separate malloc'd buffer and are stable under compaction.
  def embedded_boundary
    prev = nil
    (1..4096).each do |n|
      e = embedded?(+("a" * n))
      return n if prev == true && e == false
      prev = e
    end
    nil
  end

  # Neither length nor CONSTRUCTOR predicts the regime. At 100 bytes, measured on 4.0.6
  # arm64-darwin and 4.0.5 x86_64-linux (boundary 616 on both): a literal,
  # String.new(capacity: 100), and any SIZED read -- IO#read(100), readpartial(100),
  # sock.read(100) -- are EMBEDDED. capacity: 0 or 1000, +"" <<, byte-at-a-time growth,
  # IO#read(100, buf) into a reused buffer, and StringIO#read are HEAP. File.read of a
  # small file splits by PLATFORM: embedded on Linux, heap on macOS.
  #
  # So String.new(capacity:) is not a "force a malloc'd buffer" idiom, and a socket read is
  # not reliably heap. Always assert this on the actual subject; infer nothing, from either.
  def embedded?(str)
    raise TypeError, "not a String: #{str.class}" unless str.is_a?(String)

    ObjectSpace.dump(str).include?('"embedded":true')
  end

  # The GC size pool the object slot came from. This -- not bytesize -- is what decides
  # whether churn can reclaim a vacated slot.
  def slot_size(obj)
    require "json"
    JSON.parse(ObjectSpace.dump(obj))["slot_size"]
  end

  # RSTRING_PTR -- the actual byte address a C library would have taken.
  def bytes_ptr(str)
    Fiddle::Pointer[str].to_i
  end

  def object_addr(obj)
    ObjectSpace.dump(obj)[/"address":"([^"]+)"/, 1]
  end

  # Addresses arrive in two forms and only one of them survives `to_i`.
  #
  #   bytes_ptr    -> Integer                4877322120
  #   object_addr  -> String, hex, 0x-prefix "0x122b60788"
  #
  # `"0x122b60788".to_i` is 0. So `peek(object_addr(x), n)` -- the exact call the docstring
  # below tells you to make -- hit the null guard and returned "<null>" EVERY TIME, for
  # every object, on every run. The churn proof the harness advertises was unobtainable,
  # silently, in the false-negative direction. Parse before guarding, and raise on garbage:
  # a zero that arrives by accident must not read like a zero that was measured.
  def to_addr(addr)
    case addr
    when Integer then addr
    when String
      raise ArgumentError, "not an address: #{addr.inspect}" \
        unless addr =~ /\A(?:0[xX])?\h+\z/

      Integer(addr, 16)
    else Integer(addr)
    end
  end

  # Read back what is at an address now. Use this to PROVE the churn bit --
  # if the original bytes are still there, a "survived" result means nothing.
  #
  # Reading an UNMAPPED address SEGVs, and Ruby cannot rescue SIGSEGV. Since this is
  # called precisely on freed addresses, the read is fenced in a forked child: a crash
  # here is a HARNESS ARTIFACT, not a finding. Without the fence a `[BUG] Segmentation
  # fault` mid-run reads exactly like "the gem crashed" -- a false positive from the
  # instrument.
  def peek(addr, len)
    addr = to_addr(addr)
    return "<null>" if addr.zero?

    r, w = IO.pipe
    pid = fork do
      r.close
      # Silence the child's crash report: an unmapped read prints ruby's [BUG] banner,
      # which is exactly the "looks like the gem segfaulted" confusion this fence prevents.
      $stderr.reopen(File::NULL)
      w.write(Fiddle::Pointer.new(addr)[0, len]) rescue nil
      w.close
      exit! 0
    end
    w.close
    out = r.read
    r.close
    _, status = Process.waitpid2(pid)
    status.success? && !out.empty? ? out : "<unreadable>"
  end

  # -------------------------------------------------------------------- churn

  # Overwrite whatever now occupies the vacated slot.
  #
  # For an EMBEDDED subject the freed object slot is only reused by fillers from the same
  # GC SIZE POOL -- measured: a 100B subject (slot_size 160) is reclaimed by 100B/130B/135B
  # fillers, never by 10B (slot 40) or 600B (slot 640). Matching the subject's bytesize is
  # the easy way to guarantee the same pool: sufficient, not necessary. Check slot_size,
  # not bytesize. Get this wrong and the stale pointer reads intact data => false negative.
  #
  # For a HEAP subject (>boundary) the bytes are a malloc block, and smaller fillers do NOT
  # reliably reclaim it: measured against a freed 5000-byte buffer, a 5000-byte filler left
  # 0/5000 of the original bytes, while 1000B and 100B fillers each left 891/5000. Size-match
  # here too, and check the FULL length with peek -- the first 16 bytes changed in every one
  # of those cases, so a short peek reports success while most of the buffer is intact.
  #
  # If churn appears not to bite at all, the subject may simply still be RETAINED: nothing was
  # freed to reclaim, which is correct, not a harness defect.
  def churn(rounds = 20, size = 100)
    rounds.times { 2000.times { +("Z" * size) } }
    GC.start
  end

  # Fragment the heap BEFORE allocating the subject -- under GC.compact, allocation ORDER
  # decides whether your subject can move at all.
  #
  # The compactor slides objects from late pages into early holes. A subject allocated *before*
  # the fragmentation sits in an early page and is never a move candidate: it stays put through
  # any number of compactions while everything else shuffles beneath it.
  #
  # THE WITNESS CHECK DOES NOT CATCH THIS, which is what makes it the nastiest false negative
  # here. Measured on ruby 4.0.6 aarch64-linux, 100-byte embedded subject, 5 trials per cell --
  # witnesses reported 200/200 in EVERY cell:
  #
  #                                      | fragment before | fragment after
  #   GC.compact                         | moved 5/5       | moved 0/5   <-- silent dead run
  #   verify_compaction_references        | moved 5/5       | moved 5/5
  #     (expand_heap: true, toward: :empty)
  #
  # So it is MODE-SPECIFIC, and it bites exactly where compact_concurrent! lives: plain
  # GC.compact only fills existing holes, while verify_compaction_references with expand_heap
  # relocates almost everything and hides the ordering entirely. A harness that develops under
  # compact! and then switches to compact_concurrent! for a threaded run can go silently dead.
  #
  #   Hunt.fragment!            # first
  #   $holder = [subject]       # then the subject
  #
  # And when a row reports clean, assert the SUBJECT's own address moved -- witness counts prove
  # the compactor ran, never that your subject was movable.
  #
  # THE SAME TRAP HAS A SECOND, TYPE-SHAPED FORM, and fragment! does NOT fix this one.
  # Under plain GC.compact, class-shaped subjects do not relocate AT ALL: 8 rounds,
  # 1600/1600 String witnesses relocated, and not one T_CLASS moved -- INCLUDING a control
  # (rb_class_new + rb_const_set) that is provably unpinned and that relocates 3/3 under
  # verify_compaction_references in the same process. So compact_concurrent! has ZERO
  # sensitivity to a T_CLASS/T_MODULE subject even at full witness sensitivity, and a
  # "the class never moved" result taken under it is uninformative rather than clean.
  # Measured 4.0.6 / 3.4.10 / 3.1.6 arm64-darwin. Use verify_compaction_references
  # (expand_heap: true, toward: :empty) whenever the subject is a class or module.
  def fragment!(rounds = 60, size = 100)
    keep = []
    rounds.times { 3000.times { |i| s = +("F" * size); keep << s if i % 3 == 0 } }
    keep.each_index { |i| keep[i] = nil if i.even? }   # holes for the compactor to slide into
    GC.start
    GC.start
    nil
  end

  # ------------------------------------------------------------------ witnesses

  # Objects that must relocate, proving a clean negative is not a "nothing moved" artifact.
  # NOTE: witnesses moving does not mean the SUBJECT could move -- see fragment! above.
  #
  # Parked in a module-level array on purpose: a witness held in a live LOCAL is
  # conservatively pinned by the machine-stack scan and reports "did not move" even when
  # compaction ran perfectly.
  WITNESSES = []

  def plant_witnesses(count: 200, size: 100)
    WITNESSES.clear
    @witness_addrs = Array.new(count) do
      s = +("w" * size)
      WITNESSES << s
      bytes_ptr(s)
    end
    self
  end

  # => [moved, total]
  def witnesses_moved
    # 0/0 would print like a valid control and prove nothing -- fail loudly instead.
    raise "call plant_witnesses before compacting" if WITNESSES.empty?

    moved = WITNESSES.each_with_index.count { |s, i| bytes_ptr(s) != @witness_addrs[i] }
    [moved, WITNESSES.size]
  end

  def report_witnesses
    moved, total = witnesses_moved
    puts "witnesses relocated: #{moved}/#{total}"
    moved
  end

  # ------------------------------------------------------------- provenance

  # RubyGems prefers a precompiled platform gem over a source build of the SAME version.
  # Print what actually loaded before trusting any result.
  # Returns ALL matches, not the first: two loaded candidates is itself the finding.
  def loaded_binary(pattern)
    $LOADED_FEATURES.grep(/#{Regexp.escape(pattern)}\.(bundle|so)\z/)
  end

  # --------------------------------------------------------- GC-free discriminator

  # Does the library copy, or alias the live Ruby buffer? Mutate in place, then trigger
  # the deferred read.
  #
  #   Hunt.mutate_in_place!(xml, "<r><item>BBBBBBBB</item></r>")
  #
  # Library sees the new bytes => NON-COPYING => vulnerable.
  #
  # COPY-ON-WRITE IS A FALSE-NEGATIVE TRAP. String#[], #slice and #split return strings
  # SHARING the parent's buffer. The first write unshares and MOVES the bytes, leaving the
  # original content at the address the library recorded -- so an aliasing library reads
  # the OLD bytes and you wrongly conclude "copied => safe", the exact wrong verdict on the
  # exact bug being hunted. Build the subject at full length yourself, and this method
  # asserts the buffer did not move. A frozen subject raises FrozenError.
  def mutate_in_place!(str, replacement)
    raise ArgumentError, "length must match (#{str.bytesize} vs #{replacement.bytesize})" \
      unless str.bytesize == replacement.bytesize

    before = bytes_ptr(str)
    str.bytesplice(0, str.bytesize, replacement)
    raise "buffer moved (copy-on-write or realloc) -- result would be a false negative" \
      unless bytes_ptr(str) == before

    str
  end
end

if __FILE__ == $PROGRAM_NAME
  puts RUBY_DESCRIPTION
  puts "embedded boundary: #{Hunt.embedded_boundary} (first NON-embedded length)"

  # Self-check the instrument before trusting it on a gem. peek shipped returning "<null>"
  # unconditionally, and nothing in this file would have told you.
  probe = +("PEEKPEEKPEEK" * 4)
  %w[bytes_ptr object_addr].each do |how|
    got = Hunt.peek(Hunt.public_send(how, probe), 12)
    warn "[harness] peek via #{how}: #{got.inspect}"
    raise "peek(#{how}) is broken -- it cannot read a live object" \
      if got == "<null>" || got == "<unreadable>"
  end

  $subject = [+("a" * 100), +("a" * 5000)]
  before = $subject.map { |s| Hunt.bytes_ptr(s) }
  Hunt.plant_witnesses
  Hunt.compact!
  Hunt.report_witnesses

  %w[short(100B) long(5000B)].each_with_index do |label, i|
    now = Hunt.bytes_ptr($subject[i])
    puts format("%-12s embedded=%-5s bytes 0x%x -> 0x%x  %s",
                label, Hunt.embedded?($subject[i]), before[i], now,
                before[i] == now ? "STABLE" : "MOVED")
  end
end

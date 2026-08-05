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

  # Length is a PROXY, not the property. A String that grew by <<, was built with
  # String.new(capacity:), or came from File.read/IO#read/StringIO#read is heap-allocated
  # even at 100 bytes -- stable under compaction, and it will clear a gem that has the
  # mobility bug. Always assert this on the actual subject; never infer from bytesize.
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

  # Read back what is at an address now. Use this to PROVE the churn bit --
  # if the original bytes are still there, a "survived" result means nothing.
  #
  # Reading an UNMAPPED address SEGVs, and Ruby cannot rescue SIGSEGV. Since this is
  # called precisely on freed addresses, the read is fenced in a forked child: a crash
  # here is a HARNESS ARTIFACT, not a finding. Without the fence a `[BUG] Segmentation
  # fault` mid-run reads exactly like "the gem crashed" -- a false positive from the
  # instrument.
  def peek(addr, len)
    return "<null>" if addr.to_i.zero?

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

  # ------------------------------------------------------------------ witnesses

  # Objects that must relocate, proving a clean negative is not a "nothing moved" artifact.
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

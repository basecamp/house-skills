#!/usr/bin/env ruby
# frozen_string_literal: false
#
# CLASS B REPRODUCER -- a `char *` into a String's bytes, held across a window.
#
# Filed publicly as ruby/psych#812. Routed public under truffle-hunt SKILL.md §7 row 3:
# what pulls the trigger is which API the developer calls (emitting a Document that
# carries %TAG directives), not data flowing through a call the app already makes --
# `Psych.dump` passes a literal [] and never enters the loop. An application that
# round-trips UNTRUSTED YAML through Psych.parse_stream(...).to_yaml moves this to row 1,
# because there the attacker picks the number and contents of the directives; run the
# grep on your own corpus rather than inheriting ours.
#
# Kept here because it exercises both halves of the class in one file, with the control
# as a flag rather than a second program:
#
#   MOBILITY -- the converted String is alive but not PINNED, and relocates.
#   LIVENESS -- the conversion DUPPED, and the dup is rooted by nothing at all.
#
# One number from this run is worth more than the reproduction: 380,000 clean
# non-adversarial round-trips measured NOTHING, because 300,000 of them had zero GCs
# inside the emit call -- embedded-size dups generate no malloc_increase. See
# precedents.md, "Iteration count is not sensitivity".
#
#
# psych ext/psych/psych_emitter.c start_document_try(): lap i of the tag-directive
# loop stores a raw `char *` into data->head (an xcalloc'd array holding no VALUE),
# then lap i+1 overwrites the C locals `name`/`value` that were its only root --
# and lap i+1 can run arbitrary Ruby (StringValue -> to_str) and can allocate
# (rb_str_export_to_enc). libyaml does not copy until
# yaml_document_start_event_initialize, after the loop.
#
#   MODE=mobility  lap-0 handle is already UTF-8, so rb_str_export_to_enc returns the
#                  SAME object -- alive (tags Array) but not pinned (argv pins the
#                  Array, not its elements). Window = a compaction. CRuby zero-fills
#                  the vacated slot.
#   MODE=liveness  lap-0 handle is US-ASCII, so rb_str_export_to_enc DUPS. The dup's
#                  only root is the C local `name`. Window = ordinary GC + size-matched
#                  churn. NO compaction involved.
#   MODE=pc811     POSITIVE CONTROL: the already-confirmed sibling defect ruby/psych#811
#                  (raw VALUE in the libyaml write handler) driven through this same
#                  harness, so a clean negative here cannot be a dead harness.
#
#   COMPACT=0      CONTROL. The window becomes a no-op. Same program, one flag.
#
# Exit status: 0 = emitted correctly, 1 = corrupt (the finding), 2 = harness fault.

require "harness"
require "psych"
require "stringio"
require "objspace"

MODE  = ENV.fetch("MODE", "mobility")
HLEN  = Integer(ENV.fetch("HLEN", "100"))
WINDOW_ON = ENV["COMPACT"] != "0"

$window_fired = 0
$decoys = ObjectSpace::WeakMap.new
$notes = []

# ---------------------------------------------------------------- the window
#
# Runs from inside lap 1's StringValue(name) -> to_str, i.e. exactly between lap 0's
# `tail->handle = (yaml_char_t *)StringValueCStr(name)` and libyaml's copy after the
# loop. Returns a String so the C loop carries on normally.
class Window
  def initialize(payload) = @payload = payload

  def to_str
    $window_fired += 1
    if WINDOW_ON
      case MODE
      when "mobility"
        # Relocates the lap-0 handle. tags[0][0] is updated by the GC; tail->handle is not.
        GC.verify_compaction_references(expand_heap: true, toward: :empty)
      when "liveness"
        # Frees the unrooted dup, then reclaims its slot with same-size fillers.
        GC.start
        40.times { 2000.times { +("Z" * HLEN) } }
        GC.start
      end
    end
    @payload
  end
end

# --------------------------------------------------------------- the subject
#
# Built in a frame that POPS: a live local -- or even a stale VM-stack slot in a live
# frame -- is conservatively pinned and the run reports clean at sensitivity zero.
def build_tags(encoding)
  handle = (+("!" + "a" * (HLEN - 2) + "!")).force_encoding(encoding)
  prefix = (+("tag:example.com,2026:" + "p" * (HLEN - 21))).force_encoding(encoding)

  raise "handle is not embedded (#{HLEN}B) -- wrong regime" unless Hunt.embedded?(handle)
  $expect_handle = handle.dup.force_encoding("UTF-8")
  $expect_prefix = prefix.dup.force_encoding("UTF-8")
  $slot_size = Hunt.slot_size(handle)

  # Lap 1's handle is the coercing object; its prefix is an ordinary String.
  [[handle, prefix],
   [Window.new(+"!w!"), +"tag:example.com,2026:window:"]]
end

# Same-slot-size siblings, dropped before the call. If the window frees and reclaims
# these, it demonstrably frees and reclaims the lap-0 dup, which is the same size.
def plant_decoys(n = 8)
  n.times { |i| s = +("D" * HLEN); $decoys[s] = true }
  nil
end

def decoy_survivors = $decoys.keys.size

# ------------------------------------------------------------------- positive control
def run_pc811
  $holder = [Psych::Emitter.new(StringIO.new)]
  Hunt.plant_witnesses
  Hunt.compact! if WINDOW_ON
  moved, total = Hunt.witnesses_moved
  $notes << "witnesses #{moved}/#{total}"
  e = $holder[0]
  begin
    e.start_stream(Psych::Nodes::Stream::UTF8)
    e.start_document([], [], true)
    e.scalar("hello", nil, nil, true, false, Psych::Nodes::Scalar::ANY)
    e.end_document(true)
    e.end_stream
  rescue NoMethodError, RuntimeError => err
    return [:corrupt, "#{err.class}: #{err.message}"]
  end
  [:ok, "emitted"]
end

# ------------------------------------------------------------------- the run
def run_tagdirs
  Hunt.fragment!                       # allocation order decides movability under GC.compact
  tags = build_tags(MODE == "mobility" ? "UTF-8" : "US-ASCII")
  plant_decoys if MODE == "liveness"

  io = StringIO.new
  emitter = Psych::Emitter.new(io)
  emitter.start_stream(Psych::Nodes::Stream::UTF8)

  Hunt.plant_witnesses
  before = Hunt.object_ptr(tags[0][0])

  err = nil
  begin
    emitter.start_document([], tags, false)
    emitter.scalar("v", nil, nil, true, false, Psych::Nodes::Scalar::ANY)
    emitter.end_document(false)
    emitter.end_stream
  rescue RuntimeError => e
    err = "#{e.class}: #{e.message}"
  end

  moved, total = Hunt.witnesses_moved
  $notes << "witnesses #{moved}/#{total}"
  $notes << "subject object addr #{before == Hunt.object_ptr(tags[0][0]) ? 'STABLE' : 'MOVED'}"
  $notes << "slot_size #{$slot_size}"
  $notes << "decoy survivors #{decoy_survivors}/8" if MODE == "liveness"
  $notes << "window fired #{$window_fired}x"

  return [:corrupt, err] if err

  out = io.string
  line = out[/^%TAG .*$/]
  return [:corrupt, "no %TAG line emitted: #{out.inspect}"] if line.nil?

  want = "%TAG #{$expect_handle} #{$expect_prefix}"
  if line == want
    [:ok, "%TAG line intact"]
  else
    [:corrupt, "%TAG line differs: got #{line.inspect}"]
  end
end

# ------------------------------------------------------------------- provenance
loaded = Hunt.loaded_binary("psych")
raise "no loaded psych binary -- failed run, not a pass" if loaded.empty?
raise "two loaded psych binaries: #{loaded.inspect}" if loaded.size > 1
sha = `shasum #{loaded[0]}`.split.first

warn "== #{RUBY_DESCRIPTION}"
warn "== psych #{Psych::VERSION} libyaml #{Psych::LIBYAML_VERSION} platform #{RUBY_PLATFORM}"
warn "== loaded #{loaded[0]}"
warn "== sha1   #{sha}"
warn "== MODE=#{MODE} HLEN=#{HLEN} window=#{WINDOW_ON ? 'ON' : 'OFF (CONTROL)'}"

verdict, detail = MODE == "pc811" ? run_pc811 : run_tagdirs

warn "== #{$notes.join(' | ')}"
puts "#{verdict.to_s.upcase}: #{detail}"
exit(verdict == :ok ? 0 : 1)

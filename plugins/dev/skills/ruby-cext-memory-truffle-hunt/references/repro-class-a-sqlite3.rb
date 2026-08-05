# Orchestrator's own reproducer. Deliberately shares NO code with the agents' scripts and
# does not require harness.rb -- no Fiddle, no peek, no raw memory read anywhere, so a SEGV
# here can only come from the gem.
#
#   SITE=trace|authorizer|create_function|collation ruby 01_sqlite3_repro.rb
#   COMPACT=0 turns the compaction into a no-op => that run is the control.
#
# The subject is never held in a local at compaction time: a live local is conservatively
# pinned and would report "did not move" no matter what the extension does.

require "sqlite3"
require "objspace"

SITE = ENV.fetch("SITE", "create_function")
COMPACT = ENV["COMPACT"] != "0"

puts "ruby #{RUBY_VERSION} #{RUBY_PLATFORM}"
puts "sqlite3 #{SQLite3::VERSION}"
puts "loaded: #{$LOADED_FEATURES.grep(/sqlite3.*\.(so|bundle)\z/).inspect}"

def addr(o) = ObjectSpace.dump(o)[/"address":"([^"]+)"/, 1]

# Witnesses prove the compaction actually relocated things. Parked in a constant-held
# array rather than locals for the same reason as the subject.
WITNESS = []
300.times { WITNESS << +("w#{"x" * 60}") }
WITNESS_ADDR = WITNESS.map { |s| addr(s) }

$keep = []
# The SUBJECT is the callable whose VALUE was handed to SQLite as a user-data pointer --
# not the Database. Parked in a global array, never a local: an array slot is marked
# movable, while a live local is conservatively pinned by the machine-stack scan and would
# report "did not move" no matter what the extension does.
$subject = []

# Fragment BEFORE allocating the subject. The compactor slides objects from late pages
# into early holes, so a subject allocated first sits in an early page and is never a move
# candidate -- it stays put through any number of compactions while everything else
# shuffles beneath it, and the witness count reports 200/200 the whole time.
def fragment!
  keep = []
  60.times { 3000.times { |i| s = +("F" * 100); keep << s if i % 3 == 0 } }
  keep.each_index { |i| keep[i] = nil if i.even? }
  GC.start
  GC.start
end

setup = lambda do
  db = SQLite3::Database.new(":memory:")
  db.execute("create table t (v integer)")
  db.execute("insert into t values (10), (20), (30)")

  case SITE
  when "trace"
    seen = []
    blk = ->(sql) { seen << sql }
    db.trace(&blk)
    db.execute("select v from t")                 # warm up: lazy registration
    raise "trace never fired" if seen.empty?
    $keep << seen
    $subject << blk
  when "authorizer"
    auth = ->(*) { 0 }
    db.authorizer = auth
    db.execute("select v from t")
    $subject << auth
  when "create_function"
    fn = ->(ctx, v) { ctx.result = v.to_i + 1 }
    db.create_function("hunt_f", 1, &fn)
    raise "warm-up wrong" unless db.execute("select hunt_f(41)") == [[42]]
    $subject << fn
  when "collation"
    cmp = Class.new { def compare(a, b) = a.downcase <=> b.downcase }.new
    db.collation("hunt_c", cmp)
    db.execute("select v from t order by cast(v as text) collate hunt_c")
    $subject << cmp
  else
    raise "unknown SITE #{SITE}"
  end

  $keep << db
  db
end

fragment!
setup.call
db_addr = addr($keep.last)
subject_addr = addr($subject.last)

if COMPACT
  GC.verify_compaction_references(expand_heap: true, toward: :empty)
else
  warn "CONTROL: compaction skipped"
  GC.start
end

moved = WITNESS.each_with_index.count { |s, i| addr(s) != WITNESS_ADDR[i] }
db_moved = addr($keep.last) != db_addr
subject_moved = addr($subject.last) != subject_addr
puts "witnesses relocated: #{moved}/#{WITNESS.size}   " \
     "Database moved: #{db_moved}   subject (#{SITE}) moved: #{subject_moved}"

if COMPACT && moved.zero?
  abort "FAILED RUN: nothing relocated -- this run proves nothing either way"
end

# Witnesses moving proves the compactor RAN. It does not prove THIS subject was movable,
# and that gap is the nastiest false negative in the whole harness: under plain GC.compact
# a subject allocated before the fragmentation reports 0/5 moved while witnesses report
# 200/200. A run whose subject never left its slot never exercised the stale pointer, so
# reaching the bottom of this script proves nothing -- it is a FAILED RUN, not SURVIVED.
if COMPACT && !subject_moved
  abort "FAILED RUN: the #{SITE} subject did not move (witnesses #{moved}/#{WITNESS.size}). " \
        "Nothing went stale, so a clean result here is an artifact. Re-run; if it persists, " \
        "the subject is pinned and this instrument cannot test it."
end

# Now exercise the site again. Everything below runs THROUGH the stale pointer.
db = $keep.last
result =
  case SITE
  when "trace"           then db.execute("select v from t")
  when "authorizer"      then db.execute("select v from t")
  when "create_function" then db.execute("select hunt_f(41)")
  when "collation"       then db.execute("select v from t order by cast(v as text) collate hunt_c")
  end

puts "post-compaction result: #{result.inspect}"
# Only reachable when the compactor ran AND this subject relocated, so the call above
# really did go through an address SQLite recorded before the move.
puts "SURVIVED"

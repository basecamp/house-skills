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

setup = lambda do
  db = SQLite3::Database.new(":memory:")
  db.execute("create table t (v integer)")
  db.execute("insert into t values (10), (20), (30)")

  case SITE
  when "trace"
    seen = []
    db.trace { |sql| seen << sql }
    db.execute("select v from t")                 # warm up: lazy registration
    raise "trace never fired" if seen.empty?
    $keep << seen
  when "authorizer"
    db.authorizer = ->(*) { 0 }
    db.execute("select v from t")
  when "create_function"
    db.create_function("hunt_f", 1) { |ctx, v| ctx.result = v.to_i + 1 }
    raise "warm-up wrong" unless db.execute("select hunt_f(41)") == [[42]]
  when "collation"
    cmp = Class.new { def compare(a, b) = a.downcase <=> b.downcase }.new
    db.collation("hunt_c", cmp)
    db.execute("select v from t order by cast(v as text) collate hunt_c")
  else
    raise "unknown SITE #{SITE}"
  end

  $keep << db
  db
end

setup.call
db_addr = addr($keep.last)

if COMPACT
  GC.verify_compaction_references(expand_heap: true, toward: :empty)
else
  warn "CONTROL: compaction skipped"
  GC.start
end

moved = WITNESS.each_with_index.count { |s, i| addr(s) != WITNESS_ADDR[i] }
db_moved = addr($keep.last) != db_addr
puts "witnesses relocated: #{moved}/#{WITNESS.size}   Database moved: #{db_moved}"
if COMPACT && moved.zero?
  raise "nothing relocated -- this run proves nothing either way"
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
puts "SURVIVED"

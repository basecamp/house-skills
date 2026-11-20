#!/usr/bin/env ruby
# Usage: brakeman-diff.rb OLD_JSON NEW_JSON
# Compares two `brakeman -f json` reports by warning type, file and message.
# Brakeman can report the same message in one file several times, so each
# line gives the change in count, not only warnings that appeared or went.
require "json"

old_counts, new_counts = ARGV.map do |path|
  JSON.parse(File.read(path))["warnings"].map { |w| [ w["warning_type"], w["file"], w["message"] ] }.tally
end
abort "usage: brakeman-diff.rb OLD_JSON NEW_JSON" unless new_counts

puts "old #{old_counts.values.sum} new #{new_counts.values.sum}"
(old_counts.keys | new_counts.keys).each do |key|
  from, to = old_counts.fetch(key, 0), new_counts.fetch(key, 0)
  puts "#{from} -> #{to}  #{key.join(" | ")}" unless from == to
end

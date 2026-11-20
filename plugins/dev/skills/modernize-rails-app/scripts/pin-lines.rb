#!/usr/bin/env ruby
# Usage: pin-lines.rb OLD_LOCK NEW_LOCK
# Prints a temporary Gemfile pin for each gem that moved a minor or major
# version, holding it to its old minor line at the newest patch. A gem
# locked for several platforms is compared by its newest variant. Rails'
# own frameworks are left out. Delete each line for a gem the target Rails
# requires at the new line (read the gemspec constraints), then append the
# rest to the Gemfile for the pin-back procedure.
FRAMEWORKS = %w[rails railties activesupport activemodel activerecord activeresource actionpack actionview
  activejob actionmailer actioncable activestorage actionmailbox actiontext].freeze

def specs(path)
  File.readlines(path).grep(/\A {4}[\w.-]+ \(([^)]+)\)\n\z/)
    .map { |line| line.strip.match(/\A(\S+) \((.+)\)\z/).captures }
    .group_by(&:first)
    .transform_values { |pairs| pairs.map { |_, version| Gem::Version.new(version.split("-").first) }.max }
end

old_specs, new_specs = ARGV.map { |path| specs(path) }
abort "usage: pin-lines.rb OLD_LOCK NEW_LOCK" unless new_specs

old_specs.sort.each do |name, from|
  to = new_specs[name]
  next if to.nil? || FRAMEWORKS.include?(name)
  a, b = from.segments, to.segments
  next if a.first(2) == b.first(2)
  puts %(gem "#{name}", "~> #{(a + [ 0, 0 ]).first(3).join(".")}")
end

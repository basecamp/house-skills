#!/usr/bin/env ruby
# Usage: lock-diff.rb OLD_LOCK NEW_LOCK
# Lists each gem added, removed or changed between two Gemfile.lock files.
# A change is marked PATCH, MINOR or MAJOR by the first version segment that
# differs, or PLATFORM when only the platform suffix (-x86_64-linux-gnu)
# differs. A gem locked for several platforms is compared platform by
# platform. A changed PLATFORMS section prints first. Each MINOR or MAJOR move
# needs a reason: the target Rails requires it, or it goes back to its old
# minor line.
def specs(path)
  File.readlines(path).grep(/\A {4}[\w.-]+ \(([^)]+)\)\n\z/)
    .map { |line| line.strip.match(/\A(\S+) \((.+)\)\z/).captures }
    .group_by(&:first)
    .transform_values { |pairs| pairs.map(&:last).sort }
end

def platforms(path)
  File.read(path)[/^PLATFORMS\n((?:  .+\n)+)/, 1].to_s.split
end

def by_platform(versions)
  versions.to_h { |version| [ version.split("-", 2)[1] || "ruby", version ] }
end

# Pairs variants by platform. A platform on one side only is compared with
# the other side's ruby variant, or its only variant, when that one has no
# partner of its own: 1.15.7 -> 1.18.10-x86_64-linux-gnu is a move, not a
# removal and an addition.
def variant_pairs(olds, news)
  common = olds.keys & news.keys
  old_only, new_only = olds.keys - common, news.keys - common
  pairs = common.map { |platform| [ olds[platform], news[platform] ] }
  if (stand_in = fallback(olds, old_only)) && new_only.any?
    pairs += new_only.map { |platform| [ olds[stand_in], news[platform] ] }
    old_only -= [ stand_in ]
    new_only = []
  elsif (stand_in = fallback(news, new_only)) && old_only.any?
    pairs += old_only.map { |platform| [ olds[platform], news[stand_in] ] }
    new_only -= [ stand_in ]
    old_only = []
  end
  pairs + old_only.map { |platform| [ olds[platform], nil ] } + new_only.map { |platform| [ nil, news[platform] ] }
end

def fallback(variants, unpaired)
  platform = variants.key?("ruby") ? "ruby" : (variants.size == 1 ? variants.keys.first : nil)
  platform if unpaired.include?(platform)
end

old_specs, new_specs = ARGV.map { |path| specs(path) }
abort "usage: lock-diff.rb OLD_LOCK NEW_LOCK" unless new_specs

old_platforms, new_platforms = ARGV.map { |path| platforms(path) }
puts "PLATFORMS #{old_platforms.join(" ")} -> #{new_platforms.join(" ")}" unless old_platforms == new_platforms

(old_specs.keys | new_specs.keys).sort.each do |name|
  olds, news = old_specs.fetch(name, []), new_specs.fetch(name, [])
  pairs =
    if olds.size <= 1 && news.size <= 1
      [ [ olds.first, news.first ] ]
    else
      variant_pairs(by_platform(olds), by_platform(news))
    end

  pairs.each do |from, to|
    next if from == to
    kind =
      if from.nil? then "ADDED"
      elsif to.nil? then "REMOVED"
      else
        a, b = [ from, to ].map { |version| Gem::Version.new(version.split("-").first).segments }
        a == b ? "PLATFORM" : a[0] != b[0] ? "MAJOR" : a[1] != b[1] ? "MINOR" : "PATCH"
      end
    printf "%-8s %-32s %s -> %s\n", kind, name, from || "-", to || "-"
  end
end

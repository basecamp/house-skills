#!/usr/bin/env ruby
# Usage: gem-dates.rb CUTOFF gem=version ...
# For each gem, prints the locked version's release date and the newest
# release before CUTOFF (YYYY-MM-DD) in the same minor and major lines.
# Release data comes from the rubygems.org versions API.
require "json"
require "net/http"

# rubygems.org sometimes answers with an HTML error page; retry those.
def versions(name)
  3.times do |attempt|
    return JSON.parse(Net::HTTP.get(URI("https://rubygems.org/api/v1/versions/#{name}.json")))
  rescue JSON::ParserError
    raise if attempt == 2
    sleep 2
  end
end

cutoff = ARGV.shift or abort "usage: gem-dates.rb CUTOFF gem=version ..."
ARGV.each do |pair|
  name, version = pair.split("=")
  abort "usage: gem-dates.rb CUTOFF gem=version ... (got #{pair.inspect})" unless version
  all = versions(name).reject { |v| v["prerelease"] }.select { |v| v["platform"] == "ruby" }
  mine = all.find { |v| v["number"] == version }
  before = all.select { |v| v["created_at"] < cutoff }
  newest = ->(parts) { before.select { |v| v["number"].split(".")[0, parts] == version.split(".")[0, parts] }.max_by { |v| Gem::Version.new(v["number"]) }&.fetch("number") }
  printf "%-16s %-9s %-10s  minor-at-cut: %-8s major-at-cut: %s\n",
    name, version, mine && mine["created_at"][0, 10], newest.(2), newest.(1)
end

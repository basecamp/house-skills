#!/usr/bin/env ruby
# Usage: rails-deps.rb VERSION
# Prints the requirement each Rails framework at VERSION puts on a gem outside
# Rails, from the rubygems.org API. Compare it with the pin-back lines to find
# the gems the new Rails forces forward: Bundler 1.x reports a conflict as
# every edge into the gem, not the one that fails.
require "json"
require "net/http"

version = ARGV[0] or abort "usage: rails-deps.rb VERSION"
frameworks = %w[rails activesupport activemodel activerecord actionpack actionview activejob actionmailer
  actioncable activestorage actionmailbox actiontext railties]
frameworks.each do |name|
  response = Net::HTTP.get_response(URI("https://rubygems.org/api/v2/rubygems/#{name}/versions/#{version}.json"))
  next unless response.is_a?(Net::HTTPSuccess)

  JSON.parse(response.body).dig("dependencies", "runtime").each do |dep|
    next if frameworks.include?(dep["name"]) || dep["name"] == "bundler"

    printf "%-16s %-18s %s\n", name, dep["name"], dep["requirements"]
  end
end

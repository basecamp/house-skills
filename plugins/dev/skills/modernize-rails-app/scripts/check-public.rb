#!/usr/bin/env ruby
# Usage: check-public.rb Gemfile.lock (or the app directory)
# Lists each gem in the lock's rubygems.org section whose version rubygems.org
# does not publish for its platform. A private build installed locally (Rails
# LTS, say) can satisfy Bundler and land in the lock unnoticed.
require "json"
require "net/http"
path = ARGV[0] or abort "usage: check-public.rb Gemfile.lock"
lock = File.read(File.directory?(path) ? File.join(path, "Gemfile.lock") : path)
gem_section = lock[/^GEM\n  remote: https?:\/\/rubygems.org\/\n  specs:\n(.*?)\n\n/m, 1] or abort "no rubygems.org GEM section in #{path}"
specs = gem_section.scan(/^    ([\w\-.]+) \(([^)]+)\)$/)
missing = specs.reject do |name, locked|
  number, platform = locked.split("-", 2)
  published = JSON.parse(Net::HTTP.get(URI("https://rubygems.org/api/v1/versions/#{name}.json")))
  published.any? { |version| version["number"] == number && version["platform"] == (platform || "ruby") }
end
puts "#{specs.size} gems checked"
missing.each { |name, version| puts "NOT ON RUBYGEMS: #{name} #{version}" }

#!/usr/bin/env ruby
# Usage: sort-attrs.rb DIR...
# Sorts the attributes inside each HTML start tag of every file in DIR, in
# place. Rails 4.2 stopped sorting tag attributes, so without this every page
# differs from a Rails 4.1 page.
TAG = /<([a-zA-Z][\w-]*)((?:\s+[\w:-]+(?:="[^"]*")?)+)\s*(\/?)>/
ATTR = /\s+[\w:-]+(?:="[^"]*")?/

ARGV.each do |dir|
  Dir.glob("#{dir}/*").select { |path| File.file?(path) }.each do |path|
    text = File.binread(path).force_encoding("UTF-8").scrub
    sorted = text.gsub(TAG) do
      name, attrs, slash = $1, $2, $3
      "<#{name} #{attrs.scan(ATTR).map(&:strip).sort.join(" ")}#{" /" unless slash.empty?}>"
    end
    File.write(path, sorted)
  end
end

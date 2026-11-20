# Loaded with `ruby -r` ahead of gem-dates.rb: answers the rubygems.org
# versions API from versions.json, keyed by gem name.
require "json"
require "net/http"

module Net
  def HTTP.get(uri)
    name = uri.path[%r{/versions/(.+)\.json\z}, 1]
    JSON.generate(JSON.parse(File.read(File.join(__dir__, "versions.json"))).fetch(name))
  end
end

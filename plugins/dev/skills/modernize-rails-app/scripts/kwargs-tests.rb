#!/usr/bin/env ruby
# Usage: ruby kwargs-tests.rb FILE...
# Rewrites Rails 4 positional request calls in tests to Rails 5 keyword style:
#   functional:  get :a, {p}, {s}, {f}      -> get :a, params: {p}, session: {s}, flash: {f}
#   integration: get "/x", {p}, {h}         -> get "/x", params: {p}, headers: {h}
#   xhr :post, target, ...                  -> post target, ..., xhr: true
# Files under test/integration are integration tests; the rest are functional.
# A call whose hash mixes Rails 5 keywords with other keys, so that Rails 5
# reads it differently from Rails 4, is left as is, with a warning.
# Needs a Ruby with Prism (3.3 or later): run it from this directory, not the app.
require "prism"

VERBS = %i[get post put patch delete head].freeze
# The keywords each Rails 5 request helper accepts.
FUNCTIONAL_KEYS = %w[params session flash body method format xhr as].freeze
INTEGRATION_KEYS = %w[params headers env xhr as].freeze
# Rails 5.0 reads a hash as keywords when it holds one of these; otherwise it
# reads the hash as Rails 4 did.
FUNCTIONAL_MARKERS = %w[params session flash method body xhr].freeze
INTEGRATION_MARKERS = INTEGRATION_KEYS

def keys_of(node)
  return [] unless node.is_a?(Prism::KeywordHashNode)
  node.elements.map do |e|
    next e.slice unless e.is_a?(Prism::AssocNode)
    e.key.is_a?(Prism::SymbolNode) ? e.key.unescaped : e.key.slice
  end
end

def empty_hash?(node)
  node.is_a?(Prism::HashNode) && node.elements.empty?
end

ARGV.each do |path|
  source = File.read(path)
  integration = path.include?("test/integration")
  names = integration ? %w[params headers] : %w[params session flash]
  keys = integration ? INTEGRATION_KEYS : FUNCTIONAL_KEYS
  markers = integration ? INTEGRATION_MARKERS : FUNCTIONAL_MARKERS
  edits = []

  visit = lambda do |node|
    return unless node
    if node.is_a?(Prism::CallNode) && node.receiver.nil? && (VERBS.include?(node.name) || node.name == :xhr) && node.arguments
      args = node.arguments.arguments
      xhr = node.name == :xhr
      verb = xhr ? args.first.unescaped : node.name.to_s
      target = xhr ? args[1] : args[0]
      rest = xhr ? args[2..] : args[1..]
      hash_keys = rest.size == 1 ? keys_of(rest.first) : []
      keywords = hash_keys.any? && hash_keys.all? { |k| keys.include?(k) }
      mixed = !keywords && hash_keys.any? { |k| markers.include?(k) }
      if mixed
        warn "#{path}:#{node.location.start_line}: left as is: mixes Rails 5 keywords with #{(hash_keys - keys).join(", ")}"
      end
      unless target.nil? || mixed || (keywords && !xhr) || (rest.empty? && !xhr)
        parts = []
        parts << rest.first.slice if keywords
        rest.each_with_index do |arg, i|
          next if keywords || empty_hash?(arg)
          text = arg.slice
          text = "{ #{text} }" if arg.is_a?(Prism::KeywordHashNode)
          parts << "#{names.fetch(i)}: #{text}"
        end
        parts << "xhr: true" if xhr
        arguments = [ target.slice, *parts ].join(", ")
        call = node.opening_loc ? "#{verb}(#{arguments})" : "#{verb} #{arguments}"
        edits << [ node.location.start_offset, node.location.end_offset, call, node.slice ]
      end
    end
    node.compact_child_nodes.each { |child| visit.(child) }
  end
  visit.(Prism.parse(source).value)

  bytes = source.b
  edits.sort_by { |edit| -edit[0] }.each { |s, e, text, _| bytes[s...e] = text.b }
  File.write(path, bytes.force_encoding("UTF-8"))
  warn "#{path}: #{edits.size} calls"
end

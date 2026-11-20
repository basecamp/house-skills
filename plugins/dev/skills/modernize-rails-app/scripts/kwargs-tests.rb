#!/usr/bin/env ruby
# Usage: ruby kwargs-tests.rb FILE...
# Rewrites Rails 4 positional request calls in tests to Rails 5 keyword style:
#   functional:  get :a, {p}, {s}, {f}      -> get :a, params: {p}, session: {s}, flash: {f}
#   integration: get "/x", {p}, {h}         -> get "/x", params: {p}, headers: {h}
#   xhr :post, target, ...                  -> post target, ..., xhr: true
# Files under test/integration are integration tests; the rest are functional.
# Needs a Ruby with Prism (3.3 or later): run it from this directory, not the app.
require "prism"

VERBS = %i[get post put patch delete head].freeze
NEW_KEYS = %w[params session flash headers xhr format env as body].freeze

def keyword_style?(node)
  node.is_a?(Prism::KeywordHashNode) &&
    node.elements.all? { |e| e.is_a?(Prism::AssocNode) && e.key.is_a?(Prism::SymbolNode) && NEW_KEYS.include?(e.key.unescaped) }
end

def empty_hash?(node)
  node.is_a?(Prism::HashNode) && node.elements.empty?
end

ARGV.each do |path|
  source = File.read(path)
  names = path.include?("test/integration") ? %w[params headers] : %w[params session flash]
  edits = []

  visit = lambda do |node|
    return unless node
    if node.is_a?(Prism::CallNode) && node.receiver.nil? && (VERBS.include?(node.name) || node.name == :xhr) && node.arguments
      args = node.arguments.arguments
      xhr = node.name == :xhr
      verb = xhr ? args.first.unescaped : node.name.to_s
      target = xhr ? args[1] : args[0]
      rest = xhr ? args[2..] : args[1..]
      keywords = rest.size == 1 && keyword_style?(rest.first)
      unless target.nil? || (keywords && !xhr) || (rest.empty? && !xhr)
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

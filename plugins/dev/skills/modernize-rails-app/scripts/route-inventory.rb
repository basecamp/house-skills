# Usage: script/rails runner PATH/TO/route-inventory.rb   (from the app)
# Lists each public action of the app's own controllers that no route names
# explicitly, so only a catch-all `:controller/:action` route reaches it.
# Each one is either an action to route by name or a method that the
# catch-all exposed by accident. Runs on the app's Ruby: keep it 1.9-safe.
Rails.application.eager_load!
root = Rails.root.join("app").to_s
named = Rails.application.routes.routes.
  reject { |r| r.path.spec.to_s.include?(":controller") }.
  map { |r| [r.defaults[:controller], r.defaults[:action]] }.uniq

ActionController::Base.descendants.each do |controller|
  next if controller.abstract?
  controller.action_methods.sort.each do |action|
    file = controller.instance_method(action).source_location.to_a.first.to_s
    next unless file.start_with?(root)
    next if named.include?([controller.controller_path, action])
    puts "#{controller.controller_path}##{action}  #{file.sub(root + "/", "")}"
  end
end

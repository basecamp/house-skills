# What broke at each Rails step

From real upgrades. Add to the section for each step you take.

## 2.3 → 3.0 (Ruby 1.9.3 works)

Rewrite boot, routes, environments and the mailer in the Rails 3 shapes. The
release notes cover those. What the notes did not make obvious:

- **Session cookies.** A 2.3 cookie holding a flash fails with
  `uninitialized constant ActionController::Flash::FlashHash`, a 500 on
  every request until the cookie expires. Define the class as a subclass of
  the 3.0 flash and convert its used-keys `Hash` to a `Set` in `sweep`.
- **Missing templates.** 2.3 raised `UnknownAction` (404) when a format had no
  template; 3.0 raises `ActionView::MissingTemplate` (500). Map it to
  `:not_found` in `ActionDispatch::ShowExceptions.rescue_responses`.
- **`Accept: */*`** parses to a nameless MIME type, so `request.format.html?`
  is false. Real browsers send `text/html` first.
- **Method links.** `link_to ..., :method` renders `data-method`, which needs
  `rails.js` (copy the Prototype flavor from railties 3.0.20) and
  `csrf_meta_tag`. Pages that load no script, or an old Prototype, need the
  2.3 inline-form JavaScript instead; include the authenticity token in it.
- **CSRF on XHR.** 3.0.20 verifies XHR requests too. An unverified request
  resets the session; the app's Ajax must send the token.
- **Escaping.** Output is escaped by default. `raw` where the app emits HTML
  or JavaScript (`dom_id(x).to_json` in scripts, class attributes).
- **Extracted helpers.** `error_messages_for` → `dynamic_form`;
  `remote_form_for` left core.
- **sentry-raven 2.13** calls `prepend_around_action` (Rails 4) from its
  railtie. Load it with `require: false` and add `Raven::Rack` by hand.
- **openssl and libmariadb.** Require `openssl` before Bundler in
  `config/boot.rb`; a libmariadb that loads a newer libcrypto first makes
  Ruby 1.9.3's `OpenSSL::X509::Store.new` segfault. Remove the require at
  the Ruby 2.7 bump, where `config/boot.rb` must require nothing before
  `bundler/setup`.
- **Tests.** `assert_select` does not decode entities; use
  `css_select(...).first["attr"]` with `assert_match`, or `CGI.unescapeHTML`.
  In an integration session, the `Accept` header from an `xhr` call carries
  over to the next request; pass a browser `Accept` explicitly.
- **Times.** RSS and `Time.now` move to UTC; 2.3 used the server's zone.

## 3.0 → 3.1 (Ruby 1.9.3 works)

- **mysql2** must be 0.3; the adapter moved into Active Record.
- **`ActionDispatch::Static`** changed internals; app subclasses break every
  request. Use `config.static_cache_control`.
- **`:defaults` expansion** is now `jquery jquery_ujs`. An app on Prototype
  that uses `javascript_include_tag :all` loads scripts alphabetically.
  Restore `%w[prototype effects dragdrop controls rails]`.
- **Engines** are mountable; gems that offered `Gem.routes(self)` for 3.0
  need `mount Gem::Engine => "/path"`.
- **URL helpers** add the request's port; a host string that already carries
  the port doubles it.
- **Removed:** RJS (→ `prototype-rails` 3.1.0), `auto_link`
  (→ `rails_autolink` 1.0.9), `config.action_view.debug_rjs`.
- **Flash** is no longer a `Hash` subclass, so a 2.3 cookie no longer loads
  into a subclass of it. Load the old class as a `Hash` and convert it when
  the request first reads the flash (`alias_method_chain :flash` on
  `ActionDispatch::Request`; require `action_dispatch/middleware/flash` first).

## 3.1 → 3.2 (Ruby 1.9.3 works)

Take 3.2.22.5, the last release (2016-09-14). Bundler cannot resolve it with
`--conservative`.

- **Flash.** The flash middleware sweeps the session's flash before the
  request reads it, so a 3.1-style conversion on first read is too late.
  Convert the old flash in `ActionDispatch::Flash#call` instead.
- **Plugins.** `vendor/plugins` is deprecated (see "Plugins" in the guide).
- **Format in a template name** (`render template: "x/new.iphone.erb"`) is
  deprecated. Pass `formats: [:iphone]`. On Target 1 this also gave those
  pages their format's layout again; 3.0 and 3.1 had used the HTML layout.
- **`ActionDispatch::ShowExceptions.rescue_responses`** is deprecated. Use
  `config.action_dispatch.rescue_responses` in `config/application.rb`.
- **Fixes now in Rails and Rack.** Rails 3.2.22 limits XML depth
  (CVE-2015-3227) and Rack 1.4.7 limits parameter depth (CVE-2015-3225).
  Delete app backports of both. Deep XML raises `REXML::ParseException`.
- **`prototype-rails`** 3.2.1.
- **Markup.** `<textarea>` content starts with a newline, and inputs lose an
  empty `id=""`. Both are harmless.

## 3.2 → 4.0 (Ruby 1.9.3 works)

Take 4.0.13, the last release (2015-01-06). It is older than 3.2.22.5 and
lacks some fixes 3.2 has (see "Backported patches" in the guide).

- **Asset tags.** `javascript_include_tag :all`, `:cache`, and
  `config.action_view.javascript_expansions` are gone, and asset URLs lose
  the `?mtime` query. Target 1 kept its files in `public/` with a helper
  that lists them (library scripts first, `application` last), links
  `cache/all` when `perform_caching` is on, and appends the file's mtime in
  `compute_asset_path`. The asset pipeline can wait for a later step.
- **`attr_accessible`** is gone. `permit` the same attributes in the
  controllers (`params.require(:x).permit(...)`, or `params.fetch(:x, {})`
  where the form may be absent).
- **`protect_from_forgery`** defaults to `:null_session`. Rails 3.2 reset
  the session. Pass `with: :reset_session` to keep that, in every
  controller that calls it, including one that only adds `except:`.
- **`match`** without `via:` raises. `via: :all` keeps the old behavior.
- **Format validations** refuse `^` and `$`. Use `\A` and `\z`.
- **Finders.** `find(:all/:first, conditions:)`, `find_all_by_*`,
  `update_all(set, conditions)` and association `order:`/`conditions:`
  options are deprecated; 4.1 removes them. Move them to `where`, `order`,
  `limit` and scope blocks (`has_many :items, -> { order("position") }`).
  The Rails 2 `acts_as_list` plugin needs the same change.
- **XML request bodies** are no longer parsed into `params` (moved to
  `actionpack-xml_parser`). `ActiveSupport::XmlMini.depth` is not in 4.0.13.
- **Removed from the stack:** `Rack::Lock` in production
  (`allow_concurrency`), `Rack::Cache` (it had cached static files in the
  Rails cache), `ActionDispatch::BestStandardsSupport` (`X-UA-Compatible`).
  Middleware inserted relative to `Rack::Lock` needs a new anchor.
- **Backports** of `ActionDispatch::RequestId` and `ActiveSupport::Logger`
  are part of 4.0; remove the gems.
- **Configuration.** Set `config.eager_load` in every environment; remove
  `whiny_nils`.
- **`secret_key_base`** is warned about. Setting it upgrades signed session
  cookies to encrypted ones, which a rollback cannot read. Target 1 left it
  for a later step.
- **Tests.** `ActionController::IntegrationTest` →
  `ActionDispatch::IntegrationTest`. `rake test` runs every directory in one
  process, so the log has one `tests,` line.
- **Gems.** `prototype-rails` 4.0.1; `rails_autolink` 1.1.6 (later patches
  need Rails 4.1); `thor` at least 0.18.1.
- **Markup.** Attributes print in alphabetical order, `'` becomes `&#39;`,
  `<script>` and `<link>` drop `type`, and default `alt` text drops hyphens.

## 4.0 → 4.1 (Ruby 1.9.3 works)

Take 4.1.16, the last release (2016-07-12). Bundler cannot resolve it with
`--conservative`, and the full update moves many gems Rails does not need
(`builder`, `i18n`, `mail`, `multi_json`, `rake`, `sprockets`,
`sprockets-rails` on Target 1); pin each back. Rails needs `minitest` 5,
`tzinfo` 1 and `arel` 5. `activerecord-deprecated_finders` goes away.

- **Flash in the session** is a `{"discard" => [...], "flashes" => {...}}`
  hash, and `FlashHash` has `@discard`, not `@used`. A shim that converted
  an old flash to a 4.0 `FlashHash` now raises in views. Convert to the
  hash instead (see "Session cookies" in the guide).
- **`update_attributes(nil)`** raises `ArgumentError`. Permit with
  `params.fetch(:x, {})`.
- **`cookies_serializer`** is new. Leave it unset (Marshal) while old
  session cookies can arrive.
- **Cross-origin JavaScript.** CSRF protection now covers GET requests that
  return JavaScript to a non-XHR request. Target 1 renders JavaScript only
  for POST, PUT and DELETE, so nothing changed.
- **Gems.** `rails_autolink` 1.1.7 and 1.1.8 accept Rails 4.1 but fail to
  boot on Ruby 1.9.3; keep 1.1.6 there. `prototype-rails` 4.0.1 accepts
  `rails ~> 4.0`. `easymon` health checks render `text/plain`.
- **Markup.** The hidden `utf8`/token wrapper div is `display:none`.

## 4.1 → 4.2 (Ruby 1.9.3 works)

Take 4.2.11.3, the last release (2020-05-15). Rails needs `arel` 6,
`rack` 1.6 and `nokogiri` 1.6 (through `rails-dom-testing`); `nokogiri` 1.7
and later need Ruby 2.1, so 1.6.8.1 is the newest. New dependencies:
`activejob`, `globalid`, `rails-dom-testing`, `rails-html-sanitizer`
(pinned back to 1.3.0 on Target 1) and `loofah` (2.5.0). Keep `rake` on 10;
12.3 needs Ruby 2.0.

- **html-scanner is gone.** `prototype-rails` 4.0.1, its last release,
  fails to load (`cannot load such file --
  action_view/vendor/html-scanner`). Vendor it and rewrite
  `assert_select_rjs` with Nokogiri (see "Abandoned gems" in the guide).
- **Test selectors are Nokogiri CSS.** Quote attribute values that are not
  CSS identifiers: an unquoted `a[href=http://x]` is skipped with only a
  warning, so the assertion checks nothing. `css_select` takes no `?`
  substitution.
- **Helper overrides.** `hidden_field_tag` passes `type: :hidden` with a
  symbol key. An app `text_field_tag` that does not stringify keys renders
  two `type` attributes, and browsers show a text box.
- **`changed_attributes` is frozen.** Use `clear_attribute_changes`.
- **Nested strong parameters.** Permit each nested hash before mass
  assignment, or Rails raises `ActiveModel::ForbiddenAttributesError`.
- **Deprecations:** `deliver` → `deliver_now`; a record passed to `find`;
  `active_support.test_order` unset (set `:random`, the 4.1 behavior); in
  production only, `log_level` unset (set `:info`, the 4.1 default).
- **Markup.** Tags keep attribute order as written, the hidden-field
  wrapper div is gone, and inline CSRF tokens are masked (longer).
- **Brakeman** flags `rails-html-sanitizer` 1.3.0 (CVE-2022-32209); it
  needs `select` and `style` both allowed, which the default list is not.

## 4.2 → 5.0 (Ruby 2.2.2 or later)

Take 5.0.7.2, the last release (2019-03-13). Bump Ruby first, in its own
commit (see "When to bump Ruby" in the guide); Target 1 went from 1.9.3 to
2.2.10. Rails needs `arel` 7, `rack` 2 and `rails-dom-testing` 2; `rack` 2
forces `sprockets` 3.7. New dependencies come with `actioncable` (`nio4r`,
`websocket-driver`), even when the app does not load it.

- **Test requests take keyword arguments.** `get :show, { id: 1 }` becomes
  `get :show, params: { id: 1 }`, and `xhr :post, ...` becomes
  `post ..., xhr: true`. `scripts/kwargs-tests.rb` rewrites them.
- **`assigns` and `assert_template` are extracted** to
  `rails-controller-testing`. Use 1.0.4 on Ruby 2.2.
- **The flash middleware is gone.** A Rails 2.3 flash shim moves to
  `FlashHash.from_session_value` (see "Session cookies" in the guide).
- **Unknown formats are 406.** An action with a template for another format
  raises `ActionController::UnknownFormat`; map it if the app served 404.
- **`alias_method_chain` is deprecated**, in the app and in vendored gems.
  See "Monkey patches" in the guide.
- **Rack 2 `MockRequest`** calls `set_encoding` on the input; a test double
  for `rack.input` needs it.
- **Deprecations:** `*_filter` → `*_action`; `Mime::JS` → `Mime[:js]`;
  `render nothing: true` → `head`; `render text:` → `render plain:` (now
  `text/plain`); `ActionDispatch::Callbacks.to_prepare` →
  `ActiveSupport::Reloader.to_prepare`; `static_cache_control` →
  `public_file_server.headers`; `association(:reload)` →
  `association.reload`. The catch-all `:controller/:action` route is
  deprecated and removed in 5.2; replace it by 5.1.
- **Gems** may still call `*_filter` (removed in 5.1) or `render :text`.
  List them at this step so the 5.1 step can replace or patch them.
- **Middleware.** `ActionDispatch::Executor` replaces
  `ActiveRecord::ConnectionManagement` and `ActiveRecord::QueryCache`;
  `ActionDispatch::ParamsParser` is gone.
- **Markup.** `id: ""` now renders an empty `id`; use `id: nil`. Easymon's
  checks return `text/html` to a browser `Accept` header.

## 5.0 → 5.1 (Ruby 2.2.2 or later)

Take 5.1.7, the last release (2019-03-28). Rails needs `arel` 8 and
`erubi` (in place of `erubis`); nothing else has to move. An unpinned
`bundle update rails` moved 14 other gems a minor on Target 1; pin them
back (`scripts/pin-lines.rb`).

Rails 5.1 removes what 5.0 deprecated: `*_filter`, `render :text`,
`render nothing:`. Fix them, in the app and in its gems, in a preparation
commit on 5.0 (see "Each step" in the guide). On Target 1:

- An instrumentation gem called `before_filter` and `around_filter`. Move
  it to a revision without them.
- A failover gem called `render :text`, because its `has_render_plain`
  default needs Rails 5.2. Set it explicitly.
- The catch-all route is deprecated, and 5.2 removes it. See "Catch-all
  routes" in the guide.

At the bump:

- **Dirty tracking in after callbacks.** `attribute_changed?` inside an
  after callback is deprecated; use `saved_change_to_attribute?`. A
  callback that saved again and cleared the change to stop itself repeating
  no longer needs to clear it.
- **Middleware.** `ActionDispatch::RemoteIp` moves ahead of
  `Rails::Rack::Logger`.
- **Development server.** `rails server` binds to `ENV["HOST"]` when it is
  set; pass `-b 127.0.0.1` to the comparison servers.

## 5.1 → 5.2 (Ruby 2.2.2 or later)

Take 5.2.8.1, the last release (2022-07-12). Rails needs `arel` 9 and adds
`activestorage` and `marcel` to the lock; an app that requires each
framework's railtie does not load Active Storage. Nothing else has to move.
5.2.4.3 and later carry the fix for the CSRF token advisory that Brakeman
reports on 5.1.

At the bump:

- **mysql2.** The adapter needs `mysql2` `>= 0.4.4, < 0.6.0`; Bundler says
  `can't activate mysql2 (< 0.6.0, >= 0.4.4)`. On Target 1 the app pinned a
  fork's 0.3 branch for its latin1 handling, and the fork had a 0.4 branch
  with the same handling. See "Forks and git dependencies" and "Charset
  quirks" in the guide.
- **`image_tag` drops its default `alt`.** Earlier versions made one from
  the file name (`alt="Logo small"`). Nearly every page differs; set
  the change aside with
  `scripts/summarize-step.sh OUT 's/ alt="[^"]*"//g'`, and record it in the
  plan file.
- **Middleware.** `ActionDispatch::ContentSecurityPolicy::Middleware` and
  `Rack::TempfileReaper` join the stack. With no policy configured, no
  header changes.

## 5.2 → 6.0 (Ruby 2.5 or later)

Take 6.0.6.1, the last release (2023-01-17). Bump Ruby to 2.5 first; on
Target 1, Ruby 2.5.9 needed no gem change. `scripts/rails-deps.rb 6.0.6.1`
lists what Rails forces:

- `actionmailbox` needs `mail` 2.7.1 and `actiontext` needs `nokogiri`
  1.8.5, even when the app loads neither framework. `mail` 2.7 drops
  `mime-types` and `treetop`.
- `railties` needs `thor` 0.20.3; move an app pin there.
- `zeitwerk` joins the lock. `arel` leaves it.
- `sprockets` 3.7.5, a patch, adds `base64`; pin `base64` to the line that
  existed at the cutoff.

At the bump:

- **`secret_token` is gone.** Session cookies signed with it no longer load.
  See "Session cookies" in the guide.
- **Host authorization.** Development answers 403 `Blocked host` for any
  host but `localhost`; add the app's development domain to `config.hosts`.
  Production lists no hosts and allows all.
- **Deprecations for 6.1:** `update_attributes`, `response.content_type` in
  tests (use `media_type`), and template handlers whose `call` takes one
  argument (a vendored RJS handler; add `source = template.source`).
- **Error template.** Headings span lines; `summarize-step.sh` reads them.
- **Middleware.** `ActionDispatch::HostAuthorization` and
  `ActionDispatch::ActionableExceptions` join the stack.

## 6.0 → 6.1 (Ruby 2.5 or later)

Take 6.1.7.10, the last release (2024-10-23). Ruby 2.5 still works.
`scripts/rails-deps.rb 6.1.7.10` lists what Rails forces:

- `activesupport` needs `i18n` 1.6 and `tzinfo` 2.0; `thread_safe` leaves
  the lock.
- `railties` needs `rake` 12.2 and `thor` 1.0; move an app pin there.
- The adapter needs `mysql2` 0.5; Bundler says
  `can't activate mysql2 (~> 0.5)`. On Target 1 the fork's 0.5 branch was
  `force_latin1_to_utf8`, with no version in its name. Run the stored-bytes
  test, and its negative check, again (see "Charset quirks" in the guide).

At the bump:

- **`ActiveModel::Errors` enumeration** is deprecated, and Rails 7.0
  raises. `dynamic_form` 1.1.4 does it in `error_messages_for`; 1.2.0 reads
  the new error objects, keeps the `^` and Proc message handling, and has
  no Ruby requirement. Test the rendered error list before the swap.
- **Hidden fields** render `autocomplete="off"`. Update tests that compare
  the markup, and summarize with
  `summarize-step.sh OUT 's/ alt="[^"]*"//g; s/ autocomplete="off"//g'`.
- **Test database.** `db:test:load` raises
  `ActiveRecord::NoEnvironmentInSchemaError` on a database made before
  Rails 5.0; run `bin/rake db:environment:set RAILS_ENV=test`.
- **Middleware.** `ActionDispatch::PermissionsPolicy::Middleware` joins the
  stack. With no policy configured, no header changes.

## 6.1 → 7.0 (Ruby 2.7 or later)

Take 7.0.8.7, the last release (2024-12-10). Bump Ruby to 2.7 first (see
"When to bump Ruby" in the guide). On Target 1, Ruby 2.7.8 needed
`nokogiri` 1.11 (glibc's `canonicalize`), `json` 2.3 (keyword warnings),
and nothing required before `bundler/setup` in `config/boot.rb`.

Before the bump, switch to Zeitwerk on 6.1 in its own commit
(`config.autoloader = :zeitwerk`), since 7.0 removes the classic
autoloader. Add `bin/rake zeitwerk:check` to `bin/ci`. It checks only eager
loaded paths: a `lib/` that is on the autoload paths but not eager loaded
stays unchecked until Rails 7.1's `config.autoload_lib`.

`scripts/rails-deps.rb 7.0.8.7` lists what Rails forces:

- `actionpack` needs `rack` 2.2 and `actiontext` needs `globalid` 0.6.
- `actionmailer` adds `net-imap`, `net-pop` and `net-smtp`, which bring
  `net-protocol`, `date` and `timeout`. Pin each new gem to the minor that
  existed at the cutoff.
- `sprockets` and `sprockets-rails` leave the lock; Rails 7.0 does not
  depend on them.

At the bump:

- **Gem overrides of connection methods.** Rails 7.0 passes `async:` to
  `execute`. On Target 1, an instrumentation gem's query comment override raised
  `wrong number of arguments` on every query. Active Record's query logs
  (`config.active_record.query_log_tags_enabled`) write the same comment.
- **`ActionView::TestCase` at load.** `prototype-rails` referenced it when
  Action View loaded, which Rails 7.0 does during initialization; boot
  failed with `uninitialized constant AccountHelper`. Use
  `ActiveSupport.on_load(:action_view_test_case)`.
- **Deprecations for 7.1:** `legacy_connection_handling`; set it to
  `false`.
- **Notifications.** `ActiveSupport::Notifications.subscribe(:name)` raises
  `pattern must be specified as a String, Regexp or empty`; use a String
  or a Regexp.
- **Middleware.** `ActiveSupport::Cache::Strategy::LocalCache::Middleware`
  leaves the stack with `FileStore`, which no longer has a local cache.
  `ActionDispatch::ActionableExceptions` stays only where
  `consider_all_requests_local` is set.
- **Asset tags.** `stylesheet_link_tag` writes `media` after `href`.

## 7.0 → 7.1 (Ruby 2.7 or later)

Take 7.1.6, the last release (2025-10-28). Ruby 2.7 still works.
`scripts/rails-deps.rb 7.1.6` lists what Rails forces:

- `railties` needs `thor` 1.2.2; move an app pin there. It adds `irb`,
  which brings `rdoc`, `psych`, `reline`, `io-console`, `stringio`, `pp`
  and `erb`. Bundler 1.x picks the newest of each and fails on a Ruby
  requirement (`prism`); pin each to its minor at the cutoff before the
  update.
- `actionpack` needs `rails-html-sanitizer` 1.6 and `rails-dom-testing`
  2.2; `actionview` needs `erubi` 1.11. `rails-html-sanitizer` 1.6.2
  needs `nokogiri` 1.15.7, and `loofah` 2.21.
- `rack` 3 is allowed; keep `rack` 2.2. `rack-session` and `rackup` then
  resolve to 1.0, and `rackup` adds `webrick`.

At the bump:

- **Error reporters.** `ShowExceptions#render_exception` receives an
  `ActionDispatch::ExceptionWrapper`. sentry-raven's hook reported the
  wrapper. Prepend an app module that reports `wrapper.exception`.
- **Removed settings.** The `legacy_connection_handling` setter raises.
- **Deprecations for 7.2:** `cache_format_version` 6.1, the default
  without `load_defaults` (set 7.0), and `show_exceptions = true` (use
  `:all`).
- **`config.autoload_lib(ignore:)`** puts `lib` under eager loading and
  `zeitwerk:check`. Ignore `lib/tasks`, directories the app requires by
  hand, and files whose constant does not match the file name.
- **Error template.** The development error page has no `h1` or `h2`;
  `summarize-step.sh` reads the first line of the body.
- **Middleware.** `ActionDispatch::HostAuthorization` is added only when
  `config.hosts` lists a host, so production without hosts drops it.

## 7.1 → 7.2 (Ruby 3.1 or later)

Bump Ruby to 3.1 first, on Rails 7.1 (see "When to bump Ruby" in the
guide). On Target 1, Ruby 3.1.7 built from source in about 80 seconds,
with no extra flags, and every native gem compiled, `unicorn` 3.5.0
included. Keyword separation and Ruby 3.2's removals needed:

- `rails-controller-testing` 1.0.5: 1.0.4 broke every functional and
  integration test.
- `i18n` 1.8.11: 1.6 rendered validation messages as
  `translation missing`. 1.7 fixes that call, but 1.8 fixes the rest
  (`translate!`, Proc translations).
- `rake` 13.0.6: 12.x broke `FileUtils` calls in rake tasks
  (`mkdir_p`).
- `builder` 3.2.4: 3.1.4 references `Fixnum`, which Ruby 3.2 removes.
- `rexml` in the test group, for `Hash.from_xml`.
- Bundler 2.3.27 in the lock.

Gems that call `File.exists?`, which Ruby 3.2 removes, warn only under
`-W:deprecated`.

Before the bump, on Rails 7.1: Rails 7.2 deprecates
`ConnectionPool#connection`. A gem that calls it on every SQL query
gave about 2100 warnings on Target 1. Move it to a revision that calls
`lease_connection`. If that revision changes how the gem reports, test
what leaves the process, not the gem's notifications.

Take 7.2.4, the last release (2026-09-24). `scripts/rails-deps.rb 7.2.4`
lists what Rails forces: `concurrent-ruby` 1.3.1, `mail` 2.8 and
`useragent` 0.16. It drops `mutex_m`.

At the bump:

- **Lazy connections.** A checked-out connection connects at its first
  query, so `connection.active?` is `false` before it. easymon 1.4's
  database check returned 503; easymon 1.6.3 calls `connect!` first. Look
  for other `active?` checks in the app and its gems.
- **Removed:** `ActiveRecord::Base.clear_all_connections!`, often in a
  unicorn or puma `after_fork` hook. Call it on `connection_handler`.
- **Deprecations for 8.0:** `ActiveSupport::ProxyObject` (subclass
  `BasicObject`; `prototype-rails`' `JavaScriptProxy` does), and
  `to_time` without `to_time_preserves_timezone` (opt in with
  `config.active_support.to_time_preserves_timezone = true`).
- **Error template.** The development error pages change again;
  compare them by status line.

## 7.2 → 8.0 (Ruby 3.2 or later)

Bump Ruby to 3.2 first, on Rails 7.2, in its own commit. On Target 1,
Ruby 3.2.11 built from source in about 80 seconds, no gem moved, and the
only lock change was Bundler 2.4.19. The development error page then
prints the `error_highlight` source line as a Ruby array in its
plain-text copy; compare error pages by status line.

Take 8.0.5.2, the last release (2026-11-04). Rails 8.0 support ended on
2026-11-07, so Brakeman reports it as unmaintained.
`scripts/rails-deps.rb 8.0.5.2` lists `rails-html-sanitizer` 1.7.2,
which needs `loofah` 2.25, which needs `nokogiri` 1.19.5; the script
shows only the first link. The `nokogiri` line from `pin-lines.rb` then
fails to resolve: drop it. `activesupport` adds `uri`.

`bundle update rails --conservative` resolved on Target 1, but moved 19
gems a minor that Rails does not need. After the pin-back, `irb` and
`rdoc` re-resolved `psych` and `stringio` to new minors; run
`lock-diff.rb` again and pin those too.

At the bump:

- **`to_time`.** Rails 8.0 deprecates `to_time_preserves_timezone =
  true`; Rails 8.1 always preserves the zone. Set it to `:zone`.
- **Query log tags** are sorted by key:
  `/*action:index,application:myapp,controller:posts*/`. Update any
  test or log reader that expects the configured order.
- **`rails test` loads the app's tasks** to run `test:prepare`, in its
  own Rake application. A test that calls `Rails.application.load_tasks`
  loads them again, and Ruby warns
  `already initialized constant STATS_DIRECTORIES`. Load only the task
  file the test needs.

## 8.0 → 8.1 (Ruby 3.2 or later)

Take 8.1.4.1, the last release (2026-11-04). `actiontext` adds
`action_text-trix`. A conservative update moved the same unforced gems
as at 8.0; pin them back the same way, `psych` and `stringio` included.
On Target 1 the pin-back also switched the lock's `PLATFORMS` from
`ruby` to `x86_64-linux`; `lock-diff.rb` prints that first. Put it back
with `bundle lock --add-platform ruby`, then
`bundle lock --remove-platform x86_64-linux`.

At the bump:

- **`to_time_preserves_timezone`** is deprecated, and `to_time` always
  preserves the zone. Delete the setting.
- **Permissions policy middleware** is added only when the app configures
  a policy. Without one it set no header, so its absence from the
  middleware list changes nothing.

Then bump Ruby to 3.3, in its own commit: Ruby 3.2 reached end of life
on 2026-03-31. On Target 1, Ruby 3.3.13 built from source in about 90
seconds. `minitest` before 5.21 requires `mutex_m`, and Ruby 3.3 warns
that 3.4 removes it from the default gems; the warning reached
`bin/rails` output. Move `minitest` to 5.21, then replace each
`assert_equal nil` that it reports with `assert_nil`. Lock with Bundler
2.5.22, Ruby 3.3's default.

Then turn on YJIT, in its own commit. `load_defaults "8.1"` sets
`config.yjit = !Rails.env.local?`. An app that calls no
`load_defaults`, or calls it with a version below 7.2, keeps `false`,
so production runs without YJIT. Set it in `config/application.rb`.
Rails skips the setting
without a word when Ruby has no `RubyVM::YJIT.enable`: before Ruby 3.3,
or in a Ruby built without YJIT. A deploy image can lag the app's Ruby,
so check that the built image prints `true` for
`docker run --rm -e SECRET_KEY_BASE=x IMAGE bin/rails runner 'print RubyVM::YJIT.enabled?'`.
Test it in the suite: run
`bin/rails runner 'print RubyVM::YJIT.enabled?'` in a child process,
once with `RAILS_ENV=production` and once with `RAILS_ENV=test`, with
`RUBY_YJIT_ENABLE` removed from the child's environment. By default,
YJIT in each worker holds at most 48 MiB of machine code on Ruby 3.3
(`--yjit-exec-mem-size`; its metadata is extra), and has a soft limit
of 128 MiB of total memory on Ruby 3.4 and later (`--yjit-mem-size`).
State the limit in the PR, and compare container memory and request
time before and after the deploy.

### Ruby 4.0, when the owner asks for it

No Rails 8.1 dependency requires Ruby 4.0, so take it only on request,
after the 3.3 commit. Replace unicorn first, in its own commit on the
old Ruby: `kgio` segfaults in `kgio_tryaccept` on Ruby 4.0
(`[BUG] Segmentation fault`), in 2.9.3 and in 2.11.4, its last release,
and every unicorn release depends on it. On Target 1:

- **puma.** Pin the minor released before the target Rails (8.1 shipped
  after Rails 8.1.4.1, so `~> 8.0.4`). Copy Fizzy's `config/puma.rb`
  (`basecamp/fizzy`, `main`) and run it the way Fizzy's `Dockerfile` does,
  through `bin/rails server`, which binds `0.0.0.0` outside development. No
  `bin/puma`. Inside Fizzy's `unless Rails.env.local?`: `workers` from
  `WEB_CONCURRENCY` and `threads 1, 1`, which keep unicorn's model, plus
  `enable_keep_alives false`, which Fizzy does not set. Unicorn closed each
  connection; a kept-alive one stays with one worker and waits behind its
  request, which the outside review caught and a run confirmed. Puma
  preloads only with more than one worker, and with none it never calls
  `before_worker_boot`. Active Record 7.1+ discards pools after fork, so
  drop the hook's `clear_all_connections!`. The file needs Rails loaded:
  test it in the suite through
  `Puma::Configuration.new(config_files: [...]).clamp` inside
  `Rails.stub(:env, ActiveSupport::EnvironmentInquirer.new("production"))`.
  Fizzy's `Process.warmup` needs Ruby 3.3, and its
  `GC.config(rgengc_allow_full_mark: false)` and `out_of_band` GC need Ruby
  3.4, so add them in a commit after the Ruby bump. Rename the worker-count
  variable in `config/deploy.yml`, and name in the PR the lost request
  timeout and any log tag that changed.
- **`bin/bundle`.** Current Rails apps have none, and Fizzy has none.
  Delete it if nothing runs it (`rg bin/bundle`).
- **Forced gems.** `rake` 13.0 and `json` before 2.7.2 require `ostruct`
  at load, which Ruby 4.0 dropped from the default gems: `rake` 13.2.1,
  `json` 2.7.6. `minitest` before 5.26.1 requires Ruby older than 4.0:
  5.26.2. `nio4r` 2.3.1 fails to compile (`incompatible-pointer-types`):
  2.5.9, the oldest that builds. Lock with Bundler 4.0.20
  (`bundle update --bundler=4.0.20`).
- **String literals.** Ruby 4.0 warns when code appends to a string
  literal. Create those strings with `+""` or `String.new`. Warnings left
  in vendored or abandoned gems go in the PR.
- **Post through the server.** Sign in by form, then send an Ajax write
  with `X-CSRF-Token`, against the old server and the new one in
  production mode, and again through the image's `CMD`.

### Fizzy's image: jemalloc, bootsnap and Thruster

After the last step, add what Fizzy's `Dockerfile` (`basecamp/fizzy`,
`main`) has and the app's lacks, one commit each. These change only the
image, so test each one against a locally built image, and compare it
with an image built from the base branch.

- **jemalloc.** In the base stage, install `libjemalloc2`, link
  `/usr/lib/$(uname -m)-linux-gnu/libjemalloc.so.2` to
  `/usr/local/lib/libjemalloc.so`, and set
  `LD_PRELOAD="/usr/local/lib/libjemalloc.so"`. Every process in the
  image runs with it. Check that `/proc/self/maps` of a Ruby process in
  the image lists `libjemalloc.so.2`. On Target 1,
  memory at boot fell only from 123 MB to 111 MB; compare steady-state
  container memory before and after the deploy.
- **bootsnap.** Add `gem "bootsnap", require: false` and
  `require "bootsnap/setup"` after `require "bundler/setup"` in
  `config/boot.rb`. In the build stage, run
  `bundle exec bootsnap precompile -j 1 --gemfile` after
  `bundle install`, and `bundle exec bootsnap precompile -j 1 app/ lib/`
  after the code is copied. `-j 1` avoids a hang under QEMU
  ([rails/bootsnap#495](https://github.com/rails/bootsnap/issues/495)),
  which emulates amd64 when an arm64 machine builds the image. On Target 1,
  production boot fell from 0.97 s to 0.67 s, and the cache added 42 MB
  to the image. Measure boot with
  `ruby -e 'require "./config/environment"'` in a fresh container, five
  runs each.
- **Thruster.** Add `gem "thruster", require: false`, run
  `bundle binstubs thruster`, and set
  `CMD ["bin/thrust", "bin/rails", "server"]` and `EXPOSE 80`. Set
  `proxy: app_port: 80` in `config/deploy.yml`. Thruster listens on 80,
  starts Puma with `PORT=3000`, compresses with gzip and caches
  responses marked public, up to 64 MB per container. On Target 1,
  adding the gem also added `x86_64-linux` to the lock's `PLATFORMS`,
  and the build then compiled `nokogiri` from source; remove the
  platform as at 8.1. Check that a static file comes back with
  `Content-Encoding: gzip` and, on the second request, `X-Cache: hit`.
  Name in the PR:
  - `bin/kamal rollback` to an image from before the change works only
    from a checkout of that version, because Kamal takes `app_port`
    from the current checkout.
  - Thruster answers `304` only on an `ETag` match. Rails sends static
    files with `Last-Modified` only, so a conditional request for one
    gets `200` with the full body.
  - Thruster logs one JSON line per request to standard output.
    `LOG_REQUESTS=false` turns that off.

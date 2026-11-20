# modernize-rails-app guide

Status: draft, developed on one app, "Target 1", taken from Rails 2.3.18 and
Ruby 1.9.3 to Rails 8.1 and Ruby 4.0. Expect it to be wrong.

## The method

One Rails minor version per step. Each step ends with:

1. `bin/ci` green, apart from steps that only a person can run (sign-off).
   Run the steps one by one: a green `bin/ci` runs `gh signoff`, which
   posts a commit status to GitHub.
2. A page comparison against the previous step's commit, with every
   remaining difference either fixed or listed as accepted.
3. A plan file for the step (see "Plan file").
4. A commit of the app, and a commit of the plan file.

Do not skip a minor version. Every skipped version hides its deprecations and
its removals behind the next one's, and the failures stop pointing at a cause.

## Before the first step

### Worktrees

Work in a worktree on a local branch. Keep these beside it:

- **baseline**: a detached worktree at the commit you started from. It runs the
  original app for the first comparison and for capturing old session cookies.
- **prev**: a detached worktree at the previous step's commit. Move it forward
  after each step (`git -C PREV checkout --detach SHA`).
- The plan files' repository, if the plan files live elsewhere.

### Test coverage first

Old apps have thin integration coverage, and a Rails upgrade breaks the
integration layer most: routing, middleware, sessions, CSRF, layouts, script
order. Before the first step, add end-to-end tests of the main flows, on the
old Rails, and see them pass there:

- Sign in, the main create/update/delete flow (including XHR), sign out.
- Each alternate format or device (an iPhone layout, RSS).
- A forged request without the CSRF token.
- A session cookie written by the old Rails, replayed into the new one (see
  "Session cookies").
- Health-check endpoints.

A small app needs few tests for this to pay off; they caught most of the
regressions on Target 1.

Test a gem's effect at the app's edge, not through the gem's internals: a
test that listened for an instrumentation gem's notification broke when
the gem stopped publishing it, though the Statsd packets were the same.
Capture what leaves the process (`Statsd::Batch#send_to_socket`, the
response, the mail). Transactional fixtures connect every connection pool,
so a test of behavior before the first query needs
`self.use_transactional_tests = false`.

### Tooling in its own bundle

Add RuboCop (`rubocop-rails-omakase`) and Brakeman to `bin/ci` early, but
run them from a separate `tools/Gemfile` on a modern Ruby
(`tools/.ruby-version`), through binstubs:

```sh
exec mise exec "ruby@$(cat "$tools/.ruby-version")" -- \
  env BUNDLE_GEMFILE="$tools/Gemfile" bundle exec rubocop "$@"
```

The app's Ruby cannot run current versions of these tools. Set RuboCop's
`TargetRubyVersion` to the app's Ruby (2.0 on Ruby 1.9, which RuboCop no
longer accepts) and raise it with each Ruby bump; RuboCop then flags syntax
the next Ruby rejects.

Defer bundler-audit until the app runs a supported Ruby. Before that it
reports advisories you cannot act on. Add Brakeman to `bin/ci` at the step
where it first passes; until then record its count in each plan file.
Add bundler-audit to `bin/ci` when it first passes too, and update the
binstub comments that say the app's Ruby is too old.

When the app reaches a supported Ruby, add bundler-audit back and clear
each advisory: move the gem to its lowest patched minor, and pin back what
else moves. A gem that caps a vulnerable dependency moves too. On Target 1,
`sentry-raven` 2.13 capped `faraday` below 1.0, so `sentry-raven` moved to
3.1.2 and `faraday` to 2.14.4. Send one event to a local listener to check
that the error tracker still reports.

## Each step

Before the bump, make a preparation commit on the current Rails: fix every
deprecation the next minor turns into a removal, in the app and in the gems
it loads. A gem that calls a removed API moves to the oldest revision that
does not; read what else that revision changes. The preparation commit gets
the same checks as a step.

1. Read the target version's section in `rails-versions.md` and the
   official upgrade guide or release notes for that version.
2. Pin `rails` to the last patch release of the target minor.
3. Resolve dependencies by release date (see "Dependency versions").
4. Make the gems load, then make `bin/rake test` pass. Run the full suite,
   not just the failures: a task such as `db:test:prepare` loads the
   development environment, so a broken `config/environments/development.rb`
   only shows there.
5. For each regression, write a failing test first, then fix it.
   Fix every deprecation warning the suite prints for the target version
   (`rg -o "DEPRECATION WARNING: [^.]*\." LOG | sort | uniq -c`); the next
   minor version removes what this one deprecates. Minitest prints its
   own as `DEPRECATED:` (`rg -o "DEPRECATED: .*" LOG | sort | uniq -c`);
   a count of `DEPRECATION` misses them.
6. Run the page comparison against `prev` (see "Page comparison"), and fix or
   accept each difference.
7. Boot production locally (`RAILS_ENV=production script/rails runner ...`)
   and check what only production does: asset concatenation, middleware,
   secrets. Print `Rails.application.config.middleware` on both `prev` and
   the new commit and account for every line that moved or went away. A
   middleware can go because the object behind it changed (Rails 7.0's
   `FileStore` has no local cache); then search the app for uses of that
   object before you accept it. Rails 4 refuses a secret shorter than 30
   characters, so pass a long dummy. Read the boot's deprecation warnings
   too: some, such as Rails 4.2's `log_level`, print only in production. A
   production environment that sets
   `config.active_support.deprecation = :notify` prints none, so set it to
   `:stderr` for the check and restore the file after. The `?` timestamp on
   an asset tag is the compiled file's mtime, so it differs between two
   builds; compare the paths. Run every rake task the deploy runs
   (`rg rake Dockerfile`), and give each one a test: on Target 1,
   `assets:compile` broke on Ruby 3.1 and no test ran it. Start the app
   server as the deploy does (`unicorn -c config/unicorn.conf.rb`), then
   request every health-check path the load balancer and the deploy use
   (`/up`, `/up/database`) and compare the status with `prev`. The suite
   loads neither the server config nor a connection that has not run a
   query: on Rails 7.2, Target 1's `after_fork` hook crashed every worker,
   and `/up/database` returned 503.
8. Run Brakeman with `-f json` and compare the report with the previous
   step's (`scripts/brakeman-diff.rb OLD NEW`). It prints each change in a
   warning's count; record each one in the plan file.
9. Write the plan file, then commit the app and the plan.
10. Reflect: list what slowed you down this step, and update this skill.

### Dependency versions

Rule:

- Take the version Bundler resolves inside the target Rails' constraints.
- Keep the newest patch release in each gem's minor line, even when it
  shipped after the target Rails; patch releases carry security fixes.
- Pin back any minor or major release made after the target Rails shipped.
- A gem that the target Rails does not force to a new minor keeps its minor
  line, at its newest patch. On Target 1, `rake` 11 (allowed, not needed by
  Rails 3.2) turned on `ruby -w` for tests and added 426 warnings to the log.

Procedure:

1. Save the lock. Run `bundle update rails --conservative`. If it cannot
   resolve, which is usual for a Rails minor bump, run `bundle update rails`
   without `--conservative`.
2. Run `scripts/lock-diff.rb SAVED_LOCK Gemfile.lock`. It marks each change
   PATCH, MINOR or MAJOR. For each gem that changed, run
   `scripts/gem-dates.rb CUTOFF gem=version ...` and apply the rule. Each
   MINOR or MAJOR line needs a reason: the target Rails requires it, or it
   goes back to its old minor. On Target 1, a Rails 4.1 bump moved seven
   gems a minor that Rails did not need.
3. To pin a gem back, add a temporary Gemfile entry with `~>` on the minor
   (`scripts/pin-lines.rb SAVED_LOCK Gemfile.lock` prints one for each gem
   that moved a minor; delete the lines for gems the target Rails forces,
   which `scripts/rails-deps.rb VERSION` lists from the Rails gemspecs)
   (`gem "i18n", "~> 0.6.4"`), run `bundle update GEMS --conservative`, then
   restore the Gemfile. The lock keeps the versions and the Gemfile stays
   clean. For a gem the Gemfile already lists, edit its line instead:
   Bundler refuses a Gemfile that lists a gem twice. Name only gems already
   in the lock: Bundler refuses the whole command for a gem that is not.
   Leave such a gem out, and check it after. A git gem is the exception:
   removing a temporary `ref:` moves the lock to the branch head, so keep
   the `ref:` in the Gemfile, with a comment.
4. Boot the app (`bin/rake test`) before you accept a newer patch release.
   A gem's `required_ruby_version` does not prove it loads on the app's
   Ruby: `rails_autolink` 1.1.7 declares Ruby 1.9.3 but fails there at
   boot. Keep the newest patch that boots.
5. After the pin-back, run `lock-diff.rb` again: a pinned-back gem can move
   its own dependencies a minor (on Target 1, Rails 8.0's `irb` and `rdoc`
   pin-back moved `psych` and `stringio`). Pin those too, and repeat until
   only forced moves remain. Then run `gem-dates.rb` on the lock again and
   confirm each gem is at the newest patch of its line (the `newest-patch`
   column). A pin can hold a gem below a patch that needs a newer
   dependency: on Target 1,
   `rails-html-sanitizer` 1.6.2 (security fixes) needs `nokogiri` 1.15.7,
   and a `nokogiri` 1.14 pin left it at 1.6.0 without an error. Move the
   dependency's pin to the line the patch needs.
6. After any later `bundle update GEM --conservative`, run `lock-diff.rb`
   again. It can still move that gem's dependencies: on Target 1,
   `bundle update easymon --conservative` moved `redis` from 3.3 to 5.4.
7. Run `scripts/check-public.rb Gemfile.lock`. Bundler also resolves against
   gems installed on the machine, and on Target 1 it locked `rack`
   1.4.7.27, a Rails LTS build that rubygems.org does not publish. Pin such a
   gem to its public version.

Run every script in `scripts/` from outside the app's directory, or mise
gives it the app's old Ruby. Reload mise (`eval "$(mise hook-env -s bash)"`)
after each `cd`: the shell keeps the Ruby of the directory it left. Ad hoc
release-date queries go through `gem-dates.rb` too; on the app's Ruby it
fails at once with a syntax error, where a one-liner fails on a network
error that looks unrelated. The same goes for `fetch-wake` and other tools
in modern Ruby: start them from outside the app, and put the app command in
a script that `cd`s into the app and reloads mise.

A gem extracted from Rails (`dynamic_form`, `prototype-rails`,
`rails_autolink`) goes in the Gemfile with an exact version and a comment
naming what it provides. Bundler does not choose that version, so check its
dependencies (`gem dependency NAME -v VERSION --remote`): on Target 1 the
newest `rails_autolink` patch needed `actionview`, which arrives in
Rails 4.1.

Before each bump, list the Gemfile's exact pins (`rg '"= ?[0-9]' Gemfile`,
and bare versions). A pin made for the old Ruby or Rails can block the
resolve; on Target 1 a `thor` pin did. Move it to the newest patch the
app's Ruby and the target Rails both accept. If nothing in the app uses the
gem (`rg -w GEM app lib config`), delete the pin: on Target 1 an unused
`nokogiri` 1.5.4 pin from a Docker commit blocked Rails 4.2.

A patch release can also carry a security fix the pinned-back minor lacks.
When Brakeman flags a pinned-back gem, check whether the app can reach the
flaw (its configuration, its call sites), and record it as a risk in the
plan file with the step that clears it.

### Abandoned gems

A gem extracted from Rails can stop at an old release. Before patching it,
list the upstream branches: an unreleased branch may already support the
target Rails. Read it before you trust it; on Target 1 the `prototype-rails`
`4.2` branch still called the removed `HTML::Document`.

Otherwise vendor the last release in `vendor/gems/NAME` with a gemspec that
says it is vendored and patched, and load it with `gem "NAME", path:
"vendor/gems/NAME"`. Keep RuboCop out of `vendor/`. Patch only what the
target Rails breaks, and test the patch with a probe that must fail: a wrong
selector, a wrong position.

### Catch-all routes

A `:controller/:action/:id` route serves every public method of every
controller. Before removing it (Rails 5.2 does), list what only it reaches:
`script/rails runner PATH/scripts/route-inventory.rb`. Route the real
actions by name, with a routing test, and record the dropped URL shapes as
a risk. Accessors and helpers in the list were exposed by accident. Run the
suite: a `url_for` built from a controller and action that no route names
raises, and on Target 1 that found a sign-up email linking to a controller
that never existed.

### Monkey patches

Replace each `alias_method_chain` with a module and `prepend`. On Ruby
earlier than 3.0, a prepend on a module that a class already includes does
not reach the class: prepend to the class itself, from an `on_load` hook if
it loads late. Confirm each patch reaches its target with
`Klass.instance_method(:name).owner`, and give each hand-wired integration
(such as an error reporter hooked into `ShowExceptions`) a test that fails
without the hook.

A patch guarded by `if method_defined?(...)` or `respond_to?` skips itself
without a word when the gem changes its code path. On Target 1 a patch that
kept health checks out of an instrumentation gem's 5xx counter hooked the
Rails 2 code path and stopped running at Rails 3.0. Test the effect (no
counter for the health check), not the presence of the patch.

A gem that overrides a Rails method breaks when Rails changes its signature:
on Target 1, an instrumentation gem's `execute` override did not accept the
`async:` keyword of Rails 7.0, and every query raised `ArgumentError`. When
Rails now does what the override did, turn the gem's feature off and use
Rails'. Write the effect test on `prev` first, so it passes on both
versions: on Target 1, `QueryCommentsTest` asserted the SQL comment that the
gem wrote, then passed with Active Record's query logs.

A gem's documented default may need a newer Rails than the app runs:
`mattr_accessor :x, default: true` sets nothing before Rails 5.2. Set such
options explicitly.

### App overrides of Rails helpers

Old apps redefine Rails helpers (`text_field_tag`, `tag`, `link_to`) in
`app/helpers`. Rails' own helpers call them, so a change inside Rails
breaks the override without an error. On Target 1, Rails 4.2's
`hidden_field_tag` passed `type: :hidden` to the app's `text_field_tag`,
which merged it without stringifying keys; hidden fields rendered with two
`type` attributes and showed as text boxes. No test failed; the page
comparison found it. At each step list the overrides
(`rg -n "^\s*def \w+" app/helpers`, compared with the Rails helper names)
and read how the new Rails calls each one.

### When to bump Ruby

Only when the next Rails or one of its dependencies requires it. Go to the
nearest Ruby minor that satisfies it, at that minor's latest patch release.
Raise RuboCop's `TargetRubyVersion` in the same commit.

Bump Ruby in its own commit, before the Rails step that needs it, and run
the whole step checklist on it: suite, page comparison, production boot.

- **Finding the version.** `mise ls-remote ruby` lists only versions with
  a precompiled build. `MISE_RUBY_COMPILE=true mise ls-remote ruby` lists
  every ruby-build definition.
- **Building an old Ruby.** A current compiler rejects old Ruby sources.
  Build with `MISE_RUBY_COMPILE=true` and the warning flags that let them
  compile (`-std=gnu99 -Wno-error=implicit-function-declaration
  -Wno-incompatible-pointer-types -Wno-int-conversion`); ruby-build fetches
  OpenSSL 1.0.2 for Ruby 2.2. Check `ruby -ropenssl -rzlib -rreadline` after.
  The build takes several minutes; run it as a background job.
- **Native gems.** Some fail to compile on the new Ruby (`kgio` 2.3.3 on
  2.2); move each to the oldest release that builds. Others compile and
  crash on first use with `undefined symbol` (`bcrypt-ruby` 2.1.4 calls
  `rb_thread_blocking_region`), so only a test that calls the gem finds
  them. Look for a renamed successor (`bcrypt-ruby` → `bcrypt`), and test
  that data the old gem wrote, such as password hashes, still reads.
- **Ruby warnings.** A new Ruby warns about what the next one removes, such
  as Ruby 2.7's `Using the last argument as keyword parameters is
  deprecated`. From Ruby 2.7.2 these warnings are off by default, so run
  the suite with `RUBYOPT=-W:deprecated`, before the bump on the old Ruby
  and after it on the new one. Group the log's `warning:` lines by file,
  and move each gem that prints one to the oldest minor that does not.
- **Keyword arguments (Ruby 3.0).** A gem that passes a hash where a
  method takes keywords fails. Some fail with `ArgumentError`; others
  fail silently: on Target 1, `i18n` 1.6 rendered validation messages as
  `translation missing`.
- **Bundled gems.** Ruby 3.0 moves `rexml` out of the default gems, among
  others. Add each one the app or its tests load (`Hash.from_xml` loads
  `rexml`) to the Gemfile.
- **Bundler.** An old Bundler can warn on a new Ruby on every command
  (1.17.3 on Ruby 3.1: `Pathname#untaint is deprecated`). Lock with the
  new Ruby's default Bundler: `bundle _2.3.27_ install`.
- **Boot before the suite.** Start the server once (`script/rails runner
  'p 1'`) before the comparison. The test suite boots another way and can
  pass while `rails server` fails. In particular, `config/boot.rb` must
  require nothing before `bundler/setup`: a default gem loaded there can
  activate a newer installed copy of a gem it requires, outside the bundle.
- **Ruby 2.0 reads source as UTF-8.** A regexp with `\xNN` escapes becomes a
  UTF-8 regexp and raises `Encoding::CompatibilityError` on binary strings.
  Mark it binary (`/.../n`) and match against `dup.force_encoding("BINARY")`.
- **Deploy image.** The Dockerfile's Ruby image may not exist for an
  intermediate Ruby. Deploy only the final Ruby; record the gap as a risk.
  At the final Ruby, before the review, move `FROM` to an image that
  exists (`docker manifest inspect IMAGE`), drop install steps the old Ruby
  needed (RubyGems and Bundler pins), and `COPY` every `path:` gem before
  `bundle install`. Build the image, boot it with the deploy's environment,
  and request `/up`. On Target 1 the Dockerfile was still on Ruby 1.9.3
  after every step had passed.

### Forks and git dependencies

When the app pins a fork branch (such as a patched `mysql2`), list the
fork's branches before bumping: someone may already have made the branch you
need. Compare branches with `git log A..B` and `git diff A B --stat` to
confirm the app's patch survives.

### Charset quirks

An old MySQL app may store UTF-8 bytes in latin1 columns through a driver
that sends UTF-8 over a latin1 connection. A driver change that converts the
text instead passes every test that reads back what it wrote. Before the
change, add a test that reads the stored bytes:

```ruby
assert_equal "4D61C3B1616E61", Item.where(id: item.id).pluck(Arel.sql("HEX(content)")).first
```

Then prove the test can fail: switch the connection to `utf8` (with a utf8
collation), and see the test fail on its assertion with the latin1 bytes
(`4D61F1616E61`). A failure on a setup error, such as an invalid collation,
proves nothing. Read that run's failure in full; do not filter it.

### Backported patches

Old apps carry patches that backport security fixes (`lib/extensions/`,
initializers). For each one, find the CVE or commit it backports and check
whether the target version includes it. If it does, delete the patch and keep
its test. Backport gems (`action_dispatch_request_id_backport`,
`active_support_logger_backport`) count too: once Rails ships the feature,
the gem shadows it, so remove the gem and the code that inserts its
middleware.

The reverse also happens. The last 4.0 release is older than the last 3.2
release, and lacks fixes that 3.2 got (CVE-2015-3227, CVE-2015-7581). A test
for such a fix can fail on the new version. Do not backport it into an
intermediate step that will not be deployed: record it as a risk, and
confirm that a later step clears it. Do not trust a patch's own "obsolete"
guard: on Target 1, `defined? Rack::Utils.param_depth_limit` ran before
`Rack::Utils` loaded, so the guard never fired.

### Plugins

Rails 3.2 deprecates `vendor/plugins`. Move each plugin's code into `lib/` or
`app/`, and carry over every line of its `init.rb` into an initializer. On
Target 1, a dropped `Mime::Type.register "text/javascript", :js` left
`text/javascript` resolving to the plugin's `:iphonejs`, and the failing
tests pointed at `acts_as_list` instead.

Code moved out of `vendor/` comes into RuboCop's and Brakeman's scope.
Read every autocorrect diff: `-a` joined a multi-line argument onto one line.
Record the new Brakeman warnings in the plan file.

Brakeman's "Weak" SQL warnings in moved plugin code name the interpolated
method, not the request input behind it. Trace every `params` value the
app passes into `lib/`. Before you ignore a
`protect_from_forgery with: :reset_session` warning, look for a filter
that signs the user in again from a cookie: a forged request still runs
for the users it signs in.

### Page comparison

Unit tests miss layout, script and URL changes. Run both versions side by
side on the same development database and diff the pages:

```sh
SMOKE=path/to/smoke.rb scripts/compare.sh \
  PREV_DIR "script/rails server -b 127.0.0.1 -p 3101" NEW_DIR "script/rails server -b 127.0.0.1 -p 3102" OUT
scripts/summarize-step.sh OUT
```

`compare.sh` needs an app-specific client, `smoke.rb`: it signs in and saves
each page under every user agent and format the app serves.
`scripts/smoke.example.rb` is an example client; copy and adapt it. The
client must send a browser `Accept` header; with `Accept: */*`, Rails 3+
does not treat the request as HTML.

The summary lists pages where both sides return an error apart from the
rest, with each side's error heading. In development their bodies are Rails'
error template, which changes between versions; compare their status lines
and headings, not their bodies.

When one known change marks most pages (Rails 5.2's `image_tag` `alt`, for
example), pass a `sed -E` expression as the second argument to set it aside,
and read what remains. Record the change in the plan file.

From Rails 4.2 on, tags keep their attributes in the order the code gives
them; earlier versions sorted them. `summarize-step.sh` sorts attributes in
`OUT/norm` (`scripts/sort-attrs.rb`) so that this does not mark every page.

Mask every CSRF token the client saves, including the one Rails writes into
an inline `onclick` handler (`button_to`, `link_to ... method:`).
`smoke.example.rb` masks both. Rails 5 masks tokens on each request, so an
unmasked one differs on every run.

Read the raw diff, not only the normalized one. On Target 1 a normalizer
written for the 2.3 to 3.0 step deleted a `<script>` line and hid a script
reordering in the next step. Normalize only what both sides share: the port,
timings, CSRF tokens, asset timestamps.

### Session cookies

Users arrive with cookies the old Rails wrote. Capture one from the baseline
server (a failed login leaves a flash in it), store the Marshal payload in an
integration test, sign it with the app's secret, and assert that the new
Rails shows the flash once. Rerun the test at every step: the flash class
changed shape at 3.0 and at 3.1, the flash middleware changed at 3.2, and
the session value became a plain hash at 4.1.

From Rails 4.1 on, convert the old flash to the session value the flash
reads (`{"discard" => [...], "flashes" => {...}}`), not to a `FlashHash`.
The hash is stable across versions; the class's instance variables are not.
Leave `cookies_serializer` unset (Marshal) while old cookies can arrive:
JSON cannot load the old classes.

Rails 5.0 removed the `ActionDispatch::Flash` middleware. Convert the old
flash in a `prepend` on `ActionDispatch::Flash::FlashHash.from_session_value`
instead of wrapping the middleware's `call`.

Rails 6.0 removes `secret_token`, and with it every session cookie signed
by Rails 3.2 or earlier, or by a later app that never set
`secret_key_base`. Users without some other login cookie are signed out
once at the deploy. That is a product decision: ask it a step ahead, with
what the app keeps (a "Remember me" cookie, say) and
the alternative, a middleware that reads the old cookie. On Target 1 the
answer was to accept the sign-out. Then the flash conversion has nothing to
convert: delete it, and replace its test with one that an old cookie signs
the user out without an error, and one that the remembered login survives.
Run the sign-out test on the previous step too; there it must fail.

A live replay against both servers is a useful extra check, if it asserts
the flash text on the new server's page. Target 1's first replay looked for
`Flash` in the Base64 cookie, a check that can never pass, and the run looked
like a success.

## Plan file

One Markdown file per step, named
`<app>-<YYYYMMDD>-rails_v<from>..v<to>.md`, with these sections:

- Header: date, range, Ruby, branch.
- Summary: what was checked (test counts, the comparison).
- Dependency versions: a table of gem, from, to, release date, note. Every
  "pinned back" note names the line it was pinned to.
- Changes applied: one bullet per change, saying what broke and what fixed it.
- Accepted differences: what the comparison still shows, and why it is fine.
- Risks: numbered R1, R2, ... across all steps, never renumbered.
- Security gates: Brakeman count, bundler-audit status.

## Eval Checks

Run before each step's commit.

| # | Check | Pass criteria | If fail |
|---|-------|---------------|---------|
| 1 | CI | `bin/ci` passes every step except sign-off | Fix, or record why a step cannot pass yet |
| 2 | Full suite ran | The `tests,` count lines in the log add up to the number of tests in `test/` (3 lines on Rails 3, 1 line from Rails 4.0) | A rake prerequisite aborted; read the top of the log |
| 3 | New tests fail first | Each new test was run against the unfixed code and failed | Revert the fix, run the test, restore |
| 4 | Comparison read raw | `diff -r OUT/base OUT/new` reviewed, not only `OUT/norm` | Read it |
| 5 | Dates checked | `scripts/gem-dates.rb CUTOFF gem=ver ...` run for every gem that changed | Run it; pin back or note each later minor |
| 6 | Plan matches lock | Every version in the plan's table equals `Gemfile.lock` | Regenerate the table from the lock diff |
| 7 | No temporary pins | `git diff Gemfile` shows only intended entries | Restore the Gemfile |
| 8 | Generated files gone | `git status --short` shows no `public/*/cache/` or `tmp/` files | Delete them by path |
| 9 | Lock is public | `scripts/check-public.rb Gemfile.lock` prints no `NOT ON RUBYGEMS` line | Pin the gem to its public version |
| 10 | Minor moves are forced | Each MINOR or MAJOR line from `scripts/lock-diff.rb` is a gem the target Rails requires at that line | Pin it back to its old minor at the newest patch |
| 11 | Backports reviewed | Each patch in `lib/extensions/` and `config/initializers/`, and each backport gem, is checked against the target version | Delete included fixes; keep their tests |
| 12 | Middleware accounted for | Every line that moved or went away between `prev` and the new production middleware list is explained in the plan | Find out why, and fix it or list it as accepted |
| 13 | Deprecations gone | The test log and the production boot have no `DEPRECATION WARNING`, and the test log no Minitest `DEPRECATED:`, except ones recorded as risks | Fix them; the next minor removes them |
| 14 | Selectors are valid CSS | `rg -n "\[[\w-]+=[^'\"\]]*[^\w'\"\]-][^\]]*\]" test` finds no unquoted attribute value that is not a CSS identifier | Quote it; Nokogiri skips the assertion with only a warning |
| 15 | Helper overrides read | Each method in `app/helpers` that shares a name with a Rails helper is checked against how the new Rails calls it | Fix the override; add a helper test |
| 16 | Patches reach their target | For each `prepend` or patch the step adds, `Klass.instance_method(:name).owner` names the patch module | Prepend to the class itself |
| 17 | Gem patches have effect tests | Each app patch of a gem's behavior has a test that asserts the behavior, and fails with the patch removed | Write it; a guarded patch can stop running silently |
| 18 | Brakeman compared | `scripts/brakeman-diff.rb` output for this step is in the plan's "Security gates" | Run it; explain each count change |
| 19 | Ruby deprecations listed | At a Ruby bump, a `RUBYOPT=-W:deprecated` run on the old and the new Ruby shows no gem warning that is not recorded as a risk | Move the gem, or record it with the step that clears it |
| 20 | Production deprecations printed | The production boot and server ran with `deprecation = :stderr`, and printed no `DEPRECATION WARNING` that is not recorded as a risk | Rerun with `:stderr`; `:notify` prints nothing |
| 21 | Server serves health checks | The app server, started as the deploy starts it, returns the same status as `prev` for every health-check path | Read the server log; test the server config's hooks |
| 22 | Every deployed environment changed | Each setting a step adds to `production.rb` is also in `staging.rb` and every other deployed environment, or the plan says why not | Copy it; boot each environment and read the value back |

## Failure Modes

| Symptom | Cause | Fix |
|---------|-------|-----|
| `Incorrect MySQL client library version!` on load | mysql2 0.3 compares header and library version strings; libmariadb reports different ones | Use a branch that checks only on Windows (`basecamp/mysql2` `0.3.x_mariadbclient-support`) |
| `undefined method 'match?' for nil` in `ActionDispatch::Static#call` | An app subclass of `ActionDispatch::Static` set ivars the new version no longer has | Delete the subclass; use `config.static_cache_control` |
| Every engine route 404s (`uninitialized constant ChecksController`) | Rails 3.1 engines are mountable; `Engine.routes(self)` helpers no longer add routes to the app | `mount Engine => "/path"` at the old path |
| Scripts load alphabetically; Prototype loads late | Rails 3.1 `:defaults` expansion is jQuery, and `:all` puts `:defaults` first | Set `config.action_view.javascript_expansions[:defaults]` to the old list |
| `host:3000:3000` in URLs | Rails 3.1 URL helpers add the request port; the app's host helper added it too | Drop the port from the helper; add `request.port_string` only where a URL is built by hand |
| Old session cookie → 500 or lost flash | The flash class changed shape | Define the old class so Marshal loads it, convert on first read; test with a captured cookie |
| `undefined method 'year' for 1:Fixnum` in `config/application.rb` | Core extensions are not loaded yet at that point | Use a literal |
| A test task "fails" with no failure listed | A rake prerequisite (`db:test:prepare` → `environment`) aborted in the development environment | Read the top of the log; fix the environment file |
| Smoke client errors with keyword syntax | It ran under the app's old Ruby | `cd` to the client's directory and reload mise before running it |
| WEBrick keeps the port after the run | It stops only on `SIGINT` | Send `SIGINT` to the server you started, not to whatever holds the port; `compare.sh` does |
| The comparison hides a real change | A normalizer from an earlier step deletes lines that matter now | Normalize only the port, timings and tokens; read the raw diff |
| Plan file claims a gem was "pinned back from X" that was never tried | Written from memory | Take every version from the lock diff and `gem-dates.rb` output |
| A locked version such as `1.4.7.27` that rubygems.org does not list | Bundler used a private build installed on the machine | `scripts/check-public.rb`; pin to the public version |
| `bundle update rails --conservative` cannot resolve | A Rails minor bump moves shared dependencies | Update without `--conservative`, then apply the dependency rule to each changed gem |
| `NoMethodError` on the flash in Rails 3.2 for an old session | The 3.2 flash middleware sweeps the session flash before the request reads it | Convert the old flash in the middleware's `call`, before the sweep |
| After moving a plugin, XHR actions render RJS or the wrong template | The plugin's `init.rb` registered MIME types again, and the move dropped it | Carry every `init.rb` line into an initializer |
| A backport's "obsolete" guard stays silent after the fix ships | The guard ran before the constant it tests was loaded | Check each backport against the target version by hand |
| Hundreds of Ruby warnings in the test log | `rake` 11 or later runs tests with `-w` | Keep `rake` on its old minor unless the target Rails needs a newer one |
| Bundler cannot resolve the new Rails, naming a gem the app pins | An exact Gemfile pin made for the old Ruby or Rails | Move the pin to the newest patch both accept |
| A request with a bad CSRF token runs with an empty session instead of signing the user out | Rails 4.0 `protect_from_forgery` defaults to `:null_session`, and a controller's own call resets the strategy | Pass `with:` in every `protect_from_forgery` call; test a forged request on each controller that has one |
| `ArgumentError: Secret should be something secure` in a production runner | Rails 4 needs a secret of 30 or more characters | Pass a long dummy `SECRET_KEY_BASE` |
| Eager loading warning names a table such as `example` | `includes` with a hash condition whose value looks like `table.column` (`x@example.com`) | Use `preload` |
| Thousands of diff lines on 404 pages | The development error template changed | Compare error pages by status line |
| `invalid pattern in look-behind` at boot | A gem's newer patch uses a regex the old Ruby rejects, despite its `required_ruby_version` | Keep the newest patch that boots |
| `ArgumentError: When assigning attributes, you must pass a hash` | Rails 4.1 raises on `update_attributes(nil)`; a `permit` guarded by `if params[:x]` leaves `nil` | `params.fetch(:x, {}).permit(...)` |
| `bundle update A B --conservative` refuses: `Could not find gem 'B'` in the lock | Bundler can update only gems already in the lock | Leave the new gem out of the command; check its version after |
| `cannot load such file -- action_view/vendor/html-scanner` | Rails 4.2 removed html-scanner; `prototype-rails` 4.0.1 requires it | Vendor the gem; select with rails-dom-testing in `assert_select_rjs` |
| An `assert_select` passes whatever the page holds, with a Nokogiri warning in the log | From Rails 4.2 the selector is CSS parsed by Nokogiri; an unquoted value such as `a[href=http://x]` is invalid and is skipped | Quote attribute values |
| `<input type="text" ... type="hidden">` | An app override of `text_field_tag` does not stringify the `type: :hidden` that Rails 4.2 passes | Stringify the option keys in the override |
| `can't modify frozen ActiveSupport::HashWithIndifferentAccess` | Rails 4.2 freezes `changed_attributes` | `clear_attribute_changes([ :attr ])` |
| Every page differs after Rails 4.2 | Attribute order and the hidden-field wrapper changed | Sort attributes before diffing; accept the wrapper |
| `undefined method 'map' for nil` in a view reading the flash | The Rails 4.1 `FlashHash` was built from a 3.x-style object, whose `@used` it no longer has | Convert the old flash to the `discard`/`flashes` hash |
| `undefined symbol: rb_thread_blocking_region` at the first password check | `bcrypt-ruby` 2.1.4 uses an API Ruby 2.2 removed; it compiles, then fails on first call | Replace it with `bcrypt` 3.1.x; check that old hashes verify |
| `Encoding::CompatibilityError` in a regexp match after Ruby 2.0 | Source files are UTF-8 from Ruby 2.0, so `\xNN` regexps became UTF-8 | `/.../n` against a `BINARY` copy of the string |
| Bundler cannot resolve Rails 5.0 with sprockets 2 | Rails 5.0 needs rack 2, and sprockets 2 needs rack 1 | Move sprockets to 3.7.x |
| `undefined method 'call' for class ActionDispatch::Flash` | Rails 5.0 removed the flash middleware | Prepend to `FlashHash.from_session_value` |
| `NoMethodError: assigns` or `assert_template` | Rails 5.0 extracted them | Add `rails-controller-testing`; 1.0.4 on Ruby 2.2, since 1.0.5 passes an extra empty hash to `process` there |
| `ArgumentError (wrong number of arguments (5 for 2..4))` from `xhr` or a request helper | Rails 5.0 request helpers take keyword arguments | `scripts/kwargs-tests.rb test/functional/*.rb test/integration/*.rb` |
| 406 where the app served 404 | Rails 5.0 raises `ActionController::UnknownFormat` when the action has a template for another format | Map it in `config.action_dispatch.rescue_responses` |
| A prepended override never runs on Ruby 2.x | Prepending to a module already included in a class does not reach the class | Prepend to the class |
| `id=""` on a field that had no id | Rails 5.0 renders `id: ""`; earlier versions dropped it | `id: nil` |
| Pages differ only in an `onclick` token | Rails 5 masks the CSRF token per request, and the client masks only form tokens | Mask the `onclick` token too |
| The new server "did not start", but its log shows WEBrick listening | From Rails 5.1, `rails server` binds to `ENV["HOST"]` when it is set | Pass `-b 127.0.0.1` |
| `UrlGenerationError: No route matches {:action=>"faq", :controller=>"help"}` after removing the catch-all route | The app built a URL to a controller that only the catch-all route matched | Link to the real page; test the link |
| The lock moves a git gem to its branch head after a pin-back | Removing a temporary `ref:` changes the source, so Bundler re-resolves it | Keep the `ref:` in the Gemfile |
| A patch to a gem stopped working, and no test failed | It was guarded by `method_defined?`, and the gem changed code paths | Test the effect of the patch |
| `syntax error, unexpected ']'` from `fetch-wake` | A modern-Ruby tool started in the app's directory ran on the app's Ruby | Start it from another directory; wrap the app command in a script |
| Bundler 1.x lists a conflict for most pinned gems | It reports every edge into each gem, not the one that fails | `scripts/rails-deps.rb VERSION`; drop the pins Rails forces |
| Every development page returns 403 `Blocked host` | Rails 6.0 host authorization | `config.hosts << ".app.localhost"` in `development.rb` |
| A gem option still has its old value after the gem changed its default | `mattr_accessor ... default:` needs Rails 5.2 | Set the option explicitly |
| `can't activate mysql2 (< 0.6.0, >= 0.4.4)` | The Rails 5.2 adapter needs `mysql2` 0.4 | List the fork's branches for a 0.4 line; test the stored bytes (see "Charset quirks") |
| A negative check "fails", but the filtered output shows nothing | The run failed on setup, such as `COLLATION 'latin1_swedish_ci' is not valid for CHARACTER SET 'utf8mb3'` | Read the whole failure; the check counts only when the assertion fails |
| Nearly every page differs only in `alt` | Rails 5.2 `image_tag` no longer derives `alt` from the file name | `summarize-step.sh OUT 's/ alt="[^"]*"//g'`; record it |
| `can't activate mysql2 (~> 0.5)` | The Rails 6.1 adapter needs `mysql2` 0.5 | List the fork's branches: on Target 1 the 0.5 line was `force_latin1_to_utf8`, with no version in its name; test the stored bytes |
| `ActiveRecord::NoEnvironmentInSchemaError` from `db:test:load` | A test database made before Rails 5.0 has no `ar_internal_metadata` table | `bin/rake db:environment:set RAILS_ENV=test` |
| `DEPRECATION WARNING: Enumerating ActiveModel::Errors as a hash` from a gem | `dynamic_form` 1.1.4 `full_messages` iterates errors as pairs; Rails 7.0 raises | `dynamic_form` 1.2.0; test the rendered error list first |
| Brakeman's count changes, but no warning is new or gone | The diff compared sets, and Brakeman repeats a message in one file | `scripts/brakeman-diff.rb`, which compares counts |
| `error: conflicting types for 'canonicalize'` compiling `nokogiri` | glibc declares its own `canonicalize`; `nokogiri` renamed its function in 1.11.0 | Move `nokogiri` to 1.11 or later |
| `superclass mismatch for class StringIO` when the server starts, though the suite passes | `config/boot.rb` required `openssl` before `bundler/setup`, which activated a `stringio` outside the bundle | Require nothing before `bundler/setup`; test that boot activates no gem outside the bundle |
| `You cannot specify the same gem twice with different version requirements` during a pin-back | The pin was appended for a gem the Gemfile already lists | Edit that gem's line, then restore it |
| `wrong number of arguments` from a gem's `execute_with_instrumentation(sql, name = nil)` | The gem overrides a connection method that Rails 7.0 calls with a new keyword | Use the Rails feature that replaces it (query logs for SQL comments) |
| `uninitialized constant AccountHelper` at boot on Rails 7.0 | A gem referenced `ActionView::TestCase` while Action View loaded, before the app's helpers were on the load path | Include into it from `ActiveSupport.on_load(:action_view_test_case)` |
| A production middleware vanished | The object behind it changed, such as Rails 7.0 `FileStore` losing its local cache | Search the app for uses; accept it only if there are none |
| `prism-... requires ruby version ... >= 3.3, which is incompatible with the current version` from `bundle update rails` | Bundler 1.x picked the newest release of a new transitive gem (`irb` → `rdoc`) | Add temporary `~>` pins for the new gems on the minor at the cutoff; name only `rails` and gems already in the lock on the command line |
| A gem stays at an older patch after the pin-back, with no error | Another pin holds a dependency below what the newer patch needs | Move that dependency's pin |
| An error reporter receives an `ActionDispatch::ExceptionWrapper` | Rails 7.1 passes the wrapper to `ShowExceptions#render_exception` | Report `wrapper.exception` from an app-owned prepended module |
| `zeitwerk:check` fails with `uninitialized constant ProcessLockedIo` after `autoload_lib` | A `lib` file defines a constant whose name does not match its file name, and the app requires it by hand | Add it to `autoload_lib(ignore:)` |
| `ArgumentError: wrong number of arguments (given 2, expected 1)` from a gem's method that takes `*args` | Ruby 3.0 no longer turns a trailing hash into keywords (`rails-controller-testing` 1.0.4, `rake` 12 `FileUtils`) | Move the gem to the oldest minor that passes keywords (1.0.5; `rake` 13.0) |
| `translation missing` where a validation message was | `i18n` 1.6 passes a hash to `I18n.translate`, which takes keywords on Ruby 3 | `i18n` 1.8 |
| `cannot load such file -- rexml/document` | Ruby 3.0 made `rexml` a bundled gem | Add it to the Gemfile |
| A test unrelated to your change fails after you add a test | The new test changed the random order, and an earlier test leaked state (`ActionController::TestCase` does not clear `ActionMailer::Base.deliveries`) | Rerun with the failing `SEED` and `TESTOPTS=-v` to see the order; reset the state in a global `setup` |
| The comparison exits 0 with a stack trace from the smoke client | A server did not start, and `compare.sh` went on | `compare.sh` now stops; read `OUT/*.server.log` |
| `undefined method 'clear_all_connections!' for ActiveRecord::Base:Class` in every unicorn worker | Rails 7.2 removed the class method; a production `deprecation = :notify` hid Rails 7.1's deprecation | `ActiveRecord::Base.connection_handler.clear_all_connections!`; test the hook through `Unicorn::Configurator` |
| A database setting copied from a repo comment that says it matches production changes behavior in production | The comment was wrong: production ran a strict `sql_mode`, and `strict: false` would have truncated what it rejected | Read the value from a production container (`bin/kamal server exec` with `docker exec`, a read-only query) before you change `database.yml`: `@@GLOBAL.sql_mode`, `system_time_zone`, `/etc/localtime` |
| `[BUG] Segmentation fault` in `kgio_tryaccept` when unicorn starts on Ruby 4.0 | `kgio` segfaults on Ruby 4.0, in 2.9.3 and in 2.11.4, its last release | Replace unicorn with puma in a commit before the Ruby bump; see `rails-versions.md`, "Ruby 4.0" |
| `/up` returns 503 on Rails 7.2, and the suite passes | Rails 7.2 connects a checked-out connection only at its first query, and easymon 1.4 asks `connection.active?` | easymon 1.6.3 or later; test against a new pool with transactional tests off |
| `ConnectionPool#connection` deprecated about 2000 times in the test log | A gem's SQL subscriber calls it on every query | Move the gem to a revision that calls `lease_connection` |
| `bundle update GEM --conservative` moves another gem a major | Bundler re-resolves the updated gem's dependencies | Run `lock-diff.rb`; pin the dependency back |
| `JSON::ParserError: unexpected character: '<!DOCTYPE'` from `gem-dates.rb` | rubygems.org answered with an HTML error page | The script retries; rerun if it still fails |
| A test that passes on the fixed code also passes on the broken code | The fixture transaction connected the pool the test meant to leave unused | `self.use_transactional_tests = false`; see the test fail first |
| A plan says a gem's gemspec caps Rails, and the cap is in another gem | Written from memory (Target 1's Rails 7.2 plan blamed the wrong gem for `prototype-rails`' `< 7.3`) | Name the file the constraint is in; `rg` it before you write it |
| `lock-diff.rb` prints `PLATFORMS ruby -> x86_64-linux` after a pin-back | Bundler re-resolved the lock for the local platform | `bundle lock --add-platform ruby`, then `bundle lock --remove-platform x86_64-linux` |
| A pin-back fails: `rails ... requires nokogiri >= 1.19.5` | A gem the target Rails forces needs a newer minor of the pinned gem; `rails-deps.rb` lists only Rails' direct dependencies | Drop that pin; Bundler's message names the chain |
| `already initialized constant STATS_DIRECTORIES` in the test log on Rails 8.0 | A test called `Rails.application.load_tasks` after `rails test` loaded them for `test:prepare` | `load` only the task file the test runs |
| `Table ... doesn't exist` or `Table definition has changed` in a run that passed before | Something outside the run, such as `bin/setup` in another checkout, rebuilt the shared test database | Rerun with the same `SEED`; a pass means the failures were not yours |
| The confirmation error no longer shows on a form that tests `errors[:password]` | From Rails 4.0 a mismatch is an error on `password_confirmation` | Test `errors[:x_confirmation]` too; test a mismatch |
| An RSS title shows `Milk &amp; eggs` | From Rails 4.2 `sanitize` returns escaped text, and Builder escapes it again; Rails 2.3 returned text without tags unchanged | Sanitize only text that contains `<`; `CGI.unescapeHTML` turns typed `&lt;` into a tag |
| `HEAD` on a feed redirects to sign-in, and `GET` works | Rails 2.3 `request.get?` was true for `HEAD`; later versions say false | `request.get? \|\| request.head?`; test `HEAD` |
| Mail delivery raises a certificate error after the upgrade | The `mail` gem tries STARTTLS whenever the relay offers it and verifies the certificate; Rails 2.3 never tried it | `enable_starttls_auto: false` to keep the old behavior, or check the relay first |
| `NoMethodError` from code that no test or request reaches, such as `ActiveRecord::Base.silence` | Removed APIs in `lib/` methods with no caller in the app | `rg` `lib/` for each class method the version notes list as removed; call each public `lib/` method once in a test |

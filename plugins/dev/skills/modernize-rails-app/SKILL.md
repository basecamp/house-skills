---
name: modernize-rails-app
description: >
  Bring an old Rails app up to a supported Rails and Ruby one Rails minor
  version at a time, with a green bin/ci and a commit at each step. Use when
  asked to "upgrade this app to Rails 8", "modernize this Rails app", "get this
  app onto a supported Ruby", or to continue such an upgrade. Covers choosing
  dependency versions by release date, when to bump Ruby, test coverage to
  add first, the side-by-side page comparison between steps, and the per-step
  plan file.
---

# modernize-rails-app

Open `@references/guide.md` and follow it. Do not proceed without it.

The output is a branch with one commit per Rails minor version (plus commits
for tooling and tests), each with a green `bin/ci`, and one plan file per
step that records the dependency versions, the changes, the accepted
behavior differences and the open risks.

`@references/rails-versions.md` holds what broke at each Rails step so far.
Read the section for the step you are about to take.

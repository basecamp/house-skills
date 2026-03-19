# `cache-poisoning` — Cache Poisoning in Release Workflows

zizmor warns when setup actions (e.g., `setup-go`, `setup-ruby`, `setup-node`) enable caching
in workflows triggered by tags or `workflow_dispatch`, since a poisoned cache from a PR could
theoretically affect the release build.

**WARNING:** `--fix=all` will disable caching for these findings. Almost always revert these
auto-fixes and suppress instead.

**Suppress (default).** GitHub Actions caches are isolated by branch. A `pull_request` workflow
from a fork cannot write to the cache used by a tag-push or `workflow_dispatch` release
workflow. The cache-poisoning attack requires an actor who already has push access to the
default branch — and if they have that, they can modify the release workflow directly.

This applies even when the cached dependencies are used to build the release artifact, because
the cache isolation makes the poisoning vector impractical.

**Suppress only if:**
- The workflow uses the default cache keys from setup actions (e.g., `setup-go`, `setup-ruby`,
  `setup-node`), which are keyed by lockfile hash and branch. This is the common case.

**Do NOT suppress if:**
- The workflow uses `actions/cache` with a custom `key:` that is shared across branches or
  includes attacker-controllable values (e.g., PR number, branch name from a fork). In this
  case, **stop and report the finding.**

```yaml
- uses: ruby/setup-ruby@... # zizmor: ignore[cache-poisoning] -- cached deps are for testing, not release artifact generation
```

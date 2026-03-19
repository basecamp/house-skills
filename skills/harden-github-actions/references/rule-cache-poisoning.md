# `cache-poisoning` — Cache Poisoning in Release Workflows

zizmor warns when setup actions (e.g., `setup-go`, `setup-ruby`, `setup-node`) enable caching
in workflows triggered by tags or `workflow_dispatch`, since a poisoned cache from a PR could
theoretically affect the release build.

**WARNING:** `--fix=all` will disable caching for these findings. Almost always revert these
auto-fixes and suppress instead.

**Suppress only if ALL of these are true:**
1. The cached dependencies are used for testing or linting, NOT for building the published
   release artifact
2. The release artifact is built from a clean install or a separate non-cached step
3. The workflow's cache is not shared with workflows triggered by `pull_request` from forks
   (GitHub isolates caches by branch, but verify if the workflow uses custom cache keys)

If you cannot confirm all three, disable caching for that step. If the situation is
ambiguous, **stop and report the finding — do not suppress or fix.**

```yaml
- uses: ruby/setup-ruby@... # zizmor: ignore[cache-poisoning] -- cached deps are for testing, not release artifact generation
```

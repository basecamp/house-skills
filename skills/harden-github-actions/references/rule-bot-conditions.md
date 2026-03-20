# `bot-conditions` — Spoofable Bot Actor Check

Using `github.actor == 'dependabot[bot]'` alone in job conditions is spoofable.

**Fix (always):** Use a dual check: `github.actor` validates who triggered the current
event, `github.event.pull_request.user.login` validates who owns the PR. Both together
prevent a human-triggered event from re-entering the approve/merge path. Suppress the
zizmor finding with a comment explaining the dual check is intentional.

```yaml
# BEFORE (spoofable)
if: github.actor == 'dependabot[bot]'

# AFTER (dual check)
if: github.actor == 'dependabot[bot]' && github.event.pull_request.user.login == 'dependabot[bot]' # zizmor: ignore[bot-conditions] -- dual check: actor validates current trigger, user.login validates PR origin
```

Note: `--fix=all` will replace `github.actor` with `github.event.pull_request.user.login`.
Revert the auto-fix and apply the dual check manually instead.

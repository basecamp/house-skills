# `bot-conditions` — Spoofable Bot Actor Check

Using `github.actor == 'dependabot[bot]'` in job conditions is spoofable.

**Fix (always):** Use `github.event.pull_request.user.login` instead, which is verified by
GitHub and cannot be spoofed. The `--fix=all` auto-fix handles this correctly.

```yaml
# BEFORE (spoofable)
if: github.actor == 'dependabot[bot]'

# AFTER (verified)
if: github.event.pull_request.user.login == 'dependabot[bot]'
```

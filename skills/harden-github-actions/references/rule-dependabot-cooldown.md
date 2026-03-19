# `dependabot-cooldown` — Missing Cooldown on Dependabot Ecosystems

zizmor warns when dependabot ecosystem entries lack a cooldown period.

**Fix (always):** Add `cooldown: default-days: 10` to every ecosystem entry in
`.github/dependabot.yml`, not just `github-actions`. This gives a 10-day waiting period
after any version is published before dependabot proposes it, avoiding churn from rapid
post-release patches.

```yaml
cooldown:
  default-days: 10
```

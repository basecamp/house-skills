# `dependabot-cooldown` — Missing Cooldown on Dependabot Ecosystems

zizmor warns when dependabot ecosystem entries lack a cooldown period.

**Fix (always):** Add cooldown to every ecosystem entry in `.github/dependabot.yml`.

For real package ecosystems (bundler, npm, gomod, gradle, pip, etc.), use semver-granular
cooldowns so low-risk patches flow faster while major bumps get more soak time:

```yaml
cooldown:
  semver-major-days: 7
  semver-minor-days: 3
  semver-patch-days: 2
```

For `github-actions`, semver-granular keys are not supported. Use the default cooldown:

```yaml
cooldown:
  default-days: 7
```

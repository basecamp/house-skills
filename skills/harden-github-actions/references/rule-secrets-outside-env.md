# `secrets-outside-env` — Secrets Used Without GitHub Environment

zizmor flags jobs that use `secrets.*` (other than `secrets.GITHUB_TOKEN`) without an
`environment:` declaration. Org/repo-level secrets are available to any workflow run,
including from forks. Environment-scoped secrets are only available to jobs targeting that
environment, providing an additional access control layer.

**Fix (preferred):** Add an `environment:` declaration to the job and move the secrets into
that environment's secret store (removing them from repo/org-wide secrets).

```yaml
# BEFORE (flagged)
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - run: ./deploy.sh
        env:
          API_KEY: ${{ secrets.API_KEY }}

# AFTER (fixed)
jobs:
  deploy:
    runs-on: ubuntu-latest
    environment: production
    steps:
      - run: ./deploy.sh
        env:
          API_KEY: ${{ secrets.API_KEY }}
```

**Suppress only if ALL of these are true:**
1. The job genuinely needs a non-GITHUB_TOKEN secret (not just a misconfiguration)
2. Adding an `environment:` declaration would break the workflow (e.g., environment
   protection rules would block PR-triggered runs that need the secret)
3. The secret is not exposed to untrusted code (e.g., not passed to a step that runs
   PR-submitted code)

If you cannot confirm all three, apply the fix. If the situation is ambiguous, **stop and
report the finding — do not suppress or fix.**

```yaml
jobs:
  test:
    runs-on: ubuntu-latest # zizmor: ignore[secrets-outside-env] -- API key needed in PR test runs; environment protection would block CI
```

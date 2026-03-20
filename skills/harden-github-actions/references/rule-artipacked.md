# `artipacked` — Credential Persistence After Checkout

`actions/checkout` persists git credentials by default, which later steps can exfiltrate.

**Fix (default):** Add `persist-credentials: false` to every `actions/checkout` step.

```yaml
- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
  with:
    persist-credentials: false
```

**Suppress only if ALL of these are true:**
1. A later step in the SAME job runs `git push`, `git tag`, or similar write operation
2. The push target is the same repository (not a fork or external repo requiring a PAT)
3. No other authentication mechanism (e.g., deploy key, PAT) is used for the push

If you cannot confirm all three, apply the fix. If fixing would break the workflow and you
cannot confirm all three, **stop and report the finding — do not suppress.**

```yaml
- uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2 # zizmor: ignore[artipacked] -- credentials needed for git push
```

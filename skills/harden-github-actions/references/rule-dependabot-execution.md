# `dependabot-execution` — Insecure External Code Execution

Dependabot's `insecure-external-code-execution: allow` lets package managers run arbitrary
code during dependency resolution.

**Fix (if possible):** Remove `insecure-external-code-execution: allow`.

**Suppress only if ALL of these are true:**
1. The package ecosystem requires code execution to resolve dependencies (e.g., Bundler with
   private gem sources that use custom `source` blocks in the Gemfile)
2. You have confirmed that removing `allow` causes dependency resolution to fail
3. The private registry is trusted (e.g., an internal GitHub Packages registry)

If you cannot confirm all three, apply the fix. If fixing would break dependency resolution
and you cannot confirm all three, **stop and report the finding — do not suppress.**

```yaml
insecure-external-code-execution: allow # zizmor: ignore[dependabot-execution] -- required for Bundler to resolve gems from the private github-basecamp registry
```

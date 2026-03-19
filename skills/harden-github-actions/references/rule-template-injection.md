# `template-injection` — Expression Injection in `run:` Blocks

Using `${{ }}` directly in `run:` scripts allows attackers to inject shell commands via
crafted PR titles, branch names, etc.

**Fix (always):** Pass expressions through `env:` vars. Never use `${{ }}` directly in `run:`.

```yaml
# BEFORE (vulnerable)
- run: |
    tags="${{ steps.meta.outputs.tags }}"
    echo "$tags"

# AFTER (safe)
- env:
    TAGS: ${{ steps.meta.outputs.tags }}
  run: |
    tags="$TAGS"
    echo "$tags"
```

This applies to ALL expressions in `run:` blocks, including `github.event.pull_request.title`,
`steps.*.outputs.*`, `inputs.*`, etc. There is no valid reason to suppress this rule.

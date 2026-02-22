# Creating & Auditing AGENTS.md

## Creating a New AGENTS.md

1. **Discover the repo's default branch:**

   ```bash
   # Ensure origin/HEAD is set (may be missing in CI or shallow clones)
   git remote set-head origin --auto 2>/dev/null
   git symbolic-ref --short refs/remotes/origin/HEAD | sed 's#^origin/##'
   ```

   If `origin/HEAD` is not set and `set-head --auto` fails (no network), fall
   back to the app's AGENTS.md `Default branch:` line or the registry table.

2. **Discover deploy destinations:**

   ```bash
   ls config/deploy.*.yml | sed 's/.*deploy\.\(.*\)\.yml/\1/'
   # Fizzy: ls saas/config/deploy.*.yml | ...
   ```

3. **Check for pre-deploy steps** (e.g., `bin/rails saas:enable` for Fizzy).

4. **Read existing README.md and bin/setup** for test commands and setup steps.

5. **Write the AGENTS.md** following the template in spec.md.

## Auditing an Existing AGENTS.md

1. Run `bin/verify-app-registry` (in the coworker repo) to check branch and
   destination drift.

2. Verify MUST sections are present and accurate:
   - Deploy section has correct branch, command, and destinations
   - Commands section has test and setup commands

3. Check for anti-patterns (see spec.md).

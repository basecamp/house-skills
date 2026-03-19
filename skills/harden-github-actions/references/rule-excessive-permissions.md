# `excessive-permissions` — Overly Broad Permissions

Top-level `permissions:` grants those permissions to ALL jobs, violating least privilege.

**Fix (always):** Replace the workflow-level `permissions:` block with `permissions: {}` (deny
all) and move permissions down to each individual job. The `permissions: {}` ensures every job
starts with zero permissions, so any job that forgets to declare its own permissions will fail
safe rather than inheriting broad defaults.

```yaml
# BEFORE (too broad)
permissions:
  contents: read
  packages: write
  id-token: write
  attestations: write

jobs:
  build:
    ...
  merge:
    ...

# AFTER (deny-all at workflow level, scoped per job)
permissions: {}

jobs:
  build:
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
    ...
  merge:
    permissions:
      packages: write
      id-token: write
    ...
```

**How to determine which permissions each job needs — DO NOT GUESS:**

1. Start from the existing top-level `permissions:` block — that's the universe of permissions to distribute
2. For each job, identify which actions it uses
3. **For each action, check the permission mappings in `references/permission-mappings.md`. If the action is not in that file, you MUST fetch its README on GitHub and read the documented permission requirements before proceeding.**
4. Assign only the permissions each job actually needs
5. Don't invent permissions that weren't in the original block

**After researching a new action's permissions, add it to `references/permission-mappings.md`.**

**Reusable workflows (`uses: ./.github/workflows/...`):** Caller jobs that invoke reusable
workflows must specify permissions explicitly. Reusable workflows inherit the caller job's
permissions, and permissions can only be maintained or reduced through the chain — never
elevated. When you set `permissions: {}` at the workflow level, every job (including reusable
workflow calls) starts with zero permissions and must declare what it needs.

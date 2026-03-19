# GitHub Actions Permission Mappings

GITHUB_TOKEN permission requirements for common GitHub Actions. Used by the
`harden-github-actions` skill when resolving `excessive-permissions` findings.

**If an action is not in this table, you MUST fetch its README on GitHub and read the
documented permission requirements before proceeding. Then add it to this table.**

## Actions

| Action | Permissions | Notes |
|--------|------------|-------|
| `actions/ai-inference` | `models: read` | + `contents: read` recommended; PAT required for MCP server feature |
| `actions/attest-build-provenance` | `id-token: write`, `attestations: write`, `contents: read` | + `packages: write` when `push-to-registry: true` for container images |
| `actions/cache` | none | Uses Actions cache service via implicit runner credentials, not GITHUB_TOKEN |
| `actions/checkout` | `contents: read` | |
| `actions/configure-pages` | none | Reads Pages config only; `enablement: true` needs a PAT with `pages:write` |
| `actions/create-github-app-token` | none | Authenticates via GitHub App credentials, not GITHUB_TOKEN; *generates* a token with its own permissions |
| `actions/create-release` | `contents: write` | Archived/unmaintained; consider `softprops/action-gh-release` instead |
| `actions/dependency-review-action` | `contents: read` | + `pull-requests: write` if `comment-summary-in-pr` is `always` or `on-failure` |
| `actions/deploy-pages` | `pages: write`, `id-token: write` | |
| `actions/download-artifact` | none | `actions: read` only when using `github-token` input to download from other repos/workflow runs |
| `actions/github-script` | depends on script | No fixed permissions; required permissions depend on which GitHub API calls the script makes (e.g., `issues: write` for comments, `contents: read` for repo data). Read the script to determine. |
| `actions/labeler` | `contents: read`, `pull-requests: write` | + `issues: write` only if the action needs to create labels that don't already exist |
| `actions/setup-go` | `contents: read` | |
| `actions/setup-java` | `contents: read` | |
| `actions/setup-node` | `contents: read` | |
| `actions/setup-ruby` | `contents: read` | |
| `actions/stale` | `issues: write`, `pull-requests: write` | + `contents: write` when `delete-branch: true` |
| `actions/upload-artifact` | none | Uses Actions artifact storage via implicit runner credentials |
| `actions/upload-pages-artifact` | none | Only creates a tar archive |
| `actions/upload-release-asset` | `contents: write` | Archived/unmaintained; consider `softprops/action-gh-release` instead |
| `anchore/sbom-action` | `contents: write` | + `actions: read` when attaching release assets (implicit for public repos) |
| `aquasecurity/trivy-action` | `contents: read` | + `security-events: write` when uploading SARIF; + `contents: write` when submitting SBOMs to Dependency Graph |
| `basecamp/sdk` (sub-actions) | varies | Contains 5 composite actions under `actions/`. Only `release-orchestrate` needs a token: `actions: read`, `contents: write`. The others (`conformance-run`, `rubric-check`, `service-drift`, `smithy-verify`) need none. |
| `cachix/install-nix-action` | none | Uses `github.token` only to avoid API rate limits when downloading Nix |
| `dependabot/fetch-metadata` | `pull-requests: read` | + `pull-requests: write` for auto-approving; + `contents: write` for auto-merging; PAT required for `alert-lookup` or `compat-lookup` |
| `dev-build-deploy/commit-me` | `pull-requests: read` | + `pull-requests: write` when `update-labels: true`; + `contents: write` for automatic rebase-merge detection |
| `devcontainers/ci` | none | When pushing images to ghcr.io, the workflow must log in separately (e.g., `docker/login-action`), which requires `packages: write` |
| `docker/build-push-action` | `contents: read` | Registry auth (e.g., `packages: write` for GHCR) is handled by `docker/login-action`, not this action |
| `docker/login-action` | none | The action itself needs no permissions; pass `secrets.GITHUB_TOKEN` as the `password` input. The *token* needs `packages: write` for GHCR push, `packages: read` for pull-only. |
| `docker/metadata-action` | `contents: read` | Reads repo context (tags, branches, commits) only |
| `docker/setup-buildx-action` | none | Only configures the Docker Buildx builder environment |
| `docker/setup-qemu-action` | none | Installs QEMU static binaries for multi-platform builds |
| `dorny/paths-filter` | `pull-requests: read` | Only on `pull_request` events; on `push` events uses git commands directly and needs no permissions |
| `elastic/docs` | not a standard action | Elastic's internal documentation build tooling. Contains a composite action at `.github/actions/docs-preview` needing `issues: write` for PR comments, but not intended for external use. Check the repo source if encountered. |
| `github/codeql-action` (/init, /analyze, /upload-sarif) | `security-events: write`, `contents: read` | `security-events: write` for all advanced setup workflows; `contents: read` additionally required for private repos |
| `golangci/golangci-lint-action` | `contents: read` | + `pull-requests: read` if using `only-new-issues`; + `checks: write` for inline PR annotations |
| `google-github-actions/release-please-action` | `contents: write`, `pull-requests: write` | May also need "Allow GitHub Actions to create and approve pull requests" enabled in repo settings |
| `goreleaser/goreleaser-action` | `contents: write` | Token passed via `env: GITHUB_TOKEN`; PAT with `repo` scope needed for cross-repo operations (e.g., Homebrew taps) |
| `gradle/actions` (sub-actions) | varies | `setup-gradle`: none for basic use; `contents: write` for dependency graph submission; `pull-requests: write` for PR comment summaries. `dependency-submission`: `contents: write`. `wrapper-validation`: none. |
| `ko-build/setup-ko` | none | + `packages: write` when pushing container images to ghcr.io; no permissions needed if only building |
| `necko-actions/setup-smithy` | none | Installs Smithy CLI and adds to PATH |
| `ossf/scorecard-action` | `security-events: write`, `id-token: write`, `contents: read` | For public repos with `publish_results: true`; private repos also need `issues: read`, `pull-requests: read`, `checks: read` |
| `reviewdog/action-rubocop` | `contents: read`, `pull-requests: write` | For `github-pr-review` and `github-pr-check` reporters; `checks: write` may also be needed for `github-check` reporter |
| `rhysd/actionlint` | none | Not a standard action (no `action.yml`); used by downloading the binary via shell script or Docker. Does not use GITHUB_TOKEN. |
| `securego/gosec` | none | Runs gosec in Docker; no token input. If uploading SARIF via `github/codeql-action/upload-sarif`, that action needs `security-events: write`. |
| `sigstore/cosign-installer` | none | Only installs cosign. Subsequent cosign *commands* need `id-token: write` for OIDC keyless signing. |
| `softprops/action-gh-release` | `contents: write` | + `discussions: write` if using `discussion_category_name` |
| `rubygems/configure-rubygems-credentials` | `id-token: write` | + `contents: write` only if using `bundle exec rake release` for git push |
| `zizmorcore/zizmor-action` | none | With `advanced-security: false` (recommended in this skill). With `advanced-security: true`: `security-events: write`; private repos also need `contents: read`, `actions: read`. |
| `zzak/action-discord` | `contents: read` | Reads commit metadata for Discord webhook notifications |

## Common patterns (not specific actions)

| Pattern | Permissions | Notes |
|---------|------------|-------|
| `npm publish --provenance` | `id-token: write` | OIDC for npm provenance attestation |
| Push to GHCR | `packages: write` | |
| Sigstore/cosign signing | `id-token: write` | |
| Attestation actions | `attestations: write` | |

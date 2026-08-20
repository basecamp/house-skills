# `unpinned-images` — Container Images Not Pinned to Digest

zizmor flags container images in `services:` or `container:` blocks that use a tag without
a SHA256 digest. Tags are mutable — a compromised registry could serve different content
under the same tag.

**Suppress (default).** Determining the correct digest for a container image is nontrivial
(multi-arch manifests, registry-specific behavior, digest instability across rebuilds).
Suppress with a reason.

```yaml
image: myorg/myimage:1.0.0 # zizmor: ignore[unpinned-images] -- version tag is fine for service containers
```

## Prior art: installing the tool instead of pinning its image

`basecamp/surfguard` sidesteps the mutable-tag problem for actionlint by not using the Docker
action at all. It downloads the release binary and verifies it against a recorded checksum:

```yaml
- name: Install actionlint (checksum-verified; it is not a GitHub action)
  env:
    ACTIONLINT_VERSION: "1.7.12"
    ACTIONLINT_SHA256: "8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
  run: |
    curl --silent --show-error --fail --location --output actionlint.tar.gz \
      "https://github.com/rhysd/actionlint/releases/download/v${ACTIONLINT_VERSION}/actionlint_${ACTIONLINT_VERSION}_linux_amd64.tar.gz"
    echo "${ACTIONLINT_SHA256}  actionlint.tar.gz" | sha256sum --check
    mkdir --parents "${RUNNER_TEMP}/actionlint"
    tar --extract --gzip --file actionlint.tar.gz --directory "${RUNNER_TEMP}/actionlint" actionlint
    rm actionlint.tar.gz
    echo "${RUNNER_TEMP}/actionlint" >> "$GITHUB_PATH"
```

The last four lines are load-bearing: `sha256sum --check` verifies the archive but leaves the
binary inside it, so a step that stops there fails the next `actionlint` call with
command-not-found. Extract it and put it on `PATH` in the same step.

This gives a genuinely immutable artifact where a tag cannot. The cost is a version and a
checksum to bump together on every upgrade, which dependabot will not do for you — so it
trades a mutable base image for a manual bump. Worth knowing about; not the default, and not
a reason to reopen a suppression that was already accepted.

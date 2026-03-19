# `unpinned-images` — Container Images Not Pinned to Digest

zizmor flags container images in `services:` or `container:` blocks that use a tag without
a SHA256 digest. Tags are mutable — a compromised registry could serve different content
under the same tag.

**Fix (preferred):** Append `@sha256:<digest>` to the image reference, keeping the tag for
readability.

**How to determine the correct digest — DO NOT GUESS:**

Run `docker manifest inspect` to get the digest for the specific platform:

```bash
# For linux/amd64 (the default GitHub Actions runner):
docker manifest inspect <image>:<tag> | jq -r '.manifests[] | select(.platform.architecture == "amd64" and .platform.os == "linux") | .digest'
```

If the image is a single-platform manifest (no `.manifests` array):

```bash
docker manifest inspect <image>:<tag> | jq -r '.config.digest'
```

Then append the digest to the image reference:

```yaml
# BEFORE
image: myorg/myimage:1.0.0

# AFTER
image: myorg/myimage:1.0.0@sha256:abc123...
```

**Never fabricate a digest.** If you cannot run `docker manifest inspect` or the command
fails, **stop and report the finding — do not guess a digest value.**

**Suppress (when necessary):** If the image is from an internal/trusted registry where tag
immutability is enforced, or if pinning would create maintenance burden without meaningful
security benefit. Include the reason.

```yaml
image: myorg/myimage:1.0.0 # zizmor: ignore[unpinned-images] -- internal registry with immutable tags
```

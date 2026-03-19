# `unpinned-images` — Container Images Not Pinned to Digest

zizmor flags container images in `services:` or `container:` blocks that use a tag without
a SHA256 digest. Tags are mutable — a compromised registry could serve different content
under the same tag.

**Suppress (default).** Determining the correct digest for a container image is nontrivial
(multi-arch manifests, registry-specific behavior, digest instability across rebuilds).
The version tag already pins to a specific release. Suppress with a reason.

```yaml
image: myorg/myimage:1.0.0 # zizmor: ignore[unpinned-images] -- version tag pins to a specific release
```

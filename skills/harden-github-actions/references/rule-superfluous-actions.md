# `superfluous-actions` — Action Can Be Replaced With Inline Code

zizmor suggests replacing certain actions with equivalent inline shell commands.

**Suppress (always):** Never replace an existing action with inline code. Actions are more
readable, maintainable, and receive upstream security fixes automatically. Suppress with a
note to consider removal in the future.

```yaml
- uses: softprops/action-gh-release@... # zizmor: ignore[superfluous-actions] -- consider removal
```

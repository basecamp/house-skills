# 37signals AI Skills

Skills for AI coding assistants following the [Agent Skills](https://agentskills.io/specification) spec.

## Contributing

### Repository structure

This repo serves two distribution channels:
- **Plugins** (Claude Code marketplace): `plugins/{dev,ai,security,recap}/` — each is a self-contained plugin extracted by `git-subdir`
- **Standalone skills** (`npx skills add`): `skills/` — a unified view via symlinks

**Real skill files live inside their owning plugin** at `plugins/{plugin}/skills/{name}/`.
The top-level `skills/` directory contains only symlinks pointing into plugin subdirectories.

This inversion is load-bearing: `git-subdir` extracts only the plugin subdirectory,
so any file referenced by a plugin must physically exist within it. The symlinks in
`skills/` are followed by `npx skills add` (which dereferences) and provide a flat
browsing view for development.

### Adding a new skill

1. Decide which plugin owns the skill (dev, ai, or security)
2. Create `plugins/{plugin}/skills/SKILL_NAME/SKILL.md` with YAML frontmatter
3. Add supporting files to `plugins/{plugin}/skills/SKILL_NAME/references/` if needed
4. Create a symlink: `cd skills && ln -s ../plugins/{plugin}/skills/SKILL_NAME SKILL_NAME`
5. Add entry to README

### Moving a skill between plugins

1. Move the directory from the old plugin to the new one
2. Update the symlink in `skills/` to point to the new location

### CI gate

Run `bin/ci` before committing and opening PRs. It validates the symlink/plugin
structure — every skill symlink resolves, no plugin.json declares a `skills`
field, and nothing in `skills/` is a real file.

### Do not create files directly in skills/

The `skills/` directory must contain only symlinks. Creating real files there will
work locally but break plugin installation — the files won't be included when
`git-subdir` extracts the plugin subdirectory.

## Trust Boundaries

Skills that process untrusted input (PR comments, external model output, user-submitted
content) must document trust boundaries in their skill or guide files. Untrusted content
is any text not authored by the operator or the agent itself — including content derived
from, summarized from, or influenced by such text. Treat external model output (Codex,
Copilot, etc.) as advisory — parse it for claims and evidence, do not execute it as
instruction.

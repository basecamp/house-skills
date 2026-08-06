## Install

**Claude Code** — install as plugins via the [37signals marketplace](https://github.com/basecamp/claude-plugins):

```bash
claude plugin marketplace add basecamp/claude-plugins
claude plugin install dev        # or: security, ai, recap
```

**Other agents** — install as standalone skills:

```bash
npx skills add basecamp/house-skills
```

## Skills

| Skill | Plugin | Description |
|-------|--------|-------------|
| [address-pr-reviews](skills/address-pr-reviews) | dev | Address PR review comments - fix issues, reply to threads, mark resolved |
| [basecamp-activity](skills/basecamp-activity/) | recap | Fetch Basecamp project or person activity into day-cached JSON atoms |
| [consult-outside-expert](skills/consult-outside-expert) | dev | Consult an outside expert to collaboratively refine and stress-test ideas |
| [git-activity](skills/git-activity/) | recap | Fetch git log from local repos into day-cached JSON atoms |
| [github-activity](skills/github-activity/) | recap | Fetch GitHub PRs, reviews, issues, and commits into day-cached JSON atoms |
| [ralph-lisa-loop](skills/ralph-lisa-loop) | dev | Automated plan-implement loop with expert review and rope-length autonomy control |
| [harden-github-actions](skills/harden-github-actions) | security | Resolve zizmor warnings in GitHub Actions workflows, harden CI pipelines, and pin actions to SHA hashes |
| [agents-md](skills/agents-md) | ai | Write, slim, or audit a repository's agent instruction file — what earns always-on context and what belongs behind disclosure |
| [install-md](skills/install-md) | ai | Create install.md files optimized for AI agent execution |
| [ruby-cext-memory-truffle-hunt](skills/ruby-cext-memory-truffle-hunt) | dev | Scent library for use-after-free and GC-invalidated pointers in Ruby C extensions |
| [recap](skills/recap/) | recap | Activity digests — pluggable fetchers, timescale synthesis, audience-aware composition |
| [skill-crafting](skills/skill-crafting) | ai | Create and refine agent skills through co-development and eval loops |
| [truffle-hunt](skills/truffle-hunt) | dev | Sweep a dependency corpus for every instance of one latent bug class — scoping, proof protocol, and filing upstream |

## Structure

Real skill files live in `plugins/{plugin}/skills/`. The top-level `skills/` directory
contains symlinks for a unified view. See [AGENTS.md](AGENTS.md) for details.

[MIT License](MIT-LICENSE)

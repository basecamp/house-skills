<!-- yarkr:badges:start -->
[![Indexed on Yarkr](https://api.yarkr.com/functions/v1/api/api/v1/badges/repos/basecamp/house-skills/version.svg)](https://yarkr.com/arks/basecamp/house-skills)
<!-- yarkr:badges:end -->

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

<!-- yarkr:install:start -->
## Install with Yarkr

This repo is indexed on [Yarkr](https://yarkr.com/arks/basecamp/house-skills) as the ark [basecamp/house-skills](https://yarkr.com/arks/basecamp/house-skills), so its skills install into Claude Code, Codex, Cursor and other agent harnesses with one command.

### CLI

Install the whole ark, which Yarkr keeps equal to this repo:

```bash
npx yarkr-cli sync basecamp/house-skills
```

Or add individual skills:

```bash
npx yarkr-cli add skill:basecamp/house-skills/address-pr-reviews
npx yarkr-cli add skill:basecamp/house-skills/agents-md
npx yarkr-cli add skill:basecamp/house-skills/basecamp-activity
```

No Node.js? `curl -fsSL https://yarkr.com/install.sh | sh` (macOS, Linux) or `irm https://yarkr.com/install.ps1 | iex` (Windows PowerShell) installs `yarkr` as a standalone binary; then type `yarkr` in place of `npx yarkr-cli`.

### MCP

Connect your agent to the ark over MCP:

```bash
npx yarkr-cli mcp add basecamp/house-skills
```

Or point any MCP client at `https://api.yarkr.com/functions/v1/api/mcp/o/basecamp/house-skills`.
<!-- yarkr:install:end -->

## Skills

| Skill | Plugin | Description |
|-------|--------|-------------|
| [address-pr-reviews](skills/address-pr-reviews) | dev | Address PR review comments - fix issues, reply to threads, mark resolved |
| [basecamp-activity](skills/basecamp-activity/) | recap | Fetch Basecamp project or person activity into day-cached JSON atoms |
| [consult-outside-expert](skills/consult-outside-expert) | dev | Explicitly launch an independent outside-expert consultation and refinement loop |
| [git-activity](skills/git-activity/) | recap | Fetch git log from local repos into day-cached JSON atoms |
| [github-activity](skills/github-activity/) | recap | Fetch GitHub PRs, reviews, issues, and commits into day-cached JSON atoms |
| [jazzhands](skills/jazzhands) | dev | Borrow a disposable Linux or Mac guest from a Jazzhands lease controller for heavy, long, clean-room or other-OS work, and release it |
| [modernize-rails-app](skills/modernize-rails-app) | dev | Upgrade an old Rails app to a supported Rails and Ruby one minor version at a time, with a green `bin/ci` and a plan file at each step |
| [ralph-lisa-loop](skills/ralph-lisa-loop) | dev | Explicitly launch the automated plan-implement-review loop with rope-length control |
| [identity-security](skills/identity-security) | security | Judge sign-in, session, step-up, 2FA and passkey, recovery and app-grant changes from first principles, weighing friction, lockout and support load |
| [harden-github-actions](skills/harden-github-actions) | security | Resolve zizmor warnings in GitHub Actions workflows, harden CI pipelines, and pin actions to SHA hashes |
| [agents-md](skills/agents-md) | ai | Write, slim, or audit a repository's agent instruction file — what earns always-on context and what belongs behind disclosure |
| [install-md](skills/install-md) | ai | Create install.md files optimized for AI agent execution |
| [ruby-cext-memory-truffle-hunt](skills/ruby-cext-memory-truffle-hunt) | dev | Scent library for use-after-free and GC-invalidated pointers in Ruby C extensions |
| [recap](skills/recap/) | recap | Activity digests — pluggable fetchers, timescale synthesis, audience-aware composition |
| [skill-crafting](skills/skill-crafting) | ai | Create and refine agent skills through co-development and eval loops |
| [truffle-hunt](skills/truffle-hunt) | dev | Sweep a dependency corpus for every instance of one latent bug class — scoping, proof protocol, and filing upstream |

## Agents

The dev plugin also ships a subagent type for Claude Code.

| Agent | Plugin | Description |
|-------|--------|-------------|
| [pr-worker](plugins/dev/agents/pr-worker.md) | dev | Run one phase of a pull request (build, CI to green, one review round) from a brief, and hand off to a fresh agent for the next |

## Structure

Real skill files live in `plugins/{plugin}/skills/`. The top-level `skills/` directory
contains symlinks for a unified view. See [AGENTS.md](AGENTS.md) for details.

[MIT License](MIT-LICENSE)

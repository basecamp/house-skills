# herdr-summary

A Claude Code plugin that keeps each Claude session's title on what it's working on, so its row in [herdr](https://herdr.dev)'s agents list says so, like "Debugging flaky OAuth e2e test".

herdr's agents list with four Claude sessions, the title after each space's name:

```
Agents

Done 1
  billing-api · Running tests and fixing failures

Working 1
  storefront · Adding coupon code field to checkout

Idle 2
  weather-app · Fixing forecast cache expiration bug
  docs-site · Fixing broken links in getting-started guide
```

Without it, a row shows the title Claude Code gave the session from its first prompt, and keeps it however far the work moves. With it, every prompt you send, steering included, gets a fresh look: Haiku reads the session's first request, its recent messages, the current title and the new prompt, and names what the agent is doing and what for. A new request or a change of topic gets a new title; a correction or narrower scope reshapes it; "ok", "yes" or "thanks" leaves it alone.

It works with herdr-core and the herdr app, in a terminal herdr started (`HC_TERMINAL_ID` set). Outside one it does nothing. herdr's older one-binary releases aren't supported.

## Install

At the prompt of a Claude Code terminal session:

```
/plugin marketplace add basecamp/plugins
/plugin install herdr-summary@37signals
```

Press Enter for the user scope. From a shell instead:

```
claude plugin marketplace add basecamp/plugins
claude plugin install herdr-summary@37signals
```

New sessions load it on their own. A session that was already open picks it up after `/reload-plugins`. To update later, run `claude plugin update herdr-summary@37signals`, then `/reload-plugins`.

There is nothing to set up in herdr: its agents list shows a terminal's title for any tab you haven't named. A tab you named shows its name instead.

For an agent on another machine, install the plugin on that machine.

It needs a Claude Code build that runs mods (function-hook plugins).

## How it works

- Before each prompt enters, the mod (`hooks/register.ts`) asks Haiku for a label through `$.model.complete`, billed to the session's own credentials. Nothing is sent anywhere else. It waits at most two seconds, so a prompt enters about half a second later than it would; a label that takes longer applies on the next prompt. Slash commands aren't labeled.
- It writes the label to `$XDG_RUNTIME_DIR/claude-herdr-summary/<session id>.json` (`$TMPDIR`, else `/tmp`, where that isn't set), and the `UserPromptSubmit` command hook, `hooks/title.sh`, returns it to Claude Code as `sessionTitle`. Claude Code writes the session title to the terminal title, which herdr-core reports.
- The command hook applies it because the mod can't: on Team and Enterprise accounts, Claude Code's built-in sec-default plugin keeps classic hooks such as `UserPromptSubmit` out of reach of installed plugins' modules.
- The label is the session's title, so `/resume` lists it too, and it replaces a title you set with `/rename` at the next prompt.
- Only the prompts you send are labeled. A subagent's work doesn't change the title, and a long turn keeps its title until the next prompt.

## License

[MIT](../../MIT-LICENSE)

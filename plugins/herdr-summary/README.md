# herdr-summary

A Claude Code mod that labels each Claude session's row in [herdr](https://herdr.dev)'s agent sidebar with a short summary of what it's working on, like "Debugging flaky OAuth e2e test".

herdr's agent sidebar with four Claude sessions, the summary under each project name:

```
agents                                grouped

● billing-api
  Running tests and fixing failures

○ storefront
  Adding coupon code field to checkout

○ weather-app
  Fixing forecast cache expiration bug

○ docs-site
  Fixing broken links in getting-started guide
```

While a session runs inside a herdr pane, the mod asks Haiku for a 3–7 word label from the last few messages and reports it to herdr as the pane's `$summary` token. It refreshes at most every five minutes mid-turn and every minute when a turn ends, and only when the conversation has grown, so an idle session costs nothing. When the session ends it clears the label. Outside herdr it does nothing.

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

In the desktop app, click + next to the prompt box, then Plugins, Add marketplace with `basecamp/plugins`, then add herdr-summary.

New sessions load it on their own. A session that was already open picks it up after `/reload-plugins`. To update later, run `claude plugin update herdr-summary@37signals`, then `/reload-plugins`.

It needs a Claude Code build that runs mods (function-hook plugins).

## Show it in herdr

herdr only draws tokens your sidebar layout names. Add a `$summary` row for Claude agents to `~/.config/herdr/config.toml`:

```toml
[ui.sidebar.agents.rows_by_agent]
claude = [
  ["state_icon", "workspace"],
  [{ token = "$summary", fg = "#bb9af7" }],
]
```

`rows_by_agent.claude` replaces your default agent layout for Claude panes, so copy your own `[ui.sidebar.agents] rows` in and add the summary row to it. Drop `fg` to use the default color.

Then reload from inside herdr: open the menu and choose **reload config**, or press your `reload_config` key. `herdr server reload-config` alone isn't enough, because sidebar layouts come from the client's config, and only the in-app reload re-reads it.

## How it works

- Labels come from `$.model.complete` with Haiku, billed to the session's own credentials. Nothing is sent anywhere else.
- It reports with `herdr pane report-metadata <pane> --source claude-summary --token summary=…`, using `HERDR_BIN_PATH`, which herdr sets in every pane.
- Only the main conversation is summarized. A subagent's turns and tool calls don't trigger a refresh.

## License

[MIT](../../MIT-LICENSE)

# Inside: on a repo in one of the company's GitHub orgs, a User the repo calls
# OWNER or MEMBER; on any other repo, only the token's own account, since an
# org or a personal repo outside the company has its own members. Review bots
# are matched by type and id everywhere (a User can't be typed Bot). So is any
# bot whose GitHub App the repo's owner owns; see owned_app.
# CONTRIBUTOR is outside: one merged PR earns it, backdated to everything that
# person ever wrote. Everything outside comes back as a link for a person,
# never as text.
def company: ["basecamp"];
def repo: $head[0].data.repository;
def viewer: $head[0].data.viewer.login;
def in_company: repo.owner.login | ascii_downcase | IN(company[]);
# github-actions[bot] (41898282, GitHub's own Actions app, owned by github) is
# whatever the repo's workflows post. A same-repo PR can change a workflow that
# posts as it, and pushing a workflow takes write access. A fork PR's
# pull_request token is read-only unless a private repo's admins send fork PRs
# write tokens, which gives every forker a writer's token; pull_request_target
# runs the base branch's workflow. So this widens the inside only to what a
# collaborator with write access could already write. A workflow that copies someone's text into its comment (a PR
# title, an issue_comment argument) passes that text on, as any inside bot that
# quotes does; see owned_app.
def bots: [175728472, 199175422, 191113872, 62310815, 41898282] + $extra; # Copilot, Codex, cubic, GitHub Advanced Security, github-actions
def person: .author == null or .author.__typename != "Bot";
def member: .author.__typename == "User"
  and ((in_company and (.authorAssociation | IN("OWNER", "MEMBER"))) or .author.login == viewer);
# A bot is also inside when its GitHub App is owned by the account that owns
# the repo: on an org's repo, the apps that org owns; on a personal repo, the
# apps its user owns. Only the org's owners, and the app managers they name,
# can register an app under the org (GitHub documents that a transfer into an
# org waits for its acceptance for OAuth apps; this takes GitHub Apps to work
# the same way). So a PR can't make its author's app trusted, its bot's text is
# the org's own code at work, and nothing about any one org is written here.
# On another org's repo this trusts that org's apps, never the company's.
# $apps[0] maps an app's slug (its bot's login, lowercased, less any "[bot]")
# to its owner's login, as fetch-reviews looked it up. A bot it couldn't look up
# isn't in it, so it stays outside. Matched on type as well as login: a User
# named like an app's bot is never an app. The comparison always yields a
# boolean, so a missing or odd value reads as false rather than as nothing.
# What such a bot posts comes through whole, quotes included, as a listed bot's
# or a member's does: nothing here parses quotes. So an app that quotes outside
# text in its own comment (a landing queue echoing the comment it ejected, a
# workflow echoing a PR title) passes that text on. That's the org's own code
# doing what it was built to do, not someone getting around this; the answer
# is in the app, or in leaving it off the org.
def slug: ascii_downcase | sub("\\[bot\\]$"; "");
def owned_app: .author.__typename == "Bot"
  and (($apps[0][.author.login // "" | slug] // null) as $owner
    | ($owner | type) == "string" and ($owner | ascii_downcase) == (repo.owner.login | ascii_downcase));
def bot: .author.__typename == "Bot" and ((.author.databaseId | IN(bots[])) or owned_app);
def inside: member or bot;
# A person clears an outside item by hiding it as Resolved once they've read
# it. Hidden for any other reason (spam, off-topic, outdated, duplicate, abuse),
# it's still unread as far as anyone knows, so it stays withheld.
# GitHub doesn't say who hid a comment, so a hide counts only when its author
# couldn't have hidden it: a person whose role can't hide comments. Owners,
# members and collaborators can (on another org's repo they're still outside),
# and so can an app with write access, so their own hides don't clear.
def cleared: .isMinimized and ((.minimizedReason // "") | ascii_downcase) == "resolved"
  and person and (.authorAssociation | IN("NONE", "CONTRIBUTOR", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "MANNEQUIN"));
def pending: (inside or cleared) | not;
def nodes($pages; f): [$pages[0][].data.repository.pullRequest | f | .nodes[]];
def link($kind): {kind: $kind, url, by: (if person then "person" else "bot" end), association: .authorAssociation};
# Unicode tag characters (U+E0000-U+E007F) render as nothing on GitHub, so a
# member who pastes them from an outsider's text can't see what they pass on.
# Only that block goes: emoji joiners and variation selectors stay intact.
def keep: {id, url, login: .author.login, body: (.body | gsub("[\\x{E0000}-\\x{E007F}]+"; ""))};

# fetch-reviews runs this twice. The first run ($phase "bots") lists the
# slugs of the bots that need an app lookup: those typed Bot and not on the
# list, each once. Only a slug-shaped login goes out, since it becomes a URL
# path. The second run judges, with $apps filled in.
def unlisted_bots: [(nodes($reviews; .reviews)[], nodes($comments; .comments)[],
    (nodes($threads; .reviewThreads)[] | .comments.nodes[]))
  | select(.author.__typename == "Bot" and (.author.databaseId | IN(bots[]) | not))
  | .author.login // "" | slug | select(test("\\A[a-z0-9][a-z0-9_.-]*\\z"))] | unique;

if $phase == "bots" then unlisted_bots[] else
repo as $repo
| $repo.pullRequest as $pr
| if in_company and ($repo.owner.viewerIsAMember | not) then
    {refused: "this token can't see the org's members, so every member would read as an outsider"}
  elif ($pr | member | not) then
    {refused: "the PR's author is outside the company, so its diff is outside text too", url: $pr.url}
  elif ($pr.headRepositoryOwner.login // "") | IN($repo.owner.login, $pr.author.login) | not then
    {refused: "the PR's head is in a fork its author doesn't own, so its diff is outside text too", url: $pr.url}
  # The label catches a lane that forgot it. It can't stop an agent steered by
  # what it read: that agent decides whether to apply it.
  elif $pr.labels.pageInfo.hasNextPage != false then
    {refused: "the PR has more labels than one page holds, so outside-text can't be ruled out", url: $pr.url}
  elif any($pr.labels.nodes[]; .name | ascii_downcase == "outside-text") then
    {refused: "the PR is labeled outside-text: it was written from outside text", url: $pr.url}
  else
    nodes($reviews; .reviews) as $reviews
    | nodes($comments; .comments) as $comments
    # A thread comes through only when all of it was fetched and every outside
    # comment in it has been cleared. A cleared outside comment is dropped.
    # Whole only on an explicit false: a missing pageInfo withholds.
    | [nodes($threads; .reviewThreads)[] | . + {whole: (.comments.pageInfo.hasNextPage == false)}
        | . + {readable: (.whole and (any(.comments.nodes[]; pending) | not))}] as $threads
    | {
        url: $pr.url,
        head: $pr.headRefOid,
        mergeable: $pr.mergeable,
        # Reviewers asked for but not yet reported, Copilot's pass in progress included.
        requested: [$pr.reviewRequests.nodes[].requestedReviewer | .login // .slug],
        requested_complete: ($pr.reviewRequests.pageInfo.hasNextPage == false),
        reviews: [$reviews[] | select(inside) | keep + {state, commit: .commit.oid}],
        comments: [$comments[] | select(inside) | keep],
        threads: [$threads[] | select((.isResolved | not) and .readable) | {id, isOutdated,
          # A cleared outside comment comes through as a link without its text.
          comments: [.comments.nodes[] | if inside then keep + {databaseId, path, line}
            else {id, url, databaseId, path, line, cleared: true} end]}],
        # Each outside item stays listed until a person clears it, resolved threads
        # included, so nothing outside merges unread.
        withheld: (
          # A thread not fetched whole is withheld whole, resolved or not, since
          # the comments past the first page are unseen.
          [$threads[] | select(if .isResolved then .whole | not else .readable | not end)
            | ([.comments.nodes[] | select(pending)] + .comments.nodes)[0] | link("thread")]
          + [$threads[] | select(.isResolved and .whole) | .comments.nodes[]
              | select(pending) | link("thread-comment")]
          + [$reviews[] | select(.body != "" and pending) | link("review")]
          + [$comments[] | select(pending) | link("comment")])
      }
  end
end

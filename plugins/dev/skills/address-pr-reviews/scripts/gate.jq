# Inside: on a repo in one of the company's GitHub orgs, a User the repo calls
# OWNER or MEMBER; on any other repo, only the token's own account, since an
# org or a personal repo outside the company has its own members. Bots are
# matched by type and database id, never by login: a User can't be typed Bot,
# and an id stays with its account when an app is renamed and its old name
# taken by another app.
# CONTRIBUTOR is outside: one merged PR earns it, backdated to everything that
# person ever wrote. Everything outside comes back as a link for a person,
# never as text.
def company: ["basecamp"];
def repo: $head[0].data.repository;
def viewer: $head[0].data.viewer.login;
def in_company: repo.owner.login | ascii_downcase | IN(company[]);
# Review bots are inside everywhere: what they post is their own reading of
# the diff.
def review_bots: [
  175728472, # copilot-pull-request-reviewer[bot]
  199175422, # chatgpt-codex-connector[bot]
  191113872, # cubic-dev-ai[bot]
  62310815,  # github-advanced-security[bot]
  206951365  # cursor[bot]
];
# The company's own automation is inside only on the company's repos, where
# what it posts is the company's code at work: github-actions[bot] there is the
# repo's own workflows, since a workflow's token reaches only its own repo, and
# the apps are ones the company owns. On another org's repo, github-actions is
# that org's workflows. A workflow or app that copies outside text (an issue's
# title, an outsider's comment) into what it posts launders that text, as a
# review bot quoting it would; that's the workflow's bug to fix, not one this
# list can see.
def company_bots: [
  41898282,  # github-actions[bot]
  49699333,  # dependabot[bot]
  338590030, # 37s-banyan[bot], the landing queue
  343686697, # 37s-kitchen-line[bot]
  343683623, # 37s-kitchen-expo[bot]
  339366568, # 37s-jazzhands[bot]
  339366640, # 37s-jazzhands-watch[bot]
  342982759, # 37s-otel-gateway[bot]
  338631523, # app-previews[bot]
  257101191, # cli-release-bot[bot]
  312645762  # 37signals-grafana-git-sync[bot]
];
# A company repo adds its own bots in .github/fetch-reviews-bots on its default
# branch, one database id a line, # starting a comment. It's read from the
# default branch, never the PR's head, so a PR can't add a bot to its own read;
# whoever can land on the default branch can already put text in front of the
# agent through a commit. Another org's file is that org's word, so it's ignored.
def repo_bots: if in_company then
    [(repo.bots.text // "") | split("\n")[] | sub("#.*"; "") | scan("[0-9]+") | tonumber]
  else [] end;
def bots: review_bots + (if in_company then company_bots else [] end) + repo_bots + $extra;
def person: .author == null or .author.__typename != "Bot";
def member: .author.__typename == "User"
  and ((in_company and (.authorAssociation | IN("OWNER", "MEMBER"))) or .author.login == viewer);
def bot: .author.__typename == "Bot" and (.author.databaseId | IN(bots[]));
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
def keep: {id, url, login: .author.login, by: (if person then "person" else "bot" end),
  body: (.body | gsub("[\\x{E0000}-\\x{E007F}]+"; ""))};

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
        viewer: viewer,
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

# Inside: on a repo in one of the company's GitHub orgs, a User the repo calls
# OWNER or MEMBER; on any other repo, only the token's own account, since an
# org or a personal repo outside the company has its own members. Review bots
# are matched by type and id everywhere (a User can't be typed Bot).
# CONTRIBUTOR is outside: one merged PR earns it, backdated to everything that
# person ever wrote. Everything outside comes back as a link for a person,
# never as text.
def company: ["basecamp"];
def repo: $head[0].data.repository;
def viewer: $head[0].data.viewer.login;
def in_company: repo.owner.login | ascii_downcase | IN(company[]);
def bots: [175728472, 199175422, 191113872, 62310815] + $extra; # Copilot, Codex, cubic, GitHub Advanced Security
def person: .author == null or .author.__typename != "Bot";
def member: .author.__typename == "User"
  and ((in_company and (.authorAssociation | IN("OWNER", "MEMBER"))) or .author.login == viewer);
def bot: .author.__typename == "Bot" and (.author.databaseId | IN(bots[]));
def inside: member or bot;
def shown: .isMinimized | not;
def nodes($pages; f): [$pages[0][].data.repository.pullRequest | f | .nodes[]];
def link($kind): {kind: $kind, url, by: (if person then "person" else "bot" end), association: .authorAssociation};
# A quote reply or an email reply puts someone else's words in a member's
# comment, so a member's quoted lines don't come through. Lines inside a code
# block the member opened and closed (a suggestion included) are code and come
# through as written; a quote reply's lines all start with ">", so it can't
# open or close one. A block left open gets no exemption, so a quote reply after
# it is still left out. Characters that render as nothing don't come through
# from anyone.
def quoted: test("^\\s*>");
def own:
  reduce (split("\n")[]) as $l ({out: [], fence: null, held: []};
    .fence as $f
    | if $f != null then
        if $l | test("^ {0,3}\($f[0:1]){\($f | length),}\\s*$") then
          .out += .held + [$l] | .held = [] | .fence = null
        else .held += [$l] end
      elif $l | quoted then .out += ["[quoted text omitted]"]
      else .out += [$l] | .fence = ($l | capture("^ {0,3}(?<f>~{3,}|`{3,}(?=[^`]*$))").f // null)
      end)
  | .out + [.held[] | if quoted then "[quoted text omitted]" else . end]
  | reduce .[] as $l ([]; if $l == "[quoted text omitted]" and .[-1] == $l then . else . + [$l] end)
  | join("\n");
def keep: {id, url, login: .author.login,
  body: (if member then .body | own else .body end | gsub("\\p{Default_Ignorable_Code_Point}"; ""))};

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
  elif any($pr.labels.nodes[]; .name == "outside-text") then
    {refused: "the PR is labeled outside-text: it was written from outside text", url: $pr.url}
  else
    nodes($reviews; .reviews) as $reviews
    | nodes($comments; .comments) as $comments
    | nodes($threads; .reviewThreads) as $threads
    # A thread comes through only when all of it was fetched and every comment in
    # it that is still shown is inside. An outside comment a person hid is dropped.
    | [$threads[] | select(.isResolved | not)
        | . + {readable: ((.comments.pageInfo.hasNextPage | not)
            and all(.comments.nodes[]; inside or (shown | not)))}] as $open
    | {
        url: $pr.url,
        head: $pr.headRefOid,
        mergeable: $pr.mergeable,
        reviews: [$reviews[] | select(inside and .body != "") | keep + {state, commit: .commit.oid}],
        comments: [$comments[] | select(inside) | keep],
        threads: [$open[] | select(.readable) | {id, isOutdated,
          comments: [.comments.nodes[] | select(inside) | keep + {databaseId, path, line}]}],
        # Each outside item stays listed until a person hides it, resolved threads
        # included, so nothing outside merges unread.
        withheld: (
          [$open[] | select(.readable | not)
            | ([.comments.nodes[] | select(shown and (inside | not))] + .comments.nodes)[0] | link("thread")]
          + [$threads[] | select(.isResolved) | .comments.nodes[]
              | select(shown and (inside | not)) | link("thread-comment")]
          + [$reviews[] | select(.body != "" and shown and (inside | not)) | link("review")]
          # Bots off the list aren't reviewers; their PR comments are skipped.
          + [$comments[] | select(person and shown and (inside | not)) | link("comment")])
      }
  end

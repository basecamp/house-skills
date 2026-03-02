---
name: address-pr-reviews
description: Address PR review comments - fix issues, reply to threads, mark resolved
version: 1.1.0
triggers:
  # Direct invocations
  - address pr reviews
  - address pr comments
  - address reviews
  - /address-pr-reviews
  # Action phrases
  - fix pr comments
  - fix review comments
  - handle pr feedback
  - process pr reviews
  - resolve pr threads
  - resolve review threads
  - respond to pr reviews
  - respond to review comments
  # Question patterns
  - what did reviewers say
  - any pr feedback
  - pending review comments
---

# PR Review Comment Processing

When asked to address/process/handle PR review comments, do the following:

## 1. Fetch Reviews and Threads

Fetch both top-level reviews (which may have feedback only in the review body)
and inline review threads in a single query:

```bash
gh api graphql -f query='
query {
  repository(owner: "OWNER", name: "REPO") {
    pullRequest(number: PR_NUMBER) {
      reviews(first: 50) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          state
          body
          author { login }
          comments(first: 50) {
            pageInfo { hasNextPage endCursor }
            nodes { body path line }
          }
        }
      }
      reviewThreads(first: 50) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          isResolved
          comments(last: 50) {
            pageInfo { hasPreviousPage startCursor }
            nodes { body path line author { login } }
          }
        }
      }
    }
  }
}'
```

## 2. Process Top-Level Reviews

Reviews may contain actionable feedback in their `body` with no inline thread
comments (e.g. bot reviews from Codex, Copilot, etc.). For each review with a
non-empty body and `state` of CHANGES_REQUESTED or COMMENTED:

### Fix the issue
Address the substance of the review body in code.

### Reply as a PR comment
Top-level review bodies don't have a thread to reply to. Use a PR comment:
```bash
gh pr comment PR_NUMBER --body "Fixed — [brief explanation of what was done]"
```

## 3. Process Unresolved Threads

For each unresolved review thread:

### Fix the issue
Address the substance of the comment in code.

### Reply to the thread
```bash
gh api graphql -f query='
mutation {
  addPullRequestReviewThreadReply(input: {
    pullRequestReviewThreadId: "THREAD_ID",
    body: "Fixed — [brief explanation of what was done]"
  }) {
    comment { id }
  }
}'
```

### Resolve the thread
```bash
gh api graphql -f query='
mutation {
  resolveReviewThread(input: {threadId: "THREAD_ID"}) {
    thread { isResolved }
  }
}'
```

## Key Points

- Fetch both `reviews` and `reviewThreads` — feedback may be in either place
- For top-level review bodies (no thread), reply with `gh pr comment`
- For inline threads, reply to the thread directly, then resolve
- Keep replies concise: "Fixed — [what changed]"
- Batch parallel mutations when possible
- If `pageInfo.hasNextPage` is true, paginate with `after: "endCursor"` to fetch all reviews/threads

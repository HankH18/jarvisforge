# T-4 GitHub fixtures

Pinned snapshot: **`octocat/Hello-World`** at commit
**`7fd1a60b01f91b314f59955a4e4d4e80d8edf11d`** (branch `master`).

All fixtures in this directory are REAL recorded payloads (not
hand-written), captured with an authenticated `gh api graphql` (GitHub CLI)
against the live GitHub GraphQL API. No test in `tests/tools/test_github.py`
makes a live call -- respx intercepts every `github_query` HTTP call, and
`gitingest.ingest` is monkeypatched directly for the digest tool (see
`jarvis.tools.github.digest`'s module docstring for why gitingest can't be
intercepted by respx: it shells out to a real `git` binary, not httpx).

## Files

- `repo_meta.json` -- `REPO_META_QUERY` response (also what
  `load_repo_digest` uses internally to resolve the current head sha for
  its cache key).
- `open_prs.json` -- `OPEN_PRS_QUERY` response, `first: 5`.
- `open_issues.json` -- `OPEN_ISSUES_QUERY` response, `first: 5`.
- `last_merges.json` -- `LAST_MERGES_QUERY` response, `first: 5`. (This repo
  has exactly one merged PR, #6, ever.)
- `pr_comments.json` -- `PR_COMMENTS_QUERY` response for PR #6, `first: 5`.
- `repo_not_found.json` -- the `NOT_FOUND` GraphQL error GitHub returns for
  *both* a nonexistent repo and a private repo the token can't see (GitHub
  intentionally makes these indistinguishable so an unauthorized caller
  can't probe for private-repo existence) -- recorded against
  `octocat/this-repo-should-not-exist-zzz-999`.
- `digest_hello_world.json` -- `{summary, tree, content}` recorded from one
  real, live `gitingest.ingest("https://github.com/octocat/Hello-World",
  branch="master")` call. `gitingest` resolved the same commit sha recorded
  above, confirming both fixtures describe the same snapshot.

## Re-recording

```
gh api graphql -f query='<QUERY from jarvis.tools.github.queries>' \
  -F owner=octocat -F name=Hello-World [-F first=5] [-F number=6] \
  > <file>.json
```

```python
from gitingest import ingest
summary, tree, content = ingest("https://github.com/octocat/Hello-World", branch="master")
```

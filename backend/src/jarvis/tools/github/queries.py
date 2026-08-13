"""GraphQL query text for github_query's five kinds, plus the repo-metadata
query load_repo_digest reuses to resolve the current head sha for its cache
key. Field selections here are pinned 1:1 to the recorded fixtures under
backend/tests/tools/fixtures/github/ (see that directory's docstring/README
for the exact `gh api graphql` invocations used to record them) -- changing
a selection here means re-recording the matching fixture.
"""

from __future__ import annotations

REPO_META_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    name
    nameWithOwner
    description
    isPrivate
    stargazerCount
    forkCount
    primaryLanguage { name }
    defaultBranchRef {
      name
      target { oid }
    }
  }
}
"""

OPEN_PRS_QUERY = """
query($owner: String!, $name: String!, $first: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: OPEN, first: $first, orderBy: {field: CREATED_AT, direction: DESC}) {
      totalCount
      nodes {
        number
        title
        url
        state
        createdAt
        author { login }
      }
    }
  }
}
"""

OPEN_ISSUES_QUERY = """
query($owner: String!, $name: String!, $first: Int!) {
  repository(owner: $owner, name: $name) {
    issues(states: OPEN, first: $first, orderBy: {field: CREATED_AT, direction: DESC}) {
      totalCount
      nodes {
        number
        title
        url
        state
        createdAt
        author { login }
      }
    }
  }
}
"""

LAST_MERGES_QUERY = """
query($owner: String!, $name: String!, $first: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequests(states: MERGED, first: $first, orderBy: {field: UPDATED_AT, direction: DESC}) {
      totalCount
      nodes {
        number
        title
        url
        mergedAt
        author { login }
        mergeCommit { oid }
      }
    }
  }
}
"""

PR_COMMENTS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $first: Int!) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      number
      title
      url
      comments(first: $first) {
        totalCount
        nodes {
          author { login }
          body
          createdAt
          url
        }
      }
    }
  }
}
"""

"""Shared githubkit GraphQL client construction + a normalized error
boundary for T-4's two tools.

``GITHUB_TOKEN`` is read from the environment at call time (never
hardcoded, never required by tests -- respx intercepts the HTTP call before
any real network round-trip happens, so an unset token is fine offline).

``http_cache`` and ``auto_retry`` are disabled on purpose:
- ``http_cache=False`` avoids githubkit routing requests through its
  hishel-backed caching transport, which would sit between respx and the
  request; this ticket's own cache (``DigestCache``, see cache.py) is the
  cache load_repo_digest is required to implement, so a second implicit
  HTTP-level cache would only add confusion.
- ``auto_retry=False`` avoids githubkit's built-in retry-with-sleep for 5xx
  responses (up to 3 retries, 1s/4s/9s backoff by default) -- that would
  make error-path tests slow and nondeterministic in call count.
"""

from __future__ import annotations

import os

import githubkit
import httpx
from githubkit.exception import GitHubException, GraphQLFailed

GRAPHQL_ENDPOINT = "https://api.github.com/graphql"


class GitHubAPIError(RuntimeError):
    """Normalized error for any GraphQL/network failure talking to GitHub.

    Both tools catch only this (see ``execute_graphql`` below) so callers
    never need to know githubkit's exception hierarchy.
    """


def build_client() -> githubkit.GitHub:
    """A fresh client per call, reading ``GITHUB_TOKEN`` from env."""
    token = os.environ.get("GITHUB_TOKEN") or None
    return githubkit.GitHub(token, http_cache=False, auto_retry=False)


def execute_graphql(client: githubkit.GitHub, query: str, variables: dict) -> dict:
    """Run one GraphQL query, translating any failure into GitHubAPIError.

    Covers: GraphQL-level errors (e.g. NOT_FOUND for a nonexistent/private
    repo -- githubkit raises ``GraphQLFailed`` even though the HTTP status
    is 200, since GitHub returns 200 with an ``errors`` array rather than a
    4xx for that case), non-2xx HTTP responses, and network errors
    (timeouts, connection failures).

    A NOT_FOUND GraphQL error gets a friendlier, explicit message: GitHub
    intentionally returns the identical NOT_FOUND error for a nonexistent
    repo and for a private repo the caller's token can't see (so an
    unauthorized caller can't use the error to probe for private-repo
    existence) -- this surfaces that ambiguity honestly rather than
    asserting either specifically.
    """
    try:
        return client.graphql.request(query, variables)
    except GraphQLFailed as exc:
        messages = [e.message for e in (exc.response.errors or [])]
        types = {e.type for e in (exc.response.errors or [])}
        if "NOT_FOUND" in types:
            raise GitHubAPIError(
                "repository not found or not accessible "
                f"(nonexistent, or private and not visible to this token): {'; '.join(messages)}"
            ) from exc
        raise GitHubAPIError(f"GitHub GraphQL error: {'; '.join(messages) or exc!r}") from exc
    except GitHubException as exc:
        raise GitHubAPIError(f"GitHub API error: {exc!r}") from exc
    except httpx.HTTPError as exc:
        raise GitHubAPIError(f"GitHub API request failed: {exc!r}") from exc

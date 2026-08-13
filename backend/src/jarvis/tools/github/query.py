"""github_query() (SPEC R7; docs/DESIGN.md §Interfaces): five read-only
GraphQL lookups against a public GitHub repo via githubkit. This is the
targeted-retrieval half of T-4 that load_repo_digest degrades to when a
digest exceeds token_budget (see digest.py).

NON-GOAL (this ticket): no write actions on repos anywhere in this module --
PR-opening is T-11. Every query below is a read.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from jarvis.llm.types import ToolResult

from ..registry import ToolSpec
from .client import GitHubAPIError, build_client, execute_graphql
from .queries import (
    LAST_MERGES_QUERY,
    OPEN_ISSUES_QUERY,
    OPEN_PRS_QUERY,
    PR_COMMENTS_QUERY,
    REPO_META_QUERY,
)
from .urls import parse_github_url

Kind = Literal["open_prs", "open_issues", "pr_comments", "last_merges", "repo_meta"]

_SOURCE = "github:graphql"
_DEFAULT_FIRST = 10


def github_query(url: str, kind: Kind, args: dict) -> ToolResult:
    """Run one targeted, read-only GraphQL query against the repo at
    ``url``. Any failure (bad URL, repo not found/private/inaccessible,
    network/API error) returns a ``ToolResult`` with ``error`` set and empty
    ``data`` -- never a guess (SPEC R6) -- which feeds
    ``synthesize_grounded``'s structural no-evidence gate.
    """
    fetched_at = datetime.now(UTC).isoformat()
    parsed = parse_github_url(url)
    if parsed is None:
        return ToolResult(
            source=_SOURCE,
            fetched_at=fetched_at,
            data={},
            error=f"not a github.com repo URL: {url!r}",
        )
    owner, repo = parsed

    try:
        client = build_client()
        response = _run(client, kind, owner, repo, args or {})
    except GitHubAPIError as exc:
        return ToolResult(source=_SOURCE, fetched_at=fetched_at, data={}, error=str(exc))

    repository = response.get("repository")
    if repository is None:
        return ToolResult(
            source=_SOURCE,
            fetched_at=fetched_at,
            data={},
            error=f"repository {owner}/{repo} not found or not accessible",
        )

    # `fetched_at` is copied into `data` too: synthesize_grounded's prompt
    # builder forwards each result's source/data only, dropping the
    # top-level ToolResult.fetched_at (see llm/synthesis.py, out of scope
    # for this ticket -- same carry-over as T-3's weather tool).
    data: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "kind": kind,
        "fetched_at": fetched_at,
        **repository,
    }
    return ToolResult(source=_SOURCE, fetched_at=fetched_at, data=data, error=None)


def _run(client: Any, kind: Kind, owner: str, repo: str, args: dict) -> dict:
    first = int(args.get("first", _DEFAULT_FIRST))
    if kind == "repo_meta":
        return execute_graphql(client, REPO_META_QUERY, {"owner": owner, "name": repo})
    if kind == "open_prs":
        return execute_graphql(
            client, OPEN_PRS_QUERY, {"owner": owner, "name": repo, "first": first}
        )
    if kind == "open_issues":
        return execute_graphql(
            client, OPEN_ISSUES_QUERY, {"owner": owner, "name": repo, "first": first}
        )
    if kind == "last_merges":
        return execute_graphql(
            client, LAST_MERGES_QUERY, {"owner": owner, "name": repo, "first": first}
        )
    if kind == "pr_comments":
        number = args.get("number")
        if number is None:
            raise GitHubAPIError("pr_comments requires args['number']")
        return execute_graphql(
            client,
            PR_COMMENTS_QUERY,
            {"owner": owner, "name": repo, "number": int(number), "first": first},
        )
    raise GitHubAPIError(f"unknown github_query kind: {kind!r}")


# Heavy per DESIGN.md §Interfaces: firing this tool escalates the turn's
# reasoning+answer step to $TOOL_MODEL (see llm/routing.route_model).
GITHUB_QUERY_TOOL = ToolSpec(
    name="github_query",
    description=(
        "Run a targeted, read-only GitHub GraphQL query against a public "
        "repository. `kind` selects what to fetch: open_prs, open_issues, "
        "pr_comments (needs args.number), last_merges, or repo_meta."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "https://github.com/<owner>/<repo> URL.",
            },
            "kind": {
                "type": "string",
                "enum": ["open_prs", "open_issues", "pr_comments", "last_merges", "repo_meta"],
            },
            "args": {
                "type": "object",
                "description": (
                    "kind-specific arguments, e.g. {'number': 6} for "
                    "pr_comments, {'first': 10} to change the page size."
                ),
            },
        },
        "required": ["url", "kind", "args"],
    },
    handler=lambda **kwargs: github_query(**kwargs),
    heavy=True,
)

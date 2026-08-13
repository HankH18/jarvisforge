"""load_repo_digest() (SPEC R7; docs/DESIGN.md §Interfaces): a condensed
full-repo digest via gitingest, cached by ``(repo, head_sha)`` with a
15-minute TTL.

DESIGN.md §Decisions is explicit: "Oversize repos degrade to `github_query`
-- never build an embedding index for R7." So when the digest's estimated
token count exceeds ``token_budget``, this function does NOT return any
slice of the digest (no summary/tree/content at all) -- it returns a
``ToolResult`` whose ``data`` describes the degradation and points at
``github_query`` for targeted retrieval instead. That is the only
degradation path; there is no partial-content fallback anywhere in this
module.

gitingest's ``ingest()`` shells out to a real ``git`` binary (clone) rather
than making httpx calls, so respx cannot intercept it the way it intercepts
the GraphQL head-sha lookup below. Tests instead monkeypatch
``jarvis.tools.github.digest.ingest`` directly with a fake that returns a
recorded ``(summary, tree, content)`` tuple -- still fully offline,
deterministic, and fast; see backend/tests/tools/test_github.py and
backend/tests/tools/fixtures/github/digest_hello_world.json (recorded via
one real, live ``gitingest.ingest()`` call against the pinned repo/sha --
see that fixture directory's README for exactly how).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from gitingest import ingest

from jarvis.llm.types import ToolResult

from ..registry import ToolSpec
from .cache import DigestCache
from .client import GitHubAPIError, build_client, execute_graphql
from .queries import REPO_META_QUERY
from .urls import parse_github_url

_SOURCE = "github:gitingest"
_TTL_SECONDS = 15 * 60

# gitingest doesn't report a token count for the raw digest it hands back
# from ingest() (only the CLI's own printed summary does, informally). This
# ticket's file scope excludes backend/pyproject.toml, so pulling in a real
# tokenizer dependency is not an option -- 4 chars/token is the standard
# ballpark estimate for English/code text and is good enough to gate the
# degrade decision (SPEC R7 cares about "never answer from a truncated
# slice", not about the estimate's precision).
_CHARS_PER_TOKEN = 4

# Module-level so load_repo_digest's signature stays pinned exactly
# (url, token_budget) -- no cache/clock parameter. Tests inject a fake clock
# by constructing their own DigestCache and monkeypatching this name (see
# test_github.py's TTL tests).
_DIGEST_CACHE: DigestCache[tuple[str, str, str]] = DigestCache(ttl_seconds=_TTL_SECONDS)


def load_repo_digest(url: str, token_budget: int = 150_000) -> ToolResult:
    """Resolve ``url`` to a repo, fetch (or reuse a cached) gitingest digest
    of its default branch, and return it -- unless the digest exceeds
    ``token_budget``, in which case this degrades to a targeted-query
    pointer instead of ever returning truncated content.
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
    repo_id = f"{owner}/{repo}"

    try:
        client = build_client()
        meta = execute_graphql(client, REPO_META_QUERY, {"owner": owner, "name": repo})
    except GitHubAPIError as exc:
        return ToolResult(source=_SOURCE, fetched_at=fetched_at, data={}, error=str(exc))

    repository = meta.get("repository")
    if repository is None:
        return ToolResult(
            source=_SOURCE,
            fetched_at=fetched_at,
            data={},
            error=f"repository {repo_id} not found or not accessible",
        )
    default_branch_ref = repository.get("defaultBranchRef")
    if not default_branch_ref or not default_branch_ref.get("target"):
        return ToolResult(
            source=_SOURCE,
            fetched_at=fetched_at,
            data={},
            error=f"repository {repo_id} has no default branch to ingest",
        )
    head_sha = default_branch_ref["target"]["oid"]
    branch = default_branch_ref["name"]

    cached = _DIGEST_CACHE.get(repo_id, head_sha)
    if cached is not None:
        summary, tree, content = cached
    else:
        summary, tree, content = ingest(url, branch=branch)
        _DIGEST_CACHE.set(repo_id, head_sha, (summary, tree, content))

    estimated_tokens = len(content) // _CHARS_PER_TOKEN
    if estimated_tokens > token_budget:
        return _degrade(owner, repo, head_sha, fetched_at, token_budget, estimated_tokens)

    data: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "head_sha": head_sha,
        "branch": branch,
        "summary": summary,
        "tree": tree,
        "content": content,
        "token_budget": token_budget,
        "estimated_tokens": estimated_tokens,
        # Copied into `data` for the same reason as T-3's weather tool:
        # synthesize_grounded's prompt builder forwards each result's
        # source/data only, dropping the top-level ToolResult.fetched_at
        # (llm/synthesis.py, out of scope for this ticket).
        "fetched_at": fetched_at,
    }
    return ToolResult(source=_SOURCE, fetched_at=fetched_at, data=data, error=None)


def _degrade(
    owner: str,
    repo: str,
    head_sha: str,
    fetched_at: str,
    token_budget: int,
    estimated_tokens: int,
) -> ToolResult:
    """SPEC R7 targeted-query degradation. ``data`` deliberately omits
    summary/tree/content entirely -- no partial digest of any kind -- so
    nothing here could be mistaken for (or answered from as if it were) the
    full repo. It names ``github_query`` and the kinds it supports so the
    caller/model has a concrete next step instead of a dead end.
    """
    data: dict[str, Any] = {
        "owner": owner,
        "repo": repo,
        "head_sha": head_sha,
        "degraded": True,
        "reason": (
            f"digest is ~{estimated_tokens} estimated tokens, which exceeds "
            f"token_budget {token_budget} -- refusing to answer from a "
            "truncated slice (SPEC R7)"
        ),
        "next_tool": "github_query",
        "next_tool_kinds": ["repo_meta", "open_prs", "open_issues", "last_merges", "pr_comments"],
        "fetched_at": fetched_at,
    }
    return ToolResult(source=_SOURCE, fetched_at=fetched_at, data=data, error=None)


# Heavy per DESIGN.md §Interfaces: firing this tool escalates the turn's
# reasoning+answer step to $TOOL_MODEL (see llm/routing.route_model).
GITHUB_DIGEST_TOOL = ToolSpec(
    name="load_repo_digest",
    description=(
        "Load a condensed digest (summary, file tree, file contents) of a "
        "public GitHub repository's default branch via gitingest, cached "
        "for 15 minutes per commit. If the repo is too large for the token "
        "budget, returns a degraded result pointing at github_query for "
        "targeted retrieval instead of a truncated digest."
    ),
    parameters={
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "https://github.com/<owner>/<repo> URL.",
            },
            "token_budget": {
                "type": "integer",
                "description": (
                    "Max estimated tokens before degrading to targeted "
                    "queries (default 150000)."
                ),
            },
        },
        "required": ["url"],
    },
    handler=lambda **kwargs: load_repo_digest(**kwargs),
    heavy=True,
)

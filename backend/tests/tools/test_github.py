"""load_repo_digest() / github_query() (SPEC R7; docs/DESIGN.md
§Interfaces): gitingest whole-repo digests and githubkit GraphQL lookups
against a public GitHub repo, both returning ``ToolResult``.

Pinned snapshot: octocat/Hello-World @ 7fd1a60b01f91b314f59955a4e4d4e80d8edf11d
(branch master). See fixtures/github/README.md for exactly how each fixture
was recorded (all real payloads, no hand-written GraphQL shapes).

github_query's GraphQL calls are intercepted by respx (real HTTP layer).
load_repo_digest's gitingest call is NOT httpx-based -- gitingest shells out
to a real `git` binary -- so it is instead intercepted by monkeypatching
``jarvis.tools.github.digest.ingest`` with a fake returning a recorded
``(summary, tree, content)`` tuple. Both are equally offline/deterministic;
no test in this file makes a live call.

Four acceptance items, each proven by dedicated tests (see section headers):
  1. pinned ToolResult shape for both tools, from recorded fixtures, plus
     the heavy=True routing consequence via the REAL route_model.
  2. oversize digest -> targeted-query degradation, never truncated content
     (proven on the actual returned data, not a flag).
  3. digest cache hit/miss (unchanged head_sha, changed head_sha, expired
     TTL), clock injected -- no sleeping.
  4. invalid/private/nonexistent URL -> ToolResult.error -> REFUSAL,
     end-to-end through the REAL synthesize_grounded.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import respx

import jarvis.tools.github.digest as digest_module
from jarvis.llm.routing import route_model
from jarvis.llm.synthesis import synthesize_grounded
from jarvis.llm.types import REFUSAL
from jarvis.tools.github import (
    GITHUB_DIGEST_TOOL,
    GITHUB_QUERY_TOOL,
    github_query,
    load_repo_digest,
)
from jarvis.tools.github.cache import DigestCache
from jarvis.tools.github.client import GRAPHQL_ENDPOINT
from jarvis.tools.registry import ToolRegistry, ToolSpec

FIXTURES = Path(__file__).parent / "fixtures" / "github"
REPO_URL = "https://github.com/octocat/Hello-World"
HEAD_SHA = "7fd1a60b01f91b314f59955a4e4d4e80d8edf11d"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _digest_fixture() -> tuple[str, str, str]:
    payload = _fixture("digest_hello_world.json")
    return payload["summary"], payload["tree"], payload["content"]


def _mock_graphql(fixture_name: str) -> None:
    respx.post(GRAPHQL_ENDPOINT).mock(return_value=httpx.Response(200, json=_fixture(fixture_name)))


class ExplodingModelClient:
    """Raises if ever called -- proves a code path never reaches the model.

    Self-contained here (not imported from tests/grounding/fakes.py) since
    this ticket's file scope is backend/tests/tools/** only (same pattern as
    T-3's test_weather.py).
    """

    def complete(self, *, model: str, system: str, user: str) -> str:
        raise AssertionError("model client should not be called on this path")


class RecordingModelClient:
    """Returns a canned reply and records exactly what it was asked."""

    def __init__(self, *, reply: str) -> None:
        self.reply = reply
        self.calls = 0
        self.last_user: str | None = None

    def complete(self, *, model: str, system: str, user: str) -> str:
        self.calls += 1
        self.last_user = user
        return self.reply


def _fresh_digest_cache(monkeypatch, *, clock=None) -> DigestCache:
    """Swap in a fresh, isolated DigestCache (optionally clock-injected) so
    tests don't leak cache state into each other via the module-level
    default instance.
    """
    cache = DigestCache(
        ttl_seconds=digest_module._TTL_SECONDS, clock=clock or (lambda: datetime.now(UTC))
    )
    monkeypatch.setattr(digest_module, "_DIGEST_CACHE", cache)
    return cache


# ---------------------------------------------------------------------------
# 1. Pinned ToolResult shape for both tools, from recorded fixtures, plus
#    the heavy=True -> $TOOL_MODEL routing consequence.
# ---------------------------------------------------------------------------


@respx.mock
def test_load_repo_digest_returns_pinned_shape_from_recorded_fixture(monkeypatch):
    _fresh_digest_cache(monkeypatch)
    _mock_graphql("repo_meta.json")
    monkeypatch.setattr(digest_module, "ingest", lambda *a, **k: _digest_fixture())

    result = load_repo_digest(REPO_URL)

    assert result["source"] == "github:gitingest"
    assert result["error"] is None
    assert result["data"]["owner"] == "octocat"
    assert result["data"]["repo"] == "Hello-World"
    assert result["data"]["head_sha"] == HEAD_SHA
    summary, tree, content = _digest_fixture()
    assert result["data"]["summary"] == summary
    assert result["data"]["tree"] == tree
    assert result["data"]["content"] == content
    assert result["data"]["fetched_at"] == result["fetched_at"]


@respx.mock
def test_github_query_repo_meta_returns_pinned_shape_from_recorded_fixture():
    _mock_graphql("repo_meta.json")

    result = github_query(REPO_URL, "repo_meta", {})

    assert result["source"] == "github:graphql"
    assert result["error"] is None
    fixture_repo = _fixture("repo_meta.json")["data"]["repository"]
    assert result["data"]["nameWithOwner"] == fixture_repo["nameWithOwner"]
    assert result["data"]["defaultBranchRef"]["target"]["oid"] == HEAD_SHA


@respx.mock
def test_github_query_open_prs_returns_pinned_shape_from_recorded_fixture():
    _mock_graphql("open_prs.json")

    result = github_query(REPO_URL, "open_prs", {"first": 5})

    assert result["error"] is None
    fixture_prs = _fixture("open_prs.json")["data"]["repository"]["pullRequests"]
    assert result["data"]["pullRequests"] == fixture_prs
    assert result["data"]["kind"] == "open_prs"


@respx.mock
def test_github_query_open_issues_returns_pinned_shape_from_recorded_fixture():
    _mock_graphql("open_issues.json")

    result = github_query(REPO_URL, "open_issues", {"first": 5})

    assert result["error"] is None
    fixture_issues = _fixture("open_issues.json")["data"]["repository"]["issues"]
    assert result["data"]["issues"] == fixture_issues


@respx.mock
def test_github_query_last_merges_returns_pinned_shape_from_recorded_fixture():
    _mock_graphql("last_merges.json")

    result = github_query(REPO_URL, "last_merges", {"first": 5})

    assert result["error"] is None
    fixture_merges = _fixture("last_merges.json")["data"]["repository"]["pullRequests"]
    assert result["data"]["pullRequests"] == fixture_merges
    assert result["data"]["pullRequests"]["nodes"][0]["number"] == 6


@respx.mock
def test_github_query_pr_comments_returns_pinned_shape_from_recorded_fixture():
    _mock_graphql("pr_comments.json")

    result = github_query(REPO_URL, "pr_comments", {"number": 6, "first": 5})

    assert result["error"] is None
    fixture_pr = _fixture("pr_comments.json")["data"]["repository"]["pullRequest"]
    assert result["data"]["pullRequest"] == fixture_pr


def test_github_query_missing_pr_number_yields_error():
    result = github_query(REPO_URL, "pr_comments", {})

    assert result["error"] is not None
    assert result["data"] == {}


def test_both_tools_match_the_pinned_signature_and_are_heavy():
    """docs/DESIGN.md §Interfaces: load_repo_digest(url, token_budget=150_000)
    and github_query(url, kind, args) -> ToolResult, both registered heavy
    (github_qa escalates to $TOOL_MODEL).
    """
    assert isinstance(GITHUB_DIGEST_TOOL, ToolSpec)
    assert GITHUB_DIGEST_TOOL.name == "load_repo_digest"
    assert GITHUB_DIGEST_TOOL.heavy is True

    assert isinstance(GITHUB_QUERY_TOOL, ToolSpec)
    assert GITHUB_QUERY_TOOL.name == "github_query"
    assert GITHUB_QUERY_TOOL.heavy is True


def test_heavy_github_tools_escalate_real_model_routing(monkeypatch):
    """The routing consequence, proven via the REAL jarvis.llm.routing.route_model
    (not a stub): registering and firing either GitHub tool for a turn must
    escalate that turn to $TOOL_MODEL.
    """
    monkeypatch.setenv("CONVO_MODEL", "stub-convo-model")
    monkeypatch.setenv("TOOL_MODEL", "stub-tool-model")

    registry = ToolRegistry()
    registry.register(GITHUB_DIGEST_TOOL)
    registry.register(GITHUB_QUERY_TOOL)
    assert registry.heavy_names() == {"load_repo_digest", "github_query"}

    assert route_model([]) == "stub-convo-model"
    assert route_model([registry.get("load_repo_digest")]) == "stub-tool-model"
    assert route_model([registry.get("github_query")]) == "stub-tool-model"


# ---------------------------------------------------------------------------
# 2. Oversize digest -> targeted-query degradation, never truncated content.
# ---------------------------------------------------------------------------


_HUGE_MARKER = "SECRET_UNIQUE_REPO_CONTENT_MARKER_"


@respx.mock
def test_oversize_digest_degrades_and_omits_all_digest_content(monkeypatch):
    """SPEC R7 (a)+(b): an oversize digest must never surface as (or be
    reconstructible from) a truncated slice, and must point at github_query
    instead. Both halves are asserted on the actual returned ``data``.
    """
    _fresh_digest_cache(monkeypatch)
    _mock_graphql("repo_meta.json")
    huge_content = _HUGE_MARKER + ("x" * 4000)
    monkeypatch.setattr(
        digest_module, "ingest", lambda *a, **k: ("huge summary", "huge tree", huge_content)
    )

    result = load_repo_digest(REPO_URL, token_budget=10)

    # (b) no truncated slice anywhere in the payload -- not just missing keys.
    assert "content" not in result["data"]
    assert "tree" not in result["data"]
    assert "summary" not in result["data"]
    serialized = json.dumps(result["data"])
    assert _HUGE_MARKER not in serialized
    assert "huge summary" not in serialized
    assert "huge tree" not in serialized

    # (a) routes to targeted retrieval: the data names github_query and a kind.
    assert result["data"]["degraded"] is True
    assert result["data"]["next_tool"] == "github_query"
    assert "repo_meta" in result["data"]["next_tool_kinds"]
    assert result["data"]["head_sha"] == HEAD_SHA
    assert result["error"] is None  # not a refusal -- a redirect, truthfully grounded


@respx.mock
def test_oversize_digest_next_tool_actually_works_end_to_end(monkeypatch):
    """Proves (a) concretely: the exact next_tool/kind the degraded result
    names is not just a label -- calling it for real returns usable data.
    """
    _fresh_digest_cache(monkeypatch)
    _mock_graphql("repo_meta.json")
    huge_content = _HUGE_MARKER + ("y" * 4000)
    monkeypatch.setattr(digest_module, "ingest", lambda *a, **k: ("s", "t", huge_content))

    degraded = load_repo_digest(REPO_URL, token_budget=10)
    assert degraded["data"]["degraded"] is True

    followup = github_query(REPO_URL, "repo_meta", {})

    assert followup["error"] is None
    assert followup["data"]["nameWithOwner"] == "octocat/Hello-World"


@respx.mock
def test_oversize_digest_prompt_never_contains_truncated_content(monkeypatch):
    """End-to-end through the REAL synthesize_grounded: even when the
    degraded result clears the structural evidence gate and reaches the
    model prompt, that prompt contains no slice of the oversize digest.
    """
    _fresh_digest_cache(monkeypatch)
    _mock_graphql("repo_meta.json")
    huge_content = _HUGE_MARKER + ("z" * 4000)
    monkeypatch.setattr(digest_module, "ingest", lambda *a, **k: ("s", "t", huge_content))

    result = load_repo_digest(REPO_URL, token_budget=10)
    client = RecordingModelClient(reply=REFUSAL + ", the repo is too large to summarize directly.")

    answer = synthesize_grounded(
        "Summarize this whole repository.", [result], [], model="stub-model", client=client
    )

    assert client.calls == 1
    assert _HUGE_MARKER not in client.last_user
    assert answer.startswith(REFUSAL) or "github_query" in client.last_user


@respx.mock
def test_digest_within_budget_is_not_degraded(monkeypatch):
    """Sanity complement: the real (small) pinned fixture must NOT degrade
    at the pinned default token_budget.
    """
    _fresh_digest_cache(monkeypatch)
    _mock_graphql("repo_meta.json")
    monkeypatch.setattr(digest_module, "ingest", lambda *a, **k: _digest_fixture())

    result = load_repo_digest(REPO_URL)

    assert "degraded" not in result["data"]
    assert "content" in result["data"]


# ---------------------------------------------------------------------------
# 3. Digest cache: hit within TTL, miss on changed head_sha, miss on expired
#    TTL. Clock injected -- never sleeps.
# ---------------------------------------------------------------------------


def test_cache_hit_on_unchanged_key_within_ttl_skips_second_call():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache = DigestCache(ttl_seconds=900, clock=lambda: now)

    cache.set("octocat/Hello-World", HEAD_SHA, ("s", "t", "c"))
    now = now + timedelta(minutes=5)  # well within the 15-minute TTL

    assert cache.get("octocat/Hello-World", HEAD_SHA) == ("s", "t", "c")


def test_cache_miss_on_changed_head_sha():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache = DigestCache(ttl_seconds=900, clock=lambda: now)

    cache.set("octocat/Hello-World", HEAD_SHA, ("s", "t", "c"))

    assert cache.get("octocat/Hello-World", "a-different-sha") is None


def test_cache_miss_on_expired_ttl():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    cache = DigestCache(ttl_seconds=900, clock=lambda: now)

    cache.set("octocat/Hello-World", HEAD_SHA, ("s", "t", "c"))
    now = now + timedelta(minutes=15, seconds=1)  # just past the 15-minute TTL

    assert cache.get("octocat/Hello-World", HEAD_SHA) is None


@respx.mock
def test_load_repo_digest_cache_hit_on_unchanged_head_sha_skips_ingest(monkeypatch):
    """The production wiring, not just the cache class in isolation: two
    calls for the same (repo, head_sha) within TTL must invoke the
    (expensive, real-git-clone) ingest() call only once.
    """
    _fresh_digest_cache(monkeypatch)
    _mock_graphql("repo_meta.json")
    calls = {"n": 0}

    def fake_ingest(*args, **kwargs):
        calls["n"] += 1
        return _digest_fixture()

    monkeypatch.setattr(digest_module, "ingest", fake_ingest)

    first = load_repo_digest(REPO_URL)
    second = load_repo_digest(REPO_URL)

    assert calls["n"] == 1
    assert first["data"]["content"] == second["data"]["content"]


@respx.mock
def test_load_repo_digest_refetches_on_changed_head_sha(monkeypatch):
    _fresh_digest_cache(monkeypatch)
    old_meta = _fixture("repo_meta.json")
    new_meta = json.loads(json.dumps(old_meta))
    new_meta["data"]["repository"]["defaultBranchRef"]["target"]["oid"] = "a" * 40
    respx.post(GRAPHQL_ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=old_meta),
            httpx.Response(200, json=new_meta),
        ]
    )
    calls = {"n": 0}

    def fake_ingest(*args, **kwargs):
        calls["n"] += 1
        return _digest_fixture()

    monkeypatch.setattr(digest_module, "ingest", fake_ingest)

    first = load_repo_digest(REPO_URL)
    second = load_repo_digest(REPO_URL)

    assert calls["n"] == 2
    assert first["data"]["head_sha"] == HEAD_SHA
    assert second["data"]["head_sha"] == "a" * 40


@respx.mock
def test_load_repo_digest_refetches_after_ttl_expires(monkeypatch):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    holder = {"now": now}
    _fresh_digest_cache(monkeypatch, clock=lambda: holder["now"])
    _mock_graphql("repo_meta.json")
    calls = {"n": 0}

    def fake_ingest(*args, **kwargs):
        calls["n"] += 1
        return _digest_fixture()

    monkeypatch.setattr(digest_module, "ingest", fake_ingest)

    load_repo_digest(REPO_URL)
    holder["now"] = holder["now"] + timedelta(minutes=15, seconds=1)  # inject the clock; no sleep
    load_repo_digest(REPO_URL)

    assert calls["n"] == 2


# ---------------------------------------------------------------------------
# 4. Invalid / private / nonexistent URL -> ToolResult.error -> REFUSAL.
# ---------------------------------------------------------------------------


def test_non_github_url_yields_error_for_both_tools():
    digest_result = load_repo_digest("https://gitlab.com/octocat/Hello-World")
    query_result = github_query("https://gitlab.com/octocat/Hello-World", "repo_meta", {})

    for result in (digest_result, query_result):
        assert result["data"] == {}
        assert result["error"] is not None


def test_malformed_url_yields_error_for_both_tools():
    digest_result = load_repo_digest("not a url at all")
    query_result = github_query("not a url at all", "repo_meta", {})

    for result in (digest_result, query_result):
        assert result["data"] == {}
        assert result["error"] is not None


@respx.mock
def test_nonexistent_or_private_repo_yields_error_for_both_tools(monkeypatch):
    """GitHub's GraphQL API returns the identical NOT_FOUND shape for a
    nonexistent repo and a private repo the caller can't see (see
    fixtures/github/README.md) -- this fixture stands in for both cases.
    """
    _fresh_digest_cache(monkeypatch)
    _mock_graphql("repo_not_found.json")

    digest_result = load_repo_digest(REPO_URL)
    assert digest_result["data"] == {}
    assert digest_result["error"] is not None
    assert "not found" in digest_result["error"] and "not accessible" in digest_result["error"]


@respx.mock
def test_nonexistent_or_private_repo_yields_error_for_github_query():
    _mock_graphql("repo_not_found.json")

    result = github_query(REPO_URL, "repo_meta", {})

    assert result["data"] == {}
    assert result["error"] is not None


def test_invalid_url_feeds_the_refusal_path_not_a_guess():
    """Acceptance #4, end-to-end for load_repo_digest: an errored
    ToolResult, passed into the REAL synthesize_grounded, refuses via the
    structural no-evidence gate before ever touching a model client.
    """
    result = load_repo_digest("https://gitlab.com/not/github")

    answer = synthesize_grounded(
        "What's in this repo?", [result], [], client=ExplodingModelClient()
    )

    assert answer == REFUSAL


@respx.mock
def test_nonexistent_repo_feeds_the_refusal_path_not_a_guess(monkeypatch):
    _fresh_digest_cache(monkeypatch)
    _mock_graphql("repo_not_found.json")

    result = load_repo_digest(REPO_URL)
    answer = synthesize_grounded(
        "What's in this repo?", [result], [], client=ExplodingModelClient()
    )

    assert answer == REFUSAL


@respx.mock
def test_github_query_nonexistent_repo_feeds_the_refusal_path_not_a_guess():
    _mock_graphql("repo_not_found.json")

    result = github_query(REPO_URL, "open_prs", {})
    answer = synthesize_grounded(
        "What PRs are open?", [result], [], client=ExplodingModelClient()
    )

    assert answer == REFUSAL


@respx.mock
def test_graphql_5xx_yields_error_and_feeds_refusal_path():
    respx.post(GRAPHQL_ENDPOINT).mock(return_value=httpx.Response(503, text="unavailable"))

    result = github_query(REPO_URL, "repo_meta", {})
    assert result["data"] == {}
    assert result["error"] is not None

    answer = synthesize_grounded(
        "What's the repo about?", [result], [], client=ExplodingModelClient()
    )
    assert answer == REFUSAL


@respx.mock
def test_network_timeout_yields_error_and_feeds_refusal_path():
    respx.post(GRAPHQL_ENDPOINT).mock(side_effect=httpx.TimeoutException("connect timed out"))

    result = github_query(REPO_URL, "repo_meta", {})
    assert result["data"] == {}
    assert result["error"] is not None

    answer = synthesize_grounded(
        "What's the repo about?", [result], [], client=ExplodingModelClient()
    )
    assert answer == REFUSAL


# ---------------------------------------------------------------------------
# fetched_at (same carry-over as T-3's weather tool -- see module docstrings
# in digest.py/query.py for why it's copied into `data`).
# ---------------------------------------------------------------------------


@respx.mock
def test_github_query_fetched_at_reaches_the_model_prompt():
    _mock_graphql("repo_meta.json")
    result = github_query(REPO_URL, "repo_meta", {})
    client = RecordingModelClient(reply=f"As of {result['fetched_at']}, ...")

    answer = synthesize_grounded(
        "How current is this?", [result], [], model="stub-model", client=client
    )

    assert client.calls == 1
    assert result["fetched_at"] in client.last_user
    assert result["fetched_at"] in answer

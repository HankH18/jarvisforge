"""ClaudeCodeAgentRunner: translates claude_agent_sdk Message objects into
AgentEvent (T-11), and the PreToolUse merge/force-push guard it wires in
(R10 / acceptance #4).

``claude_agent_sdk.query`` itself is mocked here -- these tests exercise the
translation logic and the guard hook without spawning the real Claude Code
CLI subprocess. The one place the real SDK/CLI runs at all is the recorded
live run (acceptance #3, docs/agent-pr-demo.md).
"""

from __future__ import annotations

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from jarvis.agents_pr import claude_runner as claude_runner_module
from jarvis.agents_pr.claude_runner import ClaudeCodeAgentRunner, _enforce_bash_allow_list
from jarvis.agents_pr.events import AgentFailed, AgentProgress, AgentPROpened


def _fake_query(messages):
    async def query(*, prompt, options):
        for message in messages:
            yield message

    return query


async def _run(runner, tmp_path):
    return [
        event
        async for event in runner.run(
            repo_url="https://github.com/o/r", issue_number=1, workdir=tmp_path, github_token="t"
        )
    ]


# ---- message translation ---------------------------------------------------


@pytest.mark.asyncio
async def test_tool_use_blocks_become_progress_events(monkeypatch, tmp_path):
    messages = [
        AssistantMessage(
            content=[ToolUseBlock(id="1", name="Bash", input={"command": "git status"})],
            model="claude-sonnet-5",
        ),
    ]
    monkeypatch.setattr(claude_runner_module, "query", _fake_query(messages))

    events = await _run(ClaudeCodeAgentRunner(), tmp_path)

    assert events == [AgentProgress(detail="using Bash")]


@pytest.mark.asyncio
async def test_pr_url_in_assistant_text_becomes_pr_opened(monkeypatch, tmp_path):
    pr_url = "https://github.com/hankholcomb/some-test-repo/pull/7"
    messages = [
        AssistantMessage(
            content=[TextBlock(text=f"Opened the fix as {pr_url}")], model="claude-sonnet-5"
        ),
        ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=1,
            session_id="s",
        ),
    ]
    monkeypatch.setattr(claude_runner_module, "query", _fake_query(messages))

    events = await _run(ClaudeCodeAgentRunner(), tmp_path)

    assert AgentPROpened(pr_url=pr_url) in events
    # a ResultMessage that arrives after the PR was already opened must not
    # also report a failure
    assert not any(isinstance(e, AgentFailed) for e in events)


@pytest.mark.asyncio
async def test_pr_url_in_a_bash_tool_result_becomes_pr_opened(monkeypatch, tmp_path):
    pr_url = "https://github.com/hankholcomb/some-test-repo/pull/9"
    messages = [
        UserMessage(
            content=[ToolResultBlock(tool_use_id="1", content=f"created: {pr_url}\ndone")]
        ),
    ]
    monkeypatch.setattr(claude_runner_module, "query", _fake_query(messages))

    events = await _run(ClaudeCodeAgentRunner(), tmp_path)

    assert AgentPROpened(pr_url=pr_url) in events


@pytest.mark.asyncio
async def test_result_message_with_no_pr_opened_becomes_agent_failed(monkeypatch, tmp_path):
    messages = [
        ResultMessage(
            subtype="error_max_turns",
            duration_ms=1,
            duration_api_ms=1,
            is_error=True,
            num_turns=40,
            session_id="s",
            result="hit max turns without a fix",
        ),
    ]
    monkeypatch.setattr(claude_runner_module, "query", _fake_query(messages))

    events = await _run(ClaudeCodeAgentRunner(), tmp_path)

    assert events == [AgentFailed(error="hit max turns without a fix")]


# ---- PreToolUse guard (R10 / T-11 acceptance #4) ---------------------------
#
# guard.py's own test suite (test_guard.py) covers the allow-list's decision
# logic exhaustively -- the six bypasses an independent verifier found, the
# six the old deny-list already caught, and general allow/deny coverage.
# These tests only prove the hook *wires that decision through correctly*
# into the SDK's expected PreToolUse response shape.


@pytest.mark.asyncio
async def test_hook_denies_a_pr_merge_command_with_a_reason():
    result = await _enforce_bash_allow_list(
        {"tool_name": "Bash", "tool_input": {"command": "gh pr merge 42"}},
        "tool-1",
        {"signal": None},
    )
    output = result["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert output["permissionDecisionReason"]  # a human-readable reason is always included


@pytest.mark.asyncio
async def test_hook_denies_a_bypass_the_deny_list_used_to_miss():
    # gh "pr" merge -- quoting one token defeated the old regex deny-list;
    # the hook must still refuse it end to end.
    result = await _enforce_bash_allow_list(
        {"tool_name": "Bash", "tool_input": {"command": 'gh "pr" merge'}},
        "tool-1",
        {"signal": None},
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.asyncio
async def test_hook_allows_a_command_on_the_positive_allow_list():
    result = await _enforce_bash_allow_list(
        {"tool_name": "Bash", "tool_input": {"command": "gh pr create --title x --body y"}},
        "tool-1",
        {"signal": None},
    )
    assert result == {}


@pytest.mark.asyncio
async def test_hook_denies_a_missing_command_field():
    result = await _enforce_bash_allow_list(
        {"tool_name": "Bash", "tool_input": {}}, "tool-1", {"signal": None}
    )
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

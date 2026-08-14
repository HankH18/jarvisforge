"""Shared vocabulary for the agent-PR job (T-11): the job-state machine, the
events a coding-agent run reports as it works, and the ``AgentRunner``
boundary those events cross.

Pinned in docs/DESIGN.md §Interfaces::

    run_fix_job(repo_url, issue_number, user_id) -> job_id

State machine (docs/tickets.json T-11 acceptance #1)::

    started -> progress -> pr_opened | failed

``AgentRunner`` is the seam a scripted double crosses in the mocked-agent
unit tests (acceptance #1/#2) and the real Claude Agent SDK crosses in the
one recorded live run (acceptance #3) -- job.py's orchestration only ever
imports this Protocol, never ``claude_agent_sdk`` directly, so the state
machine is testable with no subprocess, no network, and no GITHUB_TOKEN.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

JobState = Literal["started", "progress", "pr_opened", "failed"]


@dataclass(frozen=True)
class AgentProgress:
    """The agent did something (a tool call, a plan step) but has not yet
    opened a PR or failed. Drives the "progress" transition."""

    detail: str = ""


@dataclass(frozen=True)
class AgentPROpened:
    """The agent opened a pull request. Terminal, success -- drives the
    "pr_opened" transition."""

    pr_url: str


@dataclass(frozen=True)
class AgentFailed:
    """The agent could not complete the fix. Terminal, failure -- drives the
    "failed" transition."""

    error: str


AgentEvent = AgentProgress | AgentPROpened | AgentFailed


class AgentRunner(Protocol):
    """Boundary the job orchestrator drives the coding agent through.

    Production (``claude_runner.ClaudeCodeAgentRunner``) wraps one headless
    Claude Agent SDK session in the isolated workdir prepared by
    ``workdir.py``; tests substitute a scripted async generator so
    ``job.execute_fix_job``'s state machine is exercised in full isolation.
    """

    def run(
        self, *, repo_url: str, issue_number: int, workdir: Path, github_token: str
    ) -> AsyncIterator[AgentEvent]:
        """Run the agent against one issue and yield progress/terminal
        events. Must yield exactly one terminal event (``AgentPROpened`` or
        ``AgentFailed``) before returning, or raise on an unrecoverable
        error -- the orchestrator treats a generator that exhausts with no
        terminal event as a failure rather than hanging in "progress"."""
        ...

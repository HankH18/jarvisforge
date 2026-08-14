"""Background issue -> PR job runner on the Claude Agent SDK (T-11).

Public surface, per docs/DESIGN.md §Interfaces::

    run_fix_job(repo_url, issue_number, user_id) -> job_id

``run_fix_job`` runs a headless Claude Agent SDK session in an isolated
workdir with a scoped GITHUB_TOKEN, announces state transitions via the
data channel (``agent_state: working``) and TTS, opens a pull request, and
never merges (R10). See ``job.py`` for the state machine
(``started -> progress -> pr_opened | failed``) and ``docs/agent-pr-demo.md``
for the one recorded live run (acceptance #3).
"""

from __future__ import annotations

from .announcer import Announcer, Speak
from .claude_runner import ClaudeCodeAgentRunner
from .events import (
    AgentEvent,
    AgentFailed,
    AgentProgress,
    AgentPROpened,
    AgentRunner,
    JobState,
)
from .guard import is_allowed_bash_command
from .job import (
    JobRecord,
    JobStore,
    default_job_store,
    execute_fix_job,
    run_fix_job,
)
from .repo_allowlist import RepoNotAllowedError, allowed_repo_urls, check_repo_allowed
from .workdir import WorkdirError, cleanup_workdir, prepare_workdir

__all__ = [
    "AgentEvent",
    "AgentFailed",
    "AgentPROpened",
    "AgentProgress",
    "AgentRunner",
    "Announcer",
    "ClaudeCodeAgentRunner",
    "JobRecord",
    "JobState",
    "JobStore",
    "RepoNotAllowedError",
    "Speak",
    "WorkdirError",
    "allowed_repo_urls",
    "check_repo_allowed",
    "cleanup_workdir",
    "default_job_store",
    "execute_fix_job",
    "is_allowed_bash_command",
    "prepare_workdir",
    "run_fix_job",
]

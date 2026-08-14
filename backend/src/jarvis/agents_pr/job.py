"""Job orchestration for the agent-PR bonus (T-11): the state machine pinned
in docs/tickets.json T-11 acceptance #1 (``started -> progress ->
pr_opened | failed``), driven by an injected ``AgentRunner`` so acceptance
#1/#2 are unit-tested with a mocked agent and no real GITHUB_TOKEN,
subprocess, or network call.

``execute_fix_job`` is the whole job as one awaitable coroutine -- what
tests call directly, and what ``run_fix_job`` schedules as a background
task. ``run_fix_job`` is the pinned public entry point (docs/DESIGN.md
§Interfaces: ``run_fix_job(repo_url, issue_number, user_id) -> job_id``),
which returns a job_id immediately and lets the run happen in the
background, per R10 ("background coding agent").
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from jarvis.pipeline.events import EventPublisher

from .announcer import Announcer, Speak
from .events import AgentFailed, AgentProgress, AgentPROpened, AgentRunner, JobState
from .repo_allowlist import RepoNotAllowedError, check_repo_allowed
from .workdir import WorkdirError, cleanup_workdir, prepare_workdir

logger = logging.getLogger(__name__)

PrepareWorkdir = Callable[[str, str], Path]
CleanupWorkdir = Callable[[Path], None]


@dataclass
class JobRecord:
    """Current/terminal state of one agent-PR job, keyed by ``job_id``."""

    job_id: str
    repo_url: str
    issue_number: int
    user_id: str
    state: JobState = "started"
    pr_url: str | None = None
    error: str | None = None


class JobStore:
    """In-memory job registry (per-process; the worker is single-process per
    docs/DESIGN.md's Fly.io process-group layout). No persistence -- a
    restart loses in-flight job status, which matches T-11's non-goal of
    "no retry queues"."""

    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}

    def create(self, record: JobRecord) -> None:
        self._jobs[record.job_id] = record

    def get(self, job_id: str) -> JobRecord | None:
        return self._jobs.get(job_id)


# Process-wide default store so a caller of run_fix_job can poll job status
# by id later without threading a store through the voice pipeline. Tests
# that only care about the state-machine transitions pass their own
# JobStore (or none -- execute_fix_job creates one internally if omitted).
default_job_store = JobStore()

# One template per JobState; formatted with issue_number always, plus
# pr_url/error where the state needs them. Spoken by the Announcer (R2/R10:
# "announcing start/progress/result audibly").
_STATE_MESSAGES: dict[JobState, str] = {
    "started": "Starting on issue #{issue_number}. This will take a few minutes.",
    "progress": "Still working on issue #{issue_number}.",
    "pr_opened": "Opened a pull request for issue #{issue_number}: {pr_url}",
    "failed": "I couldn't fix issue #{issue_number}: {error}",
}


async def execute_fix_job(
    *,
    job_id: str,
    repo_url: str,
    issue_number: int,
    user_id: str,
    github_token: str,
    runner: AgentRunner,
    store: JobStore | None = None,
    announcer: Announcer | None = None,
    prepare_workdir_fn: PrepareWorkdir = prepare_workdir,
    cleanup_workdir_fn: CleanupWorkdir = cleanup_workdir,
) -> JobRecord:
    """Run one agent-PR job to completion, driving the state machine
    (``started -> progress -> pr_opened | failed``) and announcing every
    *transition* (not every raw agent event -- repeated ``AgentProgress``
    events while already in "progress" are absorbed silently, so a chatty
    agent doesn't turn into a chatty TTS stream).

    Returns the terminal ``JobRecord``. Never raises for an ordinary agent
    failure (that is the "failed" state); only a bug in this orchestration
    itself would propagate.
    """
    store = store if store is not None else JobStore()
    announcer = announcer if announcer is not None else Announcer()
    record = JobRecord(job_id=job_id, repo_url=repo_url, issue_number=issue_number, user_id=user_id)
    store.create(record)

    # Tracked separately from record.state: JobRecord.state defaults to
    # "started" for a sensible pre-first-announcement value, so comparing
    # against record.state directly would make the very first
    # _transition("started") call a same-state no-op and silently swallow
    # the "started" announcement.
    last_announced: JobState | None = None

    async def _transition(state: JobState, **fmt: object) -> None:
        nonlocal last_announced
        if last_announced == state:
            return  # not a transition -- don't re-announce the same state
        last_announced = state
        record.state = state
        if "pr_url" in fmt:
            record.pr_url = fmt["pr_url"]  # type: ignore[assignment]
        if "error" in fmt:
            record.error = fmt["error"]  # type: ignore[assignment]
        message = _STATE_MESSAGES[state].format(issue_number=issue_number, **fmt)
        await announcer.announce(message)

    await _transition("started")

    # Verifier finding G: nothing previously tied `repo_url` to a repo the
    # user actually controls -- any https:// URL was accepted. Refuse
    # before touching a workdir/clone/agent run at all if it's not on the
    # explicit allow-list (SPEC non-goal: "no arbitrary-repo writes").
    try:
        check_repo_allowed(repo_url)
    except RepoNotAllowedError as exc:
        await _transition("failed", error=str(exc))
        return record

    try:
        workdir = await asyncio.to_thread(prepare_workdir_fn, repo_url, github_token)
    except WorkdirError as exc:
        await _transition("failed", error=str(exc))
        return record

    try:
        async for event in runner.run(
            repo_url=repo_url,
            issue_number=issue_number,
            workdir=workdir,
            github_token=github_token,
        ):
            if isinstance(event, AgentProgress):
                await _transition("progress")
            elif isinstance(event, AgentPROpened):
                await _transition("pr_opened", pr_url=event.pr_url)
                return record
            elif isinstance(event, AgentFailed):
                await _transition("failed", error=event.error)
                return record
        # The runner exhausted its generator without a terminal event --
        # never leave the job silently stuck in "progress".
        await _transition("failed", error="agent run ended without opening a pull request")
        return record
    except Exception as exc:  # noqa: BLE001 -- any agent-run failure is a job failure, not a crash
        logger.exception("agent-pr job %s failed", job_id)
        await _transition("failed", error=str(exc))
        return record
    finally:
        await asyncio.to_thread(cleanup_workdir_fn, workdir)


def run_fix_job(
    repo_url: str,
    issue_number: int,
    user_id: str,
    *,
    runner: AgentRunner | None = None,
    publisher: EventPublisher | None = None,
    speak: Speak | None = None,
    store: JobStore | None = None,
    github_token: str | None = None,
) -> str:
    """Pinned entry point (docs/DESIGN.md §Interfaces).

    Starts a background coding-agent run against ``repo_url``'s issue
    ``issue_number`` and returns a job_id immediately -- the run itself
    happens on an ``asyncio`` task. R10: announces start/progress/result
    audibly, opens a PR, never merges.

    Must be called from a running event loop (the voice worker's), per
    ``asyncio.create_task``'s own requirement -- this is the "background"
    half of "background coding agent" from docs/DESIGN.md.

    Raises synchronously (no job is scheduled) for either precondition
    failure -- missing credentials, or ``repo_url`` not on the
    ``AGENT_PR_ALLOWED_REPOS`` allow-list (verifier finding G) --  so a
    caller learns immediately rather than having to poll a job that was
    doomed before it started. ``execute_fix_job`` enforces the repo
    allow-list a second time internally, so calling it directly (as the
    unit tests do) is protected too.
    """
    check_repo_allowed(repo_url)  # raises RepoNotAllowedError -- let it propagate

    token = github_token if github_token is not None else os.environ.get("GITHUB_TOKEN")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is not set -- required to run an agent-PR job (R10)")

    if runner is None:
        from .claude_runner import ClaudeCodeAgentRunner

        runner = ClaudeCodeAgentRunner()

    job_id = uuid.uuid4().hex
    announcer = Announcer(publisher=publisher, speak=speak)
    job_store = store if store is not None else default_job_store

    asyncio.create_task(
        execute_fix_job(
            job_id=job_id,
            repo_url=repo_url,
            issue_number=issue_number,
            user_id=user_id,
            github_token=token,
            runner=runner,
            store=job_store,
            announcer=announcer,
        )
    )
    return job_id

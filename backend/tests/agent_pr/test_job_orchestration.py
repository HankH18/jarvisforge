"""T-11 acceptance #1/#2: job orchestration unit-tested with a MOCKED agent
(the ScriptedAgentRunner test double), asserting the state machine
(started -> progress -> pr_opened | failed, docs/tickets.json T-11) and that
an announcement is emitted at each state *transition* on both the data
channel and TTS, with the TTS boundary mocked (RecordingSpeaker) -- Cartesia
being down (HTTP 402, independently confirmed during this build) can never
affect these tests, since no TTS provider is ever touched.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from agent_pr_fakes import RecordingPublisher, RecordingSpeaker, ScriptedAgentRunner

from jarvis.agents_pr import job as job_module
from jarvis.agents_pr.announcer import Announcer
from jarvis.agents_pr.events import AgentFailed, AgentProgress, AgentPROpened
from jarvis.agents_pr.job import JobStore, execute_fix_job, run_fix_job
from jarvis.agents_pr.repo_allowlist import RepoNotAllowedError
from jarvis.agents_pr.workdir import WorkdirError
from jarvis.pipeline.events import agent_state_event

PR_URL = "https://github.com/hankholcomb/some-test-repo/pull/7"


def _harness(
    tmp_path: Path, *, prepare_calls: list | None = None, cleanup_calls: list | None = None
):
    publisher = RecordingPublisher()
    speaker = RecordingSpeaker()
    announcer = Announcer(publisher=publisher, speak=speaker)

    def fake_prepare(repo_url: str, github_token: str) -> Path:
        if prepare_calls is not None:
            prepare_calls.append((repo_url, github_token))
        return tmp_path

    def fake_cleanup(workdir: Path) -> None:
        if cleanup_calls is not None:
            cleanup_calls.append(workdir)

    return publisher, speaker, announcer, fake_prepare, fake_cleanup


# ---- happy path: started -> progress -> pr_opened -----------------------


@pytest.mark.asyncio
async def test_full_success_path_walks_started_progress_pr_opened(tmp_path):
    publisher, speaker, announcer, prepare, cleanup = _harness(tmp_path)
    runner = ScriptedAgentRunner(
        [AgentProgress(detail="reading issue"), AgentPROpened(pr_url=PR_URL)]
    )

    record = await execute_fix_job(
        job_id="job-1",
        repo_url="https://github.com/hankholcomb/some-test-repo",
        issue_number=42,
        user_id="user-1",
        github_token="scoped-token",
        runner=runner,
        announcer=announcer,
        prepare_workdir_fn=prepare,
        cleanup_workdir_fn=cleanup,
    )

    assert record.state == "pr_opened"
    assert record.pr_url == PR_URL
    assert record.error is None

    # exactly one announcement per transition: started, progress, pr_opened
    assert len(speaker.spoken) == 3
    assert "Starting on issue #42" in speaker.spoken[0]
    assert "Still working on issue #42" in speaker.spoken[1]
    assert speaker.spoken[2] == f"Opened a pull request for issue #42: {PR_URL}"

    assert len(publisher.events) == 3
    for event in publisher.events:
        assert event == agent_state_event("working", ts=event["ts"])


@pytest.mark.asyncio
async def test_repeated_progress_events_only_announce_once(tmp_path):
    _, speaker, announcer, prepare, cleanup = _harness(tmp_path)
    runner = ScriptedAgentRunner(
        [AgentProgress(), AgentProgress(), AgentProgress(), AgentPROpened(pr_url=PR_URL)]
    )

    record = await execute_fix_job(
        job_id="job-2",
        repo_url="https://github.com/o/r",
        issue_number=1,
        user_id="u",
        github_token="t",
        runner=runner,
        announcer=announcer,
        prepare_workdir_fn=prepare,
        cleanup_workdir_fn=cleanup,
    )

    assert record.state == "pr_opened"
    # started, progress (once, despite 3 AgentProgress events), pr_opened
    assert len(speaker.spoken) == 3


@pytest.mark.asyncio
async def test_terminal_event_can_arrive_with_no_progress_in_between(tmp_path):
    _, speaker, announcer, prepare, cleanup = _harness(tmp_path)
    runner = ScriptedAgentRunner([AgentPROpened(pr_url=PR_URL)])

    record = await execute_fix_job(
        job_id="job-3",
        repo_url="https://github.com/o/r",
        issue_number=1,
        user_id="u",
        github_token="t",
        runner=runner,
        announcer=announcer,
        prepare_workdir_fn=prepare,
        cleanup_workdir_fn=cleanup,
    )

    assert record.state == "pr_opened"
    assert len(speaker.spoken) == 2  # started, pr_opened -- progress is optional


# ---- failure paths --------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_failed_event_walks_started_then_failed(tmp_path):
    _, speaker, announcer, prepare, cleanup = _harness(tmp_path)
    runner = ScriptedAgentRunner([AgentProgress(), AgentFailed(error="no fix found")])

    record = await execute_fix_job(
        job_id="job-4",
        repo_url="https://github.com/o/r",
        issue_number=9,
        user_id="u",
        github_token="t",
        runner=runner,
        announcer=announcer,
        prepare_workdir_fn=prepare,
        cleanup_workdir_fn=cleanup,
    )

    assert record.state == "failed"
    assert record.error == "no fix found"
    assert record.pr_url is None
    assert len(speaker.spoken) == 3  # started, progress, failed
    assert speaker.spoken[2] == "I couldn't fix issue #9: no fix found"


@pytest.mark.asyncio
async def test_runner_exhausting_with_no_terminal_event_becomes_failed(tmp_path):
    _, speaker, announcer, prepare, cleanup = _harness(tmp_path)
    runner = ScriptedAgentRunner([AgentProgress()])  # never yields a terminal event

    record = await execute_fix_job(
        job_id="job-5",
        repo_url="https://github.com/o/r",
        issue_number=1,
        user_id="u",
        github_token="t",
        runner=runner,
        announcer=announcer,
        prepare_workdir_fn=prepare,
        cleanup_workdir_fn=cleanup,
    )

    assert record.state == "failed"
    assert "opening a pull request" in record.error
    assert len(speaker.spoken) == 3  # started, progress, failed -- never stuck in "progress"


@pytest.mark.asyncio
async def test_exception_raised_by_the_agent_run_is_caught_as_a_job_failure(tmp_path):
    _, speaker, announcer, prepare, cleanup = _harness(tmp_path)
    runner = ScriptedAgentRunner([AgentProgress()], raises=RuntimeError("agent process crashed"))

    record = await execute_fix_job(
        job_id="job-6",
        repo_url="https://github.com/o/r",
        issue_number=1,
        user_id="u",
        github_token="t",
        runner=runner,
        announcer=announcer,
        prepare_workdir_fn=prepare,
        cleanup_workdir_fn=cleanup,
    )

    assert record.state == "failed"
    assert record.error == "agent process crashed"


@pytest.mark.asyncio
async def test_workdir_prep_failure_short_circuits_before_the_agent_ever_runs(tmp_path):
    _, speaker, announcer, _prepare, cleanup = _harness(tmp_path)
    runner = ScriptedAgentRunner([AgentPROpened(pr_url=PR_URL)])

    def failing_prepare(repo_url: str, github_token: str) -> Path:
        raise WorkdirError("git clone failed: repository not found")

    record = await execute_fix_job(
        job_id="job-7",
        repo_url="https://github.com/o/does-not-exist",
        issue_number=1,
        user_id="u",
        github_token="t",
        runner=runner,
        announcer=announcer,
        prepare_workdir_fn=failing_prepare,
        cleanup_workdir_fn=cleanup,
    )

    assert record.state == "failed"
    assert "repository not found" in record.error
    assert runner.calls == []  # the agent must never run against a missing workdir
    assert len(speaker.spoken) == 2  # started, failed


# ---- plumbing: workdir/cleanup, job store, publisher/speaker optionality --


@pytest.mark.asyncio
async def test_runner_receives_the_exact_prepared_workdir_and_credentials(tmp_path):
    _, _speaker, announcer, prepare, cleanup = _harness(tmp_path)
    runner = ScriptedAgentRunner([AgentPROpened(pr_url=PR_URL)])

    await execute_fix_job(
        job_id="job-8",
        repo_url="https://github.com/hankholcomb/some-test-repo",
        issue_number=42,
        user_id="u",
        github_token="scoped-token-xyz",
        runner=runner,
        announcer=announcer,
        prepare_workdir_fn=prepare,
        cleanup_workdir_fn=cleanup,
    )

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["repo_url"] == "https://github.com/hankholcomb/some-test-repo"
    assert call["issue_number"] == 42
    assert call["workdir"] == tmp_path
    assert call["github_token"] == "scoped-token-xyz"


@pytest.mark.asyncio
async def test_workdir_is_cleaned_up_on_success_and_on_failure(tmp_path):
    for events, expect_state in (
        ([AgentPROpened(pr_url=PR_URL)], "pr_opened"),
        ([AgentFailed(error="nope")], "failed"),
    ):
        cleanup_calls: list = []
        _, _speaker, announcer, prepare, cleanup = _harness(tmp_path, cleanup_calls=cleanup_calls)
        runner = ScriptedAgentRunner(events)

        record = await execute_fix_job(
            job_id="job-cleanup",
            repo_url="https://github.com/o/r",
            issue_number=1,
            user_id="u",
            github_token="t",
            runner=runner,
            announcer=announcer,
            prepare_workdir_fn=prepare,
            cleanup_workdir_fn=cleanup,
        )

        assert record.state == expect_state
        assert cleanup_calls == [tmp_path]


@pytest.mark.asyncio
async def test_job_store_is_queryable_by_job_id_after_completion(tmp_path):
    _, _speaker, announcer, prepare, cleanup = _harness(tmp_path)
    store = JobStore()
    runner = ScriptedAgentRunner([AgentPROpened(pr_url=PR_URL)])

    await execute_fix_job(
        job_id="job-9",
        repo_url="https://github.com/o/r",
        issue_number=1,
        user_id="u",
        github_token="t",
        runner=runner,
        store=store,
        announcer=announcer,
        prepare_workdir_fn=prepare,
        cleanup_workdir_fn=cleanup,
    )

    looked_up = store.get("job-9")
    assert looked_up is not None
    assert looked_up.state == "pr_opened"
    assert store.get("no-such-job") is None


@pytest.mark.asyncio
async def test_no_publisher_or_speaker_supplied_still_completes_cleanly(tmp_path):
    """Announcer() with neither channel wired -- default_job_store's/
    run_fix_job's caller may not have voice output ready yet; the job must
    still run to completion rather than erroring on an absent channel."""
    _, _, _announcer, prepare, cleanup = _harness(tmp_path)
    runner = ScriptedAgentRunner([AgentPROpened(pr_url=PR_URL)])

    record = await execute_fix_job(
        job_id="job-10",
        repo_url="https://github.com/o/r",
        issue_number=1,
        user_id="u",
        github_token="t",
        runner=runner,
        announcer=Announcer(),  # no publisher, no speak
        prepare_workdir_fn=prepare,
        cleanup_workdir_fn=cleanup,
    )

    assert record.state == "pr_opened"


# ---- run_fix_job: the pinned public entry point ---------------------------


def test_run_fix_job_requires_a_github_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="GITHUB_TOKEN"):
        run_fix_job("https://github.com/o/r", 1, "user-1", runner=ScriptedAgentRunner())


@pytest.mark.asyncio
async def test_run_fix_job_returns_a_job_id_and_schedules_the_run_as_a_background_task(
    monkeypatch,
):
    """Verifies the wiring only -- what gets scheduled, with what
    credentials -- without ever letting the scheduled coroutine actually
    run (which would touch a real workdir/git clone via the default
    prepare_workdir). The state-machine behavior itself is covered by the
    execute_fix_job tests above."""
    scheduled: dict = {}

    def fake_create_task(coro):
        scheduled["coro"] = coro
        return None

    monkeypatch.setattr(job_module.asyncio, "create_task", fake_create_task)

    runner = ScriptedAgentRunner([AgentPROpened(pr_url=PR_URL)])
    store = JobStore()
    job_id = run_fix_job(
        "https://github.com/hankholcomb/some-test-repo",
        42,
        "user-1",
        runner=runner,
        github_token="scoped-token",
        store=store,
    )

    assert isinstance(job_id, str) and job_id
    assert "coro" in scheduled
    assert asyncio.iscoroutine(scheduled["coro"])
    scheduled["coro"].close()  # never actually run it -- no real git/network in this test


@pytest.mark.asyncio
async def test_run_fix_job_falls_back_to_the_github_token_env_var(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "env-scoped-token")

    def fake_create_task(coro):
        coro.close()
        return None

    monkeypatch.setattr(job_module.asyncio, "create_task", fake_create_task)

    job_id = run_fix_job(
        "https://github.com/o/r", 1, "user-1", runner=ScriptedAgentRunner(), store=JobStore()
    )
    assert job_id


# ---- repo allow-list (verifier finding G) ---------------------------------
#
# conftest.py's autouse fixture pre-authorizes the repo URLs the rest of
# this file uses. These tests narrow AGENT_PR_ALLOWED_REPOS themselves so
# the URL under test is genuinely NOT on the list.


@pytest.mark.asyncio
async def test_execute_fix_job_refuses_a_repo_not_on_the_allow_list(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_PR_ALLOWED_REPOS", "https://github.com/hankholcomb/only-this-repo")
    _, speaker, announcer, prepare, cleanup = _harness(tmp_path)
    runner = ScriptedAgentRunner([AgentPROpened(pr_url=PR_URL)])

    record = await execute_fix_job(
        job_id="job-repo-not-allowed",
        repo_url="https://github.com/attacker/not-authorized",
        issue_number=1,
        user_id="u",
        github_token="t",
        runner=runner,
        announcer=announcer,
        prepare_workdir_fn=prepare,
        cleanup_workdir_fn=cleanup,
    )

    assert record.state == "failed"
    assert "not on the agent-PR repo allow-list" in record.error
    assert runner.calls == []  # the agent must never run against an unauthorized repo
    assert len(speaker.spoken) == 2  # started, failed


def test_run_fix_job_refuses_a_repo_not_on_the_allow_list_before_scheduling(monkeypatch):
    monkeypatch.setenv("AGENT_PR_ALLOWED_REPOS", "https://github.com/hankholcomb/only-this-repo")
    scheduled: dict = {}

    def fake_create_task(coro):
        scheduled["coro"] = coro
        return None

    monkeypatch.setattr(job_module.asyncio, "create_task", fake_create_task)

    with pytest.raises(RepoNotAllowedError):
        run_fix_job(
            "https://github.com/attacker/not-authorized",
            1,
            "user-1",
            runner=ScriptedAgentRunner(),
            github_token="t",
            store=JobStore(),
        )

    assert "coro" not in scheduled  # never scheduled -- refused before any task exists

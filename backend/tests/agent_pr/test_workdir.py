"""workdir.prepare_workdir/cleanup_workdir: isolated per-job checkout (T-11).

These only exercise failure paths that don't need real network access (a
non-https URL, and a connection that's refused immediately) -- a successful
real clone is exactly what the one recorded live run (T-11 acceptance #3,
docs/agent-pr-demo.md) demonstrates end to end.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.agents_pr.workdir import WorkdirError, cleanup_workdir, prepare_workdir


def test_rejects_non_https_repo_urls():
    with pytest.raises(WorkdirError, match="https://"):
        prepare_workdir("git@github.com:owner/repo.git", "some-token")


def test_clone_failure_raises_workdir_error_and_removes_the_partial_dir():
    # Port 1 on loopback refuses immediately -- no real network dependency,
    # no DNS wait, and fast/deterministic in CI.
    with pytest.raises(WorkdirError):
        prepare_workdir("https://127.0.0.1:1/owner/repo.git", "super-secret-token")


def test_clone_failure_never_leaks_the_token_into_the_error_message():
    token = "super-secret-token-xyz"
    with pytest.raises(WorkdirError) as excinfo:
        prepare_workdir("https://127.0.0.1:1/owner/repo.git", token)
    assert token not in str(excinfo.value)


def test_cleanup_removes_an_existing_directory(tmp_path):
    target = tmp_path / "job-workdir"
    target.mkdir()
    (target / "file.txt").write_text("hello")

    cleanup_workdir(target)

    assert not target.exists()


def test_cleanup_of_a_missing_directory_does_not_raise():
    cleanup_workdir(Path("/tmp/definitely-does-not-exist-jarvis-agent-pr-test"))

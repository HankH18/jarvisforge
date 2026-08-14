"""Autouse fixtures for backend/tests/agent_pr.

``execute_fix_job``/``run_fix_job`` now refuse any ``repo_url`` that isn't
on the ``AGENT_PR_ALLOWED_REPOS`` allow-list (verifier finding G --
repo_allowlist.py). Job-orchestration/state-machine tests care about the
state machine, not the allow-list, so this autouse fixture pre-authorizes
the fixed set of repo URLs those tests use -- tests that specifically
exercise the allow-list itself (test_repo_allowlist.py, and the dedicated
"repo not allowed" cases in test_job_orchestration.py) override the env var
themselves within the test, which takes effect for the rest of that test
since it runs after this fixture's setup.
"""

from __future__ import annotations

import pytest

ALLOWED_TEST_REPOS = (
    "https://github.com/hankholcomb/some-test-repo",
    "https://github.com/o/r",
    "https://github.com/o/does-not-exist",
)


@pytest.fixture(autouse=True)
def _agent_pr_allowed_repos_env(monkeypatch):
    monkeypatch.setenv("AGENT_PR_ALLOWED_REPOS", ",".join(ALLOWED_TEST_REPOS))

"""repo_allowlist: verifier finding G -- run_fix_job/prepare_workdir used to
accept ANY https:// URL with nothing tying the target to a repo the user
actually controls (SPEC non-goal: "no arbitrary-repo writes"). This module
makes that an enforced precondition. Uses the ``env=`` override so these
tests don't depend on (or fight) the autouse ``AGENT_PR_ALLOWED_REPOS``
fixture in conftest.py.
"""

from __future__ import annotations

import pytest

from jarvis.agents_pr.repo_allowlist import (
    RepoNotAllowedError,
    allowed_repo_urls,
    check_repo_allowed,
)

ALLOWED = "https://github.com/hankholcomb/some-test-repo"


def test_allowed_repo_urls_parses_a_comma_separated_list():
    env = {"AGENT_PR_ALLOWED_REPOS": f"{ALLOWED}, https://github.com/o/other  "}
    urls = allowed_repo_urls(env=env)
    assert urls == {ALLOWED.lower(), "https://github.com/o/other".lower()}


def test_empty_or_unset_allow_list_permits_nothing():
    assert allowed_repo_urls(env={}) == frozenset()
    assert allowed_repo_urls(env={"AGENT_PR_ALLOWED_REPOS": ""}) == frozenset()


def test_check_repo_allowed_passes_silently_for_an_allowed_repo():
    check_repo_allowed(ALLOWED, env={"AGENT_PR_ALLOWED_REPOS": ALLOWED})  # must not raise


def test_check_repo_allowed_raises_for_a_repo_not_on_the_list():
    env = {"AGENT_PR_ALLOWED_REPOS": ALLOWED}
    with pytest.raises(RepoNotAllowedError, match="not on the agent-PR repo allow-list"):
        check_repo_allowed("https://github.com/attacker/evil-repo", env=env)


def test_check_repo_allowed_raises_when_the_env_var_is_unset():
    with pytest.raises(RepoNotAllowedError):
        check_repo_allowed(ALLOWED, env={})


def test_matching_is_case_insensitive_and_ignores_a_trailing_slash():
    check_repo_allowed(
        "HTTPS://GitHub.com/HankHolcomb/Some-Test-Repo/",
        env={"AGENT_PR_ALLOWED_REPOS": ALLOWED},
    )  # must not raise -- same repo, different casing/trailing slash


def test_a_different_repo_under_the_same_owner_is_not_implicitly_allowed():
    with pytest.raises(RepoNotAllowedError):
        check_repo_allowed(
            "https://github.com/hankholcomb/a-different-repo",
            env={"AGENT_PR_ALLOWED_REPOS": ALLOWED},
        )


def test_reads_the_real_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("AGENT_PR_ALLOWED_REPOS", ALLOWED)
    check_repo_allowed(ALLOWED)  # no env= override -- reads os.environ

    monkeypatch.setenv("AGENT_PR_ALLOWED_REPOS", "")
    with pytest.raises(RepoNotAllowedError):
        check_repo_allowed(ALLOWED)

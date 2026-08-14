"""Repo allow-list for the agent-PR job (verifier finding G / SPEC non-goal
"no arbitrary-repo writes; any write action on repos the user doesn't
control").

Before this module existed, ``run_fix_job``/``prepare_workdir`` accepted
*any* ``https://`` URL with no check that it belonged to a repo the caller
actually controls -- nothing in the code tied the target to an authorized
list. That the feature was safe in practice was luck (the package was never
wired into a voice/tool-calling path that could pass an attacker- or
user-supplied URL through), not a safeguard. This module makes it an
enforced precondition instead: ``repo_url`` must appear in
``AGENT_PR_ALLOWED_REPOS`` (a comma-separated env var) or the job is refused
before anything else happens -- no workdir, no clone, no agent run.
"""

from __future__ import annotations

import os


class RepoNotAllowedError(RuntimeError):
    """Raised when ``repo_url`` is not on the agent-PR repo allow-list."""


def _normalize(repo_url: str) -> str:
    return repo_url.strip().rstrip("/").lower()


def allowed_repo_urls(*, env: dict[str, str] | None = None) -> frozenset[str]:
    """The configured allow-list, read fresh from the environment (or the
    given ``env`` mapping, for tests) every call -- deliberately not cached,
    so a test that sets/clears the env var doesn't fight process-wide
    caching, and so a real deploy can rotate the list without a restart if
    it's ever read from a reloadable config source instead."""
    source = env if env is not None else os.environ
    raw = source.get("AGENT_PR_ALLOWED_REPOS", "")
    return frozenset(_normalize(url) for url in raw.split(",") if url.strip())


def check_repo_allowed(repo_url: str, *, env: dict[str, str] | None = None) -> None:
    """Raise ``RepoNotAllowedError`` unless ``repo_url`` is on the
    allow-list. Deny by default: an unset/empty ``AGENT_PR_ALLOWED_REPOS``
    allows nothing, rather than falling open."""
    allowed = allowed_repo_urls(env=env)
    if _normalize(repo_url) not in allowed:
        raise RepoNotAllowedError(
            f"{repo_url!r} is not on the agent-PR repo allow-list "
            "(AGENT_PR_ALLOWED_REPOS) -- refusing to run a coding agent against "
            "a repo the user hasn't explicitly authorized."
        )

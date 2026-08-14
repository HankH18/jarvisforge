"""Isolated per-job workdir for the agent-PR run (T-11): a fresh throwaway
directory the coding agent's Bash/Read/Write/Edit tools are confined to via
``ClaudeAgentOptions.cwd``, cloned new for every job so one job's checkout
can never leak into another's and a crashed job leaves nothing behind but a
directory under the OS temp root.

The GITHUB_TOKEN never touches argv (a git URL embedding
``https://<token>@...`` would be visible to any other process on the box via
``ps``/``/proc/<pid>/cmdline``). Instead it's handed to git through
``GIT_ASKPASS`` + an env var the askpass script reads at runtime -- env is
still not a real secret boundary (same-user ``/proc/<pid>/environ`` access
on Linux), but it's the standard, meaningfully-better-than-argv pattern git
itself documents for scripted credential injection.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_CLONE_TIMEOUT_S = 120

# Answers git's two credential prompts without ever putting the token on the
# command line: the Username prompt gets the conventional GitHub App/PAT
# placeholder, everything else (the Password prompt) gets the real token
# from the env var the parent process set for this invocation only.
_ASKPASS_SCRIPT = """#!/bin/sh
case "$1" in
    Username*) echo "x-access-token" ;;
    *) echo "$JARVIS_AGENT_PR_TOKEN" ;;
esac
"""


class WorkdirError(RuntimeError):
    """Raised when the isolated workdir could not be prepared (clone failed)."""


def prepare_workdir(repo_url: str, github_token: str) -> Path:
    """Create a fresh temp directory and shallow-clone ``repo_url`` into it,
    authenticated with ``github_token`` via ``GIT_ASKPASS`` (never argv).

    Raises ``WorkdirError`` on any failure; the partially-created directory
    is removed before raising so a failed job leaves nothing behind.
    """
    if not repo_url.startswith("https://"):
        raise WorkdirError(f"only https:// GitHub URLs are supported, got: {repo_url!r}")

    workdir = Path(tempfile.mkdtemp(prefix="jarvis-agent-pr-"))
    askpass_path = workdir.parent / f".askpass-{workdir.name}.sh"
    try:
        askpass_path.write_text(_ASKPASS_SCRIPT)
        askpass_path.chmod(0o700)
        env = {
            **os.environ,
            "GIT_ASKPASS": str(askpass_path),
            "GIT_TERMINAL_PROMPT": "0",
            "JARVIS_AGENT_PR_TOKEN": github_token,
        }
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(workdir)],
                capture_output=True,
                text=True,
                timeout=_CLONE_TIMEOUT_S,
                env=env,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            shutil.rmtree(workdir, ignore_errors=True)
            raise WorkdirError(f"git clone of {repo_url!r} failed: {exc}") from exc
    finally:
        askpass_path.unlink(missing_ok=True)

    if result.returncode != 0:
        shutil.rmtree(workdir, ignore_errors=True)
        raise WorkdirError(f"git clone of {repo_url!r} failed: {result.stderr.strip()}")

    return workdir


def cleanup_workdir(workdir: Path) -> None:
    """Best-effort removal of a job's isolated workdir. Never raises --
    cleanup failing is not a job failure."""
    shutil.rmtree(workdir, ignore_errors=True)

"""Production ``AgentRunner`` (T-11): drives ``claude_agent_sdk.query()``
headlessly in the isolated workdir prepared by ``workdir.py``, with a scoped
GITHUB_TOKEN and a PreToolUse hook (``guard.py``) that enforces a
positive-permission allow-list on every Bash invocation -- see ``guard.py``
for why this replaced an earlier deny-list (an independent verifier
defeated it six different ways) and for the exact allow-list rules.

This module -- and the Claude Agent SDK itself -- is only exercised by the
one recorded live run (T-11 acceptance #3). The mocked-agent unit tests
(acceptance #1/#2) never import it; they drive ``job.execute_fix_job``
directly against the ``AgentRunner`` Protocol in ``events.py``. This
module's own translation logic (message -> AgentEvent) and its guard hook
ARE unit-tested in ``test_claude_runner.py`` and ``test_guard.py``, with
``claude_agent_sdk.query`` itself mocked -- no subprocess, no CLI, no
network.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookContext,
    HookMatcher,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    query,
)

from .events import AgentEvent, AgentFailed, AgentProgress, AgentPROpened
from .guard import is_allowed_bash_command

logger = logging.getLogger(__name__)

# Matches the PR URL `gh pr create` prints on success, wherever it shows up
# (assistant text, or a Bash tool_result's stdout).
_PR_URL_RE = re.compile(r"https://github\.com/[^\s/]+/[^\s/]+/pull/\d+")

# `Write` stays granted (the fix itself has to be authored somehow), but
# **not** because Bash is trusted to run whatever it writes: guard.py's
# allow-list restricts Bash to a fixed binary set (`git`/`gh` only), so no
# allowed Bash invocation can ever execute a file `Write` created (no
# `bash`, `sh`, `python`, `./script.sh`, ...). That closes the
# Write-then-execute script-indirection hole structurally, without needing
# to gate `Write` itself or inspect what it writes -- the alternative the
# verifier also offered ("drop Write from allowed_tools") isn't viable here
# since the job has to be able to edit the repo's source to fix anything.
_ALLOWED_TOOLS: list[str] = ["Bash", "Read", "Edit", "Write", "Glob", "Grep"]

_SYSTEM_PROMPT = """You are a background coding agent fixing exactly one GitHub issue.

Rules, non-negotiable:
- The repository is already cloned into your current working directory.
  Work only inside it.
- Make the smallest change that plausibly fixes the issue.
- Your Bash access is restricted to a small allow-list of plain `git`/`gh`
  commands (create a branch, add, commit, push a non-force ref to a
  non-main branch, `gh pr create`, plus a few read-only ones). No shell
  chaining (&&, ;, |), no redirection, no other binaries -- each Bash call
  must be exactly one plain command. Anything else is refused before it
  runs; if that happens, adjust your approach rather than retrying
  variations of the same denied command.
- Commit your change on a new branch (never commit directly to main/master).
- Push the branch, then open a pull request against the repo's default
  branch with `gh pr create`. A GITHUB_TOKEN scoped for exactly this repo is
  already in your environment -- use it via `gh`, don't ask for another.
- Print the pull request URL `gh pr create` returns in your final message.
- NEVER merge the pull request (no `gh pr merge`, no merge commits).
  NEVER force-push. NEVER push directly to main or master. Opening the PR
  is the end of your job -- a human reviews and merges it. This is also
  enforced outside your control, so don't spend turns trying to work
  around it.
- If you cannot produce a real fix, say so plainly instead of opening an
  empty or placeholder PR.
"""


async def _enforce_bash_allow_list(
    input_data: dict[str, Any], tool_use_id: str | None, context: HookContext
) -> dict[str, Any]:
    """PreToolUse hook (matcher="Bash"): denies any Bash command that isn't
    on ``guard.py``'s positive-permission allow-list, regardless of the
    session's permission_mode.

    Per the Claude Agent SDK's own ``can_use_tool`` docstring, a hook fires
    "regardless of permission rules" -- unlike ``can_use_tool``, which is
    skipped for a tool call an ``allowed_tools``/``permission_mode`` rule
    already resolved (which Bash always is here, since it's in
    ``_ALLOWED_TOOLS``). A hook is therefore the one enforcement point that
    can't be silently bypassed by a broader-than-intended permission mode.
    """
    command = str(input_data.get("tool_input", {}).get("command", ""))
    allowed, reason = is_allowed_bash_command(command)
    if not allowed:
        # Observability for the first real exercise of this guard against a
        # live agent: every denial is logged, including ones that turn out
        # to be benign -- a refusal of something the job actually needed is
        # a design finding (widen the allow-list deliberately, in a
        # follow-up, with its own test), not something to silently work
        # around by loosening the guard mid-run.
        logger.warning("agent-pr Bash command denied: %r (%s)", command, reason)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Refused (R10 / agent-PR allow-list): {reason}. "
                    "Open the pull request and stop once it's up -- a human "
                    "reviews and merges it."
                ),
            }
        }
    logger.debug("agent-pr Bash command allowed: %r", command)
    return {}


def _tool_result_text(content: str | list[dict[str, Any]] | None) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item["text"] for item in content if isinstance(item, dict) and "text" in item
        )
    return ""


class ClaudeCodeAgentRunner:
    """Real ``AgentRunner``: one headless Claude Agent SDK session per job."""

    def __init__(self, *, model: str | None = None, max_turns: int = 40) -> None:
        # SPEC C3 / CLAUDE.md: model ids come from env vars only, never
        # hardcoded. TOOL_MODEL is the escalation model for heavy tools
        # (jarvis.llm.routing), and agent_pr is explicitly one of them.
        self._model = model if model is not None else os.environ.get("TOOL_MODEL")
        self._max_turns = max_turns

    async def run(
        self, *, repo_url: str, issue_number: int, workdir: Path, github_token: str
    ) -> AsyncIterator[AgentEvent]:
        options = ClaudeAgentOptions(
            cwd=workdir,
            model=self._model,
            system_prompt=_SYSTEM_PROMPT,
            tools=_ALLOWED_TOOLS,
            allowed_tools=_ALLOWED_TOOLS,
            permission_mode="acceptEdits",
            max_turns=self._max_turns,
            # GH_TOKEN is what `gh` reads; GITHUB_TOKEN is kept alongside it
            # since some tooling looks for that name instead.
            env={"GITHUB_TOKEN": github_token, "GH_TOKEN": github_token},
            hooks={"PreToolUse": [HookMatcher(matcher="Bash", hooks=[_enforce_bash_allow_list])]},
        )
        prompt = (
            f"Analyze and fix issue #{issue_number} in {repo_url}. The repository is "
            "already cloned into your working directory. Open a pull request with the "
            "fix and print its URL; never merge it."
        )

        pr_opened = False
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock):
                        yield AgentProgress(detail=f"using {block.name}")
                    elif isinstance(block, TextBlock):
                        found = _PR_URL_RE.search(block.text)
                        if found is not None:
                            pr_opened = True
                            yield AgentPROpened(pr_url=found.group(0))
            elif isinstance(message, UserMessage):
                blocks = message.content if isinstance(message.content, list) else []
                for block in blocks:
                    if isinstance(block, ToolResultBlock):
                        found = _PR_URL_RE.search(_tool_result_text(block.content))
                        if found is not None:
                            pr_opened = True
                            yield AgentPROpened(pr_url=found.group(0))
            elif isinstance(message, ResultMessage):
                if pr_opened:
                    return
                error = message.result or "agent run ended without opening a pull request"
                yield AgentFailed(error=error)
                return

"""Test doubles for backend/tests/agent_pr.

Named ``agent_pr_fakes`` rather than ``fakes``: test packages here have no
``__init__.py``, so sibling modules are imported by bare name off sys.path
and a second ``fakes.py`` would shadow other test packages' own
``fakes.py``. See tests/cadence/cadence_fakes.py for the same convention.

``RecordingPublisher`` mirrors tests/cadence/cadence_fakes.py's double of
the same name exactly (same shape as the real ``EventPublisher`` protocol).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from jarvis.agents_pr.events import AgentEvent


class RecordingPublisher:
    """Test EventPublisher: records payloads instead of touching a room."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, payload: dict) -> None:
        self.events.append(payload)


class RecordingSpeaker:
    """Test Speak double: records every spoken sentence instead of calling
    a TTS provider. Stands in for the "TTS boundary mocked" instruction --
    Cartesia returning 402 in the live deployment can never affect these
    tests because no TTS provider is ever touched."""

    def __init__(self) -> None:
        self.spoken: list[str] = []

    async def __call__(self, message: str) -> None:
        self.spoken.append(message)


class ScriptedAgentRunner:
    """Test AgentRunner: yields a pre-scripted sequence of AgentEvents (or
    raises a pre-scripted exception) instead of running the real Claude
    Agent SDK. Records the kwargs it was called with so a test can assert
    the orchestrator passed through the right repo_url/issue_number/workdir/
    github_token."""

    def __init__(
        self, events: Sequence[AgentEvent] = (), *, raises: Exception | None = None
    ) -> None:
        self._events = list(events)
        self._raises = raises
        self.calls: list[dict] = []

    async def run(
        self, *, repo_url: str, issue_number: int, workdir, github_token: str
    ) -> AsyncIterator[AgentEvent]:
        self.calls.append(
            {
                "repo_url": repo_url,
                "issue_number": issue_number,
                "workdir": workdir,
                "github_token": github_token,
            }
        )
        for event in self._events:
            yield event
        if self._raises is not None:
            raise self._raises

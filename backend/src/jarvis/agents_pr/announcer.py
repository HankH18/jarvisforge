"""Announces agent-PR job state transitions on two channels at once (T-11
acceptance #2): the LiveKit data channel (``agent_state: working``, pinned
in docs/DESIGN.md §Interfaces) and TTS (a spoken sentence, per R10/R2 --
"announcing start/progress/result audibly"). Both channels are optional and
independent: a caller with no publisher, no speaker, or neither still gets a
clean no-op rather than an error, so job.py's orchestration can be unit
tested with the TTS boundary mocked (per the T-11 build note: Cartesia
returning 402 must never block testing that announcements are emitted and
routed) and wired into the live pipeline incrementally later.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from jarvis.pipeline.events import EventPublisher, agent_state_event

# The TTS boundary: production wires this to something that ultimately calls
# the configured TTS provider (Cartesia, or the Aura-2 fallback per SPEC C2);
# tests pass a recording double directly. Deliberately just `str -> None` --
# this module knows nothing about LiveKit, Cartesia, or AgentSession.
Speak = Callable[[str], Awaitable[None]]


class Announcer:
    """Fan-out for one job-state-transition message to the data channel +
    TTS.

    Every announcement also flips the wire agent_state to "working": R10's
    audible-progress requirement covers the whole background job, not just
    its first turn, and "working" is the only pinned wire state (per
    docs/DESIGN.md's ``_WIRE_STATES``) a background job can legitimately
    claim -- it never owns "listening"/"thinking"/"speaking", which belong
    to the live turn loop.
    """

    def __init__(
        self, *, publisher: EventPublisher | None = None, speak: Speak | None = None
    ) -> None:
        self._publisher = publisher
        self._speak = speak

    async def announce(self, message: str) -> None:
        if self._publisher is not None:
            self._publisher.publish(agent_state_event("working"))
        if self._speak is not None:
            await self._speak(message)

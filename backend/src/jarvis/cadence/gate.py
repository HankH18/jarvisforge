"""Filler gate: speaks one filler when a heavy tool call outlives the
threshold, without ever blocking the tool call or leaking the timer task
(T-6). A failure in the filler itself (TTS hiccup, dead audio device, a
Cartesia stream error) is logged and swallowed, never allowed to replace the
guarded tool's own return value (T-17).

T-17 also removed the standalone ``decide()``/``FillerDecision`` pure-rule
pair that used to live here: it had zero call sites in ``src/`` (the live
timer path below re-encoded the same "heavy AND elapsed_ms > threshold_ms"
rule imperatively via "arm the timer only if heavy, sleep exactly
threshold_ms, then play"), and it could not honestly be wired into that live
path either -- the timer's own sleep duration IS the threshold check, so by
the time ``_speak_after_delay`` resumes from ``self._sleep(...)``, elapsed
time has *by construction* already reached (in production, strictly passed)
threshold_ms; there is no reachable moment along this path where a genuine
elapsed-time reading could still be under threshold. Keeping ``decide()``
around only as something the live path calls with a fabricated
"just-over-threshold" number would be decorative, not a real single source
of truth -- so the single copy of the rule left is the timer itself.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from jarvis.pipeline.events import EventPublisher, agent_state_event

from .fillers import FillerBank, FillerClip
from .policy import FILLER_THRESHOLD_MS

logger = logging.getLogger(__name__)

T = TypeVar("T")

Sleeper = Callable[[float], Awaitable[None]]
Player = Callable[[FillerClip], Awaitable[None]]


class FillerGate:
    """Speaks one filler when a heavy tool call outlives FILLER_THRESHOLD_MS."""

    def __init__(
        self,
        bank: FillerBank,
        play: Player,
        *,
        threshold_ms: float = FILLER_THRESHOLD_MS,
        sleep: Sleeper = asyncio.sleep,
        publisher: EventPublisher | None = None,
    ) -> None:
        self._bank = bank
        self._play = play
        self._threshold_ms = threshold_ms
        self._sleep = sleep
        self._publisher = publisher
        self._played: list[FillerClip] = []

    @property
    def played(self) -> list[FillerClip]:
        """Clips spoken this process, in order."""
        return self._played

    async def guard(self, awaitable: Awaitable[T], *, heavy: bool) -> T:
        """Await the tool call. If heavy, arm a timer that fires after
        threshold_ms and speaks exactly ONE filler; cancel the timer if the
        tool finishes first. Exceptions from the tool propagate after the
        timer is cancelled. At most one filler per guard() call.

        A failure in the filler itself -- the timer task raising anything
        other than CancelledError (a TTS hiccup, a dead audio device, a
        Cartesia stream error) -- is logged here and never allowed to replace
        ``awaitable``'s already-computed return value/exception: a `finally`
        block that raises overrides whatever the `try` was about to
        return/raise, so a naive `await timer_task` here would let a failed
        acknowledgment turn a successful tool result into an error (T-17).
        Only cancellation of ``guard()`` itself (which also cancels the
        timer) still propagates as CancelledError to the caller."""
        timer_task: asyncio.Task[None] | None = None
        if heavy:
            timer_task = asyncio.create_task(self._speak_after_delay())
        try:
            return await awaitable
        finally:
            if timer_task is not None:
                timer_task.cancel()
                try:
                    await timer_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception(
                        "filler playback failed; guarded tool result is unaffected"
                    )

    async def _speak_after_delay(self) -> None:
        await self._sleep(self._threshold_ms / 1000)
        clip = self._bank.next()
        # Record and announce before playback: the wire state should flip the
        # moment the filler starts, and a mid-playback cancel still counts as
        # spoken (the user heard the acknowledgment).
        self._played.append(clip)
        if self._publisher is not None:
            self._publisher.publish(agent_state_event("working"))
        await self._play(clip)

"""Filler gate: speaks one filler when a heavy tool call outlives the
threshold, without ever blocking the tool call or leaking the timer task
(T-6)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from jarvis.pipeline.events import EventPublisher, agent_state_event

from .fillers import FillerBank, FillerClip
from .policy import FILLER_THRESHOLD_MS

T = TypeVar("T")

Sleeper = Callable[[float], Awaitable[None]]
Player = Callable[[FillerClip], Awaitable[None]]


@dataclass(frozen=True)
class FillerDecision:
    play: bool
    reason: str  # "heavy_tool_exceeded_threshold" | "tool_faster_than_threshold" | "tool_not_heavy"


def decide(
    *, heavy: bool, elapsed_ms: float, threshold_ms: float = FILLER_THRESHOLD_MS
) -> FillerDecision:
    """Pure gating rule. Heavy AND elapsed_ms > threshold_ms => play."""
    if not heavy:
        return FillerDecision(play=False, reason="tool_not_heavy")
    if elapsed_ms > threshold_ms:
        return FillerDecision(play=True, reason="heavy_tool_exceeded_threshold")
    return FillerDecision(play=False, reason="tool_faster_than_threshold")


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
        timer is cancelled. At most one filler per guard() call."""
        timer_task: asyncio.Task[None] | None = None
        if heavy:
            timer_task = asyncio.create_task(self._speak_after_delay())
        try:
            return await awaitable
        finally:
            if timer_task is not None:
                timer_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await timer_task

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

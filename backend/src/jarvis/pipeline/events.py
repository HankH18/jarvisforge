"""Data-channel event shapes, pinned exactly in docs/DESIGN.md §Interfaces:

    {"type":"agent_state","state":"listening|thinking|working|speaking","ts":0}
    {"type":"transcript","role":"user|assistant","text":"...","final":true}
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from typing import Literal, Protocol

import livekit.rtc as rtc

logger = logging.getLogger(__name__)

# The SDK's own AgentState also has "initializing"/"idle" (pre-conversation
# states); DESIGN.md pins the wire vocabulary to just these four. "working" is
# reserved for T-6 (heavy-tool cadence) and is never emitted by T-1, which has
# no tools -- to_wire_state() below suppresses "initializing"/"idle" so only
# the pinned vocabulary ever reaches the wire.
WireAgentState = Literal["listening", "thinking", "working", "speaking"]
_WIRE_STATES = frozenset(("listening", "thinking", "working", "speaking"))


class EventPublisher(Protocol):
    """Boundary the pipeline publishes data-channel events through.

    Production wraps ``room.local_participant.publish_data``; tests use a
    stub that records payloads, so the turn loop is exercised with no
    LiveKit room and no network.
    """

    def publish(self, payload: dict) -> None: ...


def agent_state_event(state: WireAgentState, *, ts: float | None = None) -> dict:
    return {"type": "agent_state", "state": state, "ts": ts if ts is not None else time.time()}


def transcript_event(role: Literal["user", "assistant"], text: str, *, final: bool) -> dict:
    return {"type": "transcript", "role": role, "text": text, "final": final}


def to_wire_state(state: str) -> WireAgentState | None:
    """Map an SDK ``AgentState`` to the pinned wire vocabulary.

    Returns ``None`` for states outside the pinned set (currently
    "initializing"/"idle"), meaning: do not publish an event for this
    transition.
    """
    return state if state in _WIRE_STATES else None  # type: ignore[return-value]


class DataChannelPublisher:
    """Production ``EventPublisher``: JSON over the LiveKit data channel."""

    def __init__(self, room: rtc.Room) -> None:
        self._room = room
        self._pending: set[asyncio.Task] = set()

    @property
    def room(self) -> rtc.Room:
        return self._room

    def publish(self, payload: dict) -> None:
        """Send one event on the ``jarvis`` data channel.

        ``publish_data`` is a *coroutine function* in livekit-rtc. Calling it
        without scheduling builds a coroutine object and drops it on the
        floor: no bytes ever leave the process, and the only symptom is a
        RuntimeWarning buried in the worker log. That is how every
        agent_state and transcript event silently failed to reach the SPA.
        ``publish`` is pinned synchronous in DESIGN.md §Interfaces, so we
        schedule the coroutine on the running loop instead of awaiting it.
        """
        result = self._room.local_participant.publish_data(
            json.dumps(payload).encode("utf-8"), reliable=True, topic="jarvis"
        )
        if not inspect.isawaitable(result):
            return  # a synchronous SDK build, or a test double: nothing to schedule
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            result.close()  # no loop: close the coroutine so it cannot warn later
            logger.error(
                "DataChannelPublisher.publish() called with no running event loop; "
                "event dropped: %s",
                payload.get("type"),
            )
            return
        task = loop.create_task(result)
        # Hold a reference: asyncio only keeps weak ones, so an unreferenced
        # task can be garbage-collected mid-flight.
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        task.add_done_callback(self._log_publish_failure)

    @staticmethod
    def _log_publish_failure(task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error("data-channel publish failed: %r", exc)

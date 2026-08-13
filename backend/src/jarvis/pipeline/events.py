"""Data-channel event shapes, pinned exactly in docs/DESIGN.md §Interfaces:

    {"type":"agent_state","state":"listening|thinking|working|speaking","ts":0}
    {"type":"transcript","role":"user|assistant","text":"...","final":true}
"""

from __future__ import annotations

import json
import time
from typing import Literal, Protocol

import livekit.rtc as rtc

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

    @property
    def room(self) -> rtc.Room:
        return self._room

    def publish(self, payload: dict) -> None:
        self._room.local_participant.publish_data(
            json.dumps(payload).encode("utf-8"), reliable=True, topic="jarvis"
        )

"""Wires an AgentSession's events onto the data-channel contract + latency log.

Pure event-driven glue with no LiveKit room dependency, so it is exercised
directly (no network, no real room) in tests and reused unmodified by the
real worker.
"""

from __future__ import annotations

from livekit.agents import (
    AgentSession,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    UserInputTranscribedEvent,
)

from .events import EventPublisher, agent_state_event, to_wire_state, transcript_event
from .latency import LatencyLogger


def wire_session_events(
    session: AgentSession, publisher: EventPublisher, latency: LatencyLogger
) -> None:
    """Subscribe to session events and forward them per docs/DESIGN.md §Interfaces.

    - agent_state_changed -> {"type":"agent_state",...} + latency turn tracking
      ("thinking" opens a turn, "speaking" closes it with the JSONL record).
    - user_input_transcribed -> {"type":"transcript","role":"user",...}
    - conversation_item_added (assistant messages) -> {"type":"transcript",
      "role":"assistant","final":true} (assistant turns are only added to the
      chat context once complete, so this is always final).
    """

    def _on_agent_state_changed(ev: AgentStateChangedEvent) -> None:
        if ev.new_state == "thinking":
            latency.turn_ended(ts=ev.created_at)
        elif ev.new_state == "speaking":
            latency.first_audio(ts=ev.created_at)

        wire_state = to_wire_state(ev.new_state)
        if wire_state is not None:
            publisher.publish(agent_state_event(wire_state, ts=ev.created_at))

    def _on_user_input_transcribed(ev: UserInputTranscribedEvent) -> None:
        publisher.publish(transcript_event("user", ev.transcript, final=ev.is_final))

    def _on_conversation_item_added(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if getattr(item, "role", None) != "assistant":
            return
        text = getattr(item, "text_content", None)
        if text:
            publisher.publish(transcript_event("assistant", text, final=True))

    session.on("agent_state_changed", _on_agent_state_changed)
    session.on("user_input_transcribed", _on_user_input_transcribed)
    session.on("conversation_item_added", _on_conversation_item_added)

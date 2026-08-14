"""Wires an AgentSession's events onto the data-channel contract + latency log.

Pure event-driven glue with no LiveKit room dependency, so it is exercised
directly (no network, no real room) in tests and reused unmodified by the
real worker.
"""

from __future__ import annotations

import logging

from livekit.agents import (
    AgentSession,
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)

from jarvis.cadence.interrupt import TurnController

from .events import EventPublisher, agent_state_event, to_wire_state, transcript_event
from .latency import LatencyLogger

logger = logging.getLogger(__name__)

# AgentSession.commit_user_turn() bounds for the cancel-on-stop-phrase path
# below. The transcript that triggered "cancel" is already final by the time
# this fires, so these just bound the (normally near-instant) internal wait
# for the SDK to notice that -- not a meaningful extra delay.
_CANCEL_TRANSCRIPT_TIMEOUT_S = 1.0
_CANCEL_STT_FLUSH_DURATION_S = 0.5


def wire_session_events(
    session: AgentSession,
    publisher: EventPublisher,
    latency: LatencyLogger,
    *,
    turn_controller: TurnController | None = None,
) -> None:
    """Subscribe to session events and forward them per docs/DESIGN.md §Interfaces.

    - agent_state_changed -> {"type":"agent_state",...} + latency turn tracking
      ("thinking" opens a turn, "speaking" closes it with the JSONL record).
    - user_input_transcribed -> {"type":"transcript","role":"user",...}
    - conversation_item_added (assistant messages) -> {"type":"transcript",
      "role":"assistant","final":true} (assistant turns are only added to the
      chat context once complete, so this is always final).

    When ``turn_controller`` (the real T-6 ``jarvis.cadence.interrupt.TurnController``,
    reused as-is) is given, this also feeds it from the same events, per the
    cadence contract pinned in docs/DESIGN.md §Interfaces ("any other speech
    during playback = pause (barge-in)"; stop phrases cancel the turn):

    - ``agent_state_changed`` -> "speaking" marks playback started; any other
      state marks it finished. A transition to "listening" also resets the
      controller, lifting a prior stop phrase's terminal "cancelled" lock so
      the next turn is not permanently silenced.
    - ``user_state_changed`` -> "speaking" marks barge-in start. No SDK call
      follows: a streaming STT's own START_OF_SPEECH already interrupts the
      current SpeechHandle automatically, with no VAD required (confirmed
      against the installed SDK: ``agent_activity.py``'s
      ``_interrupt_by_audio_activity``, driven by ``on_start_of_speech`` under
      the default ``turn_detection="stt"``). By the time this handler runs,
      the SDK has already stopped playback -- ``TurnController`` here is
      purely state bookkeeping so a later stop-phrase check knows whether it
      is looking at mid-speech text.
    - ``user_input_transcribed`` (final) matching a stop phrase ->
      ``session.interrupt()`` (stop whatever is currently playing -- a filler
      or a real answer) plus ``session.commit_user_turn(skip_reply=True,
      ...)`` (flush the stop-phrase utterance into chat history without ever
      generating a reply to it -- confirmed against the installed SDK:
      ``agent_activity.py``'s ``_user_turn_completed_task`` appends the
      message and returns immediately once ``info.skip_reply`` is set,
      never reaching ``generate_reply()``). Both calls are best-effort: a
      failure here must not take down the turn loop, so each is guarded and
      logged rather than left to propagate.
    """

    def _on_agent_state_changed(ev: AgentStateChangedEvent) -> None:
        if ev.new_state == "thinking":
            latency.turn_ended(ts=ev.created_at)
        elif ev.new_state == "speaking":
            latency.first_audio(ts=ev.created_at)

        wire_state = to_wire_state(ev.new_state)
        if wire_state is not None:
            publisher.publish(agent_state_event(wire_state, ts=ev.created_at))

        if turn_controller is not None:
            if ev.new_state == "speaking":
                turn_controller.playback_started()
            else:
                turn_controller.playback_finished()
            if ev.new_state == "listening":
                turn_controller.reset()

    def _on_user_input_transcribed(ev: UserInputTranscribedEvent) -> None:
        publisher.publish(transcript_event("user", ev.transcript, final=ev.is_final))

        if turn_controller is not None and ev.is_final:
            action = turn_controller.user_transcript(ev.transcript)
            if action == "cancel":
                try:
                    session.interrupt()
                except Exception:
                    logger.exception("failed to interrupt playback on stop phrase")
                try:
                    session.commit_user_turn(
                        skip_reply=True,
                        transcript_timeout=_CANCEL_TRANSCRIPT_TIMEOUT_S,
                        stt_flush_duration=_CANCEL_STT_FLUSH_DURATION_S,
                    )
                except Exception:
                    logger.exception("failed to cancel the turn on stop phrase")

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

    if turn_controller is not None:
        # a narrowed local (not `TurnController | None`) so this closure
        # needs no repeated None-check/assert -- only wired at all when a
        # controller was actually given.
        controller = turn_controller

        def _on_user_state_changed(ev: UserStateChangedEvent) -> None:
            if ev.new_state == "speaking":
                controller.user_speech_started()

        session.on("user_state_changed", _on_user_state_changed)

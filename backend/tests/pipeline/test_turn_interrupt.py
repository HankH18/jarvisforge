"""T-15: the real T-6 ``TurnController`` (jarvis.cadence.interrupt) fed from
real ``AgentSession`` events via ``session_wiring.wire_session_events``.

Every test here FAILS against pre-T-15 ``wire_session_events``: it took no
``turn_controller`` parameter at all, so nothing ever called
``TurnController.user_speech_started()``/``.user_transcript()`` and a stop
phrase spoken mid-answer would just become an ordinary next turn -- R4
("barge-in pauses playback; a stop phrase cancels the turn") was unmet.

These drive the *real* session event pub/sub (``session.emit(...)``, the
same public API the SDK's own internals use to fire these events) against a
real, started ``AgentSession`` -- not a hand-rolled fake -- so
``session.interrupt()``/``session.commit_user_turn()`` are exercised for
real. What's synthesized is *when* each event happens (rather than racing
real STT/TTS timing to land a stop-phrase transcript in the narrow window
while synthetic audio is still "playing"), which is exactly what
``session.emit`` and the SDK's own state-event dataclasses are for.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from livekit.agents import (
    Agent,
    AgentSession,
    AgentStateChangedEvent,
    UserInputTranscribedEvent,
    UserStateChangedEvent,
)
from livekit.agents.voice.turn import TurnHandlingOptions
from mocks import MockLLM, MockSTT, MockTTS

from jarvis.cadence.interrupt import TurnController
from jarvis.pipeline.latency import LatencyLogger
from jarvis.pipeline.session_wiring import wire_session_events

SAMPLE_RATE = 16000


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, payload: dict) -> None:
        self.events.append(payload)


@pytest_asyncio.fixture
async def started_session(tmp_path):
    """A real, started AgentSession -- mocked STT/TTS/LLM, no room, manual
    turn detection -- wired with a real TurnController via the real
    wire_session_events. Yields (session, turn_controller, publisher)."""
    session = AgentSession(
        stt=MockSTT(transcript="irrelevant for these tests"),
        tts=MockTTS(sample_rate=SAMPLE_RATE),
        llm=MockLLM(reply="should never be spoken for a cancelled turn"),
        vad=None,
        turn_handling=TurnHandlingOptions(turn_detection="manual"),
    )
    publisher = RecordingPublisher()
    latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="mock-llm")
    turn_controller = TurnController()
    wire_session_events(session, publisher, latency, turn_controller=turn_controller)

    await session.start(agent=Agent(instructions="test agent, no tools"))
    try:
        yield session, turn_controller, publisher
    finally:
        await session.aclose()


@pytest.mark.asyncio
async def test_wire_session_events_subscribes_user_state_changed_only_when_a_controller_is_given(
    tmp_path,
):
    """Regression guard on the opt-in wiring: omitting turn_controller (as
    every pre-T-15 caller, and T-14's/T-1's own tests, already do) must not
    change wire_session_events' subscription set."""
    session = AgentSession(
        stt=MockSTT(),
        tts=MockTTS(sample_rate=SAMPLE_RATE),
        llm=MockLLM(),
        vad=None,
        turn_handling=TurnHandlingOptions(turn_detection="manual"),
    )
    on_calls: list[str] = []
    session.on = lambda event, cb: (on_calls.append(event), None)[1]  # type: ignore[method-assign]

    publisher = RecordingPublisher()
    latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="mock-llm")
    wire_session_events(session, publisher, latency)

    assert set(on_calls) == {
        "agent_state_changed",
        "user_input_transcribed",
        "conversation_item_added",
    }


@pytest.mark.asyncio
async def test_agent_state_speaking_marks_playback_started(started_session):
    session, turn_controller, _ = started_session
    assert turn_controller.state == "idle"

    session.emit(
        "agent_state_changed", AgentStateChangedEvent(old_state="thinking", new_state="speaking")
    )

    assert turn_controller.state == "speaking"


@pytest.mark.asyncio
async def test_user_state_speaking_while_agent_speaking_is_barge_in_pause(started_session):
    """R4, "pause": the SDK has already interrupted playback by the time
    user_state_changed('speaking') reaches our handler (see
    session_wiring.py's docstring) -- TurnController only needs to move to
    "paused" so a subsequent transcript is evaluated correctly."""
    session, turn_controller, _ = started_session

    session.emit(
        "agent_state_changed", AgentStateChangedEvent(old_state="thinking", new_state="speaking")
    )
    assert turn_controller.state == "speaking"

    session.emit(
        "user_state_changed", UserStateChangedEvent(old_state="listening", new_state="speaking")
    )

    assert turn_controller.state == "paused"


@pytest.mark.asyncio
async def test_stop_phrase_while_speaking_interrupts_and_skips_the_reply(started_session):
    """R4, "cancel": a stop phrase's final transcript, arriving while the
    agent is speaking, both stops current playback (session.interrupt) and
    prevents any reply to the stop-phrase utterance itself
    (session.commit_user_turn(skip_reply=True) -- confirmed against the
    installed SDK to never reach generate_reply(), see session_wiring.py)."""
    session, turn_controller, _ = started_session

    interrupt_spy = MagicMock(wraps=session.interrupt)
    commit_spy = MagicMock(wraps=session.commit_user_turn)
    session.interrupt = interrupt_spy
    session.commit_user_turn = commit_spy

    session.emit(
        "agent_state_changed", AgentStateChangedEvent(old_state="thinking", new_state="speaking")
    )
    assert turn_controller.state == "speaking"

    session.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript="stop", is_final=True),
    )
    await asyncio.sleep(0)  # let any fire-and-forget follow-up tasks get scheduled

    assert turn_controller.state == "cancelled"
    interrupt_spy.assert_called_once()
    commit_spy.assert_called_once()
    assert commit_spy.call_args.kwargs["skip_reply"] is True


@pytest.mark.asyncio
async def test_non_stop_final_transcript_while_speaking_does_not_cancel(started_session):
    """Only the pinned stop phrases cancel; ordinary barge-in speech must
    not trip session.interrupt()/commit_user_turn() from this path (the
    SDK's own automatic interruption already handles ordinary barge-in, see
    session_wiring.py's docstring)."""
    session, turn_controller, _ = started_session

    interrupt_spy = MagicMock(wraps=session.interrupt)
    commit_spy = MagicMock(wraps=session.commit_user_turn)
    session.interrupt = interrupt_spy
    session.commit_user_turn = commit_spy

    session.emit(
        "agent_state_changed", AgentStateChangedEvent(old_state="thinking", new_state="speaking")
    )
    session.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript="what's the weather today", is_final=True),
    )
    await asyncio.sleep(0)

    assert turn_controller.state == "paused"
    interrupt_spy.assert_not_called()
    commit_spy.assert_not_called()


@pytest.mark.asyncio
async def test_interim_transcript_of_a_stop_phrase_does_not_cancel(started_session):
    """Only a *final* transcript triggers cancellation -- an interim
    ("is_final=False") stop-phrase-looking fragment must not preempt the
    turn early."""
    session, turn_controller, _ = started_session

    interrupt_spy = MagicMock(wraps=session.interrupt)
    session.interrupt = interrupt_spy

    session.emit(
        "agent_state_changed", AgentStateChangedEvent(old_state="thinking", new_state="speaking")
    )
    session.emit(
        "user_input_transcribed",
        UserInputTranscribedEvent(transcript="stop", is_final=False),
    )
    await asyncio.sleep(0)

    assert turn_controller.state == "speaking"
    interrupt_spy.assert_not_called()


@pytest.mark.asyncio
async def test_transition_to_listening_resets_a_cancelled_controller(started_session):
    """A prior stop phrase's terminal "cancelled" lock must not silence
    every future turn: the next "listening" period (a fresh turn beginning)
    lifts it."""
    session, turn_controller, _ = started_session

    session.emit(
        "agent_state_changed", AgentStateChangedEvent(old_state="thinking", new_state="speaking")
    )
    session.emit(
        "user_input_transcribed", UserInputTranscribedEvent(transcript="stop", is_final=True)
    )
    await asyncio.sleep(0)
    assert turn_controller.state == "cancelled"

    session.emit(
        "agent_state_changed", AgentStateChangedEvent(old_state="speaking", new_state="listening")
    )

    assert turn_controller.state == "idle"

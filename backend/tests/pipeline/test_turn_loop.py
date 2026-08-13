"""End-to-end synthetic turn through AgentSession with mocked STT/TTS/LLM.

No LiveKit room, no network, no real API keys: `session.input.audio` /
`session.output.audio` are set directly (a documented AgentSession path --
RoomIO is optional, see `AgentSession.start()`), and turn detection is
"manual" so the test drives the turn deterministically via
`session.commit_user_turn()` instead of relying on wall-clock endpointing.

Covers acceptance #1 (synthetic audio turn completes, mocked providers, no
network), #3 (agent_state ordering listening->thinking->speaking) and #5
(per-turn latency JSONL).
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
import pytest_asyncio
from livekit import rtc
from livekit.agents import Agent, AgentSession, io
from livekit.agents.utils.audio import silence_frame
from livekit.agents.voice.turn import TurnHandlingOptions
from mocks import MockLLM, MockSTT, MockTTS

from jarvis.pipeline.latency import LatencyLogger
from jarvis.pipeline.session_wiring import wire_session_events

SAMPLE_RATE = 16000


class RecordingPublisher:
    """Test EventPublisher: records payloads instead of touching a room."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, payload: dict) -> None:
        self.events.append(payload)


class QueueAudioInput(io.AudioInput):
    """Feeds synthetic frames pushed by the test into the STT pipeline."""

    def __init__(self) -> None:
        super().__init__(label="test-audio-in")
        self._queue: asyncio.Queue[rtc.AudioFrame] = asyncio.Queue()

    def push(self, frame: rtc.AudioFrame) -> None:
        self._queue.put_nowait(frame)

    async def __anext__(self) -> rtc.AudioFrame:
        return await self._queue.get()


class CollectingAudioOutput(io.AudioOutput):
    """Collects synthesized frames and finishes playout immediately, so the
    turn completes without waiting on real-time audio playback."""

    def __init__(self) -> None:
        super().__init__(
            label="test-audio-out", capabilities=io.AudioOutputCapabilities(pause=False)
        )
        self.frames: list[rtc.AudioFrame] = []

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)
        if not self.frames:
            # a real sink reports this once playback of the segment begins;
            # AgentSession's agent_state="speaking" transition is driven by
            # this event (see agent_activity.py's `_on_first_frame`), not by
            # capture_frame itself.
            self.on_playback_started(created_at=time.time())
        self.frames.append(frame)

    def flush(self) -> None:
        super().flush()
        self.on_playback_finished(playback_position=0.0, interrupted=False)

    def clear_buffer(self) -> None:
        pass


async def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.02) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(interval)

    await asyncio.wait_for(_poll(), timeout=timeout)


@pytest_asyncio.fixture
async def wired_session(tmp_path):
    """A fully-mocked AgentSession, wired to a RecordingPublisher + a
    LatencyLogger writing into tmp_path, with no room and manual turn
    detection for deterministic driving."""
    session = AgentSession(
        stt=MockSTT(transcript="what's on my calendar"),
        tts=MockTTS(sample_rate=SAMPLE_RATE),
        llm=MockLLM(reply="Nothing scheduled today."),
        vad=None,
        turn_handling=TurnHandlingOptions(turn_detection="manual"),
    )
    publisher = RecordingPublisher()
    latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="mock-llm")
    wire_session_events(session, publisher, latency)

    audio_in = QueueAudioInput()
    audio_out = CollectingAudioOutput()
    session.input.audio = audio_in
    session.output.audio = audio_out

    return session, publisher, latency, audio_in, audio_out


async def _run_one_turn(session, audio_in) -> None:
    await session.start(agent=Agent(instructions="test agent, no tools"))
    try:
        for _ in range(3):
            audio_in.push(silence_frame(0.1, SAMPLE_RATE))
        await asyncio.sleep(0.05)  # let the frames reach the (mocked) STT

        transcript_fut = session.commit_user_turn(transcript_timeout=2.0)
        await asyncio.wait_for(transcript_fut, timeout=2.0)
    finally:
        pass


@pytest.mark.asyncio
async def test_synthetic_turn_completes_with_mocked_providers(wired_session):
    """Acceptance #1: a synthetic audio turn completes end-to-end (STT ->
    LLM -> TTS) with mocked providers and no network."""
    session, publisher, latency, audio_in, audio_out = wired_session

    await _run_one_turn(session, audio_in)

    await _wait_until(lambda: len(audio_out.frames) > 0)
    await audio_out.wait_for_playout()
    await session.aclose()

    assert audio_out.frames, "TTS never produced any audio for the reply"

    transcripts = [e for e in publisher.events if e["type"] == "transcript"]
    assert any(
        e["role"] == "user" and e["text"] == "what's on my calendar" for e in transcripts
    ), transcripts
    assert any(
        e["role"] == "assistant" and e["text"] == "Nothing scheduled today." for e in transcripts
    ), transcripts


@pytest.mark.asyncio
async def test_agent_state_transitions_are_ordered(wired_session):
    """Acceptance #3: listening -> thinking -> speaking, in that order."""
    session, publisher, latency, audio_in, audio_out = wired_session

    await _run_one_turn(session, audio_in)
    await _wait_until(lambda: len(audio_out.frames) > 0)
    await audio_out.wait_for_playout()
    await session.aclose()

    states = [e["state"] for e in publisher.events if e["type"] == "agent_state"]
    assert states, "no agent_state events were published"

    # every event uses the pinned vocabulary only
    assert set(states) <= {"listening", "thinking", "working", "speaking"}

    thinking_idx = states.index("thinking")
    speaking_idx = states.index("speaking")
    assert thinking_idx < speaking_idx
    # listening must precede thinking (the user's turn was being heard first)
    listening_before_thinking = [s for s in states[:thinking_idx] if s == "listening"]
    assert listening_before_thinking, states


@pytest.mark.asyncio
async def test_latency_jsonl_has_one_line_per_turn(wired_session):
    """Acceptance #5: per-turn latency JSONL with the pinned fields."""
    session, publisher, latency, audio_in, audio_out = wired_session

    await _run_one_turn(session, audio_in)
    await _wait_until(lambda: len(audio_out.frames) > 0)
    await audio_out.wait_for_playout()
    await session.aclose()

    lines = latency.path.read_text().strip().splitlines()
    assert len(lines) == 1

    record = json.loads(lines[0])
    assert set(record) == {"turn_id", "turn_end_ts", "first_audio_ts", "tool_ms", "model"}
    assert record["turn_id"] == 1
    assert record["first_audio_ts"] >= record["turn_end_ts"]
    assert record["tool_ms"] == 0.0
    assert record["model"] == "mock-llm"

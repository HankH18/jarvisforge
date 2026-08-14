"""T-15: the real T-6 cadence/ package wired into pipeline/ -- specifically
into ``tools_wiring._dispatch`` via an optional ``FillerGate``, and adapted
to the SDK by ``jarvis.pipeline.cadence_wiring``.

T-6 built cadence/ (69 passing tests) but pipeline/ never imported it: every
test in this file FAILS against that pre-T-15 code, for two independent
reasons that both had to be fixed together:

1. ``JarvisAgent``/``livekit_tools``/``_make_dispatcher`` took no ``gate`` at
   all -- there was nowhere to plug a ``FillerGate`` in.
2. Even if one were plugged in, ``_dispatch`` called ``spec.handler(**raw_arguments)``
   directly inline -- a blocking call with no ``await`` inside an ``async
   def``. Since asyncio is single-threaded and cooperative, that blocks the
   *entire event loop* for the call's full duration, so ``FillerGate.guard``'s
   own timer task (an ``asyncio.create_task``) could never get scheduled
   until after the blocking call had already returned. The fix is
   ``asyncio.to_thread(spec.handler, ...)``, present in every test's setup
   below implicitly (it's what makes the "slow" tests even able to observe a
   filler at all).
"""

from __future__ import annotations

import asyncio
import time
import types
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from livekit import rtc
from livekit.agents import AgentSession, io
from livekit.agents.llm import StopResponse
from livekit.agents.utils.audio import silence_frame
from livekit.agents.voice.turn import TurnHandlingOptions
from mocks import MockLLM, MockSTT, MockTTS

from jarvis.cadence.fillers import FillerBank, FillerClip
from jarvis.cadence.gate import FillerGate
from jarvis.cadence.policy import FILLER_START_DEADLINE_MS, FILLER_THRESHOLD_MS
from jarvis.llm.types import REFUSAL, ToolResult
from jarvis.pipeline.agent import JarvisAgent
from jarvis.pipeline.cadence_wiring import load_filler_bank, make_filler_player
from jarvis.pipeline.latency import LatencyLogger
from jarvis.pipeline.session_wiring import wire_session_events
from jarvis.pipeline.tools_wiring import _make_dispatcher
from jarvis.tools.registry import ToolRegistry, ToolSpec

SAMPLE_RATE = 16000


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, payload: dict) -> None:
        self.events.append(payload)


class RecordingModelClient:
    def __init__(self, *, reply: str = "grounded answer") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def complete(self, *, model: str, system: str, user: str) -> str:
        self.calls.append({"model": model, "system": system, "user": user})
        return self.reply


class QueueAudioInput(io.AudioInput):
    def __init__(self) -> None:
        super().__init__(label="test-audio-in")
        self._queue: asyncio.Queue[rtc.AudioFrame] = asyncio.Queue()

    def push(self, frame: rtc.AudioFrame) -> None:
        self._queue.put_nowait(frame)

    async def __anext__(self) -> rtc.AudioFrame:
        return await self._queue.get()


class CollectingAudioOutput(io.AudioOutput):
    """Collects every synthesized/played frame and records the wall-clock
    time of the very first one -- across the whole test, a filler always
    plays (if it plays at all) before the tool-call-triggered final answer
    even starts (dispatch doesn't call ``session.say(answer, ...)`` until
    the tool call, which the filler's timer races, actually returns), so
    "first frame" is unambiguously the filler's.
    """

    def __init__(self) -> None:
        super().__init__(
            label="test-audio-out", capabilities=io.AudioOutputCapabilities(pause=False)
        )
        self.frames: list[rtc.AudioFrame] = []
        self.first_frame_at: float | None = None

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)
        if not self.frames:
            self.on_playback_started(created_at=time.time())
            self.first_frame_at = time.time()
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


@pytest.fixture(autouse=True)
def _model_routing_env(monkeypatch):
    # jarvis.llm.routing._require_env is a hard error when unset (SPEC C3).
    monkeypatch.setenv("CONVO_MODEL", "claude-convo-test")
    monkeypatch.setenv("TOOL_MODEL", "claude-tool-test")


def _blocking_spec(*, name: str, heavy: bool, delay_s: float) -> ToolSpec:
    """A synthetic tool whose handler blocks the *calling thread* for
    ``delay_s`` -- like the real (measured 1.6-14.4s) weather/github
    handlers, but deterministic and network-free. Blocking, not
    ``asyncio.sleep``, on purpose: this is what would starve the event loop
    if ``_dispatch`` still called it inline instead of via
    ``asyncio.to_thread``.
    """

    def handler(**_: object) -> ToolResult:
        time.sleep(delay_s)
        return ToolResult(
            source=name, fetched_at=datetime.now(UTC).isoformat(), data={"ok": True}, error=None
        )

    return ToolSpec(
        name=name,
        description="synthetic tool for T-15 cadence-wiring tests",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=handler,
        heavy=heavy,
    )


@pytest_asyncio.fixture
async def driven_turn(tmp_path):
    """Starts a real AgentSession + a real JarvisAgent (one synthetic tool,
    a real FillerGate built from the real committed assets) with mocked
    STT/TTS/LLM, drives one synthetic turn, and hands back the audio sink,
    the exact spoken assistant transcript, the publisher, and the gate
    (so tests can inspect exactly which/how many clips played).
    """
    sessions: list[AgentSession] = []

    async def _drive(
        *,
        spec: ToolSpec,
        threshold_ms: float,
        reply: str = "grounded answer",
    ):
        registry = ToolRegistry()
        registry.register(spec)

        session = AgentSession(
            stt=MockSTT(transcript=f"please use {spec.name}"),
            tts=MockTTS(sample_rate=SAMPLE_RATE),
            llm=MockLLM(tool_call=(spec.name, {})),
            vad=None,
            turn_handling=TurnHandlingOptions(turn_detection="manual"),
        )
        sessions.append(session)

        publisher = RecordingPublisher()
        latency = LatencyLogger(path=tmp_path / f"latency-{len(sessions)}.jsonl", model="mock-llm")
        wire_session_events(session, publisher, latency)

        bank = load_filler_bank()
        assert bank is not None, "the real committed filler assets must be present for this test"
        gate = FillerGate(
            bank, make_filler_player(session), threshold_ms=threshold_ms, publisher=publisher
        )

        client = RecordingModelClient(reply=reply)
        agent = JarvisAgent(
            registry=registry, prefs_getter=lambda: [], model_client=client, filler_gate=gate
        )

        audio_in = QueueAudioInput()
        audio_out = CollectingAudioOutput()
        session.input.audio = audio_in
        session.output.audio = audio_out

        await session.start(agent=agent)
        for _ in range(3):
            audio_in.push(silence_frame(0.1, SAMPLE_RATE))
        await asyncio.sleep(0.05)

        start = time.time()
        transcript_fut = session.commit_user_turn(transcript_timeout=2.0)
        await asyncio.wait_for(transcript_fut, timeout=2.0)

        await _wait_until(lambda: len(audio_out.frames) > 0, timeout=5.0)
        await audio_out.wait_for_playout()

        def _assistant_texts() -> list[str]:
            return [
                e["text"]
                for e in publisher.events
                if e["type"] == "transcript" and e["role"] == "assistant"
            ]

        await _wait_until(lambda: len(_assistant_texts()) > 0, timeout=5.0)
        return {
            "audio_out": audio_out,
            "assistant_texts": _assistant_texts(),
            "publisher": publisher,
            "client": client,
            "gate": gate,
            "start": start,
        }

    yield _drive

    for session in sessions:
        await session.aclose()


@pytest.mark.asyncio
async def test_slow_heavy_tool_speaks_exactly_one_filler_and_working_state(driven_turn):
    """Acceptance: a heavy tool call that outlives the (test) threshold gets
    exactly one spoken filler, and the publisher sees exactly one
    ``agent_state: working`` event -- R2's "spoken acknowledgment within
    300ms when a lookup will be slow", assembled end-to-end. Fails against
    pre-T-15 code (no gate wiring at all, and even wired the blocking inline
    call would starve the timer -- see module docstring)."""
    spec = _blocking_spec(name="slow_heavy_tool", heavy=True, delay_s=0.15)
    result = await driven_turn(spec=spec, threshold_ms=20)

    assert len(result["gate"].played) == 1
    working_events = [
        e
        for e in result["publisher"].events
        if e["type"] == "agent_state" and e["state"] == "working"
    ]
    assert len(working_events) == 1, result["publisher"].events

    # the real answer still comes from the ToolResult data, not lost to the
    # filler (T-14 regression check: tools must still fire, answers must
    # still be spoken).
    assert result["assistant_texts"] == ["grounded answer"], result["assistant_texts"]
    assert result["client"].calls, "synthesize_grounded was never called"


@pytest.mark.asyncio
async def test_fast_heavy_tool_speaks_zero_fillers(driven_turn):
    """A heavy tool that finishes well under the threshold gets no filler at
    all -- the gate must not fire just because the tool is heavy."""
    spec = _blocking_spec(name="fast_heavy_tool", heavy=True, delay_s=0.0)
    result = await driven_turn(spec=spec, threshold_ms=200)

    assert result["gate"].played == []
    working_events = [
        e
        for e in result["publisher"].events
        if e["type"] == "agent_state" and e["state"] == "working"
    ]
    assert working_events == []
    assert result["assistant_texts"] == ["grounded answer"], result["assistant_texts"]


@pytest.mark.asyncio
async def test_slow_non_heavy_tool_speaks_zero_fillers(driven_turn):
    """Per the pinned ``ToolSpec.heavy``/filler contract (docs/DESIGN.md
    §Interfaces), only `heavy` tools are filler-eligible, no matter how
    slow a non-heavy one runs."""
    spec = _blocking_spec(name="slow_non_heavy_tool", heavy=False, delay_s=0.15)
    result = await driven_turn(spec=spec, threshold_ms=20)

    assert result["gate"].played == []
    assert result["assistant_texts"] == ["grounded answer"], result["assistant_texts"]


@pytest.mark.asyncio
async def test_filler_starts_within_the_pinned_deadline_of_the_gate_firing(driven_turn):
    """Uses the real pinned FILLER_THRESHOLD_MS (500) and
    FILLER_START_DEADLINE_MS (300), not a test-shortened one: the filler's
    first audio frame must land within 300ms of the 500ms threshold
    elapsing -- that is the whole point of *pre-recorded* clips (no TTS
    synthesis latency standing between "decided to play" and audio out)."""
    spec = _blocking_spec(name="deadline_tool", heavy=True, delay_s=1.2)
    result = await driven_turn(spec=spec, threshold_ms=FILLER_THRESHOLD_MS)

    assert len(result["gate"].played) == 1
    assert result["audio_out"].first_frame_at is not None

    elapsed = result["audio_out"].first_frame_at - result["start"]
    deadline = (FILLER_THRESHOLD_MS + FILLER_START_DEADLINE_MS) / 1000
    # generous slack for STT-roundtrip/scheduler jitter in CI, while still
    # meaningfully enforcing the pinned deadline (not the full 1.2s handler delay)
    assert elapsed < deadline + 0.25, (elapsed, deadline)


@pytest.mark.asyncio
async def test_no_gate_still_dispatches_and_speaks_the_grounded_answer(tmp_path):
    """``filler_gate=None`` (the missing-assets degrade path) must not break
    tool dispatch: the tool still fires and the answer is still spoken --
    only fillers are skipped."""
    spec = _blocking_spec(name="no_gate_tool", heavy=True, delay_s=0.05)

    registry = ToolRegistry()
    registry.register(spec)
    session = AgentSession(
        stt=MockSTT(transcript="please use no_gate_tool"),
        tts=MockTTS(sample_rate=SAMPLE_RATE),
        llm=MockLLM(tool_call=(spec.name, {})),
        vad=None,
        turn_handling=TurnHandlingOptions(turn_detection="manual"),
    )
    try:
        publisher = RecordingPublisher()
        latency = LatencyLogger(path=tmp_path / "latency.jsonl", model="mock-llm")
        wire_session_events(session, publisher, latency)

        client = RecordingModelClient(reply="grounded answer")
        agent = JarvisAgent(
            registry=registry, prefs_getter=lambda: [], model_client=client, filler_gate=None
        )

        audio_in = QueueAudioInput()
        audio_out = CollectingAudioOutput()
        session.input.audio = audio_in
        session.output.audio = audio_out

        await session.start(agent=agent)
        for _ in range(3):
            audio_in.push(silence_frame(0.1, SAMPLE_RATE))
        await asyncio.sleep(0.05)

        transcript_fut = session.commit_user_turn(transcript_timeout=2.0)
        await asyncio.wait_for(transcript_fut, timeout=2.0)

        await _wait_until(lambda: len(audio_out.frames) > 0, timeout=5.0)
        await audio_out.wait_for_playout()

        def _assistant_texts() -> list[str]:
            return [
                e["text"]
                for e in publisher.events
                if e["type"] == "transcript" and e["role"] == "assistant"
            ]

        await _wait_until(lambda: len(_assistant_texts()) > 0, timeout=5.0)
        assert _assistant_texts() == ["grounded answer"]
    finally:
        await session.aclose()


# ---- T-17: a failed filler must not turn a lookup into a REFUSAL -----------


class _FakeToolSession:
    """Minimal ``RunContext.session`` double: just the surface
    ``tools_wiring._dispatch`` actually touches -- ``.history.items`` (for
    ``_last_user_utterance``) and ``.say(...)`` (the final spoken answer) --
    no real AgentSession/audio pipeline needed to drive the dispatcher and
    prove the guard()/tools_wiring integration in isolation.
    """

    def __init__(self, *, transcript: str) -> None:
        self.history = types.SimpleNamespace(
            items=[types.SimpleNamespace(role="user", text_content=transcript)]
        )
        self.say_calls: list[dict] = []

    def say(self, text, *, allow_interruptions=None):
        self.say_calls.append({"text": text, "allow_interruptions": allow_interruptions})


class _FakeRunContext:
    def __init__(self, session: _FakeToolSession) -> None:
        self.session = session


@pytest.mark.asyncio
async def test_a_failed_filler_does_not_turn_a_successful_lookup_into_a_refusal():
    """T-17 acceptance #3: integration test through tools_wiring. Before the
    guard() fix, a raising filler player's exception replaced the guarded
    span's return value inside ``_dispatch`` -- the real ``ToolResult`` the
    handler produced never survived to reach ``synthesize_grounded`` at all.
    After the fix, that ``ToolResult`` (non-empty ``data``, ``error=None``)
    reaches the REAL ``synthesize_grounded`` (its ``has_supporting_evidence``
    structural gate included, not mocked) and the grounded answer is spoken
    -- not REFUSAL -- even though the filler blew up along the way.
    """
    spec = _blocking_spec(name="flaky_filler_tool", heavy=True, delay_s=0.05)

    async def _raising_player(clip: FillerClip) -> None:
        raise RuntimeError("TTS hiccup")

    bank = FillerBank(
        [FillerClip(slug="x", text="One sec.", path=Path("unused.wav"), duration_ms=400)]
    )
    # Real asyncio.sleep (no VirtualClock here): threshold_ms is well under
    # the guarded span's real duration (handler delay_s=0.05 alone) so the
    # timer reliably fires and the raising player reliably runs.
    gate = FillerGate(bank, _raising_player, threshold_ms=5)

    client = RecordingModelClient(reply="octocat/Hello-World has 42 stars.")
    dispatch = _make_dispatcher(spec, prefs_getter=lambda: [], model_client=client, gate=gate)

    session = _FakeToolSession(transcript="please use flaky_filler_tool")
    ctx = _FakeRunContext(session)

    with pytest.raises(StopResponse):
        await dispatch({}, ctx)

    # sanity: the filler actually fired and failed -- otherwise this test
    # would pass vacuously without ever exercising the T-17 fix.
    assert len(gate.played) == 1, "the filler never fired; widen the timing margin"

    assert session.say_calls == [
        {"text": "octocat/Hello-World has 42 stars.", "allow_interruptions": True}
    ]
    assert session.say_calls[0]["text"] != REFUSAL

    # The REAL synthesize_grounded's structural no-evidence gate must have
    # seen the handler's real ToolResult (data={"ok": True}, error=None) --
    # proven by it actually reaching the model with that data in the prompt,
    # not refusing before ever calling it.
    assert client.calls, "synthesize_grounded never reached the model -- refused instead"
    assert '"ok": true' in client.calls[0]["user"].lower()


# ---- jarvis.pipeline.cadence_wiring, directly -------------------------------


def test_load_filler_bank_loads_the_real_committed_assets():
    bank = load_filler_bank()
    assert bank is not None
    assert len(bank) == 6  # backend/assets/fillers/manifest.json


def test_load_filler_bank_degrades_to_none_when_assets_are_missing(monkeypatch):
    import jarvis.pipeline.cadence_wiring as cadence_wiring

    def _raise(*_args, **_kwargs):
        raise FileNotFoundError("no manifest here")

    monkeypatch.setattr(cadence_wiring.FillerBank, "from_manifest", _raise)

    assert cadence_wiring.load_filler_bank() is None


def test_build_filler_gate_returns_none_when_assets_are_missing(monkeypatch):
    import jarvis.pipeline.cadence_wiring as cadence_wiring

    monkeypatch.setattr(cadence_wiring, "load_filler_bank", lambda: None)

    assert cadence_wiring.build_filler_gate(session=object()) is None


class _FakeSessionForPlayer:
    """Records exactly what ``session.say()`` was called with -- proves the
    filler player plays pre-recorded audio, not re-synthesized TTS text,
    without needing a real AgentSession/audio pipeline."""

    def __init__(self) -> None:
        self.say_calls: list[dict] = []

    def say(self, text, *, audio=None, allow_interruptions=None, add_to_chat_ctx=None):
        self.say_calls.append(
            {
                "text": text,
                "audio": audio,
                "allow_interruptions": allow_interruptions,
                "add_to_chat_ctx": add_to_chat_ctx,
            }
        )


@pytest.mark.asyncio
async def test_filler_player_speaks_pre_recorded_audio_not_re_synthesized_text():
    bank = load_filler_bank()
    assert bank is not None
    clip: FillerClip = bank.next()

    fake_session = _FakeSessionForPlayer()
    player = make_filler_player(fake_session)
    await player(clip)

    assert len(fake_session.say_calls) == 1
    call = fake_session.say_calls[0]
    assert call["text"] == clip.text
    assert call["audio"] is not None
    assert hasattr(call["audio"], "__anext__"), "audio must be an AsyncIterable of frames"
    assert call["allow_interruptions"] is True
    assert call["add_to_chat_ctx"] is False  # fillers never enter chat history

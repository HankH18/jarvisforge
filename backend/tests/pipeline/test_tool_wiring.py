"""T-14: the real tool registry wired into a real AgentSession.

Drives a full synthetic turn (mocked STT/TTS/LLM, same doubles as
test_turn_loop.py) through the REAL production pieces underneath: the real
JarvisAgent/tools_wiring adaptation, the real ToolRegistry with the real
WEATHER_TOOL/GITHUB_QUERY_TOOL specs (T-3/T-4, only their outbound HTTP is
faked via respx -- the tool handlers themselves run unmodified), the real
jarvis.llm.routing.route_model escalation decision, and the real
jarvis.llm.synthesis.synthesize_grounded grounding/refusal gate (only its
Anthropic network boundary is faked, via the same ModelClient injection seam
llm/synthesis.py already defines for this purpose).

Every test here FAILS against the pre-T-14 wiring: ``JarvisAgent()`` took no
``tools=`` at all and ``pipeline/worker.py`` never referenced
``jarvis.tools.registry`` or ``jarvis.llm.synthesis``, so a tool-call chunk
from the LLM had nothing to dispatch to and no answer could ever be grounded
in a ``ToolResult``.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest
import pytest_asyncio
import respx
from livekit import rtc
from livekit.agents import AgentSession, io
from livekit.agents.utils.audio import silence_frame
from livekit.agents.voice.turn import TurnHandlingOptions
from mocks import MockLLM, MockSTT, MockTTS

from jarvis.llm.types import REFUSAL
from jarvis.pipeline.agent import JarvisAgent
from jarvis.pipeline.latency import LatencyLogger
from jarvis.pipeline.session_wiring import wire_session_events
from jarvis.pipeline.tools_wiring import LazyMemoryStore, build_registry
from jarvis.tools.github.client import GRAPHQL_ENDPOINT
from jarvis.tools.weather.service import FORECAST_URL, GEOCODING_URL

SAMPLE_RATE = 16000


class RecordingPublisher:
    """Test EventPublisher: records payloads instead of touching a room --
    reused from test_turn_loop.py's pattern to read back the exact spoken
    text (audio frames alone carry no text)."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(self, payload: dict) -> None:
        self.events.append(payload)


class RecordingModelClient:
    """Returns a canned reply and records exactly what it was asked --
    self-contained (not shared with tests/tools or tests/grounding) since
    this ticket's file scope is backend/tests/pipeline/** only.
    """

    def __init__(self, *, reply: str = "grounded answer") -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def complete(self, *, model: str, system: str, user: str) -> str:
        self.calls.append({"model": model, "system": system, "user": user})
        return self.reply


class ExplodingModelClient:
    """Raises if ever called -- proves the refusal path never reaches a model."""

    def complete(self, *, model: str, system: str, user: str) -> str:
        raise AssertionError("model client should not be called on this path")


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
    """Collects synthesized frames and finishes playout immediately."""

    def __init__(self) -> None:
        super().__init__(
            label="test-audio-out", capabilities=io.AudioOutputCapabilities(pause=False)
        )
        self.frames: list[rtc.AudioFrame] = []

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)
        if not self.frames:
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


@pytest.fixture(autouse=True)
def _model_routing_env(monkeypatch):
    # jarvis.llm.routing._require_env is a hard error when unset (SPEC C3);
    # distinct values so a test can tell which one a call actually used.
    monkeypatch.setenv("CONVO_MODEL", "claude-convo-test")
    monkeypatch.setenv("TOOL_MODEL", "claude-tool-test")


def _build_agent(*, model_client) -> JarvisAgent:
    """The real production registry (weather/github/self_manifest/memory,
    see tools_wiring.build_registry) wired into a real JarvisAgent. The
    memory store is a LazyMemoryStore with no DATABASE_URL, so it degrades
    to unavailable without touching a network -- irrelevant to these tests,
    which never call the memory tool.
    """
    memory = LazyMemoryStore(database_url=None)
    registry = build_registry(memory=memory, user_id_getter=lambda: "test-user")
    return JarvisAgent(registry=registry, prefs_getter=lambda: [], model_client=model_client)


@pytest_asyncio.fixture
async def driven_turn(tmp_path):
    """Starts a real AgentSession + the given agent with mocked STT/TTS/LLM
    and no room (manual turn detection), and returns a function that drives
    one synthetic turn, waits for spoken audio, and hands back both the
    audio sink and the exact assistant transcript text (via the same
    session_wiring.wire_session_events the real worker uses).
    """
    sessions: list[AgentSession] = []

    async def _drive(*, transcript: str, tool_call: tuple[str, dict], agent: JarvisAgent):
        session = AgentSession(
            stt=MockSTT(transcript=transcript),
            tts=MockTTS(sample_rate=SAMPLE_RATE),
            llm=MockLLM(tool_call=tool_call),
            vad=None,
            turn_handling=TurnHandlingOptions(turn_detection="manual"),
        )
        sessions.append(session)

        publisher = RecordingPublisher()
        latency = LatencyLogger(path=tmp_path / f"latency-{len(sessions)}.jsonl", model="mock-llm")
        wire_session_events(session, publisher, latency)

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

        # conversation_item_added (which publishes the assistant transcript,
        # see session_wiring.py) fires when the item lands in chat_ctx, which
        # is not guaranteed to happen strictly before playout finishes -- it
        # races under load. Audio already proves speech happened; poll
        # separately for the transcript text instead of assuming ordering.
        await _wait_until(lambda: len(_assistant_texts()) > 0, timeout=5.0)
        return audio_out, _assistant_texts()

    yield _drive

    for session in sessions:
        await session.aclose()


def test_jarvis_agent_attaches_a_nonempty_real_tool_set():
    """Acceptance #1: AgentSession/Agent is constructed with the tool
    registry attached, not a bare chatbot."""
    agent = _build_agent(model_client=ExplodingModelClient())

    assert agent.tools, "JarvisAgent was built with zero tools"
    names = {t.info.name for t in agent.tools}
    assert names == {
        "get_weather",
        "github_query",
        "load_repo_digest",
        "self_manifest",
        "recall_memory",
    }


@pytest.mark.asyncio
@respx.mock
async def test_weather_question_invokes_get_weather_and_speaks_grounded_data(driven_turn):
    """Acceptance #2: a weather question invokes the real get_weather tool
    (its HTTP calls are observed below) and the spoken answer comes from
    synthesize_grounded fed that ToolResult's data -- not the CONVO_MODEL's
    own free-text continuation (that path is proven cut off: MockLLM only
    ever emits the tool-call chunk, never a text reply, for this turn)."""
    geocode_route = respx.get(GEOCODING_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "name": "London",
                        "country": "United Kingdom",
                        "latitude": 51.5,
                        "longitude": -0.12,
                    }
                ]
            },
        )
    )
    forecast_route = respx.get(FORECAST_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "timezone": "Europe/London",
                "current": {"temperature_2m": 15.2, "weather_code": 3},
                "current_units": {"temperature_2m": "°C"},
            },
        )
    )

    reply = "It's 15.2 degrees and cloudy in London right now."
    client = RecordingModelClient(reply=reply)
    agent = _build_agent(model_client=client)

    audio_out, assistant_texts = await driven_turn(
        transcript="what's the weather in london right now",
        tool_call=("get_weather", {"location": "London", "when": "now"}),
        agent=agent,
    )

    assert geocode_route.called, "the real get_weather handler never hit Open-Meteo geocoding"
    assert forecast_route.called, "the real get_weather handler never hit Open-Meteo forecast"

    assert audio_out.frames, "no spoken audio was produced for the grounded answer"
    assert assistant_texts == [reply], assistant_texts  # spoken text is exactly the grounded answer

    assert client.calls, "synthesize_grounded never called the model with the tool's data"
    call = client.calls[0]
    assert call["model"] == "claude-convo-test"  # non-heavy tool: stays on CONVO_MODEL
    assert "15.2" in call["user"]  # the real ToolResult's data reached the prompt
    assert "London" in call["user"]


@pytest.mark.asyncio
@respx.mock
async def test_heavy_github_tool_escalates_to_tool_model(driven_turn):
    """Acceptance #3: firing a `heavy` tool (github_query) escalates the
    answer step's model to $TOOL_MODEL, unlike the weather (non-heavy) case
    above which stayed on $CONVO_MODEL."""
    respx.post(GRAPHQL_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "repository": {
                        "name": "Hello-World",
                        "nameWithOwner": "octocat/Hello-World",
                        "description": "My first repository on GitHub!",
                        "isPrivate": False,
                        "stargazerCount": 42,
                        "forkCount": 7,
                        "primaryLanguage": {"name": "Python"},
                        "defaultBranchRef": {"name": "main", "target": {"oid": "abc123"}},
                    }
                }
            },
        )
    )

    reply = "octocat/Hello-World has 42 stars."
    client = RecordingModelClient(reply=reply)
    agent = _build_agent(model_client=client)

    audio_out, assistant_texts = await driven_turn(
        transcript="how many stars does octocat/Hello-World have",
        tool_call=(
            "github_query",
            {"url": "https://github.com/octocat/Hello-World", "kind": "repo_meta", "args": {}},
        ),
        agent=agent,
    )

    assert audio_out.frames, "no spoken audio was produced for the grounded answer"
    assert assistant_texts == [reply], assistant_texts
    assert client.calls, "synthesize_grounded never called the model"
    assert client.calls[0]["model"] == "claude-tool-test"  # heavy tool: escalated to TOOL_MODEL
    assert "42" in client.calls[0]["user"]  # the real ToolResult's data reached the prompt


@pytest.mark.asyncio
@respx.mock
async def test_refusal_holds_when_the_tool_has_no_supporting_data(driven_turn):
    """Acceptance #4: a question no tool result supports still returns the
    REFUSAL prefix -- and never reaches the model at all (ExplodingModelClient
    proves the structural no-evidence gate in synthesize_grounded, not a
    model choosing to refuse, is what fires)."""
    # unknown location -> get_weather returns ToolResult.error set, data empty
    respx.get(GEOCODING_URL).mock(return_value=httpx.Response(200, json={"results": []}))

    agent = _build_agent(model_client=ExplodingModelClient())

    audio_out, assistant_texts = await driven_turn(
        transcript="what's the weather in nowhereville",
        tool_call=("get_weather", {"location": "Nowhereville", "when": "now"}),
        agent=agent,
    )

    assert audio_out.frames, "REFUSAL should still be spoken, not silently dropped"
    assert assistant_texts == [REFUSAL], assistant_texts

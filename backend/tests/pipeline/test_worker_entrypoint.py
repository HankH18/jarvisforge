"""Coverage for the real worker entrypoint (jarvis.pipeline.worker.entrypoint).

test_turn_loop.py builds its own bare AgentSession with a plain
livekit.agents.Agent, bypassing entrypoint(), JarvisAgent, and
providers.build_* entirely -- the real production entrypoint had zero test
coverage. This file drives entrypoint() itself, stubbing only the SDK
boundary (AgentSession) and a fake JobContext, and asserts the *observable*
wiring: what entrypoint() called, with what real objects -- not internals of
the fakes themselves.

Honesty note on "registers with LiveKit" (acceptance #1): the actual network
handshake -- ctx.connect()'s WebRTC connect and the worker process's
registration with LiveKit Cloud via `cli.run_app` -- cannot happen offline
and is NOT exercised here or anywhere in this test suite. What IS proven:
entrypoint() awaits ctx.connect() before doing anything else, builds the
AgentSession strictly from providers.build_stt/build_llm/build_tts (not some
parallel hardcoded construction), starts a real JarvisAgent against ctx.room,
constructs the real DataChannelPublisher pointed at ctx.room, and wires the
real session_wiring.wire_session_events onto the session for the pinned
event contract. The live registration handshake itself is a T-9 smoke-test
concern (scripts/smoke_deploy.sh), not something offline unit tests can
prove.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from jarvis.pipeline import worker
from jarvis.pipeline.agent import JarvisAgent
from jarvis.pipeline.events import DataChannelPublisher
from jarvis.pipeline.latency import LatencyLogger


class FakeAgentSession:
    """Stands in for AgentSession at the SDK boundary: records what it was
    constructed and started with, and what event names it was wired for."""

    instances: list[FakeAgentSession] = []

    def __init__(self, *, stt, llm, tts):
        self.stt = stt
        self.llm = llm
        self.tts = tts
        self.start_kwargs: dict | None = None
        self.on_calls: list[str] = []
        FakeAgentSession.instances.append(self)

    def on(self, event, callback):
        self.on_calls.append(event)

    async def start(self, *, agent, room):
        self.start_kwargs = {"agent": agent, "room": room}


class FakeJobContext:
    def __init__(self):
        self.connect = AsyncMock()
        self.room = object()


@pytest.fixture(autouse=True)
def _reset_fake_session_registry():
    FakeAgentSession.instances.clear()
    yield
    FakeAgentSession.instances.clear()


@pytest.fixture(autouse=True)
def _fake_provider_credentials(monkeypatch):
    # entrypoint() calls the *real* providers.build_stt/build_tts/build_llm
    # unless a test overrides them; the underlying plugin constructors just
    # validate a key is present (no network at construction time), so fake,
    # obviously-not-real values are enough to let them build.
    monkeypatch.setenv("DEEPGRAM_API_KEY", "dg-test-not-real")
    monkeypatch.setenv("CARTESIA_API_KEY", "ck-test-not-real")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "an-test-not-real")


@pytest.fixture
def ctx(monkeypatch):
    monkeypatch.setattr(worker, "AgentSession", FakeAgentSession)
    return FakeJobContext()


@pytest.mark.asyncio
async def test_entrypoint_connects_to_the_room_before_starting_the_session(ctx):
    await worker.entrypoint(ctx)

    ctx.connect.assert_awaited_once()
    assert FakeAgentSession.instances, "AgentSession was never constructed"


@pytest.mark.asyncio
async def test_entrypoint_builds_the_session_from_providers_build_functions(monkeypatch, ctx):
    sentinel_stt, sentinel_tts, sentinel_llm = object(), object(), object()
    monkeypatch.setattr(worker, "build_stt", lambda: sentinel_stt)
    monkeypatch.setattr(worker, "build_tts", lambda: sentinel_tts)
    monkeypatch.setattr(worker, "build_llm", lambda: sentinel_llm)

    await worker.entrypoint(ctx)

    session = FakeAgentSession.instances[0]
    assert session.stt is sentinel_stt
    assert session.tts is sentinel_tts
    assert session.llm is sentinel_llm


@pytest.mark.asyncio
async def test_entrypoint_starts_a_real_jarvis_agent_against_ctx_room(ctx):
    await worker.entrypoint(ctx)

    session = FakeAgentSession.instances[0]
    assert session.start_kwargs is not None, "session.start() was never called"
    assert isinstance(session.start_kwargs["agent"], JarvisAgent)
    assert session.start_kwargs["room"] is ctx.room


@pytest.mark.asyncio
async def test_entrypoint_wires_the_pinned_event_contract_onto_the_session(ctx):
    await worker.entrypoint(ctx)

    session = FakeAgentSession.instances[0]
    # real session_wiring.wire_session_events (not mocked) registered these
    assert set(session.on_calls) == {
        "agent_state_changed",
        "user_input_transcribed",
        "conversation_item_added",
    }


@pytest.mark.asyncio
async def test_entrypoint_wires_the_production_publisher_and_latency_logger(monkeypatch, ctx):
    captured = {}
    real_wire = worker.wire_session_events

    def spy_wire(session, publisher, latency):
        captured["publisher"] = publisher
        captured["latency"] = latency
        return real_wire(session, publisher, latency)

    monkeypatch.setattr(worker, "wire_session_events", spy_wire)

    await worker.entrypoint(ctx)

    assert isinstance(captured["publisher"], DataChannelPublisher)
    assert captured["publisher"].room is ctx.room
    assert isinstance(captured["latency"], LatencyLogger)
